"""框架层：适配器抽象 + 非消息事件订阅。

这两块是「能不能叫框架」的分水岭：

  · 适配器如果只有 OneBot 一个实现，`BaseAdapter` 就是摆设 —— 这里用真的
    Webhook 适配器跑一遍，证明换平台不用改 runtime/插件。
  · 插件如果只能接消息，戳一戳/入群/好友申请就永远写不了插件 —— 这里验证
    on_notice / on_request 真的被调用、异常被隔离、注册失败会回滚。
"""
import asyncio
import json
import socketserver
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bot import adapters as A                      # noqa: E402
from bot.adapters.base import AdapterError, BaseAdapter  # noqa: E402
from bot.framework import EventContext, FeatureManager  # noqa: E402

PASS = FAIL = 0


def check(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}  {detail}")


# ---------- 1) 注册表 ----------
print("· 适配器注册表")
check("onebot11 已注册", "onebot11" in A.REGISTRY, str(list(A.REGISTRY)))
check("webhook 已注册（≥2 个实现才算抽象成立）", "webhook" in A.REGISTRY, str(list(A.REGISTRY)))
check("available() 给出面板用的清单", len(A.available()) >= 2, str(A.available()))
ad = A.build({"adapter": "onebot11", "owner_id": "42"})
check("缺省 adapter 时就是 onebot11", ad.platform == "onebot11", ad.platform)
check("owner_id 传进适配器", ad.owner_id == "42", ad.owner_id)
try:
    A.build({"adapter": "nope"})
    check("未知适配器报错", False)
except AdapterError as e:
    check("未知适配器报错并列出可用项", "webhook" in str(e), str(e))

# ---------- 2) 基类契约 ----------
print("\n· 基类契约")
base = BaseAdapter({"owner_id": "7"})
check("默认 chat_id 形态是 OneBot 风格",
      base.chat_id_of(True, "123", "9") == "group:123"
      and base.chat_id_of(False, "", "9") == "private:9")
check("describe() 带能力表", "capabilities" in base.describe())
try:
    base.send_image("group:1", "/tmp/x.png")
    check("未实现的能力抛 AdapterError（不是 AttributeError）", False)
except AdapterError as e:
    check("未实现的能力抛 AdapterError", "base" in str(e), str(e))
try:
    base.get_group_member_list("1")
    check("拿群成员也抛 AdapterError", False)
except AdapterError:
    check("拿群成员也抛 AdapterError", True)

# ---------- 3) Webhook 适配器真的发出去 ----------
print("\n· Webhook 适配器（起一个真 HTTP 服务收）")
got = []


class Hook(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n)
        got.append({"headers": dict(self.headers), "json": json.loads(body)})
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", "11")
        self.end_headers()
        self.wfile.write(b'{"ok":true}')

    do_PUT = do_POST


srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Hook)
srv.daemon_threads = True
threading.Thread(target=srv.serve_forever, daemon=True).start()
url = f"http://127.0.0.1:{srv.server_address[1]}/hook"

wh = A.build({"adapter": "webhook", "webhook_url": url,
              "webhook_token": "tk-1", "webhook_field": "message", "owner_id": "7"})
check("platform 是 webhook", wh.platform == "webhook")
r = wh.send_text("group:123", "你好")
check("send_text 返回 ok", bool(r.get("ok")), str(r))
check("请求带 Bearer token",
      got and got[-1]["headers"].get("Authorization") == "Bearer tk-1",
      str(got[-1]["headers"].get("Authorization") if got else None))
check("正文字段可配置（message）",
      got and got[-1]["json"].get("message") == "你好", str(got[-1]["json"] if got else None))
check("chat_id 一起带上", got and got[-1]["json"].get("chat_id") == "group:123")
check("能力表里 text/image 为真、群成员为假",
      wh.CAPABILITIES["text"] and wh.CAPABILITIES["image"]
      and not wh.CAPABILITIES["group_member_list"])
try:
    wh.get_group_member_list("1")
    check("webhook 不支持的能力抛 AdapterError", False)
except AdapterError:
    check("webhook 不支持的能力抛 AdapterError", True)

img = Path(tempfile.mkdtemp()) / "a.png"
img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
wh.send_image("group:123", str(img), "看图")
check("发图走 base64 字段",
      got and got[-1]["json"].get("image_base64", "").startswith("iVBOR"),
      str(got[-1]["json"].keys() if got else None))

try:
    A.build({"adapter": "webhook"}).send_text("group:1", "x")
    check("没配 webhook_url 时报错", False)
except AdapterError as e:
    check("没配 webhook_url 时报错", "webhook_url" in str(e), str(e))
srv.shutdown()

# ---------- 4) OneBot 适配器 ----------
print("\n· OneBot 适配器")
ob = A.build({"adapter": "onebot11", "napcat_http": "http://127.0.0.1:9", "owner_id": "1"})
check("describe 里有 base / has_token",
      ob.describe().get("base", "").endswith(":9") and "has_token" in ob.describe())
h = ob.health()
check("连不上时 health 返回 ok=False 而不是抛异常", h.get("ok") is False and "error" in h, str(h)[:80])
ob.reconfigure({"napcat_http": "http://127.0.0.1:3101", "napcat_token": "abc"})
check("reconfigure 换地址后 base/token 都更新",
      ob.base == "http://127.0.0.1:3101" and ob.token == "abc", f"{ob.base} {ob.token}")
check("chat_id_of 与老实现一致",
      ob.chat_id_of(True, "123", "9") == "group:123"
      and ob.chat_id_of(False, "", "9") == "private:9")

# ---------- 5) 非消息事件钩子 ----------
print("\n· 事件订阅（on_notice / on_request）")
tmp = Path(tempfile.mkdtemp())
(tmp / "p.py").write_text(
    "NAME='t'\nDESCRIPTION='t'\n"
    "def register(api):\n"
    "    async def n(ctx):\n"
    "        api.state.setdefault('seen', []).append(('notice', ctx.notice_type, ctx.user_id))\n"
    "    async def q(ctx):\n"
    "        api.state.setdefault('seen', []).append(('request', ctx.request_type, ctx.user_id))\n"
    "    api.on_notice(n)\n"
    "    api.on_request(q)\n", encoding="utf-8")


class FakeAd:
    platform = "onebot11"
    owner_id = "42"
    CAPABILITIES = {"text": True, "group_member_list": False}

    def chat_id_of(self, is_group, gid, uid):
        return f"group:{gid}" if is_group else f"private:{uid}"


class FakeHost:
    features_dir = str(tmp)
    feature_prefix = "."
    active_character = "klee"
    active_character_info = {}
    owner_id = "42"
    adapter = FakeAd()


mgr = FeatureManager(FakeHost())
mgr.load()
check("插件注册了 notice 钩子", len(mgr.hooks_notice) == 1, str(len(mgr.hooks_notice)))
check("插件注册了 request 钩子", len(mgr.hooks_request) == 1, str(len(mgr.hooks_request)))

asyncio.run(mgr.run_event_hooks("notice", {"post_type": "notice", "notice_type": "poke",
                                          "user_id": "9", "group_id": "123"}))
asyncio.run(mgr.run_event_hooks("request", {"post_type": "request", "request_type": "friend",
                                           "user_id": "8", "flag": "f1"}))
seen = mgr.plugins["p"] and (mgr.hooks_notice[0].handler.__closure__ or [])
ctxn = EventContext(adapter=FakeAd(), kind="notice",
                    raw={"notice_type": "poke", "user_id": "9", "group_id": "123"})
check("EventContext.notice_type / user_id 正确",
      ctxn.notice_type == "poke" and ctxn.user_id == "9")
check("EventContext.chat_id 走适配器", ctxn.chat_id == "group:123", ctxn.chat_id)
check("EventContext.is_group", ctxn.is_group is True)
check("EventContext.is_owner 认 adapter.owner_id", ctxn.is_owner is False)
check("EventContext.supports 读能力表",
      ctxn.supports("text") is True and ctxn.supports("group_member_list") is False)

# 一个钩子抛异常不该影响后面的钩子
bad = Path(tempfile.mkdtemp())
(bad / "boom.py").write_text(
    "def register(api):\n"
    "    def b(ctx):\n"
    "        raise RuntimeError('boom')\n"
    "    api.on_notice(b, priority=1)\n", encoding="utf-8")


class Host2(FakeHost):
    features_dir = str(bad)


mgr2 = FeatureManager(Host2())
mgr2.load()
res = asyncio.run(mgr2.run_event_hooks("notice", {"notice_type": "poke"}))
check("钩子抛异常被吞掉（不让内置流程挂）", isinstance(res, list), str(res))
check("错误计入插件错误数", mgr2.errors.get("boom", 0) == 1, str(mgr2.errors))

# 注册到一半失败 → 钩子回滚
half = Path(tempfile.mkdtemp())
(half / "half.py").write_text(
    "def register(api):\n"
    "    api.on_notice(lambda ctx: None)\n"
    "    raise RuntimeError('挂了')\n", encoding="utf-8")


class Host3(FakeHost):
    features_dir = str(half)


mgr3 = FeatureManager(Host3())
mgr3.load()
check("register 抛异常 → 半截注册被回滚",
      len(mgr3.hooks_notice) == 0 and len(mgr3.hooks_request) == 0,
      f"notice={len(mgr3.hooks_notice)}")
check("失败的插件标记 error 且未启用",
      mgr3.plugins["half"]["error"] and not mgr3.plugins["half"]["enabled"])

# ---------- 6) 端到端：BotRuntime + Webhook 适配器发一条真消息 ----------
print("\n· Runtime 换适配器后照样能发（这就是框架的意义）")
got2 = []


class Hook2(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        got2.append(json.loads(self.rfile.read(n)))
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"{}")


srv2 = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Hook2)
srv2.daemon_threads = True
threading.Thread(target=srv2.serve_forever, daemon=True).start()

from bot.runtime import BotRuntime          # noqa: E402
from bot.characters import CharacterStore   # noqa: E402


class _Models:
    def active(self):
        return {"provider": "x", "model": "y"}

    def vision(self):
        return {}

    def agent(self):
        return {}

    def chat(self, *a, **k):
        return "（测试不调模型）"


tmp2 = Path(tempfile.mkdtemp())
rt = BotRuntime(
    features_dir=ROOT / "bot" / "features",
    config={"adapter": "webhook",
            "webhook_url": f"http://127.0.0.1:{srv2.server_address[1]}/in",
            "owner_id": "42", "self_id": "9", "feature_prefix": ".",
            "split_sentences": False, "quote_reply": False, "at_sender": False,
            "rate_limit_per_min": 0, "tts_enabled": False, "vision_enabled": False,
            "kb_enabled": False, "agent_enabled": False, "log_enabled": False},
    models=_Models(), soul_path=tmp2 / "SOUL.md",
    characters=CharacterStore(tmp2), state_dir=tmp2)
check("runtime.adapter 是 webhook", rt.adapter.platform == "webhook", rt.adapter.platform)
check("runtime.ob 指向适配器（老代码零改动）", rt.ob is rt.adapter)
asyncio.run(rt.send("group:123", "换平台以后照样能回话"))
check("消息真的发出去了", len(got2) == 1, str(len(got2)))
check("正文正确", got2 and got2[0].get("text") == "换平台以后照样能回话",
      str(got2[0] if got2 else None))
check("平台名一起带上（下游可分流）", got2 and got2[0].get("platform") == "webhook")
srv2.shutdown()

print(f"\nPASS {PASS} / {PASS + FAIL}")
sys.exit(1 if FAIL else 0)
