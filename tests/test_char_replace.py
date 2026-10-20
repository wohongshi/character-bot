"""角色包「覆盖升级」：先整套备份，再换掉定义 / 人格 / 主题 / 插件。

用 test_charpack 那套「源仓库 → 目标仓库」的做法：目标里先放一个旧版本角色，
再用同一份 zip 覆盖安装，检查备份齐全、旧插件被替换、语料库不受影响。
"""
from __future__ import annotations

import io
import json
import os
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
TMP = Path(tempfile.mkdtemp(prefix="charreplace-"))
os.environ["KLEE_STATE_DIR"] = str(TMP / "ps")

from bot.characters import CharacterStore                  # noqa: E402

PASS = FAIL = 0


def check(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}  {detail}")


def make_pack(version: str) -> bytes:
    """造一个 character.json/SOUL/theme/plugins/kb 齐全的包（模拟用户改版后的导出）。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("character.json", json.dumps({
            "id": "upg", "name": f"升级版{version}", "en": "Upg", "emoji": "🧪",
            "theme": "upg", "kb": "upg", "chips": ["a"]}, ensure_ascii=False))
        zf.writestr("SOUL.md", f"# 升级版 {version}\n\n" + "这是覆盖测试用的人格。" * 8)
        zf.writestr("theme/theme.json", json.dumps({"name": f"主题{version}"}, ensure_ascii=False))
        zf.writestr("theme/theme.css", f":root{{--red:#12{version}456}}")
        zf.writestr("plugins/upg_one.py",
                    'CHARACTERS = ("upg",)\nNAME = "一号"\n\n\ndef register(api):\n'
                    f'    api.on_command("一号{version}", lambda ctx: "{version}")\n')
        zf.writestr("kb/upg.jsonl",
                    "\n".join(json.dumps({"text": f"第 {i} 条语料 {version}"},
                                         ensure_ascii=False) for i in range(2)) + "\n")
    return buf.getvalue()


DST = TMP / "dst"
(DST / "bot" / "ui_themes" / "upg").mkdir(parents=True)
(DST / "bot" / "features").mkdir(parents=True)
(DST / "state").mkdir(parents=True)
(DST / "bot" / "ui_themes" / "upg" / "theme.json").write_text('{"name": "旧主题"}',
                                                             encoding="utf-8")
(DST / "bot" / "ui_themes" / "upg" / "theme.css").write_text(":root{--red:#000000}",
                                                             encoding="utf-8")
(DST / "bot" / "features" / "upg_one.py").write_text(
    'CHARACTERS = ("upg",)\nNAME = "旧一号"\n\n\ndef register(api):\n'
    '    api.on_command("一号old", lambda ctx: "old")\n', encoding="utf-8")
(DST / "bot" / "features" / "gen_other.py").write_text(
    'NAME = "别的插件"\n\n\ndef register(api):\n    pass\n', encoding="utf-8")

import server as SV                                       # noqa: E402

SV.STUDIO_HOME = DST
SV.FEATURES_DIR = DST / "bot" / "features"
SV.RELOAD_MARK = SV.FEATURES_DIR / ".reload"
SV.STATE_DIR = DST / "state"
KB = TMP / "kb"
KB.mkdir()
SV.KB_DIR = KB
store = CharacterStore(DST / "state", seeds_dir=ROOT / "bot" / "character_seeds")
SV._char_store = lambda: store
built = []
SV._build_kb_async = lambda cid, name: built.append(cid)

print("[1] 先把旧版本装上")
old = store.add({"id": "upg", "name": "旧版", "theme": "upg", "kb": "upg"},
                soul="# 旧版\n\n" + "旧人格。" * 20)
check("旧角色存在", store.get("upg")["name"] == "旧版", str(store.get("upg")))

print("[2] 不勾覆盖 → 拒绝，且什么都不动")
try:
    SV._import_character_zip(make_pack("v2"))
    check("默认拒绝覆盖", False)
except SV.ImportError_ as e:
    check("默认拒绝覆盖", "已存在" in str(e) and "覆盖" in str(e), str(e))
check("名字还是旧的", store.get("upg")["name"] == "旧版")
check("旧插件还在", (SV.FEATURES_DIR / "upg_one.py").is_file())
check("没有留下备份目录", not (DST / "state" / "trash").exists())

print("[3] 勾上覆盖 → 备份 + 替换")
res = SV._import_character_zip(make_pack("v2"), replace=True)
check("导入成功", bool(res.get("ok")), str(res)[:160])
check("标记成覆盖", res.get("replaced") is True, str(res.get("replaced")))
check("给了备份路径", bool(res.get("backup")), str(res.get("backup")))
bak = DST / "state" / res.get("backup", "")
check("备份目录存在", bak.is_dir(), str(bak))
check("备份里有角色定义", (bak / "upg.json").is_file())
check("备份里有旧人格", (bak / "character" / "SOUL.md").is_file()
      and "旧版" in (bak / "character" / "SOUL.md").read_text(encoding="utf-8"))
check("备份里有旧主题", (bak / "theme" / "theme.json").is_file())
check("备份里有旧插件", (bak / "plugins" / "upg_one.py").is_file())

check("角色定义换成新的", store.get("upg")["name"] == "升级版v2", str(store.get("upg")["name"]))
check("人格换成新的", "升级版 v2" in store.soul("upg"), store.soul("upg")[:40])
check("主题换成新的", json.loads((SV.FEATURES_DIR.parent / "ui_themes" / "upg"
                                / "theme.json").read_text(encoding="utf-8"))["name"] == "主题v2")
check("插件换成新的（命令名变了）",
      "一号v2" in (SV.FEATURES_DIR / "upg_one.py").read_text(encoding="utf-8"))
check("别的角色的插件没被碰", (SV.FEATURES_DIR / "gen_other.py").is_file())
check("触发了一次建索引", built == ["upg"], str(built))

print("[4] 再覆盖一次（第二次备份不能覆盖第一次）")
res2 = SV._import_character_zip(make_pack("v3"), replace=True)
check("第二次也成功", bool(res2.get("ok")), str(res2)[:120])
check("备份路径不同", res2.get("backup") != res.get("backup"),
      f"{res.get('backup')} vs {res2.get('backup')}")
check("两份备份都在", (DST / "state" / res["backup"]).is_dir()
      and (DST / "state" / res2["backup"]).is_dir())
check("最新的是 v3", store.get("upg")["name"] == "升级版v3")

print("[5] 语料库不动（那是 klee-kb 的库，覆盖安装不该清）")
corpus = KB / "corpus" / "upg" / "processed" / "imported.jsonl"
check("新语料落进去了", corpus.is_file())
check("语料行数 = 2", len([x for x in corpus.read_text(encoding="utf-8").splitlines()
                           if x.strip()]) == 2)

print("[6] 面板")
HTML = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
for anchor in ('id="zipReplace"', "?replace=1", "覆盖同名角色", "d.replaced", "d.backup"):
    check(f"面板有 {anchor}", anchor in HTML)
SRV = (ROOT / "server.py").read_text(encoding="utf-8")
check("服务端读 replace 参数", 'parse_qs(u.query).get("replace")' in SRV)
check("备份函数在", "def _char_pack_backup" in SRV)

print(f"\nPASS {PASS} / {PASS + FAIL}")
sys.exit(0 if FAIL == 0 else 1)
