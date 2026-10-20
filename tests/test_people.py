#!/usr/bin/env python3
"""群友画像（`bot/people.py`）。

守的是「统计不能骗人」：

1. 「她回了谁」按时间顺序配对（一条助手消息只算回一个人，且要在一个时间窗内）；
2. 会话要分清（A 群的回复不能算到 B 群的人头上）；
3. 没有说话人字段的老行归「未记录」，**不能按文本猜**；
4. 面板自己的 `web:` 会话默认不算「群友」（几百个会话会把列表挤满）。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bot import people as P          # noqa: E402

PASS, FAIL = [], []


def check(name, ok, extra=""):
    (PASS if ok else FAIL).append(name)
    print(f"  {'✓' if ok else '✗'} {name}" + (f"   [{extra}]" if extra and not ok else ""))


def section(t):
    print(f"\n== {t}")


NOW = 1_000_000.0


def u(ts, cid="group:1", text="话", uid="u1", name="小明"):
    return {"ts": NOW - ts, "chat_id": cid, "role": "user", "text": text,
            "user_id": uid, "name": name}


def a(ts, cid="group:1", text="嗯"):
    return {"ts": NOW - ts, "chat_id": cid, "role": "assistant", "text": text}


def main():
    section("1. 基本计数")
    rows = [u(300, text="在吗"), a(290), u(200, text="今天好累啊啊", uid="u2", name="小红"),
            u(100, text="我也累"), a(99)]
    r = P.summarize(rows, now=NOW)
    by = {p["name"]: p for p in r["people"]}
    check("1.1 人数", r["total"] == 2, str(r["total"]))
    check("1.2 条数", by["小明"]["n"] == 2 and by["小红"]["n"] == 1, str(by))
    check("1.3 平均字数", by["小明"]["avg_len"] == 2.5, str(by["小明"]["avg_len"]))
    check("1.4 按条数排序", r["people"][0]["name"] == "小明")
    check("1.5 总条数", r["total_msgs"] == 3)
    check("1.6 busiest", r["busiest"] == "小明")

    section("2. 「她回了谁」")
    check("2.1 两条回复都算小明的", by["小明"]["replied"] == 2, str(by["小明"]["replied"]))
    check("2.2 小红没被回（回复超出时间窗）", by["小红"]["replied"] == 0,
          str(by["小红"]["replied"]))
    check("2.3 回复率", by["小明"]["reply_rate"] == 1.0)
    r2 = P.summarize([u(300, uid="u1"), a(299), a(298)], now=NOW)
    check("2.4 一条用户消息只算回一次", r2["people"][0]["replied"] == 1,
          str(r2["people"][0]["replied"]))
    r3 = P.summarize([u(300, uid="u1"), a(0)], now=NOW)         # 差 300 秒
    check("2.5 超过时间窗不算回复", r3["people"][0]["replied"] == 0)
    r4 = P.summarize([a(300), u(290, uid="u1")], now=NOW)
    check("2.6 助手先说、用户后说 = 不算回复", r4["people"][0]["replied"] == 0)

    section("2b. 全局口径下跨会话交错")
    rows = [u(300, cid="group:1", uid="u1", name="甲"),          # 甲说话
            a(295, cid="group:2"),                              # 别的群先回了
            a(290, cid="group:1")]                              # 甲这条回复不能被吃掉
    r = P.summarize(rows, now=NOW)
    by = {p["name"]: p for p in r["people"]}
    check("2b.1 别的群的助手消息不吃掉本群待配对",
          by["甲"]["replied"] == 1, str(by["甲"]["replied"]))
    rows3 = [u(300, cid="group:1", uid="u1", name="甲"),
             u(295, cid="group:2", uid="u2", name="乙"),
             a(290, cid="group:1")]
    r3 = P.summarize(rows3, now=NOW)
    by3 = {p["name"]: p for p in r3["people"]}
    check("2b.3 别的群的用户消息也不吃掉本群配对",
          by3["甲"]["replied"] == 1, str(by3["甲"]["replied"]))
    r2 = P.summarize([u(300, cid="group:1", uid="u1", name="甲"), a(299, cid="group:1"),
                      a(298, cid="group:1")], now=NOW)
    check("2b.2 同一条只算回一次", r2["people"][0]["replied"] == 1,
          str(r2["people"][0]["replied"]))

    section("3. 会话隔离")
    rows = [u(300, cid="group:1", uid="u1", name="甲"), a(295, cid="group:1"),
            u(200, cid="group:2", uid="u2", name="乙"), a(199, cid="group:1")]
    r = P.summarize(rows, now=NOW, chat_id="group:1")
    check("3.1 只看一个群", r["total"] == 1 and r["people"][0]["name"] == "甲", str(r["total"]))
    check("3.2 别的群的回复不算进来", r["people"][0]["replied"] == 1,
          str(r["people"][0]["replied"]))
    r = P.summarize(rows, now=NOW)
    check("3.3 不指定就全算", r["total"] == 2, str(r["total"]))

    section("4. 老行（没有说话人字段）")
    r = P.summarize([{"ts": NOW - 10, "chat_id": "group:9", "role": "user", "text": "老"}],
                    now=NOW)
    check("4.1 归到「未记录」", r["people"][0]["name"] == "未记录", str(r["people"][0]))
    check("4.2 不按文本瞎猜是谁", r["people"][0]["user_id"] == "")
    rows = [{"ts": NOW - 10, "chat_id": "group:9", "role": "user", "text": "a"},
            {"ts": NOW - 9, "chat_id": "group:9", "role": "user", "text": "b"}]
    r = P.summarize(rows, now=NOW)
    check("4.3 同一个未知会话合并成一个人", r["total"] == 1 and r["people"][0]["n"] == 2,
          str(r["people"][0]))
    # 先只有 id、后面带了真名：名字要能更新过来
    rows = [{"ts": NOW - 20, "chat_id": "g", "role": "user", "text": "a", "user_id": "u9"},
            {"ts": NOW - 10, "chat_id": "g", "role": "user", "text": "b", "user_id": "u9",
             "name": "阿九"}]
    r = P.summarize(rows, now=NOW)
    check("4.4 后来的真名会替换掉 id", r["people"][0]["name"] == "阿九",
          str(r["people"][0]["name"]))

    section("5. web: 会话默认不算群友")
    rows = [u(300, cid="web:s1", uid="u1", name="面板"), u(200, cid="group:1", uid="u2",
                                                          name="群里")]
    check("5.1 默认排除面板会话", P.summarize(rows, now=NOW)["total"] == 1)
    check("5.2 明确要看就带上",
          P.summarize(rows, now=NOW, include_web=True)["total"] == 2)
    check("5.3 指定了面板会话也能看",
          P.summarize(rows, now=NOW, chat_id="web:s1")["total"] == 1)
    check("5.4 chats() 默认也不列面板会话",
          [c["id"] for c in P.chats(rows)] == ["group:1"], str(P.chats(rows)))

    section("6. 时间窗 / 天数")
    rows = [u(10 * 86400, uid="u1", name="老客"), u(60, uid="u2", name="新客")]
    check("6.1 days=7 只算最近的", P.summarize(rows, now=NOW, days=7)["total"] == 1)
    check("6.2 days=0 全算", P.summarize(rows, now=NOW, days=0)["total"] == 2)
    check("6.3 时间窗是最近一条/最早一条", P.summarize(rows, now=NOW)["window"] == NOW - 10 * 86400)

    section("7. 脏数据")
    rows = [None, "x", {"ts": "坏", "chat_id": "g", "role": "user", "text": "t"},
            {"ts": NOW, "chat_id": "", "role": "user", "text": "没会话"},
            {"ts": NOW, "chat_id": "g", "role": "system", "text": "系统"},
            {"ts": NOW, "chat_id": "g", "role": "user", "text": "好"}]
    r = P.summarize(rows, now=NOW)
    check("7.1 坏行跳过、system 不算", r["total_msgs"] == 1, str(r["total_msgs"]))
    check("7.2 空文本也照样计一条", r["people"][0]["n"] == 1)

    section("8. 上限")
    rows = [u(1000 - i, uid=f"u{i}", name=f"人{i}") for i in range(P.MAX_PEOPLE + 20)]
    r = P.summarize(rows, now=NOW)
    check("8.1 不会无限膨胀", r["total"] <= P.MAX_PEOPLE + 1, str(r["total"]))
    check("8.2 top 生效", len(r["people"]) == 20, str(len(r["people"])))
    # 溢出桶也要能算「回了谁」（别人被溢出时，回复不能凭空消失）
    rows = []
    for i in range(P.MAX_PEOPLE + 5):
        rows.append(u(100000 - i * 10, uid=f"u{i}", name=f"人{i}"))
        rows.append(a(100000 - i * 10 - 1))
    r = P.summarize(rows, now=NOW)
    ov = [p for p in r["people"] if p["name"].startswith("……")] or []
    check("8.3 溢出桶的回复也计上了", bool(ov) and ov[0]["replied"] > 0, str(ov)[:120])

    section("9. chats()")
    rows = [u(300, cid="group:1"), u(200, cid="group:2"), u(100, cid="group:1")]
    cs = P.chats(rows)
    check("9.1 按最近活跃排序", [c["id"] for c in cs] == ["group:1", "group:2"], str(cs))
    check("9.2 条数对", cs[0]["n"] == 2 and cs[1]["n"] == 1)

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
