#!/usr/bin/env python3
"""面板自持 bot 运行时测试：bot/runtime.py / onebot.py / llm.py。

不联网、不碰生产状态：临时插件目录 + 假 OneBot + 假模型。
"""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

STUDIO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(STUDIO))

TMP = Path(tempfile.gettempdir()) / "klee_bot_runtime_test"
if TMP.exists():
    import shutil
    shutil.rmtree(TMP)
(TMP / "features").mkdir(parents=True)
(TMP / "state").mkdir(parents=True)

(TMP / "features" / "config.json").write_text(json.dumps(
    {"enabled": ["t1"], "settings": {}}), encoding="utf-8")
(TMP / "features" / "t1.py").write_text('''
NAME = "测试插件"
DESCRIPTION = "runtime test"
VERSION = "1.0"


def register(api):
    async def ping(ctx):
        await ctx.reply("pong")

    def echo(ctx):
        return "echo:" + ctx.arg_text

    async def who(ctx):
        return "who:" + ctx.user_id

    def block(ctx):
        if ctx.text == "blocked":
            return api.BLOCK
        return None

    async def pic(ctx):
        await ctx.adapter.send_image_file(ctx.chat_id, "/tmp/t1.png", caption="测试图")

    api.on_command("ping", ping, help="回 pong")
    api.on_command("echo", echo, help="回显")
    api.on_command("pic", pic, help="发一张图")
    api.on_command("who", who)
    api.on_nlu(r"说句话", "ping")
    api.on_message(block)
''', encoding="utf-8")

from bot.runtime import BotRuntime, _segments, split_sentences   # noqa: E402

PASS, FAIL = [], []


def check(name, ok, extra=""):
    (PASS if ok else FAIL).append(name)
    print(f"  {'✓' if ok else '✗'} {name}" + (f"  [{extra}]" if extra and not ok else ""))


class FakeOB:
    def __init__(self):
        self.calls = []
        self.n = 0

    def send_text(self, chat_id, text, reply_to=None, at_uid=None):
        self.n += 1
        self.calls.append({"kind": "text", "chat": chat_id, "text": text,
                           "reply_to": reply_to, "at_uid": at_uid})
        return {"message_id": 5000 + self.n}

    def send_image(self, chat_id, path, caption=None, reply_to=None, at_uid=None):
        self.n += 1
        self.calls.append({"kind": "image", "chat": chat_id, "path": str(path), "caption": caption})
        return {"message_id": 6000 + self.n}

    def get_group_member_list(self, gid):
        return [{"user_id": 1001, "card": "小明", "nickname": "小明"}]

    def get_msg(self, mid):
        return {"message": [{"type": "text", "data": {"text": f"原消息{mid}"}}]}

    def send_voice(self, chat_id, path, reply_to=None):
        self.n += 1
        self.calls.append({"kind": "voice", "chat": chat_id, "path": str(path)})
        return {"message_id": 8000 + self.n}

    def get_status(self):
        return {"online": True, "good": True}


class FakeModels:
    def __init__(self, reply="好呀好呀～", fail=False):
        self.reply, self.fail, self.calls = reply, fail, []

    def chat(self, messages, **kw):
        self.calls.append(messages)
        if self.fail:
            raise RuntimeError("boom")
        if callable(self.reply):
            return self.reply()
        return self.reply

    def active(self):
        return {"provider": "fake", "model": "fake-1"}


def make_rt(**cfg_over):
    cfg = {"self_id": "999", "owner_id": "1001", "split_sentences": True,
           "split_soft_max": 40, "send_interval_min": 0, "send_interval_max": 0,
           "quote_reply": True, "group_policy": "mention", "chat_enabled": True,
           "kb_enabled": False, "history_messages": 4}
    cfg.update(cfg_over)
    rt = BotRuntime(features_dir=TMP / "features", config=cfg, models=FakeModels(),
                    soul_path=TMP / "SOUL.md", state_dir=TMP / "state")
    rt.ob = FakeOB()
    return rt


def ev(text="", *, mt="group", uid="1001", gid="555", at=False, reply=None,
       mid=1, msg=None, post="message"):
    if msg is None:
        segs = []
        if at:
            segs.append({"type": "at", "data": {"qq": "999"}})
        if reply:
            segs.append({"type": "reply", "data": {"id": str(reply)}})
        segs.append({"type": "text", "data": {"text": text}})
        msg = segs
    e = {"post_type": post, "message_type": mt, "self_id": 999, "user_id": uid,
         "message_id": mid, "message": msg,
         "sender": {"user_id": uid, "nickname": "小明", "card": "小明",
                    "role": "member"}}
    if mt == "group":
        e["group_id"] = gid
    return e


async def main():
    print("[1] split_sentences")
    check("1.1 短句原样", split_sentences("你好呀。") == ["你好呀。"])
    long = "这是第一句话，长度肯定超过二十四个字了呀。" * 1
    units = split_sentences("第一句很短。第二句也很短。第三句稍微长一点点，但还不够长呢。第四句。")
    check("1.2 短句向上合并（不是一句一条）", len(units) < 4, str(units))
    hard = split_sentences("啊" * 120, soft_max=40)
    check("1.3 超长单句不无限膨胀（≤1 条或按 soft_max 切）", all(len(u) <= 200 for u in hard), str([len(u) for u in hard]))
    check("1.4 连续标点尾巴粘回上一句",
          split_sentences("真的吗？？？") == ["真的吗？？？"], str(split_sentences("真的吗？？？")))

    print("[2] _segments（array / CQ 字符串）")
    arr = [{"type": "text", "data": {"text": "hi"}},
           {"type": "at", "data": {"qq": "999"}}]
    check("2.1 array 原样", _segments(arr) == arr)
    cq = _segments("hi[CQ:at,qq=999] [CQ:image,file=a.jpg,url=http://x/y.png]")
    kinds = [x["type"] for x in cq if x["type"] != "text" or x["data"].get("text", "").strip()]
    at_seg = next(x for x in cq if x["type"] == "at")
    img_seg = next(x for x in cq if x["type"] == "image")
    check("2.2 CQ 字符串解析出 at + image（忽略空白文本段）",
          kinds == ["text", "at", "image"]
          and at_seg["data"]["qq"] == "999" and img_seg["data"]["file"] == "a.jpg", str(cq))
    cq2 = _segments("[CQ:face,id=14]")
    check("2.3 纯 CQ 段", cq2 == [{"type": "face", "data": {"id": "14"}}], str(cq2))

    print("[3] 事件分发")
    rt = make_rt()
    await rt.handle_event(ev(" .ping"))                       # 群里没 @（前缀命令）
    check("3.1 群里未 @ 的前缀命令 → 执行（与旧适配器一致）",
          any(c["text"] == "pong" for c in rt.ob.calls), str(rt.ob.calls))
    rt.ob.calls.clear()
    await rt.handle_event(ev("今天天气不错"))                  # 群里没 @（闲聊）
    check("3.1b 群里未 @ 的闲聊 → 不响应", not rt.ob.calls, str(rt.ob.calls))
    await rt.handle_event(ev(" .ping", at=True))
    check("3.2 群里 @ → 执行命令", any(c["text"] == "pong" for c in rt.ob.calls), str(rt.ob.calls))
    rt.ob.calls.clear()
    await rt.handle_event(ev(" .echo 你好 世界", mt="private"))
    check("3.3 私聊命令 + 参数", any(c["text"] == "echo:你好 世界" for c in rt.ob.calls), str(rt.ob.calls))
    rt.ob.calls.clear()
    await rt.handle_event(ev(" .who", mt="private", uid="2002"))
    check("3.4 ctx.user_id 正确", any(c["text"] == "who:2002" for c in rt.ob.calls), str(rt.ob.calls))
    rt.ob.calls.clear()
    await rt.handle_event(ev(" .ping", mt="private", uid="999"))
    check("3.5 自己发的消息忽略", not rt.ob.calls)
    rt.ob.calls.clear()
    await rt.handle_event(ev(" .ping", mt="private", post="notice"))
    check("3.6 非 message 事件忽略", not rt.ob.calls)
    rt.ob.calls.clear()
    await rt.handle_event(ev("说句话", mt="private"))
    check("3.7 NLU 命中 → 转命令", any(c["text"] == "pong" for c in rt.ob.calls), str(rt.ob.calls))
    rt.ob.calls.clear()
    await rt.handle_event(ev("blocked", mt="private"))
    check("3.8 入站钩子 BLOCK → 不回复", not rt.ob.calls)
    rt.ob.calls.clear()
    await rt.handle_event(ev(msg="[CQ:at,qq=999] [CQ:text]" if False else "[CQ:at,qq=999] .ping",
                             mt="group"))
    check("3.9 CQ 字符串消息也能 @ 命中",
          any(c["text"] == "pong" for c in rt.ob.calls), str(rt.ob.calls))

    print("[4] 引用 / 图片 / 聊天")
    rt = make_rt()
    await rt.handle_event(ev(" .ping", mt="private", mid=77))
    own_id = str(rt.ob.calls[0].get("reply_to") or "")
    first_sent_id = "5001"
    check("4.1 回复带引用（reply_to 是 inbound message_id）",
          rt.ob.calls[0]["reply_to"] == "77", str(rt.ob.calls))
    rt.ob.calls.clear()
    await rt.handle_event(ev("在吗", mt="group", at=False, reply=first_sent_id, mid=78))
    # 引用 bot 自己的消息 → 当作被叫到 → 走聊天
    check("4.2 引用 bot 消息 → 视为被叫到", bool(rt.ob.calls), str(rt.ob.calls))
    rt.ob.calls.clear()
    rt.models.calls.clear()
    await rt.handle_event(ev("", mt="private", msg=[{"type": "image", "data": {"url": "http://x/y.png"}}]))
    # 行为变更（2026-10-17 图片理解）：没配 vision 模型时，只发一张图不再把「[图片]」丢给聊天模型，
    # 而是直接回一句友好说明（不联网、不瞎编图里有什么）。带文字的消息仍照常聊（见 test_vision 7.1）。
    check("4.3 只发图 + 没配 vision 模型 → 友好说明、不调聊天模型",
          any("看不懂图片" in c["text"] or "眼睛还没装好" in c["text"] for c in rt.ob.calls)
          and not rt.models.calls, str(rt.ob.calls)[:160])
    rt.ob.calls.clear()
    rt.models.fail = True
    await rt.handle_event(ev("你好呀", mt="private"))
    check("4.4 模型失败 → 友好兜底，不静默",
          any("卡住" in c["text"] for c in rt.ob.calls), str(rt.ob.calls))

    print("[5] 发送拆分 / 状态")
    rt = make_rt()
    long_text = "第一句话写得比较长一些。第二句话也不短呢。第三句话继续加长度。第四句话收尾啦。"
    await rt.send("private:1", long_text, sentence_split=True)
    check("5.1 多气泡拆分", len(rt.ob.calls) >= 2, str([c["text"] for c in rt.ob.calls]))
    rt.ob.calls.clear()
    await rt.send("private:1", "一句话", sentence_split=False)
    check("5.2 sentence_split=False 不拆", len(rt.ob.calls) == 1)
    st = rt.status()
    check("5.3 status 形状", st["ok"] and st["features"]["plugins"] >= 1 and "stats" in st)
    rt2 = make_rt()
    check("5.4 没有事件时 online=False", rt2.status()["online"] is False)
    await rt2.handle_event(ev(" .ping", mt="private"))
    check("5.5 有事件后 online=True", rt2.status()["online"] is True)

    print("[6] OneBotHTTP 报文构造")
    from bot.onebot import OneBotHTTP
    ob = OneBotHTTP("http://127.0.0.1:3101", "tok")
    sent = []
    ob.call = lambda action, **kw: (sent.append((action, kw)), {"message_id": 9})[1]
    ob.send_text("group:123", "hi", reply_to=5)
    action, kw = sent[-1]
    check("6.1 群消息：send_group_msg + reply 段 + int group_id",
          action == "send_group_msg" and kw["group_id"] == 123
          and kw["message"][0] == {"type": "reply", "data": {"id": "5"}}
          and kw["message"][1]["data"]["text"] == "hi", str(kw))
    ob.send_text("private:456", "yo")
    action, kw = sent[-1]
    check("6.2 私聊：send_private_msg + int user_id",
          action == "send_private_msg" and kw["user_id"] == 456, str(kw))
    check("6.3 chat_id 解析", OneBotHTTP._split_chat_id("group:1") == ("group", "1")
          and OneBotHTTP._split_chat_id("private:2") == ("private", "2"))

    print("[7] ModelStore")
    from bot.llm import ModelStore
    mp = TMP / "models.json"
    ms = ModelStore(mp)
    check("7.1 默认 provider 存在", len(ms.providers()) >= 1 and ms.active()["model"])
    ms.set_active(ms.providers()[0]["id"], ms.providers()[0]["models"][0])
    check("7.2 保存后重新加载一致", ModelStore(mp).active() == ms.active())
    try:
        ms.set_active("nope", "x")
        bad = False
    except ValueError:
        bad = True
    check("7.3 未知 provider 拒绝", bad)

    print("[8] 新功能：引用上下文 / 通知 / 搭话 / 长文分块")
    rt = make_rt()
    rt.ob.calls.clear()
    await rt.handle_event(ev("你怎么看", mt="private", reply="88", mid=90))
    last = rt.models.calls[-1] if rt.models.calls else []
    check("8.1 引用消息文本进入上下文",
          any("[引用: 原消息88]" in str(m.get("content")) for m in last), str(last[-1:])[:160])

    rt = make_rt()
    rt.ob.calls.clear()
    await rt.handle_event({"post_type": "notice", "notice_type": "notify", "sub_type": "poke",
                           "self_id": 999, "target_id": 999, "user_id": 1001, "group_id": 555})
    check("8.2 戳一戳 → 回一句", bool(rt.ob.calls), str(rt.ob.calls))
    rt.ob.calls.clear()
    await rt.handle_event({"post_type": "notice", "notice_type": "notify", "sub_type": "poke",
                           "self_id": 999, "target_id": 1001, "user_id": 1001, "group_id": 555})
    check("8.3 戳别人不掺和", not rt.ob.calls)

    rt = make_rt(greet_on_group_add=True)
    rt.ob.calls.clear()
    await rt.handle_event({"post_type": "notice", "notice_type": "group_increase",
                           "self_id": 999, "group_id": 555, "user_id": 3003})
    check("8.4 入群欢迎（开关打开时）", any("欢迎" in c["text"] for c in rt.ob.calls), str(rt.ob.calls))

    rt = make_rt(group_chime_probability=1.0, group_chime_cooldown=3600)
    rt.ob.calls.clear()
    await rt.handle_event(ev("今天好累啊", mt="group", at=False, mid=91))
    first = len(rt.ob.calls)
    await rt.handle_event(ev("真的累", mt="group", at=False, mid=92))
    check("8.5 随机搭话：概率 1 + 冷却 → 只搭一次",
          first > 0 and len(rt.ob.calls) == first, f"{first} vs {len(rt.ob.calls)}")

    rt = make_rt()
    rt.max_chars = 400
    rt.ob.calls.clear()
    big = "".join(f"第{i}句话写得长一点。" for i in range(80))
    await rt.send("private:1", big, sentence_split=False)
    got = "".join(c["text"] for c in rt.ob.calls)
    check("8.6 超长文本分块发送且不丢内容",
          len(rt.ob.calls) >= 2 and got == big, f"{len(rt.ob.calls)} 块 {len(got)}/{len(big)}")

    print("[9] 语音 / 限流 / 撤回")
    import bot.features_ext as fx

    # 9.1 语音标记 → 文字 + 语音各一条
    tmp_wav = TMP / "tts_test.wav"
    tmp_wav.write_bytes(b"RIFF" + b"\0" * 4096)
    calls = {"n": 0}

    def fake_synth(text, **kw):
        calls["n"] += 1
        return tmp_wav

    fx.synthesize = fake_synth
    rt = make_rt(tts_enabled=True)
    rt.models.reply = "晚安呀[语音:晚安啦，做个好梦]"
    rt.ob.calls.clear()
    await rt.handle_event(ev("晚安", mt="private"))
    kinds = [c["kind"] for c in rt.ob.calls]
    texts = [c.get("text", "") for c in rt.ob.calls if c["kind"] == "text"]
    check("9.1 [语音:…] → 文字（去掉标记）+ 语音段",
          "voice" in kinds and any("晚安呀" in t and "[语音" not in t for t in texts)
          and calls["n"] == 1, f"{kinds} {texts}")

    # 9.2 tts 关 → 剥标记但不发语音
    fx.synthesize = fake_synth
    calls["n"] = 0
    rt = make_rt(tts_enabled=False)
    rt.models.reply = "晚安呀[语音:晚安啦]"
    rt.ob.calls.clear()
    await rt.handle_event(ev("晚安", mt="private"))
    check("9.2 tts 关 → 只发文字、不发语音",
          not any(c["kind"] == "voice" for c in rt.ob.calls)
          and any("晚安呀" in c.get("text", "") for c in rt.ob.calls), str(rt.ob.calls))

    # 9.3 TTS 合成失败 → 文字照常
    fx.synthesize = lambda *a, **k: None
    rt = make_rt(tts_enabled=True)
    rt.models.reply = "嗨[语音:嗨]"
    rt.ob.calls.clear()
    await rt.handle_event(ev("嗨", mt="private"))
    check("9.3 TTS 失败 → 文字照常发出",
          any(c["kind"] == "text" for c in rt.ob.calls)
          and not any(c["kind"] == "voice" for c in rt.ob.calls), str(rt.ob.calls))

    # 9.4 限流：1 条/分钟、桶容量 1 → 第二条被丢
    rt = make_rt(rate_limit_per_min=1, rate_limit_burst=1)
    rt.ob.calls.clear()
    r1 = await rt.send("private:1", "第一条", sentence_split=False)
    r2 = await rt.send("private:1", "第二条", sentence_split=False)
    check("9.4 限流：第二条被丢且计数",
          r1.success and not r2.success and rt.stats["rate_limited"] == 1,
          f"{r1} {r2} {rt.stats['rate_limited']}")

    # 9.5 撤回通知：只统计不回话
    rt = make_rt()
    rt.ob.calls.clear()
    await rt.handle_event({"post_type": "notice", "notice_type": "group_recall",
                           "self_id": 999, "group_id": 555, "user_id": 1001,
                           "message_id": 5})
    check("9.5 撤回通知：stats.recalls+1、不回复",
          rt.stats["recalls"] == 1 and not rt.ob.calls, str(rt.stats))

    print("[10] OCR2 修复回归：SSRF / 非法 ID / 模型校验 / 生命周期")
    from bot import vision as V
    from bot.onebot import OneBotError

    for bad in ("http://127.0.0.1/x.png", "http://169.254.169.254/latest/meta-data",
                "http://[::1]/x.png"):
        try:
            V._assert_public_host(bad.split("//", 1)[1].split("/")[0].strip("[]"))
            ok = False
        except V.VisionError:
            ok = True
        check(f"10.1 SSRF 拒绝 {bad}", ok)

    try:
        V._from_b64("A" * (10 * 1024 * 1024), max_bytes=1024)
        ok = False
    except V.VisionError:
        ok = True
    check("10.2 base64 超大在解码前拒绝", ok)

    for bad_id in ("", "group:", "group:abc", "group:²", "weird:123"):
        try:
            OneBotHTTP._split_chat_id(bad_id)
            ok = False
        except OneBotError:
            ok = True
        check(f"10.3 非法 chat_id 抛 OneBotError（{bad_id!r}）", ok)

    ms = ModelStore(TMP / "models2.json")
    try:
        ms.upsert_provider({"id": "x", "models": []})
        ok = False
    except ValueError:
        ok = True
    check("10.4 空 models 的 provider 被拒", ok)
    ms.data["providers"].append({"id": "empty", "api": "http://127.0.0.1:9",
                                 "key_env": "", "models": []})
    try:
        ms.chat([{"role": "user", "content": "hi"}], provider="empty", model="")
        ok = False
    except RuntimeError as e:
        ok = "没有可用模型" in str(e)
    check("10.5 显式 provider 无模型 → 明确报错（不发请求）", ok)

    from bot.service import BotService
    home2 = TMP / "home2"
    (home2 / "bot" / "features").mkdir(parents=True, exist_ok=True)
    (home2 / "bot" / "features" / "config.json").write_text('{"enabled": []}', encoding="utf-8")
    (home2 / "state").mkdir(parents=True, exist_ok=True)
    svc = BotService(home2)
    svc.start()
    st1 = svc.status().get("ok")
    svc.stop()
    st2 = svc.status().get("ok")
    svc.start()
    st3 = svc.status().get("ok")
    svc.stop()
    check("10.6 生命周期：start→stop→再 start", st1 is True and st2 is False and st3 is True,
          f"{st1}/{st2}/{st3}")

    # 技能 handler 要在 bot 事件循环里跑（不是每次新建 loop）：start 后必须把 loop 绑定到 runtime
    svc.start()
    rt_loop = getattr(svc.runtime, "loop", None)
    rt_thread = getattr(svc.runtime, "loop_thread", None)
    check("10.7 runtime 绑定了 bot 的 loop/线程",
          rt_loop is svc.loop and rt_thread is svc._thread, f"{rt_loop is svc.loop}/{rt_thread is svc._thread}")
    svc.stop()

    print("[11] agent 接入")
    class FakeAgentModels(FakeModels):
        def __init__(self, rounds=None, fail=False):
            super().__init__(reply="普通聊天回复", fail=fail)
            self.rounds = list(rounds or [])
            self.agent_kw = []

        def complete_raw(self, messages, **kw):
            # 快照：agent 循环会继续往 messages 里追加 tool 结果
            self.agent_kw.append({**kw, "messages": [dict(m) for m in messages]})
            if not self.rounds:
                raise RuntimeError("no scripted agent round")
            r = self.rounds.pop(0)
            if isinstance(r, Exception):
                raise r
            return r

        def agent(self):
            return {"provider": "fake", "model": "fake-agent"}

    rt = make_rt(agent_enabled=True, agent_owner_only=True, agent_tools=["now"])
    rt.models = FakeAgentModels([
        {"text": "", "tool_calls": [{"id": "c1", "name": "now", "arguments": "{}"}]},
        {"text": "agent 的回答", "tool_calls": []},
    ])
    await rt.handle_event(ev("现在几点", mt="private", uid="1001"))
    texts = [c["text"] for c in rt.ob.calls]
    check("11.1 主人私聊 → 走 agent", "agent 的回答" in texts and rt.models.agent_kw,
          str(texts)[:150])
    check("11.2 工具白名单生效", [t["function"]["name"] for t in rt.models.agent_kw[0]["tools"]] == ["now"],
          str(rt.models.agent_kw[0].get("tools"))[:160])
    check("11.3 指标记录 agent 轮次/工具数",
          rt.stats["agent_runs"] == 1 and rt.stats["agent_tool_calls"] == 1,
          f'{rt.stats["agent_runs"]}/{rt.stats["agent_tool_calls"]}')

    rt.ob.calls.clear()
    rt = make_rt(agent_enabled=True, agent_owner_only=True)
    rt.models = FakeAgentModels([RuntimeError("模型炸了")])
    await rt.handle_event(ev("你好呀", mt="private", uid="1001"))
    texts = [c["text"] for c in rt.ob.calls]
    check("11.4 agent 失败 → 回退普通聊天", "普通聊天回复" in texts and rt.stats["agent_fallbacks"] == 1,
          str(texts)[:150])

    rt.ob.calls.clear()
    rt = make_rt(agent_enabled=True, agent_owner_only=True)
    rt.models = FakeAgentModels([{"text": "不该走 agent", "tool_calls": []}])
    await rt.handle_event(ev("你好呀", mt="private", uid="2002"))
    texts = [c["text"] for c in rt.ob.calls]
    check("11.5 仅主人：非主人走普通聊天", not rt.models.agent_kw and "普通聊天回复" in texts,
          str(texts)[:150])

    rt.ob.calls.clear()
    rt = make_rt(agent_enabled=False, agent_owner_only=True)
    rt.models = FakeAgentModels([{"text": "强制 agent 回答", "tool_calls": []}])
    await rt.handle_event(ev(" .agent 查一下天气", mt="private", uid="1001"))
    texts = [c["text"] for c in rt.ob.calls]
    check("11.6 .agent 命令在关闭时也能强制走 agent", "强制 agent 回答" in texts, str(texts)[:150])

    rt.ob.calls.clear()
    rt = make_rt(agent_enabled=True, agent_owner_only=True)
    rt.models = FakeAgentModels([{"text": "x", "tool_calls": []}])
    await rt.handle_event(ev(" .agent 查一下", mt="private", uid="2002"))
    texts = [c["text"] for c in rt.ob.calls]
    check("11.7 非主人用 .agent → 拒绝", any("只有主人" in t for t in texts) and not rt.models.agent_kw,
          str(texts)[:150])

    print("[12] 插件即技能")
    rt = make_rt(agent_enabled=True, agent_owner_only=True)
    hub = rt.skills
    names = [s["name"] for s in hub.skills()]
    check("12.1 技能目录来自插件", names == ["t1"], str(names))
    check("12.2 索引里有命令", ".ping" in hub.index_text(is_owner=True, is_admin=True, is_group=False))
    rt.models = FakeAgentModels([
        {"text": "", "tool_calls": [{"id": "s1", "name": "list_skills", "arguments": "{}"}]},
        {"text": "技能清单我看到了", "tool_calls": []},
    ])
    await rt.handle_event(ev("有哪些技能", mt="private", uid="1001"))
    first_msgs = rt.models.agent_kw[0]["messages"]
    tool_out = [m for m in rt.models.agent_kw[1]["messages"] if m["role"] == "tool"]
    check("12.3 system 注入了技能目录",
          any("技能目录" in str(m.get("content")) and "t1" in str(m.get("content"))
              for m in first_msgs if m["role"] == "system"), str(first_msgs[0].get("content"))[-160:])
    check("12.4 技能工具在 tools 里",
          {"list_skills", "use_skill"} <= {t["function"]["name"] for t in rt.models.agent_kw[0]["tools"]})
    check("12.5 list_skills 结果回灌给模型",
          tool_out and "t1" in tool_out[0]["content"], str(tool_out)[:160])

    rt.ob.calls.clear()
    rt.models = FakeAgentModels([
        {"text": "", "tool_calls": [{"id": "s2", "name": "use_skill",
                                     "arguments": json.dumps({"name": "t1", "text": "echo 你好"})}]},
        {"text": "它说 echo:你好", "tool_calls": []},
    ])
    await rt.handle_event(ev("让 echo 说你好", mt="private", uid="1001"))
    tool_out = [m for m in rt.models.agent_kw[1]["messages"] if m["role"] == "tool"]
    check("12.6 use_skill 执行插件命令并捕获输出",
          tool_out and "echo:你好" in tool_out[0]["content"], str(tool_out)[:160])
    check("12.7 插件输出没被直接发到 QQ",
          [c["text"] for c in rt.ob.calls] == ["它说 echo:你好"], str(rt.ob.calls)[:160])

    rt.ob.calls.clear()
    rt.models = FakeAgentModels([
        {"text": "", "tool_calls": [{"id": "s3", "name": "use_skill",
                                     "arguments": json.dumps({"name": "t1", "text": "pic"})}]},
        {"text": "图发你啦", "tool_calls": []},
    ])
    await rt.handle_event(ev("发张图", mt="private", uid="1001"))
    kinds = [c["kind"] for c in rt.ob.calls]
    check("12.8 技能生成的图片由 runtime 发出", "image" in kinds and kinds[-1] == "text", str(kinds))

    rt.ob.calls.clear()
    rt.models = FakeAgentModels([{"text": "no", "tool_calls": []}])
    await rt.handle_event(ev(" .技能列表", mt="private", uid="1001"))
    check("12.9 .技能列表 命令列出插件", any(".ping" in c["text"] for c in rt.ob.calls),
          str(rt.ob.calls)[:160])

    rt = make_rt(agent_enabled=True, agent_owner_only=True, agent_skills=False)
    rt.models = FakeAgentModels([{"text": "ok", "tool_calls": []}])
    await rt.handle_event(ev("你好", mt="private", uid="1001"))
    tools = {t["function"]["name"] for t in rt.models.agent_kw[0]["tools"]}
    sys0 = rt.models.agent_kw[0]["messages"][0]["content"]
    check("12.10 关掉技能后：工具/索引都不出现",
          "use_skill" not in tools and "技能目录" not in sys0, str(tools))

    rt = make_rt(agent_enabled=True, agent_owner_only=True)
    rt.models = FakeAgentModels([{"text": "ok", "tool_calls": []}])
    had = "agent" in rt.mgr.commands
    rt.mgr.load()                                            # 插件热重载路径
    check("11.8 热重载后内置 .agent 仍在", had and "agent" in rt.mgr.commands,
          f"before={had} after={'agent' in rt.mgr.commands}")

    print("[13] 好友/入群请求")
    rt = make_rt(agent_enabled=False, request_notify=True)
    rt.ob.calls.clear()
    await rt.handle_event({"post_type": "request", "request_type": "friend",
                           "flag": "flag-1", "user_id": "2002", "nickname": "路人甲",
                           "comment": "加个好友"})
    texts = [c["text"] for c in rt.ob.calls if c["kind"] == "text"]
    check("13.1 请求计数 + 私聊提醒主人",
          rt.stats["requests"] == 1 and any("好友请求" in t for t in texts)
          and any(c["chat"] == "private:1001" for c in rt.ob.calls), str(texts)[:160])
    check("13.2 提醒里说明不会自动同意", any("没有自动同意" in t for t in texts), str(texts)[:160])
    recs = [json.loads(x) for x in (TMP / "state" / "requests.jsonl").read_text(encoding="utf-8").splitlines()]
    check("13.3 请求落盘（flag/类型/备注都在）",
          recs and recs[-1]["flag"] == "flag-1" and recs[-1]["kind"] == "friend"
          and recs[-1]["comment"] == "加个好友", str(recs[-1:])[:200])
    check("13.4 没有调用任何审批接口（只发了提醒）",
          all(c["kind"] != "approve" for c in rt.ob.calls), str(rt.ob.calls)[:160])

    rt.ob.calls.clear()
    rt.config["request_notify"] = False
    await rt.handle_event({"post_type": "request", "request_type": "group", "sub_type": "add",
                           "flag": "flag-2", "user_id": "2003", "group_id": "555",
                           "comment": ""})
    check("13.5 关掉提醒后：只记录不发消息",
          rt.stats["requests"] == 2 and not rt.ob.calls, str(rt.ob.calls)[:120])

    rt.ob.calls.clear()
    await rt.handle_event({"post_type": "request", "request_type": "friend", "user_id": "2004"})
    check("13.6 没有 flag 的请求直接忽略", rt.stats["requests"] == 2 and not rt.ob.calls)

    print("[14] 配置损坏不覆盖")
    home3 = TMP / "home3"
    (home3 / "bot" / "features").mkdir(parents=True, exist_ok=True)
    (home3 / "bot" / "features" / "config.json").write_text('{"enabled": []}', encoding="utf-8")
    (home3 / "state").mkdir(parents=True, exist_ok=True)
    cfg_path = home3 / "state" / "bot_config.json"
    cfg_path.write_text("{ 坏掉的 json", encoding="utf-8")
    svc3 = BotService(home3)
    check("14.1 坏配置 → 回退默认值但原文件保留",
          cfg_path.read_text(encoding="utf-8") == "{ 坏掉的 json"
          and svc3.config["owner_id"] == "", cfg_path.read_text(encoding="utf-8")[:40])
    backups = list((home3 / "state").glob("bot_config.json.corrupt-*.bak"))
    check("14.2 坏文件已备份", bool(backups) and backups[0].read_text(encoding="utf-8") == "{ 坏掉的 json",
          str(backups))
    check("14.3 首次运行（无配置文件）才写默认", not (home3 / "state" / "bot_config.json.init.tmp").exists())

    print()
    print(f"结果：{len(PASS)} 通过 / {len(FAIL)} 失败")
    if FAIL:
        print("失败项：")
        for x in FAIL:
            print("  ✗", x)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
