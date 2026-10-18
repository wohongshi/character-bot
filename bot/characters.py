"""角色定义与存储：数据驱动，可手动新增角色。

定义来源（后者覆盖前者，按 id 合并）：
1. `BUILTIN`：仓库内置的五个角色（代码里的默认值）；
2. `bot/characters.json`：仓库级覆盖/追加（可选，随代码版本走）；
3. `state/characters.d/<id>.json`：运行期角色（面板新增或改绑定，落在这里）。

每个角色：
- 人格 `state/characters/<id>/SOUL.md`（首次从 `bot/character_seeds/<seed>` 播种）；
- 知识库 `klee-kb/data/kb_<id>.sqlite3`（klee 用 `kb.sqlite3` 兼容历史）；
- 界面主题 `bot/ui_themes/<theme>/`（见 themes.py）。
当前角色存在 `state/characters.json`，只能由面板切换。
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import threading
import time
from pathlib import Path

log = logging.getLogger("klee.bot.characters")

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
_HEX_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")
_TOKEN_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
_SEED_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}\.md$")


def _safe_token(value, default: str) -> str:
    """角色 id 形态的字段（kb / theme）：不是纯 token 就回默认值。"""
    v = str(value or "").strip().lower()
    return v if _TOKEN_RE.match(v) else str(default)


def _safe_seed(value, default: str) -> str:
    """种子文件名：必须是一个 .md 文件名，不能带路径分隔符或 ..。"""
    v = str(value or "").strip()
    if _SEED_RE.match(v) and ".." not in v and "/" not in v and "\\" not in v:
        return v
    return str(default)

# 顺序就是面板里的默认顺序
BUILTIN = [
    {
        "id": "klee", "name": "可莉", "en": "Klee", "emoji": "💥",
        "tagline": "蒙德城的火花骑士", "color": "#e4573d", "color2": "#ffd9c9",
        "kb": "klee", "seed": "klee.md", "theme": "klee",
        "greeting": "哒哒哒！可莉在这里哦～",
        "hero_note": "人格与知识库都属于可莉；切换角色请到「角色」页。",
        "chips": ["讲个蒙德的故事", "猜个谜语", "夸夸我", "你今天在干嘛呀"],
    },
    {
        "id": "hutao", "name": "胡桃", "en": "Hu Tao", "emoji": "🦋",
        "tagline": "往生堂第七十七代堂主", "color": "#b23a48", "color2": "#ffe3d6",
        "kb": "hutao", "seed": "hutao.md", "theme": "hutao",
        "greeting": "哟，客官，来坐～",
        "hero_note": "往生堂今日也开着门；想聊什么，慢慢说。",
        "chips": ["来一首打油诗", "往生堂今天忙吗", "你怎么看生死", "陪我聊会儿"],
    },
    {
        "id": "citlali", "name": "茜特菈莉", "en": "Citlali", "emoji": "🔮",
        "tagline": "纳塔的烟谜主", "color": "#3f7c8a", "color2": "#dff1ef",
        "kb": "citlali", "seed": "citlali.md", "theme": "citlali",
        "greeting": "嗯……烟还没散，先坐吧。",
        "hero_note": "烟谜主的占卜不替你走路，只告诉你风向。",
        "chips": ["帮我看看今天的征兆", "你又在睡觉吗", "给我一句预言", "烟里有什么"],
    },
    {
        "id": "cyrene", "name": "昔莲", "en": "Cyrene", "emoji": "🕯️",
        "tagline": "翁法罗斯的记忆旅人", "color": "#8a6bb3", "color2": "#eee4ff",
        "kb": "cyrene", "seed": "cyrene.md", "theme": "cyrene",
        "greeting": "我在听。你慢慢说。",
        "hero_note": "被记住的人不会真正消失；这里的记忆只属于昔莲。",
        "chips": ["听我讲个过去的故事", "你会忘记我吗", "陪我想想以前的事", "说点什么吧"],
    },
    {
        "id": "honghong", "name": "涂山红红", "en": "Tushan Honghong", "emoji": "🦊",
        "tagline": "涂山之王 · 红线仙", "color": "#c8322f", "color2": "#ffd9c2",
        "kb": "honghong", "seed": "honghong.md", "theme": "honghong",
        "greeting": "涂山，我罩的。……说吧，找我什么事。",
        "hero_note": "涂山之王，也是替人和妖续缘的红线仙。她的过去比五百年还长。",
        "chips": ["给我讲讲苦情树", "红线仙是做什么的", "东方月初是谁", "帮我看看这段缘分"],
    },
    {
        "id": "atri", "name": "亚托莉", "en": "ATRI", "emoji": "⚙️",
        "tagline": "高性能机器人少女", "color": "#3f7fb8", "color2": "#dcefff",
        "kb": "atri", "seed": "atri.md", "theme": "atri",
        "greeting": "你好，我是亚托莉。",
        "hero_note": "高性能机器人，正在学习「心」是什么。",
        "chips": ["你在算什么", "心是什么", "今天开心吗", "陪我聊会儿"],
    },
]

# 兼容旧代码/测试：内置列表本身
CHARACTERS = BUILTIN
DEFAULT_ID = "klee"
SAVE_LOCK = threading.Lock()

_FIELDS = ("id", "name", "en", "emoji", "tagline", "color", "color2",
           "kb", "seed", "theme", "greeting", "hero_note", "chips")


class CharacterError(ValueError):
    pass


def _clean_def(raw: dict, *, fallback_theme: str = "klee") -> dict:
    """收敛一个角色定义：只保留已知字段，补默认值，非法值拒绝。"""
    if not isinstance(raw, dict):
        raise CharacterError("角色定义必须是对象")
    cid = str(raw.get("id") or "").strip().lower()
    if not _ID_RE.match(cid):
        raise CharacterError("id 只能是小写字母/数字/-/_，最长 32（且必须以字母或数字开头）")
    name = str(raw.get("name") or "").strip()
    if not name:
        raise CharacterError("name 不能为空")
    out = {k: raw.get(k) for k in _FIELDS}
    out.update({"id": cid, "name": name[:40],
                "en": str(raw.get("en") or cid)[:40],
                "emoji": str(raw.get("emoji") or "📖")[:8],
                "tagline": str(raw.get("tagline") or "")[:80],
                # 这三个会被拼成文件路径（seeds/、kb_<id>.sqlite3、ui_themes/）：
                # 只接受纯文件名/纯 id，挡掉 ../../etc/passwd 这类越界。
                "kb": _safe_token(raw.get("kb") or cid, cid),
                "seed": _safe_seed(raw.get("seed") or f"{cid}.md", f"{cid}.md"),
                "theme": _safe_token(raw.get("theme") or fallback_theme, fallback_theme),
                "greeting": str(raw.get("greeting") or f"我是{name}。")[:120],
                "hero_note": str(raw.get("hero_note") or "")[:200]})
    for key, default in (("color", "#e4573d"), ("color2", "#ffe3d6")):
        val = str(raw.get(key) or default)
        out[key] = val if _HEX_RE.match(val) else default
    chips = raw.get("chips")
    if isinstance(chips, str):
        chips = re.split(r"[\n,，]+", chips)
    elif not isinstance(chips, (list, tuple)):
        chips = []                     # 5 / true / null 之类：当没有，别让推导式抛 TypeError
    out["chips"] = [str(x).strip()[:40] for x in chips if str(x).strip()][:8]
    return out


def _read_json(path: Path):
    """读可选的 JSON 定义文件。

    文件不存在是**正常情况**（`bot/characters.json` 就是可选的仓库级覆盖，
    没建也别在日志里吓人）；只有「文件在但读不动 / JSON 坏了」才告警。
    """
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as e:
        log.warning("读取 %s 失败：%s", path, e)
        return None


def load_definitions(state_dir: Path) -> dict:
    """合并三层定义，返回有序 dict（内置顺序优先，新角色按 id 排序在后）。"""
    defs: dict[str, dict] = {}
    for c in BUILTIN:
        d = _clean_def(c)
        defs[d["id"]] = d

    repo_file = Path(__file__).with_name("characters.json")
    data = _read_json(repo_file)
    if data:
        rows = data.get("characters") if isinstance(data, dict) else data
        if isinstance(rows, dict):        # 单个角色对象也认
            rows = [rows]
        if not isinstance(rows, (list, tuple)):
            # `{"characters": 5}` 这种：直接迭代会 TypeError，整个启动挂掉
            log.warning("characters.json 的 characters 必须是数组，已忽略")
            rows = []
        for row in rows:
            try:
                d = _clean_def(row)
                defs[d["id"]] = d
            except CharacterError as e:
                log.warning("characters.json 里的角色被忽略：%s", e)

    user_dir = Path(state_dir) / "characters.d"
    if user_dir.is_dir():
        for p in sorted(user_dir.glob("*.json")):
            data = _read_json(p)
            if not data:
                continue
            if isinstance(data, dict) and "characters" in data:
                data = data["characters"]
            for row in (data if isinstance(data, list) else [data]):
                try:
                    d = _clean_def(row)
                    defs[d["id"]] = d
                except CharacterError as e:
                    log.warning("角色文件 %s 被忽略：%s", p.name, e)
    return defs


def normalize_id(cid: str, defs: dict | None = None) -> str:
    cid = str(cid or "").strip().lower()
    defs = defs or {c["id"]: c for c in BUILTIN}
    return cid if cid in defs else DEFAULT_ID


class CharacterStore:
    def __init__(self, state_dir: Path, seeds_dir: Path | None = None):
        self.state_dir = Path(state_dir)
        self.dir = self.state_dir / "characters"
        self.user_dir = self.state_dir / "characters.d"
        self.seeds = Path(seeds_dir) if seeds_dir else Path(__file__).with_name("character_seeds")
        self.path = self.state_dir / "characters.json"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.user_dir.mkdir(parents=True, exist_ok=True)
        self.defs = load_definitions(self.state_dir)
        self._ensure_souls()
        self.data = self._load()

    # ---------- 初始化 ----------
    def reload_defs(self) -> dict:
        self.defs = load_definitions(self.state_dir)
        self._ensure_souls()
        # 注意：不能写 `if self.active_id() not in self.defs` —— active_id()
        # 在 id 不存在时已经回 DEFAULT_ID，条件永远为假，坏值就永远留在文件里。
        if self.data.get("active") not in self.defs:
            self.data["active"] = DEFAULT_ID
        return self.defs

    def _ensure_souls(self) -> None:
        for c in self.defs.values():
            dest = self.dir / c["id"] / "SOUL.md"
            if dest.exists():
                continue
            src = self.seeds / c.get("seed", f"{c['id']}.md")
            try:
                dest.parent.mkdir(parents=True, exist_ok=True)
                # 先写临时文件再 os.replace：中途失败（磁盘满/被 kill）不会留下
                # 半截 SOUL.md —— 那种文件下次启动会被 exists() 当成「已播种」，
                # 角色的人格就永久缺一块，而且没有任何报错。
                tmp = dest.with_name(f"{dest.name}.{os.getpid()}.{threading.get_ident()}.tmp")
                try:
                    if src.exists():
                        shutil.copy2(src, tmp)
                    else:
                        tmp.write_text(
                            f"# {c['name']}\n\n你是{c['name']}。用自然的短句聊天，像在手机上发消息。\n",
                            encoding="utf-8")
                    os.replace(tmp, dest)
                finally:
                    try:
                        tmp.unlink(missing_ok=True)
                    except OSError:
                        pass
                log.info("角色 %s 人格已播种：%s", c["id"], dest)
            except OSError as e:
                log.warning("角色 %s 人格播种失败：%s", c["id"], e)

    def _load(self) -> dict:
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(d, dict):
                raise ValueError("不是对象")
        except FileNotFoundError:
            d = {}
        except (OSError, ValueError) as e:
            log.warning("characters.json 读取失败（%s），使用默认角色", e)
            d = {}
        active = str(d.get("active") or DEFAULT_ID)
        if active not in self.defs:
            active = DEFAULT_ID
        order = d.get("order")
        if not isinstance(order, (list, tuple)):
            order = []                    # `"order": 5` 迭代会 TypeError
        return {"active": active, "order": [str(x) for x in order]}

    def save(self) -> None:
        with SAVE_LOCK:
            tmp = self.path.with_name(f"{self.path.name}.{os.getpid()}.tmp")
            tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
            os.replace(tmp, self.path)

    # ---------- 查询 ----------
    def list(self) -> list[dict]:
        out = []
        for c in self.defs.values():
            d = dict(c)
            d["soul_path"] = str(self.dir / c["id"] / "SOUL.md")
            d["builtin"] = c["id"] in {x["id"] for x in BUILTIN}
            out.append(d)
        return out

    def get(self, cid: str) -> dict | None:
        return self.defs.get(str(cid or "").strip().lower())

    def active_id(self) -> str:
        aid = self.data.get("active") or DEFAULT_ID
        return aid if aid in self.defs else DEFAULT_ID

    def active(self) -> dict:
        return self.defs[self.active_id()]

    def set_active(self, cid: str) -> dict:
        cid = str(cid or "").strip().lower()
        if cid not in self.defs:
            raise CharacterError(f"没有角色 {cid!r}")
        self.data["active"] = cid
        self.save()
        log.info("当前角色切换为 %s", cid)
        return self.defs[cid]

    # ---------- 新增 / 修改 ----------
    def _write_user_def(self, definition: dict) -> Path:
        self.user_dir.mkdir(parents=True, exist_ok=True)
        p = self.user_dir / f"{definition['id']}.json"
        tmp = p.with_name(f"{p.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_text(json.dumps(definition, ensure_ascii=False, indent=2) + "\n",
                       encoding="utf-8")
        os.replace(tmp, p)
        return p

    def add(self, payload: dict, soul: str = "") -> dict:
        """新增或更新一个角色（写入 state/characters.d/<id>.json）。"""
        d = _clean_def(payload)
        if d["id"] in {x["id"] for x in BUILTIN} and not payload.get("allow_builtin"):
            # 允许改内置角色的主题/文案，但 id 冲突时按「覆盖」处理
            log.info("角色 %s 是内置角色，覆盖写入运行期定义", d["id"])
        self._write_user_def(d)
        self.reload_defs()
        if soul and soul.strip():
            self.save_soul(d["id"], soul)
        if d["id"] not in self.defs:
            raise CharacterError("角色写入后未生效，检查 state/characters.d/")
        log.info("角色已保存：%s（%s）", d["id"], d["name"])
        return self.defs[d["id"]]

    def set_theme(self, cid: str, theme_id: str) -> dict:
        cid = str(cid or "").strip().lower()
        theme_id = str(theme_id or "").strip().lower()
        cur = self.get(cid)
        if not cur:
            raise CharacterError(f"没有角色 {cid!r}")
        if not theme_id:
            raise CharacterError("theme 不能为空")
        if not _TOKEN_RE.match(theme_id):
            raise CharacterError("theme 只能是小写字母/数字/-/_，最长 32")
        d = {k: cur.get(k) for k in _FIELDS}
        d["theme"] = theme_id
        self._write_user_def(d)
        self.reload_defs()
        return self.defs[cid]

    # ---------- 人格 ----------
    def soul_path(self, cid: str) -> Path:
        cid = normalize_id(cid, self.defs)
        return self.dir / cid / "SOUL.md"

    def soul(self, cid: str) -> str:
        try:
            return self.soul_path(cid).read_text(encoding="utf-8")
        except OSError:
            return ""

    def save_soul(self, cid: str, text: str) -> dict:
        cid = str(cid or "").strip().lower()
        if cid not in self.defs:
            raise CharacterError(f"没有角色 {cid!r}")
        text = str(text or "")
        if not text.strip():
            raise CharacterError("人格不能为空")
        p = self.soul_path(cid)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(f"SOUL.md.{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, p)
        return {"chars": len(text), "est_tokens": int(len(text) * 0.65), "path": str(p)}
