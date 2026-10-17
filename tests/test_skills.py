#!/usr/bin/env python3
"""「插件即技能」测试：索引 / 详情 / 调用 / 权限 / 超时 / 图片捕获。"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

STUDIO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(STUDIO))

TMP = Path(tempfile.gettempdir()) / "klee_skills_test"
Path("/tmp/fake.png").write_bytes(
    bytes.fromhex("89504e470d0a1a0a0000000d494844520000000100000001080600000"
                  "01f15c4890000000d4944415478da63f8cfc000000301010018dd8db0"
                  "0000000049454e44ae426082"))
if TMP.exists():
    shutil.rmtree(TMP)
(TMP / "features").mkdir(parents=True)
(TMP / "features" / "config.json").write_text(json.dumps(
    {"enabled": ["demo", "secret", "grouponly", "slow", "broken", "img", "loopinfo"],
     "settings": {}}),
    encoding="utf-8")

(TMP / "features" / "demo.py").write_text('''
NAME = "示例技能"
DESCRIPTION = "演示用的技能，会回显参数"
VERSION = "1.2"


def register(api):
    def echo(ctx):
        return "echo:" + ctx.arg_text

    async def async_echo(ctx):
        await ctx.reply("async:" + ctx.arg_text)

    def once(ctx):
        return "只给一次：" + ctx.arg_text

    api.on_command("echo", echo, help="回显")
    api.on_command("once", once, cooldown=60, help="有冷却的命令")
    api.on_command("aecho", async_echo, aliases=("ae",), help="异步回显")
''', encoding="utf-8")

(TMP / "features" / "secret.py").write_text('''
NAME = "主人技能"
DESCRIPTION = "只有主人能用"


def register(api):
    api.on_command("secret", lambda ctx: "秘密", permission="owner", help="主人专用")
''', encoding="utf-8")

(TMP / "features" / "grouponly.py").write_text('''
NAME = "群技能"
DESCRIPTION = "只在群里"


def register(api):
    api.on_command("gonly", lambda ctx: "群里好", scope="group", help="群限定")
''', encoding="utf-8")

(TMP / "features" / "slow.py").write_text('''
NAME = "慢技能"
DESCRIPTION = "很慢"
import time


def register(api):
    def slow(ctx):
        time.sleep(3)
        return "终于好了"

    api.on_command("slow", slow, help="睡三秒")
''', encoding="utf-8")

(TMP / "features" / "broken.py").write_text('''
NAME = "坏技能"
DESCRIPTION = "会抛异常"


def register(api):
    def boom(ctx):
        raise RuntimeError("炸了")

    api.on_command("boom", boom, help="必炸")
''', encoding="utf-8")

(TMP / "features" / "loopinfo.py").write_text('''
NAME = "线程探测"


def register(api):
    import threading

    def where(ctx):
        return "thread:" + threading.current_thread().name

    api.on_command("where", where)
''', encoding="utf-8")

(TMP / "features" / "img.py").write_text('''
NAME = "画图技能"
DESCRIPTION = "发一张图"


def register(api):
    async def pic(ctx):
        await ctx.adapter.send_image_file(ctx.chat_id, "/tmp/fake.png", caption="给你")

    api.on_command("pic", pic, help="发图")
''', encoding="utf-8")

from bot.framework import FeatureContext, FeatureManager     # noqa: E402
from bot.skills import SkillHub                              # noqa: E402

PASS, FAIL = [], []


def check(name, ok, extra=""):
    (PASS if ok else FAIL).append(name)
    print(f"  {'✓' if ok else '✗'} {name}" + (f"  [{extra}]" if extra and not ok else ""))


class FakeAdapter:
    owner_id = "1001"
    features_dir = str(TMP / "features")
    feature_prefix = "."

    def __init__(self):
        self.sent, self.images = [], []

    def _chat_id(self, is_group, group_id, user_id):
        return f"group:{group_id}" if is_group else f"private:{user_id}"

    def _member_names(self, gid):
        return {}

    async def send(self, chat_id, text, **kw):
        self.sent.append((chat_id, text))

    async def send_image_file(self, chat_id, path, caption=None, **kw):
        self.images.append((chat_id, path, caption))


adapter = FakeAdapter()
mgr = FeatureManager(adapter)
mgr.load()
hub = SkillHub(mgr)


def ctx(user="1001", group=True, role="member"):
    return {"adapter": adapter, "is_group": group, "group_id": "555", "user_id": user,
            "user_name": "小明", "role": role}


print("[1] 目录 / 索引")
skills = hub.skills()
names = [s["name"] for s in skills]
check("1.1 只列出有命令的插件",
      set(names) == {"demo", "secret", "grouponly", "slow", "broken", "img", "loopinfo"},
      str(names))
check("1.2 标题/描述/版本带出来",
      next(s for s in skills if s["name"] == "demo")["title"] == "示例技能"
      and next(s for s in skills if s["name"] == "demo")["version"] == "1.2")
check("1.3 别名进了命令表",
      any("ae" in c["aliases"] for c in next(s for s in skills if s["name"] == "demo")["commands"]))
idx_owner = hub.index_text(is_owner=True, is_admin=True, is_group=True)
idx_member = hub.index_text(is_owner=False, is_admin=False, is_group=True)
check("1.4 索引含技能名/命令", "demo（示例技能）" in idx_owner and ".echo" in idx_owner, idx_owner[:200])
check("1.5 非主人看不到 owner 命令", ".secret" not in idx_member and ".secret" in idx_owner)
idx_dm = hub.index_text(is_owner=True, is_admin=True, is_group=False)
check("1.6 私聊看不到群限定命令", ".gonly" not in idx_dm and ".gonly" in idx_owner)
check("1.7 索引有上限", len(hub.index_text(is_owner=True, is_admin=True, is_group=True,
                                            max_chars=60)) < 400)

print("[2] 详情")
d = hub.detail("demo")
check("2.1 详情含标题/命令/帮助/别名", "示例技能" in d and ".echo" in d and "回显" in d and ".ae" in d, d[:200])
check("2.2 按标题也能查", hub.detail("示例技能").startswith("# 示例技能"))
check("2.3 未知技能友好报错", "没有技能" in hub.detail("nope"))
check("2.4 owner 命令标注权限", "[owner]" in hub.detail("secret"), hub.detail("secret"))

print("[3] 调用")
r = hub.run("demo", "echo 你好", **ctx())
check("3.1 同步命令返回捕获文字", r["ok"] and r["text"] == "echo:你好", json.dumps(r, ensure_ascii=False))
check("3.2 没直接发到真实 adapter", not adapter.sent, str(adapter.sent))
r = hub.run("demo", "ae 异步", **ctx())
check("3.3 异步命令也能跑", r["ok"] and r["text"] == "async:异步", json.dumps(r, ensure_ascii=False))
r = hub.run("demo", "", **ctx())
check("3.4 只给技能名 → 用第一个命令", r["ok"] and r["command"] in ("echo", "aecho"),
      json.dumps(r, ensure_ascii=False))
r = hub.run("demo", ".echo 带前缀", **ctx())
check("3.5 命令带前缀也能解析", r["ok"] and r["text"] == "echo:带前缀")
r = hub.run("demo", "pic", **ctx())
check("3.6 命令不属于该技能 → 拒绝", not r["ok"] and "属于技能" in r["error"], str(r))

print("[3b] 冷却（time import 回归）")
r = hub.run("demo", "once 第一次", **ctx())
check("3b.1 第一次可用", r["ok"] and r["text"].startswith("只给一次"), str(r))
r = hub.run("demo", "once 第二次", **ctx())
check("3b.2 冷却中拒绝且不是 NameError",
      not r["ok"] and "刚用过" in r["error"], str(r))

print("[4] 权限 / 作用域")
r = hub.run("secret", "secret", **ctx(user="2002", role="member"))
check("4.1 非主人用 owner 命令被拒", not r["ok"] and "没有权限" in r["error"], str(r))
r = hub.run("secret", "secret", **ctx(user="1001"))
check("4.2 主人可以用", r["ok"] and r["text"] == "秘密", str(r))
r = hub.run("grouponly", "gonly", **ctx(group=False))
check("4.3 私聊用群限定命令被拒", not r["ok"] and "没有权限" in r["error"], str(r))
r = hub.run("grouponly", "gonly", **ctx(group=True))
check("4.4 群里可以", r["ok"] and r["text"] == "群里好")

print("[5] 失败 / 超时 / 图片")
r = hub.run("broken", "boom", **ctx())
check("5.1 插件异常 → 错误信息（不崩）", not r["ok"] and "炸了" in r["error"], str(r))
t0 = time.time()
r = hub.run("slow", "slow", timeout=1.0, **ctx())
check("5.2 超时返回错误", not r["ok"] and "超时" in r["error"], str(r))
check("5.3 超时及时返回（<2.5s）", time.time() - t0 < 2.5, f"{time.time() - t0:.2f}s")
r = hub.run("img", "pic", **ctx())
check("5.4 图片被捕获而不是直接发",
      r["ok"] and r["images"] and r["images"][0]["path"] == "/tmp/fake.png"
      and r["images"][0]["caption"] == "给你" and not adapter.images, str(r)[:200])

print("[6] agent 工具")
from bot import agent as A        # noqa: E402

tc = A.ToolContext(skills=hub, chat=ctx(user="1001"))
check("6.1 list_skills 有内容", "demo" in A.tool_list_skills({}, tc))
check("6.2 list_skills 过滤", "secret" not in A.tool_list_skills({"query": "示例"}, tc))
check("6.3 read_skill", "示例技能" in A.tool_read_skill({"name": "demo"}, tc))
check("6.4 use_skill 返回技能输出", "echo:嗨" in A.tool_use_skill({"name": "demo", "text": "echo 嗨"}, tc))
A.tool_use_skill({"name": "img", "text": "pic"}, tc)
check("6.5 use_skill 收集图片", len(tc.images_out) == 1, str(tc.images_out))
check("6.6 无 hub 时明确报错", "不可用" in A.tool_list_skills({}, A.ToolContext()))
check("6.7 默认工具含技能三件套",
      {"list_skills", "read_skill", "use_skill"} <= set(A.DEFAULT_TOOLS))

print("[7] 技能出图 → OCR 回流")
import bot.vision as V          # noqa: E402


class FakeModels:
    def vision(self):
        return {"provider": "x", "model": "v", "configured": True}

    def get_provider(self, pid):
        return {"id": "x", "api": "http://127.0.0.1:9/v1"}


orig_cfg, orig_desc = V.configured, V.describe
try:
    V.configured = lambda m: True
    V.describe = lambda *a, **k: "诗文之章\n黄金之章"
    tc2 = A.ToolContext(skills=hub, chat=ctx(user="1001"), models=FakeModels())
    out = A.tool_use_skill({"name": "img", "text": "pic"}, tc2)
    check("7.1 OCR 文字进入工具结果", "图片文字" in out and "诗文之章" in out, out[:200])
    V.describe = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("vision 挂了"))
    tc3 = A.ToolContext(skills=hub, chat=ctx(user="1001"), models=FakeModels())
    out2 = A.tool_use_skill({"name": "img", "text": "pic"}, tc3)
    check("7.2 OCR 失败不影响技能结果", "技能生成了一张图片" in out2 and "诗文" not in out2, out2[:160])
    tc4 = A.ToolContext(skills=hub, chat=ctx(user="1001"))     # 没有 models
    out3 = A.tool_use_skill({"name": "img", "text": "pic"}, tc4)
    check("7.3 没配模型时跳过 OCR", "图片文字" not in out3, out3[:160])
finally:
    V.configured, V.describe = orig_cfg, orig_desc

print("[8] handler 跑在 bot 事件循环里")
import asyncio as _aio       # noqa: E402
import threading as _th      # noqa: E402

loop = _aio.new_event_loop()
box = {}
t = _th.Thread(target=lambda: (_aio.set_event_loop(loop), loop.run_forever()),
               name="test-bot-loop", daemon=True)
t.start()
time.sleep(0.2)
try:
    r = hub.run("loopinfo", "where", **{**ctx(), "chat": {"loop": loop, "loop_thread": t}})
    check("8.1 有事件循环时 handler 在循环线程执行",
          r["ok"] and r["text"] == "thread:test-bot-loop", json.dumps(r, ensure_ascii=False))
    # 从循环线程内部调用不能死锁：应退回独立线程
    inner = {}

    def call_in_loop():
        inner["r"] = hub.run("loopinfo", "where", **{**ctx(), "chat": {"loop": loop, "loop_thread": _th.current_thread()}})

    loop.call_soon_threadsafe(call_in_loop)
    time.sleep(0.5)
    check("8.2 在循环线程里调用不死锁", inner.get("r", {}).get("ok") is True, str(inner))
finally:
    loop.call_soon_threadsafe(loop.stop)
    t.join(timeout=2)
    if not loop.is_running():
        loop.close()

print()
print(f"PASS {len(PASS)} / {len(PASS) + len(FAIL)}")
if FAIL:
    print("FAILED:", FAIL)
    sys.exit(1)
