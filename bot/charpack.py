"""角色包导出：把「一个角色」打成能原样装回来的 zip。

和 `POST /api/characters/import` 是**同一套格式**（那个入口的读法就是这里的写法的
规格），所以导出的包能不经过任何改动直接装回另一台机器：

    character.json          角色定义（id / 名字 / 配色 / 主题 / 问候 / chips …）
    SOUL.md                 人格全文
    theme/theme.json        主题元信息
    theme/theme.css         主题样式
    plugins/<name>.py       这个角色的限定插件（CHARACTERS 里含它）
    kb/<库名>.jsonl         知识库语料（每行 {"text":…,"title":…,"source":…}）
    pack.json               这份包是怎么来的（不是导入必需的，只用来溯源）

几个刻意的取舍
--------------
- **知识库是「尽力而为」**：语料在 klee-kb 自己的 sqlite 里，那边不在/没有这个库时
  照样导出，只是 `pack.json` 里写明 `kb_rows: 0` 与原因 —— 不能让「导一个包」因为
  另一个进程没起而彻底失败。
- **不导 state**：聊天记录、记忆、心情、贴纸都是「这台机器上发生过的事」，
  不属于角色本身；塞进包里既大又会把人家的存档覆盖掉。
- **`seed` 不导**：那是「首次播种用的内置人格文件名」，导出去指向一个不存在的文件
  只会让导入端困惑（导入端本来也会把它改写成 `<id>.md`）。
- 只写 `IMPORT_ALLOWED` 里的扩展名：导入端遇到别的类型会整包拒绝，多一个 `.DS_Store`
  就装不回去。
"""
from __future__ import annotations

import io
import json
import logging
import re
import sqlite3
import time
import zipfile
from pathlib import Path

log = logging.getLogger("klee.bot.charpack")

# 和导入端的白名单保持一致（`server.IMPORT_ALLOWED`）。这里复制一份而不是
# import server：charpack 要能被单测直接调用，不能把 HTTP 服务牵扯进来。
ALLOWED = {".json", ".md", ".py", ".css", ".txt", ".jsonl",
           ".jpg", ".jpeg", ".png", ".webp"}

MAX_KB_ROWS = 20000          # 单角色语料上限（再多也不是「角色包」了）
# 下面几个上限必须**和导入端一致**（server._import_character_zip）：
# 导出端的唯一目标是「装得回去」，自己更宽松只会产出一个必然被拒的包。
MAX_PLUGINS = 60             # 导入端：>60 直接拒
MAX_SOUL = 60000             # 导入端：>60000 字拒
MIN_SOUL = 80                # 导入端：<80 字拒
IMPORT_ID = re.compile(r"^[a-z][a-z0-9_-]{1,29}$")

# 不写进 character.json 的字段：运行时/统计出来的东西，导出去只会误导导入端
_STRIP = ("active", "builtin", "soul_path", "plugins", "theme_name", "theme_version")


def db_name(cid: str) -> str:
    """角色 → 语料库文件名。klee 用旧的 `kb.sqlite3`（历史兼容，别改）。"""
    cid = str(cid or "").strip().lower()
    return "kb.sqlite3" if cid == "klee" else f"kb_{cid}.sqlite3"


def resolve_db_dir(cfg: dict | None, root: Path) -> Path:
    """语料库目录：配置 → 环境变量 → 仓库旁边的 `klee-kb/data`。"""
    import os
    raw = str((cfg or {}).get("kb_db_dir") or "").strip()
    if not raw:
        raw = str(os.environ.get("KLEE_KB_DB_DIR") or "").strip()
    if raw:
        return Path(raw).expanduser()
    return Path(root).parent / "klee-kb" / "data"


def read_kb(db_dir: Path | str, cid: str, *, count_only: bool = False
            ) -> tuple[list[dict], str]:
    """读某个角色的全部语料块。返回 (rows, 说明)；读不到就是 ([], 原因)。

    只读模式打开：导出绝不能因为拿不到写锁而卡住正在跑的知识库检索。
    """
    p = Path(db_dir) / db_name(cid)
    if not p.is_file():
        return [], f"找不到语料库（{p.name}）"
    try:
        con = sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=2.0)
    except sqlite3.Error as e:
        return [], f"打不开语料库：{e}"
    try:
        cols = {str(r[1]) for r in con.execute("PRAGMA table_info(chunks)")}
        if "text" not in cols:
            return [], "语料库里没有 chunks.text（版本不匹配）"
        if count_only:
            # 只数条数：面板的 dry 预览要显示「多少块语料」，没必要把正文全读出来
            try:
                n = int(con.execute(
                    "SELECT COUNT(*) FROM chunks WHERE trim(COALESCE(text,'')) <> ''"
                ).fetchone()[0])
            except sqlite3.Error as e:
                return [], f"读语料库失败：{e}"
            return [{}] * min(n, MAX_KB_ROWS), ""
        pick = [c for c in ("text", "title", "source", "type") if c in cols]
        sql = f"SELECT {', '.join(pick)} FROM chunks LIMIT {int(MAX_KB_ROWS)}"
        rows = []
        for rec in con.execute(sql):
            d = dict(zip(pick, rec))
            text = str(d.get("text") or "").strip()
            if not text:
                continue
            rows.append({"text": text,
                         "title": str(d.get("title") or "")[:200],
                         "source": str(d.get("source") or "")[:80],
                         "type": str(d.get("type") or "")[:40]})
        return rows, ""
    except sqlite3.Error as e:
        return [], f"读语料库失败：{e}"
    finally:
        con.close()


def _plugin_files(plugins_dir: Path, cid: str) -> list[tuple[str, bytes]]:
    """挑出 CHARACTERS 含这个角色的插件源码（按文件扫，不执行）。"""
    import re
    pat = re.compile(r"^CHARACTERS\s*=\s*(?:\(([^)]*)\)|\[\s*([^\]]*)\])", re.M)
    out = []
    cid = str(cid or "").strip().lower()
    for p in sorted(Path(plugins_dir).glob("*.py")):
        if p.name.startswith("_"):
            continue
        try:
            src = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        m = pat.search(src)
        if not m:
            continue
        names = re.findall(r"""["']([\w-]+)["']""", (m.group(1) or m.group(2) or ""))
        if cid in [n.lower() for n in names]:
            out.append((p.name, src.encode("utf-8")))
            if len(out) >= MAX_PLUGINS:
                break
    return out


def _theme_files(root: Path, theme: str) -> list[tuple[str, bytes]]:
    d = Path(root) / "bot" / "ui_themes" / str(theme or "")
    if not d.is_dir():
        return []
    out = []
    for p in sorted(d.rglob("*")):
        if not p.is_file() or p.suffix.lower() not in ALLOWED:
            continue
        rel = p.relative_to(d).as_posix()
        if ".." in rel.split("/"):
            continue
        try:
            out.append((f"theme/{rel}", p.read_bytes()))
        except OSError as e:
            log.info("角色包：读主题文件 %s 失败（跳过）：%s", p, e)
    return out


def build_pack(root: Path, store, cid: str, *, plugins_dir: Path | None = None,
               kb_dir: Path | str | None = None,
               include_kb: bool = True, include_plugins: bool = True,
               zip_too: bool = True) -> tuple[bytes, str, dict]:
    """组装 zip。返回 (bytes, 文件名, 清单)；角色不存在/装不回去时抛 ValueError。

    `zip_too=False` 只出清单（面板的 dry 预览）：不读语料正文、不压缩。
    """
    root = Path(root)
    cid = str(cid or "").strip().lower()
    info = store.get(cid)
    if not info:
        raise ValueError(f"没有角色 {cid!r}")
    if not IMPORT_ID.match(cid):
        # 角色库允许数字开头 / 31-32 位，导入端不允许 —— 这种 id 导出去装不回来
        raise ValueError(f"角色 id {cid!r} 不符合导入规则（小写字母开头、2-30 位）")

    soul_path = store.soul_path(cid)
    try:
        soul = soul_path.read_text(encoding="utf-8")
    except OSError as e:
        raise ValueError(f"读不到人格文件（{soul_path.name}）：{e}") from None
    if len(soul.strip()) < MIN_SOUL:
        # 导入端要求 ≥80 字；导出一个装不回去的包毫无意义
        raise ValueError(f"人格文件太短（不足 {MIN_SOUL} 字），装了也进不去")
    if len(soul) > MAX_SOUL:
        raise ValueError(f"人格文件超过 {MAX_SOUL} 字，装了也进不去")

    payload = {k: v for k, v in dict(info).items() if k not in _STRIP}
    payload.pop("seed", None)                 # 见模块说明：那是本地播种用的文件名
    payload["id"] = cid
    payload["theme"] = str(info.get("theme") or cid)

    files: list[tuple[str, bytes]] = []
    files.append(("SOUL.md", soul.encode("utf-8")))
    theme_files = _theme_files(root, payload["theme"])
    # 导入端硬性要求 theme/theme.json 存在、能解析、有 name。角色定义允许任意
    # theme token（面板加角色不校验），所以「主题目录不在」的角色必须在这里拦住，
    # 否则又是一个「导出成功、装不回去」的包。
    tj = next((blob for name, blob in theme_files if name == "theme/theme.json"), None)
    if tj is None:
        raise ValueError("这个角色的主题文件缺失（theme/theme.json），装回去会被拒")
    try:
        meta = json.loads(tj.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise ValueError("主题文件 theme.json 不是合法 JSON，装回去会被拒") from None
    if not isinstance(meta, dict) or not str(meta.get("name") or "").strip():
        raise ValueError("主题文件 theme.json 缺少 name，装回去会被拒")
    files.extend(theme_files)

    plugs = _plugin_files(Path(plugins_dir or (root / "bot" / "features")), cid) \
        if include_plugins else []
    files.extend((f"plugins/{name}", blob) for name, blob in plugs)

    kb_rows: list[dict] = []
    kb_note = ""
    if include_kb:
        if kb_dir is None:
            kb_dir = resolve_db_dir({}, root)
        kb_rows, kb_note = read_kb(kb_dir, cid, count_only=not zip_too)
        if kb_rows and zip_too:
            # 一行一块：导入端按行解析，混进换行会把它拆碎
            body = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in kb_rows)
            files.append((f"kb/{cid}.jsonl", body.encode("utf-8")))

    if include_plugins and not plugs:
        # 导入端硬性要求至少一个插件；导出一个必然被拒的包只会让人白折腾
        raise ValueError("这个角色没有专属插件，导出的包装不回去（导入要求至少一个）")

    manifest = {
        "format": "character-pack/1",
        "exported_at": int(time.time()),
        "from": "character-bot",
        "id": cid,
        "name": str(info.get("name") or cid),
        "soul_chars": len(soul),
        "theme": payload["theme"],
        "plugins": [n for n, _ in plugs],
        "kb_rows": len(kb_rows),
        "kb_note": kb_note,
        "note": "人物档案、主题、插件与语料；不含聊天记录/记忆/心情",
    }
    payload["pack"] = {"format": manifest["format"], "exported_at": manifest["exported_at"]}
    files.append(("character.json",
                  (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")))
    files.append(("pack.json",
                  (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")))

    if not zip_too:
        return b"", f"{cid}-pack.zip", manifest

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for name, blob in files:
            info_z = zipfile.ZipInfo(name, date_time=time.localtime(time.time())[:6])
            info_z.compress_type = zipfile.ZIP_DEFLATED
            # 权限统一 0644：Windows 上解压不会变成「仅自己可读」的怪文件
            info_z.external_attr = 0o644 << 16
            zf.writestr(info_z, blob)
    return buf.getvalue(), f"{cid}-pack.zip", manifest
