#!/usr/bin/env python3
"""插件元数据 + 配置 schema（NoneBot __plugin_meta__ / AstrBot _conf_schema 风格）。"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

STUDIO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(STUDIO))

TMP = Path(tempfile.gettempdir()) / "klee_meta_test"
if TMP.exists():
    shutil.rmtree(TMP)
(TMP / "features").mkdir(parents=True)
(TMP / "features" / "config.json").write_text(json.dumps(
    {"enabled": ["rich", "plain", "half"], "settings": {"rich": {"limit": 5, "mode": "fast"}}}),
    encoding="utf-8")

(TMP / "features" / "rich.py").write_text('''
"""一句话描述会被 docstring 兜底。"""

__plugin_meta__ = {
    "name": "富元数据插件",
    "description": "带 usage/author/homepage 的插件",
    "usage": ".rich <参数>",
    "author": "可莉",
    "homepage": "https://example.com/rich",
    "version": "2.1",
}

CONFIG_SCHEMA = {
    "limit": {"type": "integer", "default": 5, "min": 1, "max": 100, "description": "条数"},
    "mode": {"type": "select", "default": "fast", "options": ["fast", "slow"], "description": "模式"},
    "enabled_flag": {"type": "bool", "default": True, "description": "开关"},
    "ratio": {"type": "number", "default": 0.5, "min": 0, "max": 1, "description": "比例"},
    "tags": {"type": "list", "default": ["a"], "description": "标签"},
    "extra": {"type": "json", "default": {"k": 1}, "description": "额外"},
    "title": {"default": "默认标题", "description": "靠默认值推断成 str"},
    "broken": "not-a-dict",
}


def register(api):
    api.on_command("rich", lambda ctx: "ok", help="示例")
''', encoding="utf-8")

(TMP / "features" / "half.py").write_text('''
NAME = "半截插件"


def register(api):
    api.on_command("halfway", lambda ctx: "不该存在")
    raise RuntimeError("注册到一半炸了")
''', encoding="utf-8")

(TMP / "features" / "plain.py").write_text('''
NAME = "老式插件"
DESCRIPTION = "只用模块常量"
VERSION = "1.0"


def register(api):
    api.on_command("plain", lambda ctx: "ok")
''', encoding="utf-8")

from bot.framework import (FeatureManager, coerce_settings, merge_plugin_settings,  # noqa: E402
                           normalize_schema)
from bot.skills import SkillHub                                                      # noqa: E402

PASS, FAIL = [], []


def check(name, ok, extra=""):
    (PASS if ok else FAIL).append(name)
    print(f"  {'✓' if ok else '✗'} {name}" + (f"  [{extra}]" if extra and not ok else ""))


class FakeAdapter:
    owner_id = "1001"
    features_dir = str(TMP / "features")
    feature_prefix = "."

    def _chat_id(self, is_group, group_id, user_id):
        return f"private:{user_id}"

    async def send(self, *a, **k):
        pass

    async def send_image_file(self, *a, **k):
        pass


print("[1] 元数据")
mgr = FeatureManager(FakeAdapter())
mgr.load()
snap = {p["name"]: p for p in mgr.snapshot()["plugins"]}
rich, plain = snap["rich"], snap["plain"]
check("1.1 __plugin_meta__ 覆盖标题/描述/版本",
      rich["title"] == "富元数据插件" and rich["description"].startswith("带 usage")
      and rich["version"] == "2.1", json.dumps(rich, ensure_ascii=False)[:200])
check("1.2 usage/author/homepage 带出来",
      rich["usage"] == ".rich <参数>" and rich["author"] == "可莉"
      and rich["homepage"].endswith("/rich"))
check("1.3 老插件仍走 NAME/DESCRIPTION/VERSION",
      plain["title"] == "老式插件" and plain["version"] == "1.0" and plain["author"] == "")

half = snap["half"]
check("1.4 注册失败 → 标记停用且命令被回滚",
      half["enabled"] is False and "注册到一半" in half["error"] and "halfway" not in mgr.commands,
      json.dumps({k: half[k] for k in ("enabled", "error")}, ensure_ascii=False))
check("1.5 回滚不影响别的插件命令", "rich" in mgr.commands and "plain" in mgr.commands)

print("[2] schema 规范化")
schema = rich["schema"]
check("2.1 类型别名 integer/number", schema["limit"]["type"] == "int"
      and schema["ratio"]["type"] == "float", str({k: v["type"] for k, v in schema.items()}))
check("2.2 select 选项保留", schema["mode"]["options"] == ["fast", "slow"])
check("2.3 非 dict 声明不炸、按默认值推断 str", schema["broken"]["type"] == "str"
      and schema["title"]["type"] == "str")
check("2.4 min/max 保留", schema["limit"]["min"] == 1 and schema["limit"]["max"] == 100)
check("2.5 没声明 schema 的插件为空", plain["schema"] == {})

print("[3] 取值/校验")
clean, errors = coerce_settings(schema, {
    "limit": "7", "mode": "slow", "enabled_flag": "false", "ratio": "0.25",
    "tags": "x, y\nz", "extra": '{"a": 2}', "title": 123, "unknown": 1})
check("3.1 数字/布尔/列表/JSON 都转对",
      clean["limit"] == 7 and clean["enabled_flag"] is False and clean["ratio"] == 0.25
      and clean["tags"] == ["x", "y", "z"] and clean["extra"] == {"a": 2}
      and clean["title"] == "123", json.dumps(clean, ensure_ascii=False))
check("3.2 未知键报错并忽略", any("unknown" in e for e in errors) and "unknown" not in clean, str(errors))
clean2, err2 = coerce_settings(schema, {"limit": 999, "mode": "turbo"})
check("3.3 数字夹到 max", clean2.get("limit") == 100, str(clean2))
check("3.4 select 非法值报错", any("mode" in e for e in err2) and "mode" not in clean2, str(err2))
clean3, err3 = coerce_settings(schema, {"limit": "abc"})
check("3.5 数字坏值报错", any("limit" in e for e in err3) and "limit" not in clean3, str(err3))
clean4, err4 = coerce_settings({}, {"anything": [1, 2]})
check("3.6 没 schema 时原样接受（兼容老插件）", clean4 == {"anything": [1, 2]} and not err4)
check("3.7 默认值合法", all(
    coerce_settings({k: f}, {k: f.get("default")})[1] == [] for k, f in schema.items()))

clean5, err5 = coerce_settings({"n": {"type": "int"}}, {"n": "1e400"})
check("3.8 int 收到 inf → 报错而不是 500", "n" not in clean5 and any("n" in e for e in err5), str(err5))
clean6, err6 = coerce_settings({"f": {"type": "float"}}, {"f": "nan"})
check("3.9 float 收到 NaN → 拒绝", "f" not in clean6 and err6, str(err6))

print("[4] 合并进 config.json")
cfg = {"enabled": ["rich"], "settings": {"rich": {"limit": 5, "keep": "x"}}}
merged, errs = merge_plugin_settings(cfg, "rich", {"limit": 9, "mode": "slow"}, schema)
check("4.1 只改提交的键，旧键保留",
      merged["settings"]["rich"] == {"limit": 9, "keep": "x", "mode": "slow"},
      json.dumps(merged["settings"], ensure_ascii=False))
check("4.2 不改原对象", cfg["settings"]["rich"] == {"limit": 5, "keep": "x"})
check("4.3 非法值不进配置", "mode" not in merge_plugin_settings(
    cfg, "rich", {"mode": "turbo"}, schema)[0]["settings"]["rich"])

print("[4b] config.json 损坏 → 全部不启用（fail-closed）")
TMP2 = TMP / "badcfg"
(TMP2 / "features").mkdir(parents=True)
(TMP2 / "features" / "config.json").write_text("{ 不是 json", encoding="utf-8")
(TMP2 / "features" / "ok.py").write_text(
    "NAME='应被停用'\nDEFAULT_OFF=False\n\ndef register(api):\n    api.on_command('ok', lambda c: 'x')\n",
    encoding="utf-8")


class BadAdapter(FakeAdapter):
    features_dir = str(TMP2 / "features")


bad = FeatureManager(BadAdapter())
bad.load()
bad_snap = {p["name"]: p for p in bad.snapshot()["plugins"]}
check("4b.1 坏配置下插件不启用（不会把管理员停用的又打开）",
      bad_snap["ok"]["enabled"] is False and "ok" not in bad.commands, str(bad_snap)[:160])

print("[5] 技能详情带 usage")
hub = SkillHub(mgr)
detail = hub.detail("rich")
check("5.1 usage 出现在技能说明里", ".rich <参数>" in detail, detail[:300])
check("5.2 普通插件详情仍正常", "老式插件" in hub.detail("plain"))

print()
print(f"PASS {len(PASS)} / {len(PASS) + len(FAIL)}")
if FAIL:
    print("FAILED:", FAIL)
    sys.exit(1)
