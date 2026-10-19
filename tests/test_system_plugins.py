#!/usr/bin/env python3
"""系统插件（适配器）测试。

这一套守的是新理念：**适配器 = 可开关的系统插件，默认全关，可以多开**。
以前「一个 bot 只有一个 adapter」，现在多平台并存，最容易错的三件事：

  1. 默认值：新装一份仓库，不应该偷偷连 QQ；
  2. 路由：飞书来的消息不能从 QQ 的号回出去；
  3. 入站：每个平台自己验签（飞书 token / 微信 sha1 / QQ Ed25519），
     server 只负责把路径对到适配器。
"""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bot import adapters as A                                      # noqa: E402
from bot import ed25519                                            # noqa: E402
from bot.adapters.base import BaseAdapter, Inbound, register        # noqa: E402
from bot.adapters.http import sha1_signature                        # noqa: E402
from bot.runtime import BotRuntime, NullAdapter                     # noqa: E402
from bot.characters import CharacterStore                           # noqa: E402
from bot.service import DEFAULT_CONFIG, BotService                  # noqa: E402

PASS = FAIL = 0


def check(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}  {detail}")


# ---------- 1) 注册表：五个系统插件，默认全关 ----------
print("· 系统插件清单")
sysinfo = {x["name"]: x for x in A.system_plugins()}
check("内置 6 个系统插件（5 个接别人的平台 + 1 个面板自带）",
      set(sysinfo) >= {"onebot11", "webhook", "feishu", "wechat", "qqofficial", "web"},
      str(sorted(sysinfo)))
# 网页是面板自带的那一个平台：它默认开（否则网页聊天没有平台身份），
# 其余平台一律默认关 —— 没配任何凭据时不该有任何东西在往外连。
check("接外部的平台全部默认关闭",
      all(v["default_off"] for k, v in sysinfo.items() if k != "web"),
      str({k: v["default_off"] for k, v in sysinfo.items()}))
check("网页默认开启（面板自带）", sysinfo["web"]["default_off"] is False)
check("网页有自带小页面", sysinfo["web"].get("has_page") is True)
check("每个都有说明", all(x["description"] for x in sysinfo.values()),
      str({k: v["description"] for k, v in sysinfo.items()}))
check("QQ/飞书/微信都有入站路径",
      all(sysinfo[k]["ingress"] for k in ("onebot11", "feishu", "wechat", "qqofficial")),
      str({k: sysinfo[k]["ingress"] for k in sysinfo}))
check("webhook 只出站（没有入站路径）", sysinfo["webhook"]["ingress"] is None,
      str(sysinfo["webhook"]["ingress"]))
check("每个都带配置表单定义",
      all(isinstance(x["schema"], dict) and x["schema"] for x in sysinfo.values()))
check("能力表随平台不同（官方接口没有群成员/撤回）",
      sysinfo["qqofficial"]["capabilities"].get("group_member_list") is False
      and sysinfo["onebot11"]["capabilities"].get("group_member_list") is True)

# ---------- 2) 默认配置：一个都不开 ----------
print("\n· 默认配置")
check("DEFAULT_CONFIG 的 system_plugins 是空列表",
      DEFAULT_CONFIG.get("system_plugins") == [], str(DEFAULT_CONFIG.get("system_plugins")))
check("新平台参数都在默认配置里（否则面板存不进去）",
      all(k in DEFAULT_CONFIG for k in
          ("feishu_app_id", "wechat_token", "qq_app_id", "qq_bot_secret")),
      str([k for k in ("feishu_app_id", "wechat_token", "qq_app_id") if k not in DEFAULT_CONFIG]))

# 老配置迁移：没有 system_plugins 键 → 把原 adapter 当成已启用
tmp = Path(tempfile.mkdtemp())
(tmp / "state").mkdir()
(tmp / "state" / "bot_config.json").write_text(json.dumps(
    {"adapter": "onebot11", "napcat_token": "t"}), encoding="utf-8")
svc = BotService(home=tmp)
sp = list(svc.config.get("system_plugins") or [])
check("老配置迁移：保留原来的 onebot11（升级后不会静默掉线）", "onebot11" in sp, str(sp))
check("老配置迁移：网页也补进去（否则 ctx.platform 会退回 onebot11）", "web" in sp, str(sp))
check("迁移只做一次（标记落盘，用户后来关掉不会再被打开）", svc.config.get("web_seen") is True)

# ---------- 3) 运行时：一个都没开 → NullAdapter ----------
print("\n· 无平台时的运行时")


class FakeModels:
    def chat(self, messages, **kw):
        return "（测试回复）"

    def active(self):
        return {"provider": "fake", "model": "m"}

    def vision(self):
        return {"configured": False}

    def agent(self):
        return {}


FEAT = Path(tempfile.mkdtemp())
(FEAT / "config.json").write_text('{"enabled": [], "settings": {}}', encoding="utf-8")

BASE_CFG = {"self_id": "1", "owner_id": "1", "split_sentences": False,
            "send_interval_min": 0, "send_interval_max": 0, "quote_reply": False,
            "group_policy": "open", "chat_enabled": True, "kb_enabled": False,
            "tts_enabled": False, "vision_enabled": False, "agent_enabled": False,
            "log_enabled": False, "rate_limit_per_min": 0}


def make_rt(platforms, **over):
    cfg = {**BASE_CFG, "system_plugins": list(platforms), **over}
    return BotRuntime(features_dir=FEAT, config=cfg, models=FakeModels(),
                      soul_path=tmp / "SOUL.md", state_dir=Path(tempfile.mkdtemp()),
                      characters=CharacterStore(Path(tempfile.mkdtemp())))


rt0 = make_rt([])
check("没开任何平台时 adapter 是 NullAdapter", isinstance(rt0.adapter, NullAdapter),
      type(rt0.adapter).__name__)
check("NullAdapter 的能力全为 false",
      not any(rt0.adapter.CAPABILITIES.values()), str(rt0.adapter.CAPABILITIES))
res0 = asyncio.run(rt0.send("private:1", "hi"))
check("没有平台时 send 返回失败（不是假装成功）",
      getattr(res0, "success", None) is False, str(res0))

# ---------- 4) 多平台路由 ----------
print("\n· 多平台并存")
sent: dict = {}


@register
class _ProbeA(BaseAdapter):
    platform = "probea"
    title = "探针 A"
    DESCRIPTION = "测试用"
    INGRESS = "/probe/a"
    CAPABILITIES = {"text": True}

    def send_text(self, chat_id, text, *, reply_to=None, at_uid=None):
        sent.setdefault("probea", []).append(text)
        return {"ok": True, "message_id": "a1"}


@register
class _ProbeB(BaseAdapter):
    platform = "probeb"
    title = "探针 B"
    DESCRIPTION = "测试用"
    INGRESS = "/probe/b"
    CAPABILITIES = {"text": True}

    def send_text(self, chat_id, text, *, reply_to=None, at_uid=None):
        sent.setdefault("probeb", []).append(text)
        return {"ok": True, "message_id": "b1"}

    def parse_inbound(self, method, path, query, headers, body):
        ev = json.loads(body or b"{}")
        return Inbound(event=ev, reply=("application/json", b'{"ok":true}'))


rt = make_rt(["probea", "probeb"])
check("两个都建起来了", set(rt.adapters) == {"probea", "probeb"}, str(list(rt.adapters)))
check("主适配器按注册顺序取第一个", rt.adapter.platform == "probea", rt.adapter.platform)

ev_b = {"post_type": "message", "message_type": "private", "user_id": "42",
        "message_id": "m1", "message": [{"type": "text", "data": {"text": "hi"}}],
        "sender": {"user_id": "42"}}
asyncio.run(rt.handle_event({**ev_b, "_source": "probeb"}))
check("B 来的消息从 B 回（不会串到 A）",
      sent.get("probeb") and not sent.get("probea"), str(sent))
check("路由表记住了 chat_id → 平台",
      rt._routes.get("private:42") == "probeb", str(rt._routes))

# 关掉 B 后：路由要清掉，回复回落到主适配器
rt.apply_config({**rt.config, "system_plugins": ["probea"]})
check("关掉平台后实例被移除", list(rt.adapters) == ["probea"], str(list(rt.adapters)))
check("指向已关闭平台的路由被清掉", "private:42" not in rt._routes, str(rt._routes))
check("主适配器换成剩下的那个", rt.adapter.platform == "probea", rt.adapter.platform)

rt.apply_config({**rt.config, "system_plugins": []})
check("全部关掉 → NullAdapter", isinstance(rt.adapter, NullAdapter), type(rt.adapter).__name__)

# ---------- 5) 入站：飞书 ----------
print("\n· 入站解析")
fe = A.build({"feishu_app_id": "cli_x", "feishu_app_secret": "s",
              "feishu_verification_token": "tok"}, platform="feishu")
res = fe.parse_inbound("POST", "/feishu/event", {}, {},
                       json.dumps({"challenge": "abc", "token": "tok"}).encode())
check("飞书 challenge 原样回显", res.reply and b'"challenge"' in res.reply[1], str(res))
bad = fe.parse_inbound("POST", "/feishu/event", {}, {},
                       json.dumps({"challenge": "abc", "token": "WRONG"}).encode())
check("飞书 token 不对 → 403", bad.status == 403, str(bad.status))

fe_body = {"schema": "2.0",
           "header": {"event_type": "im.message.receive_v1", "token": "tok"},
           "event": {"sender": {"sender_id": {"open_id": "ou_1"}},
                     "message": {"chat_id": "oc_1", "chat_type": "group",
                                 "message_id": "om_1", "message_type": "text",
                                 "content": json.dumps({"text": "@_user_1 你好"}),
                                 "mentions": [{"key": "@_user_1",
                                               "id": {"open_id": "ou_bot"}}]}}}
res = fe.parse_inbound("POST", "/feishu/event", {}, {}, json.dumps(fe_body).encode())
ev = res.event or {}
check("飞书群消息被解析成 group 事件", ev.get("message_type") == "group", str(ev)[:120])
check("飞书 @ 被识别（群聊策略才能用 mention）",
      any(s["type"] == "at" for s in ev.get("message") or []), str(ev.get("message")))
check("@ 占位符从正文里去掉", "你好" in json.dumps(ev, ensure_ascii=False)
      and "@_user_1" not in json.dumps(ev.get("message"), ensure_ascii=False))

# ---------- 6) 入站：微信公众号 ----------
print("\n· 微信")
wx = A.build({"wechat_app_id": "wx", "wechat_app_secret": "s",
              "wechat_token": "wxtok"}, platform="wechat")
sig = sha1_signature("wxtok", "1700000000", "n1")
res = wx.parse_inbound("GET", "/wechat/event",
                       {"signature": [sig], "timestamp": ["1700000000"],
                        "nonce": ["n1"], "echostr": ["hello"]}, {}, b"")
check("微信 GET 验签通过并回 echostr",
      res.reply and res.reply[1] == b"hello", str(res.reply))
bad = wx.parse_inbound("GET", "/wechat/event",
                       {"signature": ["no"], "timestamp": ["1"], "nonce": ["2"],
                        "echostr": ["x"]}, {}, b"")
check("微信签名不对 → 403", bad.status == 403, str(bad.status))

xml = ("<xml><ToUserName>gh_1</ToUserName><FromUserName>oUser</FromUserName>"
       "<CreateTime>1700000000</CreateTime><MsgType>text</MsgType>"
       "<Content>在吗</Content><MsgId>123</MsgId></xml>").encode()
res = wx.parse_inbound("POST", "/wechat/event",
                       {"signature": [sig], "timestamp": ["1700000000"], "nonce": ["n1"]},
                       {}, xml)
ev = res.event or {}
check("微信 XML 解析出文本", "在吗" in json.dumps(ev, ensure_ascii=False), str(ev)[:120])
check("微信入站回空串（5 秒限制，后面用客服消息发）",
      res.reply is not None and res.reply[1] == b"", str(res.reply))

# ---------- 7) 入站：QQ 官方（Ed25519） ----------
print("\n· QQ 官方机器人")
qq = A.build({"qq_app_id": "1", "qq_app_secret": "s",
              "qq_bot_secret": "DG5g3B4j9X2KOErG"}, platform="qqofficial")
res = qq.parse_inbound("POST", "/qqofficial/event", {}, {},
                       json.dumps({"op": 13, "d": {"plain_token": "tok",
                                                   "event_ts": "1700000000"}}).encode())
payload = json.loads(res.reply[1]) if res.reply else {}
check("op 13 返回 signature", bool(payload.get("signature")), str(payload))
seed = (b"DG5g3B4j9X2KOErG" + b"\0" * 32)[:32]
ok_sig = ed25519.verify(ed25519.public_key(seed),
                        b"1700000000" + b"tok",
                        bytes.fromhex(payload.get("signature") or ""))
check("signature 能被自己验证（Ed25519 正确）", ok_sig)

body = json.dumps({"op": 0, "t": "GROUP_AT_MESSAGE_CREATE",
                   "d": {"id": "m1", "content": "你好",
                         "author": {"user_openid": "u1"},
                         "group_openid": "g1"}}).encode()
ts = "1700000001"
good = ed25519.sign(seed, ts.encode() + body).hex()
res = qq.parse_inbound("POST", "/qqofficial/event", {},
                       {"X-Signature-Ed25519": good, "X-Signature-Timestamp": ts}, body)
check("验签通过 → 产出群消息事件", (res.event or {}).get("message_type") == "group",
      str(res.event)[:100])
res = qq.parse_inbound("POST", "/qqofficial/event", {},
                       {"X-Signature-Ed25519": "00" * 64,
                        "X-Signature-Timestamp": ts}, body)
check("签名不对 → 401（不接受伪造事件）", res.status == 401 and not res.event,
      str(res.status))

# ---------- 8) 面板能看见的东西 ----------
print("\n· 面板数据")
from bot.adapters.base import BaseAdapter as _B                       # noqa: E402
check("describe() 带系统插件字段",
      all(k in A.build({}, platform="feishu").describe()
          for k in ("system", "ingress", "version")))
check("system_info() 与普通插件 meta 同构",
      {"name", "title", "description", "version", "schema", "commands"}
      <= set(sysinfo["feishu"].keys()))

html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
for label in ("全部", "限定", "系统", "已启用", "已禁用", "报错"):
    check(f"筛选栏有「{label}」", f">{label}</button>" in html)
check("系统插件卡片有独立样式", ".plugcard.syscard" in html)
check("抽屉区分系统插件", "系统插件（适配器）" in html)

print(f"\nPASS {PASS} / {PASS + FAIL}")
sys.exit(1 if FAIL else 0)
