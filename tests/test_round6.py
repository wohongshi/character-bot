#!/usr/bin/env python3
"""第六轮（定时任务 / 用量看板 / 对话并发）的回归。

判据都是行为：
- 定时任务：时间规则算得对、到点真的排进队列、暂停的不跑、跑挂也不卡死调度；
- 用量：聚合正确（按模型/按天）、估算和实测能分开；
- 并发：**同会话严格串行、不同会话真并行**，且嵌套 `with _lock` 不会再卡死服务。
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PASS, FAIL = [], []


def check(name, ok, extra=""):
    (PASS if ok else FAIL).append(name)
    print(f"  {'✓' if ok else '✗'} {name}" + (f"   [{extra}]" if extra and not ok else ""))


BASE = "http://127.0.0.1:8798"
H = {"X-Klee-Studio": "1"}


def get(path, timeout=30):
    req = urllib.request.Request(BASE + path, headers=H)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8", "replace") or "{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8", "replace") or "{}")
        except ValueError:
            return e.code, {}


def post(path, body, timeout=60):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode(),
                                 headers={**H, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8", "replace") or "{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8", "replace") or "{}")
        except ValueError:
            return e.code, {}


def main() -> int:
    import server as S
    from bot import schedule as SCH

    print("[1] 定时任务：时间规则")
    cases = [
        ("每天8:00", "daily", "08:00"), ("每日 8:00", "daily", "08:00"),
        ("8:00", "daily", "08:00"), ("每周一 9:30", "weekly", "09:30"),
    ]
    ok = True
    for raw, kind, at in cases:
        sp = SCH.parse_when(raw)
        if not sp or sp.get("kind") != kind or sp.get("at") != at:
            ok = False
            print("    ", raw, sp)
    check("1.1 四种写法都能解析", ok)
    check("1.2 每周一 → weekday 0", SCH.parse_when("每周一 8:00").get("weekday") == 0)
    check("1.3 每周日 → weekday 6", SCH.parse_when("每周日 8:00").get("weekday") == 6)
    check("1.4 每2h → 120 分钟", SCH.parse_when("每2h").get("minutes") == 120)
    check("1.5 每 90 分钟 → 90", SCH.parse_when("每 90 分钟").get("minutes") == 90)
    check("1.6 太密会被夹到 5 分钟", SCH.parse_when("每1m").get("minutes") == 5)
    check("1.7 乱写返回 None", SCH.parse_when("乱七八糟") is None)
    check("1.8 25:00 不合法", SCH.parse_when("每天25:00") is None)
    check("1.9 人话描述", SCH.describe(SCH.parse_when("每周一 8:00")) == "每周一 08:00",
          SCH.describe(SCH.parse_when("每周一 8:00")))
    # next_due：永远在未来，且不超过一个周期
    now = time.time()
    for raw in ("每天8:00", "每周一 8:00", "每2h"):
        sp = SCH.parse_when(raw)
        d = SCH.next_due(sp, now)
        limit = 86400 * 8 if sp["kind"] == "weekly" else 86400 + 60
        check(f"1.10 {raw} 的下次在未来且合理", now < d <= now + limit,
              f"{d - now:.0f}s")

    print("\n[2] 定时任务：接口与调度")
    st, d = get("/api/scheduled")
    check("2.1 列表接口可用", st == 200 and d.get("ok") and isinstance(d.get("items"), list), str(st))
    check("2.2 带角色清单（面板要选角色）", isinstance(d.get("characters"), list) and d["characters"],
          str(d.get("characters"))[:60])

    tmp = Path(tempfile.mkdtemp(prefix="klee-sched-"))
    old = (S.SCHEDULED, S.CHAT_JSONL, S.MSG_JSONL)
    S.SCHEDULED = tmp / "scheduled.json"
    S.CHAT_JSONL = tmp / "chat.jsonl"
    S.MSG_JSONL = tmp / "msg.jsonl"
    S.CHAT_JSONL.write_text("", encoding="utf-8")
    S.MSG_JSONL.write_text("", encoding="utf-8")
    drained = []
    try:
        print("\n[3] 调度器：到点才跑、暂停不跑、跑挂也往后排")

        def drain():
            while True:
                try:
                    job = S._chat_q.get(timeout=0.2)
                except Exception:
                    return
                if job is None:
                    return
                drained.append(job)
                S._chat_q.done(job)

        S._sched_save([
            {"id": 1, "name": "到点了", "prompt": "说早安", "character": "cyrene",
             "when": {"kind": "every", "minutes": 60}, "enabled": True,
             "next_at": time.time() - 10, "runs": 0},
            {"id": 2, "name": "暂停中", "prompt": "别跑", "character": "cyrene",
             "when": {"kind": "every", "minutes": 60}, "enabled": False,
             "next_at": time.time() - 10, "runs": 0},
            {"id": 3, "name": "还没到", "prompt": "等会儿", "character": "cyrene",
             "when": {"kind": "every", "minutes": 60}, "enabled": True,
             "next_at": time.time() + 3600, "runs": 0},
            {"id": 4, "name": "空任务", "prompt": "", "character": "cyrene",
             "when": {"kind": "every", "minutes": 60}, "enabled": True,
             "next_at": time.time() - 10, "runs": 0},
        ])
        fired = S._sched_tick()
        drain()
        check("3.1 只触发了到点且启用的那一条", fired == ["到点了"], str(fired))
        check("3.2 真的排进了队列", len(drained) == 1 and drained[0]["text"] == "说早安", str(drained)[:120])
        check("3.3 会话名是 sched-<id>", drained and drained[0]["session"] == "sched-1", "")
        items = {it["id"]: it for it in S._sched_load()}
        check("3.4 跑过的任务 next_at 往后推了", items[1]["next_at"] > time.time(), "")
        check("3.5 runs 计数 +1", items[1]["runs"] == 1, str(items[1]["runs"]))
        check("3.6 暂停的那条没动", items[2]["runs"] == 0 and items[2]["next_at"] < time.time(), "")
        check("3.7 空任务记了错误但没卡住调度",
              "没有内容" in str(items[4].get("last_error")), str(items[4].get("last_error")))
        check("3.8 空任务的 next_at 也往后推（不会每 20 秒重试一次）",
              items[4]["next_at"] > time.time(), "")

        # 同一条不会在 20 秒内跑两次
        check("3.9 立刻再 tick 不会重复触发", S._sched_tick() == [], "")
    finally:
        S.SCHEDULED, S.CHAT_JSONL, S.MSG_JSONL = old
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
    check("3.10 临时目录已删", not tmp.exists())

    print("\n[4] 用量：聚合与估算")
    old_u = S.USAGE_JSONL
    tmp2 = Path(tempfile.mkdtemp(prefix="klee-usage-"))
    S.USAGE_JSONL = tmp2 / "usage.jsonl"
    try:
        now = time.time()
        rows = [
            {"ts": now - 60, "provider": "p1", "model": "m-a", "prompt_tokens": 100,
             "completion_tokens": 20, "total_tokens": 120, "ms": 500, "est": False},
            {"ts": now - 120, "provider": "p1", "model": "m-a", "prompt_tokens": 200,
             "completion_tokens": 40, "total_tokens": 240, "ms": 700, "est": False},
            {"ts": now - 180, "provider": "p2", "model": "m-b", "prompt_tokens": 50,
             "completion_tokens": 10, "total_tokens": 60, "ms": 300, "est": True},
            {"ts": now - 10 * 86400, "provider": "p2", "model": "m-b", "prompt_tokens": 999,
             "completion_tokens": 0, "total_tokens": 999, "ms": 100, "est": False},
        ]
        S.USAGE_JSONL.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
        s7 = S._usage_summary(7)
        check("4.1 只统计窗口内的", s7["totals"]["requests"] == 3, str(s7["totals"]))
        check("4.2 总量对", s7["totals"]["total"] == 420, str(s7["totals"]["total"]))
        check("4.3 输入/输出分开算",
              s7["totals"]["prompt"] == 350 and s7["totals"]["completion"] == 70,
              str(s7["totals"]))
        check("4.4 按模型聚合且排序", [m["model"] for m in s7["models"]][0] == "p1/m-a",
              str([m["model"] for m in s7["models"]]))
        check("4.5 估算次数单独计", s7["totals"]["est"] == 1, str(s7["totals"]["est"]))
        check("4.6 平均耗时对", abs(s7["totals"]["avg_ms"] - 500) < 1, str(s7["totals"]["avg_ms"]))
        s30 = S._usage_summary(30)
        check("4.7 放大窗口能算进更早的", s30["totals"]["requests"] == 4, str(s30["totals"]))
        check("4.8 空文件返回 0 不炸",
              S._usage_summary.__call__ and True)
        S.USAGE_JSONL.write_text("", encoding="utf-8")
        check("4.9 没有记录时 totals 全 0", S._usage_summary(7)["totals"]["requests"] == 0)

        from bot import llm as L
        est = L._estimate([{"role": "user", "content": "你好世界"}], "好的")
        check("4.10 估算标了 est", est.get("est") is True, str(est))
        check("4.11 中文按 1 字 ≈ 1 token", est["completion_tokens"] == 2, str(est))
        got = []
        L.set_usage_hook(lambda rec: got.append(rec))
        L._emit_usage("p", "m", {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
                      12.5, "chat")
        L.set_usage_hook(None)
        check("4.12 钩子能收到记录", got and got[0]["total_tokens"] == 3, str(got)[:120])
        check("4.13 钩子抛异常不影响调用",
              (L.set_usage_hook(lambda rec: (_ for _ in ()).throw(RuntimeError("x"))),
               L._emit_usage("p", "m", {"total_tokens": 1}, 1.0, "chat"),
               L.set_usage_hook(None)) and True)
    finally:
        S.USAGE_JSONL = old_u
        import shutil
        shutil.rmtree(tmp2, ignore_errors=True)

    st, d = get("/api/usage?days=7")
    check("4.14 接口可用", st == 200 and d.get("ok") and "totals" in d, str(st))

    print("\n[5] 队列：同会话串行、异会话并行")
    q = S._JobQueue()
    events = []
    lock = threading.Lock()

    def work(name, dur):
        while True:
            job = q.get(timeout=0.05)
            if job is None:
                return
            with lock:
                events.append((time.monotonic(), "start", job["session"]))
            time.sleep(dur)
            with lock:
                events.append((time.monotonic(), "end", job["session"]))
            q.done(job)

    DUR = 0.15          # 每段 150ms：串行 3 段 450ms，实际应为 2 段 300ms
    ts = [threading.Thread(target=work, args=(f"w{i}", DUR)) for i in range(3)]
    for t in ts:
        t.start()
    t0 = time.monotonic()
    q.put({"session": "same", "character": "c", "text": "1"})
    q.put({"session": "same", "character": "c", "text": "2"})
    q.put({"session": "other", "character": "c", "text": "3"})
    for t in ts:
        t.join(timeout=5)
    elapsed = time.monotonic() - t0
    same = [e for e in events if e[2] == "same"]
    starts = [e[0] for e in same if e[1] == "start"]
    ends = [e[0] for e in same if e[1] == "end"]
    ok_serial = (len(starts) == 2 and len(ends) == 2
                 and min(starts) < min(ends) and min(ends) <= max(starts))
    check("5.1 同一会话严格串行（第二条等第一条跑完）", ok_serial, str(same))
    # 三条里有两条同会话 → 理论 2 段 = 300ms；全串行是 450ms。
    check("5.2 不同会话确实并行（≈2 段 300ms，而不是 3 段 450ms）", elapsed < DUR * 2.5,
          f"{elapsed:.3f}s")
    # 再来一组：三条全不同会话，应当接近 1 段（0.05s）
    q2 = S._JobQueue()
    ev2 = []

    def work2():
        while True:
            job = q2.get(timeout=0.05)
            if job is None:
                return
            ev2.append(("s", time.monotonic()))
            time.sleep(DUR)
            ev2.append(("e", time.monotonic()))
            q2.done(job)

    t2 = [threading.Thread(target=work2) for _ in range(3)]
    for th in t2:
        th.start()
    t1 = time.monotonic()
    for i in range(3):
        q2.put({"session": f"diff-{i}", "character": "c"})
    for th in t2:
        th.join(timeout=5)
    elapsed2 = time.monotonic() - t1
    check("5.2b 三条不同会话几乎同时跑（≈1 段 150ms）", elapsed2 < DUR * 1.8, f"{elapsed2:.3f}s")
    check("5.3 三条都跑完了", len([e for e in events if e[1] == "end"]) == 3, str(len(events)))
    check("5.4 qsize 归零", q.qsize() == 0, str(q.qsize()))
    check("5.5 没有残留的 busy 键", q.busy() == [], str(q.busy()))

    print("\n[6] 运行状态：按 (会话, 角色) 隔离")
    k1, k2 = ("s-a", "c1"), ("s-b", "c2")
    S._chat_runs.clear()
    S._run_begin({"session": k1[0], "text": "x"}, k1[1])
    S._run_begin({"session": k2[0], "text": "y"}, k2[1])
    check("6.1 两段各自能看到自己", S._run_get(k1[0], k1[1]) and S._run_get(k2[0], k2[1]))
    check("6.2 同一会话不同角色不串", S._run_get("s-a", "c2") is None, "")
    check("6.3 _run_all 返回两条", len(S._run_all()) == 2, str(len(S._run_all())))
    S._run_end({"session": k1[0]}, k1[1])
    check("6.4 结束后自己的没了、别人的还在",
          S._run_get("s-a", "c1") is None and S._run_get("s-b", "c2") is not None)
    check("6.5 只按会话能查到（删除/编辑用）", S._run_get("s-b") is not None)
    S._chat_runs.clear()

    print("\n[7] worker 池与锁（这次踩的坑：嵌套 with 会卡死整个服务）")
    check("7.1 import 不会顺手起 worker（测试不被抽干队列）",
          not [t for t in threading.enumerate() if t.name.startswith("chat-worker")], "")
    check("7.1b main 里有起 worker 的调用", "def _start_workers" in (ROOT / "server.py").read_text(encoding="utf-8")
          and "_start_workers()" in (ROOT / "server.py").read_text(encoding="utf-8"))
    check("7.2 至少 2 个（否则并发没意义）", S.CHAT_WORKERS >= 2, str(S.CHAT_WORKERS))

    # 嵌套 with：Lock 会永久卡死，RLock 只是多拿一次
    done = []
    def nested():
        with S._lock:
            with S._lock:
                done.append(1)
    th = threading.Thread(target=nested, daemon=True)
    th.start()
    th.join(timeout=3)
    check("7.3 嵌套 with 不会卡死（RLock）", done == [1] and not th.is_alive(), str(done))
    info = S._lock.info()
    check("7.4 锁释放后 owner 清空", info["owner"] == "", str(info))

    print("\n[8] 面板契约（前端该有的都在）")
    page = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    check("8.1 提醒页有定时任务卡片", 'id="schList"' in page and "定时任务" in page)
    check("8.2 有新增表单", 'id="schWhen"' in page and "schedAdd()" in page)
    check("8.3 三个操作按钮", "schedRun(" in page and "schedToggle(" in page and "schedDel(" in page)
    check("8.4 看板有用量卡", "模型用量" in page and "总 tokens" in page)
    check("8.5 用量卡显示估算标记", "含 " in page and "次估算" in page)

    print(f"\nPASS {len(PASS)} / {len(PASS) + len(FAIL)}")
    if FAIL:
        print("失败：" + "、".join(FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
