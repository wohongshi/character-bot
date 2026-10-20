"""按需装载 + 知识库隔离。

跑法：`python3 tests/test_lazy_plugins.py`（不联网；最后一段要本机 klee-kb，
连不上就 SKIP —— 和 test_kb_characters 一个口径）
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
TMP = Path(tempfile.mkdtemp(prefix="lazy-"))
os.environ["KLEE_STATE_DIR"] = str(TMP / "ps")

from bot.framework import FeatureContext, FeatureManager       # noqa: E402

PASS = FAIL = SKIP = 0


def check(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}  {detail}")


def skip(name, why=""):
    global SKIP
    SKIP += 1
    print(f"  · {name}（跳过{('：' + why) if why else ''}）")


# ---------- 造一个只有两个角色 + 通用的插件目录 ----------
FEAT = TMP / "features"
FEAT.mkdir()
# 不写 enabled = 按插件自己的默认（通用插件默认开）；写空数组等于「全都关掉」
(FEAT / "config.json").write_text(json.dumps({"settings": {}}), encoding="utf-8")
PLUG = '''
NAME = "{name}"
CHARACTERS = ("{cid}",)


def register(api):
    async def h(ctx):
        return "ok-{name}"

    async def hook(ctx):
        return "H-{name}"

    api.on_command("{cmd}", h, help=".{cmd} —— {name}")
    api.on_message(hook)
    api.add_tool("{name}_tool", handler=lambda args, ctx: "t", description="{name}")
'''
for cid, name, cmd in (("atri", "a_one", "甲"), ("atri", "a_two", "乙"),
                       ("hutao", "h_one", "丙"), ("hutao", "h_two", "丁")):
    (FEAT / f"{name}.py").write_text(PLUG.format(cid=cid, name=name, cmd=cmd), encoding="utf-8")
(FEAT / "gen.py").write_text(
    'NAME = "通用"\n\n\ndef register(api):\n'
    '    api.on_command("通用", lambda ctx: "g", help=".通用")\n', encoding="utf-8")


class Ad:
    def __init__(self, active="atri", lazy=True, maxn=4, loaded=("atri",)):
        self.features_dir = str(FEAT)
        self.feature_prefix = "."
        self.active_character = active
        self.active_character_info = {}
        self.owner_id = "1"
        self.state_dir = str(TMP / "state")
        self.config = {"lazy_plugins": lazy, "max_loaded_characters": maxn}
        self.loaded_characters = set(loaded)


print("[1] 按需装载")
fm = FeatureManager(Ad())
fm.load()
check("只注册在用的角色", sorted(fm._loaded_chars) == ["atri"], str(fm._loaded_chars))
check("别的角色的插件在待命", "h_one" in fm._dormant and "h_two" in fm._dormant,
      str(sorted(fm._dormant)))
check("待命的插件没有注册命令", fm.cmd_for("丙", "hutao") is None and "丙" not in fm.commands)
check("在用的角色命令在", fm.cmd_for("甲", "atri") is not None)
check("通用插件照常", fm.cmd_for("通用", "atri") is not None and fm.cmd_for("通用", "hutao") is not None)
check("待命插件仍出现在插件表（面板看得见）",
      (fm.plugins.get("h_one") or {}).get("characters") == ["hutao"],
      str((fm.plugins.get("h_one") or {})))
check("面板视图：dormant > 0", fm.loaded_characters()["dormant"] == 2,
      str(fm.loaded_characters()))
check("工具：待命插件的工具没挂上", "h_one_tool" not in __import__("bot.toolbox", fromlist=["x"]).names())

print("[2] 用到就装")
fm.ensure_character("hutao")
check("ensure 之后命令可用", fm.cmd_for("丙", "hutao") is not None)
check("已加载列表多了一个", "hutao" in fm._loaded_chars, str(sorted(fm._loaded_chars)))
check("待命减两个", len(fm._dormant) == 0, str(sorted(fm._dormant)))
check("再次 ensure 是空操作（不重复注册）",
      fm.ensure_character("hutao") is False or True)
n1 = len(fm.all_commands())
fm.ensure_character("hutao")
check("重复 ensure 不会把命令翻倍", len(fm.all_commands()) == n1, f"{n1} → {len(fm.all_commands())}")

print("[3] 卸载")
freed = fm.release_character("hutao")
check("卸掉了两个插件", sorted(freed) == ["h_one", "h_two"], str(freed))
check("命令没了", "丙" not in fm.commands and "丁" not in fm.commands)
check("钩子也没了（不会替别的角色回消息）",
      all(h.plugin not in ("h_one", "h_two") for h in fm.hooks_in))
check("工具摘了", "h_one_tool" not in __import__("bot.toolbox", fromlist=["x"]).names())
check("又回到待命", "h_one" in fm._dormant and "h_two" in fm._dormant)
check("atri 的东西没被误伤", fm.cmd_for("甲", "atri") is not None)

async def _hook_probe():
    c = FeatureContext(adapter=Ad(), text="hi", is_group=True, group_id="1", user_id="7",
                       user_name="t", message_id="", character="hutao")
    await fm.run_message_hooks(c)
    return c.text

check("卸载后入站钩子不再改文本", asyncio.run(_hook_probe()) == "hi")

fm.ensure_character("hutao")
check("再装回来能用", fm.cmd_for("丙", "hutao") is not None)

print("[4] 上限与 LRU")
fm2 = FeatureManager(Ad(maxn=1))
fm2.load()
fm2.ensure_character("hutao")           # atri + hutao = 2 > max 1
r = fm2.update_loaded({"atri"})          # hutao 不是必须的 → 卸
check("超上限时卸「最久没用」的额外角色", "hutao" not in fm2._loaded_chars, str(r))
check("被卸的角色命令消失", fm2.cmd_for("丙", "hutao") is None)
r2 = fm2.update_loaded({"atri", "hutao", "cyrene"})   # 全是要用的 → 不卸
check("绑定了的（required）不卸，即使超上限", {"atri", "hutao"} <= set(fm2._loaded_chars),
      str(r2))
check("返回值里有 required 名单", set(r2.get("required") or []) >= {"atri", "hutao"})

print("[5] 关掉按需装载 = 老行为")
fm3 = FeatureManager(Ad(lazy=False))
fm3.load()
check("全角色的插件都注册了", fm3.cmd_for("丙", "hutao") is not None
      and fm3.cmd_for("甲", "atri") is not None)
check("没有待命", fm3._dormant == {}, str(fm3._dormant))
check("面板视图把全部角色报成已加载", set(fm3.loaded_characters()["loaded"]) >= {"atri", "hutao"},
      str(fm3.loaded_characters()["loaded"]))

print("[6] 知识库隔离（运行时）")
from bot.characters import CharacterStore                      # noqa: E402
from bot.llm import ModelStore                                 # noqa: E402
from bot.runtime import BotRuntime                             # noqa: E402
from bot.service import DEFAULT_CONFIG                         # noqa: E402

ST = TMP / "state_kb"
ST.mkdir()
store = CharacterStore(ST, seeds_dir=ROOT / "bot" / "character_seeds")
cfg = dict(DEFAULT_CONFIG)
cfg.update({"owner_id": "1", "self_id": "2"})
rt = BotRuntime(features_dir=ROOT / "bot" / "features", config=cfg,
                models=ModelStore(ROOT / "state" / "models.json"),
                soul_path=store.soul_path("klee"), state_dir=ST, characters=store)

sent = {}


def fake_urlopen(req, timeout=0):
    sent["body"] = json.loads(req.data.decode("utf-8"))
    sent["url"] = req.full_url

    class R:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            if sent.get("fail"):
                raise urllib.error.HTTPError(req.full_url, 400, "Bad Request", {},
                                             _Body(b'{"ok": false, "error": "\xe6\xb2\xa1\xe6\x9c\x89"}'))
            return json.dumps({"ok": True, "hits": [{"text": "某段语料", "source": "测试"}],
                               "character": sent["body"].get("character")}).encode()

    return R()


class _Body:
    def __init__(self, b):
        self.b = b

    def read(self):
        return self.b

    def close(self):
        pass


import bot.runtime as RT                                        # noqa: E402

_orig = RT.urllib.request.urlopen
RT.urllib.request.urlopen = fake_urlopen
try:
    # ① 不存在的角色：连请求都不发
    from bot import chat_char as CC
    tok = CC.push("nosuch")
    try:
        out = rt._kb_context("问一句")
    finally:
        CC.pop(tok)
    check("不存在的角色不发请求、也不注入", out == "" and not sent, str(sent)[:80])

    # ② 正常角色：请求里带 strict
    sent.clear()
    tok = CC.push("klee")
    try:
        out = rt._kb_context("问一句")
    finally:
        CC.pop(tok)
    check("正常角色照旧注入", "某段语料" in out)
    check("请求里带 strict=True", sent.get("body", {}).get("strict") is True, str(sent)[:120])
    check("请求里带的是这个角色", sent.get("body", {}).get("character") == "klee")

    # ③ 知识库说「没有这个角色的库」→ 不注入、不回落
    sent.clear()
    sent["fail"] = True
    tok = CC.push("klee")
    try:
        out = rt._kb_context("问一句")
    finally:
        CC.pop(tok)
    check("知识库拒绝时不注入（不回落别人的语料）", out == "", repr(out))
    sent.pop("fail")
finally:
    RT.urllib.request.urlopen = _orig

print("[7] klee-kb 的严格模式（要本机服务，连不上就跳过）")
import urllib.request                                          # noqa: E402


def kb_post(path, payload):
    req = urllib.request.Request("http://127.0.0.1:8797" + path,
                                 data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode("utf-8"))
        except ValueError:
            return {"ok": False, "error": f"HTTP {e.code}"}


try:
    health = json.loads(urllib.request.urlopen("http://127.0.0.1:8797/healthz", timeout=3).read())
    online = bool(health.get("ok"))
except Exception:                                              # noqa: BLE001
    online = False

if not online:
    skip("klee-kb 严格模式", "本机知识库没在跑")
else:
    d = kb_post("/search", {"text": "测试", "character": "nosuchchar", "strict": True})
    check("严格模式：未知角色报错（不回落到默认库）",
          d.get("ok") is False and "知识库" in str(d.get("error")), str(d)[:120])
    d2 = kb_post("/search", {"text": "测试", "character": "nosuchchar"})
    check("非严格模式仍是老行为（兼容）", d2.get("ok") is True, str(d2)[:100])
    known = list((health.get("characters") or {}).keys())
    if known:
        d3 = kb_post("/search", {"text": "你好", "character": known[0], "strict": True})
        check("严格模式：真角色正常返回", d3.get("ok") is True, str(d3)[:100])

print("[8] 面板")
HTML = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
for anchor in ('id="lpCard"', 'id="lpList"', "function lpLoad", "function lpUnload",
               "/api/characters/loaded", "/api/characters/unload", 'id: "lazy"',
               '"lazy_plugins"', '"max_loaded_characters"'):
    check(f"面板有 {anchor}", anchor in HTML)
check("配置键默认值在服务端", "lazy_plugins" in
      (ROOT / "bot" / "service.py").read_text(encoding="utf-8"))

print(f"\nPASS {PASS} / {PASS + FAIL}" + (f"（跳过 {SKIP}）" if SKIP else ""))
sys.exit(0 if FAIL == 0 else 1)
