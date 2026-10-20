#!/usr/bin/env python3
"""主动消息（`bot/proactive.py`）。

关键点：
1. **默认全关** —— 没配置就一条都不发；
2. 每日上限 / 最小间隔 / 多久没聊 / 命中概率，四道闸逐个生效并说明原因；
3. 会话策略说「完全不理」或静默时段 → 一条都不发（判在 server，但要有测试钉住）；
4. 连续失败 3 次自动暂停，别对着掉线的账号每 5 分钟重试；
5. panel 的「按字段保存」不能把别的设置悄悄恢复默认。
"""
from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PASS, FAIL = [], []


def check(name, ok, extra=""):
    (PASS if ok else FAIL).append(name)
    print(f"  {'✓' if ok else '✗'} {name}" + (f"   [{extra}]" if extra and not ok else ""))


def section(t):
    print(f"\n== {t}")


class Rng:
    """固定返回值的假随机源（避免测试偶发失败）。"""

    def __init__(self, v):
        self.v = v

    def random(self):
        return self.v


def main():
    from bot import proactive as PV

    tmp = Path(tempfile.mkdtemp(prefix="klee-proact-"))
    now = time.mktime((2026, 10, 20, 15, 0, 0, 0, 0, -1))

    section("1. 默认值")
    check("1.1 默认是关的", PV.DEFAULTS["on"] is False)
    check("1.2 默认每天 2 条", PV.DEFAULTS["per_day"] == 2)
    check("1.3 没配置时 load 出空表", PV.load(tmp) == {"chats": {}})

    section("2. decide：四道闸")
    base = {"on": True, "per_day": 2, "min_gap_h": 6, "idle_h": 12, "chance": 1.0}
    r = PV.decide(base, now=now, last_user_ts=now - 20 * 3600, rng=Rng(0.0))
    check("2.1 都满足就发", r["send"] is True, str(r))
    r = PV.decide({**base, "on": False}, now=now, last_user_ts=now - 20 * 3600, rng=Rng(0.0))
    check("2.2 关着就不发", r["send"] is False and r["reason"] == "没开")
    r = PV.decide(base, now=now, last_user_ts=now - 20 * 3600, blocked="静默时段", rng=Rng(0.0))
    check("2.3 被策略拦就不发", r["send"] is False and r["reason"] == "静默时段")
    r = PV.decide(base, now=now, last_user_ts=now - 3600, rng=Rng(0.0))
    check("2.4 刚聊过不发", r["send"] is False and "刚聊过" in r["reason"], r["reason"])
    r = PV.decide({**base, "sent_day": time.strftime("%Y-%m-%d", time.localtime(now)),
                   "sent_today": 2}, now=now, last_user_ts=now - 20 * 3600, rng=Rng(0.0))
    check("2.5 到每日上限就不发", r["send"] is False and "已经发了" in r["reason"], r["reason"])
    r = PV.decide({**base, "sent_day": time.strftime("%Y-%m-%d", time.localtime(now - 86400)),
                   "sent_today": 2}, now=now, last_user_ts=now - 20 * 3600, rng=Rng(0.0))
    check("2.6 换天之后计数归零", r["send"] is True, str(r))
    r = PV.decide({**base, "last_sent_ts": now - 3600}, now=now,
                  last_user_ts=now - 20 * 3600, rng=Rng(0.0))
    check("2.7 没到最小间隔就不发", r["send"] is False and "还差" in r["reason"], r["reason"])
    r = PV.decide({**base, "fails": PV.FAIL_STOP}, now=now,
                  last_user_ts=now - 20 * 3600, rng=Rng(0.0))
    check("2.8 连续失败会暂停", r["send"] is False and "暂停" in r["reason"], r["reason"])
    r = PV.decide(base, now=now, last_user_ts=0, rng=Rng(0.0))
    check("2.9 没有对话记录就不主动", r["send"] is False and "没有对话记录" in r["reason"])
    r = PV.decide({**base, "chance": 0.0}, now=now, last_user_ts=now - 20 * 3600, rng=Rng(1.0))
    check("2.10 概率没命中就不发", r["send"] is False and r["reason"] == "这次没轮到")
    r = PV.decide({"on": True, "per_day": 0}, now=now, last_user_ts=now - 99 * 3600,
                  rng=Rng(0.0))
    check("2.11 per_day=0 等于全关", r["send"] is False)

    section("3. 参数钳制")
    c = PV._clamp({"per_day": 999, "min_gap_h": -5, "idle_h": 99999, "chance": 9})
    check("3.1 每日上限封顶", c["per_day"] == PV.LIMITS["per_day"][1], str(c["per_day"]))
    check("3.2 间隔不会变成负数", c["min_gap_h"] >= PV.LIMITS["min_gap_h"][0])
    check("3.3 没聊门槛封顶", c["idle_h"] == PV.LIMITS["idle_h"][1])
    check("3.4 概率封顶 1", c["chance"] == 1.0)
    check("3.5 坏值退回默认", PV._clamp({"per_day": "abc"})["per_day"] == PV.DEFAULTS["per_day"])

    section("4. set_chat：按字段合并")
    PV.set_chat(tmp, "group:1", on=True, per_day=3)
    cfg = PV.get_chat(tmp, "group:1")
    check("4.1 on 写进去了", cfg["on"] is True and cfg["per_day"] == 3, str(cfg))
    PV.set_chat(tmp, "group:1", chance=0.5)
    cfg = PV.get_chat(tmp, "group:1")
    check("4.2 只改 chance，别的字段不丢", cfg["per_day"] == 3 and cfg["chance"] == 0.5, str(cfg))
    PV.set_chat(tmp, "group:1", on=False, per_day=2, min_gap_h=6, idle_h=12,
                chance=0.35, note="")
    check("4.3 面板「关」要保留这一行（否则一点就删配置）",
          PV.get_chat(tmp, "group:1").get("on") is False, str(PV.get_chat(tmp, "group:1")))
    PV.set_chat(tmp, "group:3", on=True)
    PV.set_chat(tmp, "group:3")
    check("4.3b 没提 on 且全默认才不占位",
          PV.get_chat(tmp, "group:3")["on"] is True)
    PV.set_chat(tmp, "group:4", note="")
    check("4.3c 没提 on 也没别的设置 → 不占位",
          PV.get_chat(tmp, "group:4") == {}, str(PV.get_chat(tmp, "group:4")))
    # 已存在的行：把一个数字改回默认值，不能把整行删掉（面板逐字段保存的常见路径）
    PV.set_chat(tmp, "group:5", on=False, per_day=3)
    PV.set_chat(tmp, "group:5", per_day=2)
    check("4.3d 已有行改成默认值也保留",
          PV.get_chat(tmp, "group:5").get("per_day") == 2,
          str(PV.get_chat(tmp, "group:5")))
    PV.set_chat(tmp, "group:2", on=True)
    check("4.4 remove 返回是否删掉", PV.remove_chat(tmp, "group:2") is True)
    check("4.5 删完就没了", PV.get_chat(tmp, "group:2") == {})
    check("4.6 删不存在的返回 False", PV.remove_chat(tmp, "group:none") is False)
    try:
        PV.set_chat(tmp, "")
        ok, err = False, "没报错"
    except ValueError as e:
        ok, err = True, str(e)
    check("4.7 空会话 id 报错", ok, err)

    section("5. 会话数量上限")
    many = Path(tempfile.mkdtemp(prefix="klee-proact-many-"))
    for i in range(PV.MAX_CHATS):
        PV.set_chat(many, f"group:{i}", on=True)
    try:
        PV.set_chat(many, "group:extra", on=True)
        ok, err = False, "没报错"
    except ValueError as e:
        ok, err = True, str(e)
    check("5.1 超过上限报错", ok, err)
    check("5.2 已存在的还能改", PV.set_chat(many, "group:0", per_day=5)["per_day"] == 5)

    section("6. intent / 提示词")
    check("6.1 早上", PV.pick_intent(time.mktime((2026, 10, 20, 7, 0, 0, 0, 0, -1)))["key"] == "morning")
    check("6.2 中午", PV.pick_intent(time.mktime((2026, 10, 20, 12, 0, 0, 0, 0, -1)))["key"] == "noon")
    check("6.3 下午", PV.pick_intent(time.mktime((2026, 10, 20, 15, 0, 0, 0, 0, -1)))["key"] == "afternoon")
    check("6.4 晚上", PV.pick_intent(time.mktime((2026, 10, 20, 20, 0, 0, 0, 0, -1)))["key"] == "evening")
    check("6.5 深夜", PV.pick_intent(time.mktime((2026, 10, 20, 2, 0, 0, 0, 0, -1)))["key"] == "night")
    msgs = PV.build_messages("你是测试", intent={"key": "noon", "label": "中午", "hint": "吃饭"},
                             recent="对方：在忙", name="主人", now=now)
    sys_text = msgs[0]["content"]
    check("6.6 人格在最前", sys_text.startswith("你是测试"))
    check("6.7 说明是主动开口", "这次是你先开口" in sys_text)
    check("6.8 带上最近对话", "在忙" in sys_text)
    check("6.9 带上昵称", "主人" in sys_text)
    check("6.10 最后一条是 cue", msgs[-1]["role"] == "user" and "没有新消息" in msgs[-1]["content"])
    check("6.11 不要出现元信息指令的反面措辞", "别提「主动」" in sys_text)

    section("7. 记账 / 日志")
    st = Path(tempfile.mkdtemp(prefix="klee-proact-log-"))
    PV.set_chat(st, "group:9", on=True, per_day=5)
    before = PV.get_chat(st, "group:9")["sent_today"] if "sent_today" in PV.get_chat(st, "group:9") else 0
    cur = PV.mark_sent(st, "group:9", "早上好呀", now=now, intent="morning")
    check("7.1 计数 +1", cur["sent_today"] == before + 1, str(cur.get("sent_today")))
    check("7.2 记了日期", cur["sent_day"] == time.strftime("%Y-%m-%d", time.localtime(now)))
    check("7.3 记了 last_sent_ts", cur["last_sent_ts"] == now)
    log = PV.read_log(st, 5)
    check("7.4 日志里有一条", len(log) == 1 and log[0]["text"] == "早上好呀", str(log))
    check("7.5 日志按时间倒序（新的在前）", PV.read_log(st, 5)[0]["event"] == "sent")
    PV.mark_fail(st, "group:9", "超时", now=now + 1)
    cur = PV.get_chat(st, "group:9")
    check("7.6 失败计数", cur["fails"] == 1 and "超时" in cur["last_error"], str(cur))
    PV.mark_sent(st, "group:9", "又一条", now=now + 2, intent="noon")
    check("7.7 成功一次就清掉失败计数", PV.get_chat(st, "group:9")["fails"] == 0)
    stamp = time.mktime((2026, 10, 21, 9, 0, 0, 0, 0, -1))
    cur = PV.mark_sent(st, "group:9", "第二天", now=stamp, intent="morning")
    check("7.8 换天计数归 1", cur["sent_today"] == 1, str(cur["sent_today"]))
    big = Path(tempfile.mkdtemp(prefix="klee-proact-big-"))
    for i in range(PV.LOG_MAX_LINES + 30):
        PV._log(big, {"ts": i, "event": "sent", "chat_id": "c", "text": str(i)})
    lines = (big / "proactive_log.jsonl").read_text(encoding="utf-8").splitlines()
    check("7.9 日志有上限", len(lines) == PV.LOG_MAX_LINES, str(len(lines)))
    check("7.10 留的是最新的", json.loads(lines[-1])["text"] == str(PV.LOG_MAX_LINES + 29))

    section("8. last_user_ts")
    mj = st / "messages.jsonl"
    rows = [{"ts": 100.0, "chat_id": "group:1", "role": "user", "text": "a"},
            {"ts": 200.0, "chat_id": "group:1", "role": "assistant", "text": "b"},
            {"ts": 300.0, "chat_id": "group:2", "role": "user", "text": "c"},
            {"ts": 400.0, "chat_id": "group:1", "role": "user", "text": "d"}]
    mj.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in rows) + "\n",
                  encoding="utf-8")
    check("8.1 找到最后一条用户消息", PV.last_user_ts(mj, "group:1") == 400.0)
    check("8.2 别的会话互不干扰", PV.last_user_ts(mj, "group:2") == 300.0)
    check("8.3 没有记录返回 0", PV.last_user_ts(mj, "group:none") == 0.0)
    check("8.4 文件不存在返回 0", PV.last_user_ts(st / "nope.jsonl", "group:1") == 0.0)
    mj.write_text('{"ts": 1, "chat_id": "group:1", "role": "user"}\n不是 json\n', encoding="utf-8")
    check("8.5 坏行跳过", PV.last_user_ts(mj, "group:1") == 1.0)
    big = st / "big.jsonl"
    rows2 = [{"ts": float(i), "chat_id": "group:1", "role": "user", "text": "x" * 200}
             for i in range(400)]
    rows2.append({"ts": 999.0, "chat_id": "group:9", "role": "user", "text": "y"})
    big.write_text("\n".join(json.dumps(x) for x in rows2) + "\n", encoding="utf-8")
    check("8.6 窗口内取最新", PV.last_user_map(big, tail_bytes=4096).get("group:9") == 999.0)
    # 被挤出窗口的会话查不到（这是有意的：宁可漏判不发，也不猜）
    old_chat = st / "window.jsonl"
    lines = [json.dumps({"ts": 1.0, "chat_id": "group:old", "role": "user"})]
    lines += [json.dumps({"ts": 100.0 + i, "chat_id": "group:busy", "role": "user",
                          "text": "x" * 300}) for i in range(200)]
    old_chat.write_text("\n".join(lines) + "\n", encoding="utf-8")
    win = PV.last_user_map(old_chat, tail_bytes=8192)
    check("8.7 挤出窗口的会话查不到（宁可不发也不猜）", "group:old" not in win, str(list(win)))
    check("8.8 窗口内的照常取到", win.get("group:busy") == 299.0, str(win.get("group:busy")))

    section("9. server 接线：策略拦截判定")
    import server as SV

    class Pol:
        def __init__(self, blocks=False, quiet=False):
            self.d = {"blocks_all": blocks, "quiet": quiet}

        def policy_for(self, chat_id, *, is_group):
            return self.d

    check("9.1 完全不理 → 拦", SV._proactive_blocked(Pol(blocks=True), "group:1") != "")
    check("9.2 静默时段 → 拦", "静默" in SV._proactive_blocked(Pol(quiet=True), "group:1"))
    check("9.3 都放行 → 空串", SV._proactive_blocked(Pol(), "group:1") == "")

    class BoomPol:
        def policy_for(self, chat_id, *, is_group):
            raise RuntimeError("坏了")

    check("9.4 策略读不出来也不发（宁可不发）",
          "失败" in SV._proactive_blocked(BoomPol(), "group:1"))

    section("10. 清洗模型输出")
    check("10.1 去掉引号", SV._clean_proactive('"早上好呀"') == "早上好呀")
    check("10.2 去掉「回复：」前缀", SV._clean_proactive("回复：早呀") == "早呀")
    check("10.3 多行压成一行", SV._clean_proactive("早呀\n\n今天冷") == "早呀 今天冷")
    check("10.4 长度封顶", len(SV._clean_proactive("字" * 500)) == 200)
    check("10.5 空白返回空串", SV._clean_proactive("   ") == "")

    print()
    if FAIL:
        print(f"FAIL {len(FAIL)} / {len(PASS) + len(FAIL)}")
        for f in FAIL:
            print("  ✗", f)
        return 1
    print(f"PASS {len(PASS)} / {len(PASS)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
