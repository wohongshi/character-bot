#!/usr/bin/env python3
"""会话策略（`bot/chatpolicy.py` + runtime 接线）。

关键点：
1. 跨零点的静默时段要算对（23:30~07:00 是**跨天**的，不是空区间）；
2. 没设过的会话必须完全跟全局走（不能因为加了这个功能就改变现有行为）；
3. 静默时段里：命令照常、聊天闭嘴；`quiet_mention` 决定被 @ 是否例外；
4. 坏数据（时间写错、模式写错、文件坏了）不能让消息完全不处理。
"""
from __future__ import annotations

import datetime
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


def at(hh, mm):
    return datetime.datetime(2026, 10, 20, hh, mm).timestamp()


def main():
    from bot import chatpolicy as CP

    tmp = Path(tempfile.mkdtemp(prefix="klee-chatpol-"))

    section("1. 默认 = 完全跟随全局")
    r = CP.resolve(tmp, "group:1", "mention")
    check("1.1 没设过 → inherit", r["configured"] == "inherit", str(r))
    check("1.2 生效模式 = 全局", r["mode"] == "mention", str(r["mode"]))
    check("1.3 没有静默", r["quiet"] is False)
    for g in ("open", "mention", "disabled"):
        check(f"1.4 全局 {g} 原样透传", CP.resolve(tmp, "group:x", g)["mode"] == g)

    section("2. 单独设模式")
    CP.set_chat(tmp, "group:1", mode="open", note="小群")
    r = CP.resolve(tmp, "group:1", "mention")
    check("2.1 覆盖成 open", r["mode"] == "open" and r["global_mode"] == "mention", str(r))
    check("2.2 别的会话不受影响", CP.resolve(tmp, "group:2", "mention")["mode"] == "mention")
    check("2.3 备注留着", r["note"] == "小群")
    CP.set_chat(tmp, "group:1", mode="inherit")
    check("2.4 改回 inherit", CP.resolve(tmp, "group:1", "disabled")["mode"] == "disabled")

    section("3. 静默时段（含跨零点）")
    CP.set_chat(tmp, "group:9", quiet=["23:30", "07:00"])
    for hh, mm, want in ((23, 29, False), (23, 30, True), (23, 59, True), (0, 0, True),
                         (6, 59, True), (7, 0, False), (12, 0, False)):
        got = CP.resolve(tmp, "group:9", "mention", now=at(hh, mm))["quiet"]
        check(f"3.x {hh:02d}:{mm:02d} → {'静默' if want else '正常'}", got == want, f"got {got}")
    CP.set_chat(tmp, "group:9", quiet=["09:00", "12:00"])
    check("3.6 同日区间：10:00 静默", CP.resolve(tmp, "group:9", "mention", now=at(10, 0))["quiet"])
    check("3.7 同日区间：08:59 正常", not CP.resolve(tmp, "group:9", "mention", now=at(8, 59))["quiet"])
    check("3.8 同日区间：12:00 结束", not CP.resolve(tmp, "group:9", "mention", now=at(12, 0))["quiet"])

    section("4. 非法输入")
    for bad, why in ((["25:00", "07:00"], "小时越界"), (["23:30"], "只有一个时间"),
                     ("23:30", "不是数组"), (["23:30", "23:30"], "起止相同")):
        try:
            CP.set_chat(tmp, "group:8", quiet=bad)
            # 静默非法时不该写进去
            saved = CP.load(tmp)["chats"].get("group:8") or {}
            check(f"4.x 拒绝{why}", "quiet" not in saved, str(saved))
        except ValueError:
            check(f"4.x 拒绝{why}", True)
    try:
        CP.set_chat(tmp, "group:8", mode="loud")
        check("4.5 拒绝非法模式", False)
    except ValueError:
        check("4.5 拒绝非法模式", True)
    (tmp / "chat_policy.json").write_text("{坏掉的", encoding="utf-8")
    check("4.6 坏文件 = 空表（不炸）", CP.load(tmp) == {"chats": {}})
    check("4.7 坏文件时按全局处理", CP.resolve(tmp, "group:9", "open")["mode"] == "open")

    section("5. 清除")
    CP.save(tmp, {"chats": {}})
    CP.set_chat(tmp, "group:5", mode="open", quiet=["01:00", "02:00"], note="x")
    check("5.1 设了三项", bool(CP.load(tmp)["chats"]["group:5"]))
    CP.set_chat(tmp, "group:5", mode="inherit", quiet=[], note="")
    check("5.2 全清后条目消失", CP.load(tmp)["chats"] == {}, str(CP.load(tmp)))

    section("6. 上限")
    for i in range(CP.MAX_CHATS):
        CP.set_chat(tmp, f"group:{i}", mode="mention")
    try:
        CP.set_chat(tmp, "group:over", mode="mention")
        check("6.1 超过上限要报错", False)
    except ValueError:
        check("6.1 超过上限要报错", True)

    section("7. runtime 接线（策略真的作用在分发上）")
    from bot import runtime as RT

    src = (ROOT / "bot" / "runtime.py").read_text(encoding="utf-8")
    check("7.1 有 policy_for", "def policy_for" in src)
    check("7.2 disabled 走会话策略", 'pol["blocks_all"]' in src and "blocks_all" in src)
    check("7.3 @ 门槛走会话策略", 'pol["mode"] == "mention"' in src)
    check("7.4 有静默判断", 'pol["quiet"]' in src and "quiet_mention" in src)
    check("7.5 读坏了退回全局", "会话策略读取失败" in src)
    # 全局 group_policy=disabled 只该关群聊：私聊要「显式设过」才认
    check("7.6 私聊只在显式配置时才认 disabled", "blocks_all" in src)
    from bot import chatpolicy as _cp
    for pol, is_group, want in (
            ({"mode": "disabled", "configured": "inherit"}, True, True),
            ({"mode": "disabled", "configured": "inherit"}, False, False),   # 全局 disabled 不哑私聊
            ({"mode": "disabled", "configured": "disabled"}, False, True),   # 私聊显式设过 → 哑
            ({"mode": "open", "configured": "inherit"}, True, False)):
        check(f"7.6b blocks_all {pol['mode']}/{pol['configured']}/{is_group}",
              _cp.blocks_all(pol, is_group=is_group) is want, "")
    # 静默时不该掷彩蛋骰子（会白烧冷却）
    check("7.7 静默时不掷彩蛋", "quiet_now" in src and "_maybe_chime" in src)

    section("8. 活服务")
    import urllib.error
    import urllib.request
    HDR = {"X-Klee-Studio": "1"}
    BASE = "http://127.0.0.1:8798"

    def post(url, body):
        req = urllib.request.Request(url, headers=HDR, method="POST",
                                     data=json.dumps(body).encode())
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def get(url):
        req = urllib.request.Request(url, headers=HDR)
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read())

    try:
        # 先只设模式：静默窗口留到 8.6 再设（否则「现在」可能正好落在窗口里，
        # 8.4 会被静默拦住 —— 那测的就不是「open 是否生效」了）
        code, d = post(BASE + "/api/control/chat",
                       {"chat_id": "group:998877", "mode": "open",
                        "quiet": [], "quiet_mention": True, "note": "测试"})
        check("8.1 保存 200", code == 200 and d.get("ok"), f"{code} {str(d)[:80]}")
        saved = json.loads((ROOT / "state" / "chat_policy.json").read_text(encoding="utf-8"))
        row = saved["chats"].get("group:998877") or {}
        check("8.2 落盘内容对", row.get("mode") == "open" and not row.get("quiet")
              and row.get("quiet_mention") is True and row.get("note") == "测试", str(row))
        check("8.3 快照里能看到", (d["after"]["chats"].get("group:998877") or {}).get("mode") == "open")
        # 设计成 open 的会话：模拟「群里随便说一句」应交给模型
        _, sim = post(BASE + "/api/simulate", {"text": "随便聊聊", "is_group": True,
                                              "chat_id": "group:998877"})
        check("8.4 open 会话不用 @ 也交给模型", sim.get("action") == "model", str(sim)[:120])
        _, sim2 = post(BASE + "/api/simulate", {"text": "随便聊聊", "is_group": True,
                                                "chat_id": "group:111222"})
        check("8.5 没设的会话仍按全局（不 @ 不理）", sim2.get("action") == "ignore", str(sim2)[:100])
        # 静默：把窗口设成「现在」，@ 也不回（quiet_mention 先关掉）
        now = time.localtime()
        cur = f"{now.tm_hour:02d}:{now.tm_min:02d}"
        end = f"{(now.tm_hour + 2) % 24:02d}:{now.tm_min:02d}"
        post(BASE + "/api/control/chat", {"chat_id": "group:998877", "mode": "open",
                                          "quiet": [cur, end], "quiet_mention": False})
        _, sim3 = post(BASE + "/api/simulate", {"text": "随便聊聊", "is_group": True,
                                                "mentioned": True,
                                                "chat_id": "group:998877"})
        check("8.6 静默时段 @ 也不聊", sim3.get("action") == "ignore"
              and "静默" in str(sim3.get("reason")), str(sim3.get("reason")))
        _, sim4 = post(BASE + "/api/simulate", {"text": ".b64e hi", "is_group": True,
                                                "chat_id": "group:998877"})
        check("8.7 静默时段命令照常", sim4.get("action") == "command", str(sim4)[:100])
        code, bad = post(BASE + "/api/control/chat",
                         {"chat_id": "group:998877", "quiet": ["25:00", "07:00"]})
        check("8.8 非法时间 400", code == 400, str(code))
        # 清掉测试数据（不能留在生产策略表里）
        post(BASE + "/api/control/chat", {"chat_id": "group:998877", "mode": "inherit",
                                          "quiet": [], "quiet_mention": False, "note": ""})
        saved = json.loads((ROOT / "state" / "chat_policy.json").read_text(encoding="utf-8"))
        check("8.9 测试数据已清", "group:998877" not in saved["chats"], str(saved))
    except (urllib.error.URLError, OSError) as e:
        print(f"  · 服务没起来，跳过（{type(e).__name__}）")

    print(f"\nPASS {len(PASS)} / {len(PASS) + len(FAIL)}")
    if FAIL:
        print("失败：" + "、".join(FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
