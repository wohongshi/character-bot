"""角色包导出：结构正确 + 能原样装回去（导出→导入 全链路）。

跑法：`python3 tests/test_charpack.py`（不联网；导入那段把 server 的路径全局
换成临时目录，不碰生产的主题/插件/语料）。
"""
from __future__ import annotations

import io
import json
import os
import sqlite3
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
TMP = Path(tempfile.mkdtemp(prefix="charpack-"))
os.environ["KLEE_STATE_DIR"] = str(TMP / "ps")

from bot import charpack                              # noqa: E402
from bot.characters import CharacterStore             # noqa: E402

PASS = FAIL = 0


def check(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}  {detail}")


def make_kb(dirpath: Path, cid: str, n: int = 5) -> Path:
    """造一个和 klee-kb 同构的语料库（只用到 chunks 表）。"""
    dirpath.mkdir(parents=True, exist_ok=True)
    p = dirpath / charpack.db_name(cid)
    con = sqlite3.connect(p)
    con.execute("CREATE TABLE chunks (id INTEGER PRIMARY KEY, source TEXT, type TEXT,"
                " title TEXT, text TEXT, embed_text TEXT)")
    con.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
    for i in range(n):
        con.execute("INSERT INTO chunks (source, type, title, text, embed_text)"
                    " VALUES (?,?,?,?,?)",
                    ("测试来源", "设定", f"标题{i}", f"第 {i} 块正文，带一个换行\n第二行", ""))
    con.commit()
    con.close()
    return p


STORE = CharacterStore(TMP / "state", seeds_dir=ROOT / "bot" / "character_seeds")

print("[1] 数据库文件名")
check("klee 用历史库名", charpack.db_name("klee") == "kb.sqlite3")
check("其它角色按 id 命名", charpack.db_name("hutao") == "kb_hutao.sqlite3")
check("大小写/空白都归一", charpack.db_name("  HuTao ") == "kb_hutao.sqlite3")

print("[2] 语料读取")
KBD = TMP / "kbdata"
make_kb(KBD, "hutao", 5)
rows, why = charpack.read_kb(KBD, "hutao")
check("读到 5 块", len(rows) == 5 and not why, why)
check("字段齐全", all({"text", "title", "source", "type"} <= set(r) for r in rows), str(rows[:1]))
rows2, why2 = charpack.read_kb(KBD, "nobody")
check("没有这个库 → 空 + 原因（不抛）", rows2 == [] and "找不到" in why2, why2)
bad = TMP / "bad"
bad.mkdir()
(bad / charpack.db_name("x")).write_bytes(b"not a database")
rows3, why3 = charpack.read_kb(bad, "x")
check("坏库 → 空 + 原因（不抛）", rows3 == [] and why3, why3)

print("[3] 打包结构")
blob, fname, man = charpack.build_pack(ROOT, STORE, "hutao", kb_dir=KBD)
check("文件名带角色", fname == "hutao-pack.zip", fname)
zf = zipfile.ZipFile(io.BytesIO(blob))
names = zf.namelist()
check("有 SOUL.md", "SOUL.md" in names)
check("有 character.json / pack.json", "character.json" in names and "pack.json" in names)
check("主题跟着走", "theme/theme.json" in names and "theme/theme.css" in names)
check("插件全在 plugins/", sum(1 for n in names if n.startswith("plugins/")) == len(man["plugins"]),
      str(len(man["plugins"])))
check("语料是 jsonl", any(n.startswith("kb/") and n.endswith(".jsonl") for n in names))
check("扩展名都在导入白名单里",
      all(Path(n).suffix.lower() in charpack.ALLOWED for n in names),
      str([n for n in names if Path(n).suffix.lower() not in charpack.ALLOWED]))
check("没有把人家的 state 塞进去",
      not any(n.startswith(("state/", "memories/", "sessions/")) for n in names))

c = json.loads(zf.read("character.json"))
check("character.json 有 id/名字", c.get("id") == "hutao" and c.get("name") == "胡桃", str(c)[:90])
check("不带运行时字段", not ({"soul_path", "active", "builtin"} & set(c)), str(sorted(c)))
check("不带 seed（那是本地播种用的）", "seed" not in c)
check("主题指向自己", c.get("theme") == "hutao", str(c.get("theme")))
check("SEED 里内容就是 SOUL", zf.read("SOUL.md").decode() == STORE.soul("hutao"))

kb_lines = [json.loads(x) for x in zf.read(f"kb/hutao.jsonl").decode().splitlines() if x.strip()]
check("语料 5 行", len(kb_lines) == 5, str(len(kb_lines)))
check("语料的换行没把行拆碎", all("\n" in r["text"] for r in kb_lines), str(kb_lines[:1]))

plug = zf.read("plugins/poem.py").decode()
check("插件源码是原样复制的", plug == (ROOT / "bot" / "features" / "poem.py").read_text(encoding="utf-8"))

print("[4] 只导一部分")
blob2, _, man2 = charpack.build_pack(ROOT, STORE, "hutao", kb_dir=KBD, include_kb=False)
zf2 = zipfile.ZipFile(io.BytesIO(blob2))
check("不导语料时不带 kb/", not any(n.startswith("kb/") for n in zf2.namelist()))
check("清单里写明 kb_rows=0", man2["kb_rows"] == 0)
blob3, _, man3 = charpack.build_pack(ROOT, STORE, "hutao", kb_dir=KBD, include_plugins=False)
check("不导插件时不带 plugins/",
      not any(n.startswith("plugins/") for n in zipfile.ZipFile(io.BytesIO(blob3)).namelist()))

print("[5] 失败要说人话")
for bad_id, why_ok in (("nobody", "没有角色"), ("", "没有角色")):
    try:
        charpack.build_pack(ROOT, STORE, bad_id, kb_dir=KBD)
        check(f"未知角色被拒（{bad_id!r}）", False)
    except ValueError as e:
        check(f"未知角色被拒（{bad_id!r}）", why_ok in str(e), str(e))

# 一个 SOUL 太短的角色：装不回去，就别导出一个白折腾的包
short = CharacterStore(TMP / "state2", seeds_dir=ROOT / "bot" / "character_seeds")
short.add({"id": "tiny", "name": "小"}, soul="# 小\n\n短。")
try:
    charpack.build_pack(ROOT, short, "tiny", kb_dir=KBD)
    check("人格太短 → 拒绝导出", False)
except ValueError as e:
    check("人格太短 → 拒绝导出", "太短" in str(e), str(e))

print("[6] 导出 → 导入 全链路")
# 内置角色在任何新 store 里都「已存在」（导入拒绝覆盖），所以这一节用一个
# 自己造的角色，从「源仓库」导出、装进「另一个仓库」。
import server as SV                                       # noqa: E402

SRC = TMP / "src"
(SRC / "bot" / "ui_themes" / "packtest").mkdir(parents=True)
(SRC / "bot" / "features").mkdir(parents=True)
(SRC / "bot" / "ui_themes" / "packtest" / "theme.json").write_text(
    json.dumps({"name": "测试主题", "version": "1.0"}), encoding="utf-8")
(SRC / "bot" / "ui_themes" / "packtest" / "theme.css").write_text(
    ":root{--red:#123456}", encoding="utf-8")
# 两个插件：一个属于这个角色，一个通用（不该被导出去）
(SRC / "bot" / "features" / "pt_one.py").write_text(
    'CHARACTERS = ("packtest",)\nNAME = "一号"\n\n\ndef register(api):\n'
    '    api.on_command("一号命令", lambda ctx: "ok")\n', encoding="utf-8")
(SRC / "bot" / "features" / "pt_general.py").write_text(
    'NAME = "通用"\n\n\ndef register(api):\n    api.on_command("通用命令", lambda ctx: "ok")\n',
    encoding="utf-8")

src_store = CharacterStore(SRC / "state", seeds_dir=ROOT / "bot" / "character_seeds")
SOUL_TEXT = "# 测试角色\n\n" + "你是测试角色，说话短、爱吐槽。" * 6 + "\n"
src_store.add({"id": "packtest", "name": "测试角色", "en": "PackTest", "emoji": "🧪",
               "tagline": "只用来验证角色包", "theme": "packtest", "kb": "packtest",
               "chips": ["你好", "再见"]}, soul=SOUL_TEXT)
SRCKB = TMP / "srckb"
make_kb(SRCKB, "packtest", 3)

blob_s, fname_s, man_s = charpack.build_pack(SRC, src_store, "packtest",
                                             plugins_dir=SRC / "bot" / "features",
                                             kb_dir=SRCKB)
zf_s = zipfile.ZipFile(io.BytesIO(blob_s))
check("只导出属于它的插件", man_s["plugins"] == ["pt_one.py"], str(man_s["plugins"]))
check("包里有主题和语料", "theme/theme.css" in zf_s.namelist() and
      any(n.startswith("kb/") for n in zf_s.namelist()))

DST = TMP / "dst"
(DST / "bot" / "ui_themes").mkdir(parents=True)
(DST / "bot" / "features").mkdir(parents=True)
(DST / "state").mkdir(parents=True)
DSTKB = TMP / "dstkb"
(DSTKB / "corpus").mkdir(parents=True)

SV.STUDIO_HOME = DST
SV.FEATURES_DIR = DST / "bot" / "features"
SV.RELOAD_MARK = SV.FEATURES_DIR / ".reload"
SV.KB_DIR = DSTKB
_new_store = CharacterStore(DST / "state", seeds_dir=ROOT / "bot" / "character_seeds")
_orig_store = SV._char_store
SV._char_store = lambda: _new_store
_built = []
_orig_build = SV._build_kb_async
SV._build_kb_async = lambda cid, name: _built.append(cid)
try:
    check("目标仓库里还没有这个角色", _new_store.get("packtest") is None)
    res = SV._import_character_zip(blob_s)
    check("导入成功", bool(res.get("ok")), str(res)[:160])
    got = _new_store.get("packtest")
    check("角色建出来了", bool(got) and got["name"] == "测试角色", str(got)[:90])
    check("人格一字不差", _new_store.soul("packtest").strip() == SOUL_TEXT.strip())
    check("主题装进去了", (DST / "bot" / "ui_themes" / "packtest" / "theme.css").is_file())
    check("插件装进去了", res.get("plugins") == ["pt_one.py"], str(res.get("plugins")))
    corpus = DSTKB / "corpus" / "packtest" / "processed" / "imported.jsonl"
    check("语料落到 corpus", corpus.is_file() and
          len([x for x in corpus.read_text(encoding="utf-8").splitlines() if x.strip()]) == 3)
    check("触发了一次建索引", _built == ["packtest"], str(_built))
    try:
        SV._import_character_zip(blob_s)
        check("重复导入被拒（不覆盖）", False)
    except SV.ImportError_ as e:
        check("重复导入被拒（不覆盖）", "已存在" in str(e), str(e))
finally:
    SV._char_store = _orig_store
    SV._build_kb_async = _orig_build

print(f"\nPASS {PASS} / {PASS + FAIL}")
sys.exit(0 if FAIL == 0 else 1)
