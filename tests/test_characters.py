#!/usr/bin/env python3
"""角色系统：CharacterStore（SOUL 播种/切换/备份）+ 插件角色门控。"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

STUDIO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(STUDIO))

from bot.characters import CharacterStore, CHARACTERS
from bot.framework import FeatureManager

TMP = Path(tempfile.gettempdir()) / "klee_char_test"
if TMP.exists():
    shutil.rmtree(TMP)
(TMP / "features").mkdir(parents=True)
(TMP / "state").mkdir(parents=True)
(TMP / "features" / "config.json").write_text(json.dumps(
    {"enabled": ["common"], "settings": {}}), encoding="utf-8")

(TMP / "features" / "common.py").write_text('''
NAME = "通用"
DESCRIPTION = "所有角色都能用"


def register(api):
    api.on_command("common", lambda ctx: "ok")
''', encoding="utf-8")
(TMP / "features" / "shared.py").write_text('''
NAME = "双角色限定"
DESCRIPTION = "klee/hutao 可用"
CHARACTERS = ("klee", "hutao")


def register(api):
    api.on_command("shared", lambda ctx: "ok")
''', encoding="utf-8")
(TMP / "features" / "onlyhutao.py").write_text('''
NAME = "胡桃限定"
DESCRIPTION = "只有胡桃"
CHARACTERS = ("hutao",)


def register(api):
    api.on_command("hutao_only", lambda ctx: "ok")
''', encoding="utf-8")
(TMP / "features" / "cyrene.py").write_text('''
NAME = "昔莲限定"
CHARACTERS = ("cyrene",)


def register(api):
    api.on_command("cyrene_only", lambda ctx: "ok")
''', encoding="utf-8")

PASS, FAIL = [], []


def check(name, ok, extra=""):
    (PASS if ok else FAIL).append(name)
    print(f"  {'✓' if ok else '✗'} {name}" + (f"  [{extra}]" if extra and not ok else ""))


class FakeAdapter:
    owner_id = "1001"
    features_dir = str(TMP / "features")
    feature_prefix = "."
    active_character = "klee"
    active_character_info = {"id": "klee", "name": "可莉"}

    def _chat_id(self, is_group, group_id, user_id):
        return f"private:{user_id}"

    async def send(self, *a, **k):
        pass

    async def send_image_file(self, *a, **k):
        pass


print("[1] CharacterStore")
store = CharacterStore(TMP / "state", seeds_dir=STUDIO / "bot" / "character_seeds")
ids = [c["id"] for c in store.list()]
check("1.1 五个角色齐全", ids == [c["id"] for c in CHARACTERS] and len(ids) == 5, str(ids))
check("1.2 默认角色可莉", store.active_id() == "klee")
missing = [c["id"] for c in store.list() if not Path(c["soul_path"]).is_file()]
check("1.3 SOUL 全部播种", not missing, str(missing))
souls = {c["id"]: store.soul(c["id"]) for c in store.list()}
check("1.4 每个角色人格都非空且含名字",
      all(len(souls[c["id"]]) > 200 and c["name"] in souls[c["id"]] for c in store.list()),
      str({k: len(v) for k, v in souls.items()}))
store.set_active("hutao")
check("1.5 切换落盘", CharacterStore(TMP / "state").active_id() == "hutao")
try:
    store.set_active("nope")
    check("1.6 未知角色被拒", False)
except ValueError:
    check("1.6 未知角色被拒", True)
res = store.save_soul("hutao", souls["hutao"] + "\n\n## 测试追加\n")
check("1.7 保存人格·不留回档",
      (store.soul_path("hutao").read_text(encoding="utf-8").endswith("测试追加\n"))
      and not store.soul_path("hutao").with_name("SOUL.md.bak").exists(), str(res))
try:
    store.save_soul("hutao", "   ")
    check("1.8 空人格被拒", False)
except ValueError:
    check("1.8 空人格被拒", True)
check("1.9 角色定义有主题色", all(c.get("color") and c.get("color2") for c in CHARACTERS))

print("[1b] 手动新增角色 / 主题绑定")
newbie = store.add({"id": "newbie", "name": "新角色", "emoji": "🧪",
                    "theme": "hutao", "chips": ["a", "b"]},
                   soul="# 新角色\n\n你是新角色。")
check("1b.1 新增成功并播种 SOUL",
      newbie["id"] == "newbie" and "你是新角色" in store.soul("newbie")
      and (store.state_dir / "characters.d" / "newbie.json").exists(), str(newbie)[:120])
check("1b.2 新角色出现在列表", any(c["id"] == "newbie" for c in store.list()))
store.set_theme("newbie", "citlali")
check("1b.3 主题绑定落盘",
      CharacterStore(TMP / "state").get("newbie")["theme"] == "citlali")
try:
    store.add({"id": "../bad", "name": "坏"})
    check("1b.4 非法 id 被拒", False)
except ValueError:
    check("1b.4 非法 id 被拒", True)
try:
    store.add({"id": "noname"})
    check("1b.5 缺名字被拒", False)
except ValueError:
    check("1b.5 缺名字被拒", True)
try:
    store.set_theme("nope", "klee")
    check("1b.6 未知角色绑主题被拒", False)
except ValueError:
    check("1b.6 未知角色绑主题被拒", True)
check("1b.7 chips 字符串会拆分", store.add(
    {"id": "chip", "name": "C", "chips": "x,y"})["chips"] == ["x", "y"])

print("[2] 插件角色门控")
ad = FakeAdapter()
mgr = FeatureManager(ad)
mgr.load()
snap = {p["name"]: p for p in mgr.snapshot()["plugins"]}
check("2.1 通用插件按白名单启用", snap["common"]["enabled"] and not snap["common"]["locked"])
check("2.2 klee 时：双角色限定启用", snap["shared"]["enabled"] and snap["shared"]["locked"])
check("2.3 klee 时：胡桃限定停用", not snap["onlyhutao"]["enabled"], str(snap["onlyhutao"]))
check("2.4 klee 时：昔莲限定停用", not snap["cyrene"]["enabled"])
check("2.5 角色限定命令没注册", "hutao_only" not in mgr.commands and "cyrene_only" not in mgr.commands)

# 把通用插件从白名单拿掉 → 应该真的停；角色限定不受白名单影响
cfg = {"enabled": [], "settings": {}}
(TMP / "features" / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
mgr.load()
snap = {p["name"]: p for p in mgr.snapshot()["plugins"]}
check("2.6 通用插件可关闭", not snap["common"]["enabled"], str(snap["common"]))
check("2.7 角色限定无视白名单（仍启用）", snap["shared"]["enabled"], str(snap["shared"]))

mgr.set_character("hutao", {"id": "hutao", "name": "胡桃"})
snap = {p["name"]: p for p in mgr.snapshot()["plugins"]}
check("2.8 切到胡桃：胡桃限定启用", snap["onlyhutao"]["enabled"])
check("2.9 切到胡桃：双角色限定仍启用", snap["shared"]["enabled"])
check("2.10 切到胡桃：昔莲限定停用", not snap["cyrene"]["enabled"])
check("2.11 命令随角色变化", "hutao_only" in mgr.commands and "cyrene_only" not in mgr.commands)

mgr.set_character("cyrene", {"id": "cyrene", "name": "昔莲"})
snap = {p["name"]: p for p in mgr.snapshot()["plugins"]}
check("2.12 切到昔莲：只有昔莲限定",
      snap["cyrene"]["enabled"] and not snap["onlyhutao"]["enabled"]
      and not snap["shared"]["enabled"], str({k: snap[k]["enabled"] for k in ("cyrene", "onlyhutao", "shared")}))

print()
print("[3] 角色字段清洗（路径穿越 / 坏类型）")
from bot.characters import _clean_def  # noqa: E402

bad = _clean_def({"id": "evil", "name": "Evil", "seed": "../../etc/passwd",
                  "kb": "../kb", "theme": "/tmp/theme", "chips": 5, "color": "#abcde"})
check("3.1 seed 路径穿越被拒", bad["seed"] == "evil.md", bad["seed"])
check("3.2 kb 只接受纯 id", bad["kb"] == "evil", bad["kb"])
check("3.3 theme 只接受纯 id", ".." not in bad["theme"] and "/" not in bad["theme"], bad["theme"])
check("3.4 chips 非序列不炸", bad["chips"] == [], str(bad["chips"]))
check("3.5 5 位颜色被拒", bad["color"] == "#e4573d", bad["color"])
good = _clean_def({"id": "ok", "name": "Ok", "seed": "ok.md", "kb": "kb_ok",
                   "theme": "t-1", "chips": ["a"], "color": "#abcdef"})
check("3.6 合法值原样保留",
      (good["seed"], good["kb"], good["theme"], good["chips"], good["color"])
      == ("ok.md", "kb_ok", "t-1", ["a"], "#abcdef"), str(good))

print(f"PASS {len(PASS)} / {len(PASS) + len(FAIL)}")
if FAIL:
    print("FAILED:", FAIL)
    sys.exit(1)
