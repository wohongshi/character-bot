#!/usr/bin/env python3
"""OCR 第六轮：把「修了什么」钉成回归。

这一组不是复述实现，而是**每条都验行为**：群聊策略、引用回复下的命令分发、
语音限流、坏 characters.json、theme 越界、webhook 重配置、适配器重名、json 收敛、
agent_tools 清空、空白 docstring。

全部临时目录 + 假适配器，不联网、不碰生产状态。
"""
from __future__ import annotations

import asyncio
import json
import shutil
import sys
import tempfile
from pathlib import Path

STUDIO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(STUDIO))

from bot import adapters as A                                   # noqa: E402
from bot.adapters.base import AdapterError, BaseAdapter         # noqa: E402
from bot.characters import CharacterError, CharacterStore       # noqa: E402
from bot.framework import coerce_setting                        # noqa: E402
from bot.runtime import BotRuntime                              # noqa: E402

PASS = FAIL = 0


def check(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}  {detail}")


TMP = Path(tempfile.mkdtemp())
FEAT = TMP / "features"
FEAT.mkdir()
(FEAT / "config.json").write_text(json.dumps({"enabled": ["t1"], "settings": {}}),
                                  encoding="utf-8")
(FEAT / "t1.py").write_text('''
NAME = "测试"
DESCRIPTION = "ocr6"
VERSION = "1.0"


def register(api):
    async def ping(ctx):
        await ctx.reply("pong")

    api.on_command("ping", ping, help="回 pong")
''', encoding="utf-8")


class FakeOB:
    platform = "onebot11"
    CAPABILITIES = {"text": True, "image": True, "voice": True,
                    "group_member_list": True, "message_lookup": True,
                    "request_approval": True, "recall": True}
    owner_id = "1001"

    def __init__(self):
        self.sent, self.voice, self.images = [], [], []

    def chat_id_of(self, is_group, group_id, user_id):
        return f"group:{group_id}" if is_group else f"private:{user_id}"

    def send_text(self, chat_id, text, *, reply_to=None, at_uid=None):
        self.sent.append({"chat": chat_id, "text": text, "reply_to": reply_to})
        return {"message_id": str(len(self.sent))}

    def send_image(self, chat_id, path, caption=None, *, reply_to=None, at_uid=None):
        self.images.append({"chat": chat_id, "path": path, "reply_to": reply_to})
        return {"message_id": "img"}

    def send_voice(self, chat_id, path, *, reply_to=None):
        self.voice.append({"chat": chat_id, "path": path})
        return {"message_id": "v"}

    def get_msg(self, mid):
        return {"message": [{"type": "text", "data": {"text": "被引用的原话"}}]}

    def get_group_member_list(self, gid):
        return []

    def recall(self, mid):
        return {"ok": True}


class FakeModels:
    def __init__(self):
        self.calls = 0
        self.seen = []

    def chat(self, messages, **kw):
        self.calls += 1
        self.seen.append(messages)
        return "模型回话"

    def active(self):
        return {"provider": "fake", "model": "m"}

    def vision(self):
        return {"configured": False}

    def agent(self):
        return {}


def make_rt(**over):
    cfg = {"self_id": "999", "owner_id": "1001", "split_sentences": False,
           "send_interval_min": 0, "send_interval_max": 0, "quote_reply": True,
           "group_policy": "mention", "chat_enabled": True, "kb_enabled": False,
           "tts_enabled": False, "vision_enabled": False, "agent_enabled": False,
           "log_enabled": False, "rate_limit_per_min": 0}
    cfg.update(over)
    rt = BotRuntime(features_dir=FEAT, config=cfg, models=FakeModels(),
                    soul_path=TMP / "SOUL.md", state_dir=TMP / "state")
    rt.ob = FakeOB()
    return rt


def msg(text="", *, at=False, reply=None, gid="555", uid="2002", mid=1):
    segs = []
    if at:
        segs.append({"type": "at", "data": {"qq": "999"}})
    if reply:
        segs.append({"type": "reply", "data": {"id": str(reply)}})
    segs.append({"type": "text", "data": {"text": text}})
    return {"post_type": "message", "message_type": "group", "self_id": 999,
            "user_id": uid, "group_id": gid, "message_id": mid, "message": segs,
            "sender": {"user_id": uid, "nickname": "小明", "card": "小明",
                       "role": "member"}}


# ---------- 1) 群聊策略三分支 ----------
print("· 群聊策略（OCR #3）")
rt = make_rt(group_policy="mention")
asyncio.run(rt.handle_event(msg("随便说点什么")))          # 没 @
check("mention：没被叫到不吭声", not rt.ob.sent and not rt.models.calls)
asyncio.run(rt.handle_event(msg("在吗", at=True)))
check("mention：@ 了才回", rt.models.calls == 1 and rt.ob.sent)

rt = make_rt(group_policy="open")
asyncio.run(rt.handle_event(msg("群友闲聊一句")))
check("open：没 @ 也看（会回）", rt.models.calls >= 1, f"calls={rt.models.calls}")

rt = make_rt(group_policy="disabled")
asyncio.run(rt.handle_event(msg(".ping")))                 # 命令
asyncio.run(rt.handle_event(msg("在吗", at=True)))          # @
check("disabled：连命令和 @ 都不回（完全不理）",
      not rt.ob.sent and rt.models.calls == 0, f"sent={len(rt.ob.sent)} calls={rt.models.calls}")

# ---------- 2) 引用回复下的命令分发 ----------
print("\n· 引用回复 + 命令（OCR #28）")
rt = make_rt()
asyncio.run(rt.handle_event(msg(".ping", reply=77)))
check("回复一条消息发 .ping 仍然命中命令",
      any("pong" in s["text"] for s in rt.ob.sent), str(rt.ob.sent)[:160])
check("命令没被当成闲聊喂给模型", rt.models.calls == 0)

rt = make_rt()
asyncio.run(rt.handle_event(msg("今天想聊点别的", reply=77, at=True)))
check("普通聊天仍然带上引用上下文",
      bool(rt.models.seen) and any("被引用的原话" in str(c)
                                   for c in rt.models.seen[-1]),
      str(rt.models.seen[-1])[-200:] if rt.models.seen else "没调模型")

# ---------- 3) 只有语音的回复也要限流 / 计数 ----------
print("\n· 语音限流（OCR #27）")
rt = make_rt(rate_limit_per_min=1, rate_limit_burst=1)
res1 = asyncio.run(rt.send("private:1", "[语音:第一句]"))
res2 = asyncio.run(rt.send("private:1", "[语音:第二句]"))
check("第一条语音发出去", getattr(res1, "success", False) is True, str(res1))
check("第二条被限流（不再绕过）", getattr(res2, "error", "") == "rate_limited", str(res2))
check("发出去的语音计入 sent", rt.stats.get("sent", 0) == 1, str(rt.stats.get("sent")))

# ---------- 4) 坏 characters.json 不该让启动挂掉 ----------
print("\n· 角色文件容错（OCR #14 / #15 / #16）")
bad = Path(tempfile.mkdtemp())
(bad / "characters.json").write_text('{"active": "klee", "characters": 5, "order": 5}',
                                     encoding="utf-8")
try:
    st = CharacterStore(bad)
    check("characters 是数字也不崩", True)
except Exception as e:                                        # noqa: BLE001
    check("characters 是数字也不崩", False, f"{type(e).__name__}: {e}")
    st = None
if st is not None:
    st.data["active"] = "nobody"
    st.reload_defs()
    check("坏 active 值会被修回默认", st.data.get("active") == st.active_id(),
          f'{st.data.get("active")} vs {st.active_id()}')
    try:
        st.set_theme("klee", "../../etc/passwd")
        check("theme 越界被拒", False, "居然接受了")
    except CharacterError:
        check("theme 越界被拒", True)
    files = [p.name for p in (bad / "characters.d").glob("*.json")]
    check("被拒的 theme 没有落盘", not files, str(files))

# ---------- 5) webhook：能清空、图片带回复、状态码真实 ----------
print("\n· Webhook 适配器（OCR #10 / #11 / #12 / #13 / #32）")
wh = A.build({"adapter": "webhook", "webhook_url": "http://127.0.0.1:9/x",
              "webhook_field": "text"})
wh.reconfigure({"webhook_url": "", "webhook_field": "message"})
check("webhook_url 能清空", wh.url == "", repr(wh.url))
check("字段名能改", wh.field == "message", wh.field)
wh.reconfigure({"webhook_url": "http://127.0.0.1:9/y", "webhook_timeout": 3})
check("timeout 热更新", wh.timeout == 3, str(wh.timeout))
check("health 说没配地址", wh.health()["ok"] is False or wh.url != "", str(wh.health()))

img = Path(tempfile.mkdtemp()) / "a.png"
img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 32)
posted = {}
wh._post = lambda payload: posted.update(payload) or {"ok": True, "status": 200}
wh.send_image("group:1", str(img), "标题", reply_to="42", at_uid="7")
check("图片 payload 带 reply_to", posted.get("reply_to") == "42", str(posted)[:120])
check("图片 payload 带 at_uid", posted.get("at_uid") == "7")

big = Path(tempfile.mkdtemp()) / "big.bin"
with open(big, "wb") as f:
    f.seek(11 * 1024 * 1024)
    f.write(b"0")
try:
    wh.send_image("group:1", str(big))
    check("超大图片被拒", False)
except AdapterError as e:
    check("超大图片被拒（且没整份读进内存）", "过大" in str(e), str(e))

# ---------- 6) 适配器注册 / 元信息 ----------
print("\n· 适配器注册表（OCR #6 / #7）")
try:
    @A.register
    class _Dup(BaseAdapter):
        platform = "onebot11"
        title = "重复"
    check("同名 platform 直接报错", False, "居然注册成功了")
except AdapterError as e:
    check("同名 platform 直接报错", "已被" in str(e), str(e))

class _BlankDoc(BaseAdapter):
    platform = "blankdoc"
    title = "空白文档"
    """   """

A.REGISTRY["blankdoc"] = _BlankDoc
try:
    rows = A.available()
    check("全空白 docstring 不会让清单崩", any(r["id"] == "blankdoc" for r in rows))
except Exception as e:                                        # noqa: BLE001
    check("全空白 docstring 不会让清单崩", False, f"{type(e).__name__}: {e}")
A.REGISTRY.pop("blankdoc", None)

# ---------- 7) 配置收敛：0 / False 不能被当成空 ----------
print("\n· 配置收敛（OCR #21）")
f = {"type": "json"}
check("json 0 保持 0", coerce_setting(f, 0) == 0, repr(coerce_setting(f, 0)))
check("json false 保持 false", coerce_setting(f, False) is False)
check("json 空串 = None", coerce_setting(f, "") is None)
check("json 对象照常解析", coerce_setting(f, '{"a": 1}') == {"a": 1})

# ---------- 8) agent_tools 清空要真的清空 ----------
print("\n· 工具白名单（OCR #12b）")
rt = make_rt(agent_enabled=True)
before = list(rt.agent_tools)
rt.apply_config({**rt.config, "agent_tools": []})
check("全取消 = 没有工具（不再被 or 顶回旧值）", rt.agent_tools == [], str(rt.agent_tools))
rt.apply_config({**rt.config, "agent_tools": ["now"]})
check("只留一个工具", rt.agent_tools == ["now"], str(rt.agent_tools))
rt.apply_config({**rt.config, "agent_tools": before})
check("能恢复全量", len(rt.agent_tools) == len(before))

# ---------- 9) 聊天请求必须走 _open_url（测试不能打到真模型） ----------
print("\n· 模型请求出口（OCR #2 的护栏）")
import bot.llm as L                                            # noqa: E402

cfg_dir = Path(tempfile.mkdtemp())
(cfg_dir / "models.json").write_text(json.dumps({
    "active": {"provider": "p", "model": "m"},
    "providers": [{"id": "p", "name": "P", "api": "http://127.0.0.1:9/v1",
                   "key_env": "", "models": ["m"]}]}), encoding="utf-8")
ms = L.ModelStore(cfg_dir / "models.json")


def _boom(*a, **k):
    raise RuntimeError("no network in tests")


_orig = L._open_url
L._open_url = _boom
try:
    try:
        ms.complete_raw([{"role": "user", "content": "x"}])
        check("complete_raw 走 _open_url（不直连网络）", False, "居然没报错")
    except RuntimeError as e:
        check("complete_raw 走 _open_url（不直连网络）", "no network" in str(e), str(e))
finally:
    L._open_url = _orig

print(f"\nPASS {PASS} / {PASS + FAIL}")
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if FAIL else 0)
