#!/usr/bin/env python3
"""用量预算（`bot/budget.py` + llm 的闸 + server 的接线）。

守的是「说好了到上限就停，就不能被别的路径绕过去」：

1. `decide()` 是纯函数，四种动作（ok/near/warn/cheap/stop）各自成立；
2. 小时线比日线先破时要按小时算（不然一小时内烧完一天的量也不拦）；
3. `stop` 必须让 `complete_raw` 真的抛错，而且**不能被降级链吞掉**
   （否则配了备用模型 = 预算形同虚设）；
4. `cheap` 只换掉这一次的主模型，不改用户配置；
5. `Meter` 跨天/跨小时自动归零，面板的「今天」不能一直是昨天那个数。
"""
from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bot import budget as B          # noqa: E402
from bot import llm as L             # noqa: E402

PASS, FAIL = [], []


def check(name, ok, extra=""):
    (PASS if ok else FAIL).append(name)
    print(f"  {'✓' if ok else '✗'} {name}" + (f"   [{extra}]" if extra and not ok else ""))


def section(t):
    print(f"\n== {t}")


class FakeResp:
    def __init__(self, payload):
        self._b = payload.encode("utf-8")

    def read(self, *a):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def main():
    # 取整点会被「这一小时」的窗口卡到（15:00:00 减 60 秒 = 上一小时），用半点
    now = time.mktime((2026, 10, 20, 15, 30, 0, 0, 0, -1))

    section("1. 默认值 / 参数夹取")
    d = B.DEFAULTS
    check("1.1 默认全关", d["on"] is False)
    check("1.2 默认不限量", d["daily_tokens"] == 0 and d["hourly_tokens"] == 0)
    check("1.3 默认只提醒", d["action"] == "warn")
    c = B.clamp({"daily_tokens": -5, "hourly_tokens": "abc", "action": "nuke",
                 "warn_at": 99})
    check("1.4 负数归 0", c["daily_tokens"] == 0)
    check("1.5 坏值用默认", c["hourly_tokens"] == 0)
    check("1.6 不认识的动作退回 warn", c["action"] == "warn", c["action"])
    check("1.7 warn_at 封顶 1.0", c["warn_at"] == 1.0)

    section("2. decide：没超就放行")
    cfg = {"on": True, "daily_tokens": 1000, "hourly_tokens": 400, "action": "stop"}
    r = B.decide(cfg, used_day=10, used_hour=10)
    check("2.1 正常放行", r["allow"] and r["action"] == "ok", str(r))
    check("2.2 带上百分比", r["pct_day"] == 0.01 and r["pct_hour"] == 0.025, str(r))
    r = B.decide({**cfg, "on": False}, used_day=99999, used_hour=99999)
    check("2.3 没启用就不管", r["allow"] and r["action"] == "ok")
    r = B.decide({"on": True, "daily_tokens": 0, "hourly_tokens": 0},
                 used_day=9_999_999, used_hour=9_999_999)
    check("2.4 上限 0 = 不限", r["allow"] and r["action"] == "ok")

    section("3. 到 80% 先提醒")
    r = B.decide(cfg, used_day=800, used_hour=100)
    check("3.1 到 80% 标 near", r["action"] == "near" and r["allow"], str(r["action"]))
    check("3.2 说明里有数字", "800" in r["reason"], r["reason"])
    r = B.decide(cfg, used_day=100, used_hour=330)
    check("3.3 小时线到 80% 也提醒", r["action"] == "near" and "这一小时" in r["reason"],
          r["reason"])

    section("4. 超限的三种动作")
    r = B.decide({**cfg, "action": "warn"}, used_day=1000, used_hour=0)
    check("4.1 warn 仍然放行", r["allow"] and r["action"] == "warn", str(r))
    r = B.decide({**cfg, "action": "stop"}, used_day=1000, used_hour=0)
    check("4.2 stop 拦下", r["allow"] is False and r["action"] == "stop")
    check("4.3 stop 的原因是中文人话", "上限" in r["reason"] and "token" in r["reason"],
          r["reason"])
    r = B.decide({**cfg, "action": "cheap", "cheap_provider": "p1",
                  "cheap_model": "flash"}, used_day=1000, used_hour=0)
    check("4.4 cheap 放行且标 cheap", r["allow"] and r["action"] == "cheap")
    r = B.decide({**cfg, "action": "cheap", "cheap_model": ""},
                 used_day=1000, used_hour=0)
    check("4.5 cheap 但没配模型 → 退化成 warn", r["action"] == "warn", r["action"])
    r = B.decide({**cfg, "action": "cheap", "cheap_model": "flash"},
                 used_day=1000, used_hour=0)
    check("4.6 cheap 缺 provider 也退化成 warn（llm 那边会跳过降级）",
          r["action"] == "warn", r["action"])

    section("5. 小时线优先")
    r = B.decide(cfg, used_day=100, used_hour=400)
    check("5.1 小时超了按小时算", r["limit_hit"] == "hour", str(r.get("limit_hit")))
    check("5.2 原因说的是这一小时", "这一小时" in r["reason"], r["reason"])
    r = B.decide(cfg, used_day=1000, used_hour=400)
    check("5.3 两条都超也按小时", r["limit_hit"] == "hour")

    section("6. 配置读写")
    tmp = Path(tempfile.mkdtemp(prefix="klee-budget-"))
    check("6.1 没文件时是默认值", B.load(tmp) == B.DEFAULTS, str(B.load(tmp)))
    B.save(tmp, {"on": True, "daily_tokens": 5000, "action": "cheap",
                 "cheap_model": "flash"})
    got = B.load(tmp)
    check("6.2 存得住", got["on"] and got["daily_tokens"] == 5000)
    check("6.3 按字段合并", B.set_field(tmp, action="stop")["action"] == "stop")
    got = B.load(tmp)
    check("6.4 合并不覆盖别的字段", got["daily_tokens"] == 5000 and got["cheap_model"] == "flash",
          str(got))
    (tmp / "budget.json").write_text("{坏", encoding="utf-8")
    check("6.5 坏文件退回默认", B.load(tmp)["on"] is False)

    section("7. Meter 归零")
    lt = time.localtime(now)
    m = B.Meter(now)
    m.add(100, now)
    m.add(50, now)
    check("7.1 累加", m.snapshot(now)["used_day"] == 150)
    check("7.2 小时计数也在", m.snapshot(now)["used_hour"] == 150)
    later = now + 3600                    # 下一个小时
    check("7.3 跨小时归零", m.snapshot(later)["used_hour"] == 0)
    check("7.4 日计数不清", m.snapshot(later)["used_day"] == 150)
    tomorrow = now + 86400
    check("7.5 跨天归零", m.snapshot(tomorrow)["used_day"] == 0)
    m.seed(700, 300, now)
    check("7.6 seed 设初值", m.snapshot(now) == {"used_day": 700, "used_hour": 300})
    m.add(-5, now)
    check("7.7 负数不加", m.snapshot(now)["used_day"] == 700)

    section("8. rollup（从用量行汇总）")
    rows = [{"ts": now, "total_tokens": 100}, {"ts": now - 60, "total_tokens": 50},
            {"ts": now - 86400 * 2, "total_tokens": 999}, {"ts": now, "total_tokens": 0},
            {"ts": "坏", "total_tokens": 5}, "不是字典"]
    r = B.rollup(rows, now=now)
    check("8.1 今天算两条", r["used_day"] == 150, str(r))
    check("8.2 这一小时两条都在", r["used_hour"] == 150)
    check("8.3 前天的没算进今天", r["used_day"] < 999)
    check("8.4 只数有 token 的调用", r["calls_day"] == 2, str(r["calls_day"]))

    section("9. llm 的闸")
    class M(L.ModelStore):
        def __init__(self, path):
            super().__init__(path)

    mf = Path(tempfile.mkdtemp(prefix="klee-budget-llm-")) / "models.json"
    mf.write_text('{"active":{"provider":"p1","model":"m1"},'
                  '"providers":[{"id":"p1","name":"P","api":"http://127.0.0.1:1/v1",'
                  '"key_env":"K","models":["m1","cheap"]}]}', encoding="utf-8")
    st = M(mf)
    calls = []

    def fake_open(req, timeout=0):
        calls.append(req.full_url)
        return FakeResp('{"choices":[{"message":{"content":"ok"},"finish_reason":"stop"}],'
                        '"usage":{"prompt_tokens":10,"completion_tokens":2,'
                        '"total_tokens":12}}')

    old_open, old_hook = L._open_url, L._BUDGET_HOOK
    L.set_usage_hook(lambda rec: None)
    try:
        L._open_url = fake_open
        L.set_budget_hook(lambda: {"allow": True, "action": "ok"})
        out = st.chat([{"role": "user", "content": "hi"}])
        check("9.1 正常时能出话", out == "ok", out)

        def blocked():
            return {"allow": False, "action": "stop", "reason": "今天到上限了"}

        L.set_budget_hook(blocked)
        try:
            st.chat([{"role": "user", "content": "hi"}])
            ok, err = False, "没报错"
        except Exception as e:                                    # noqa: BLE001
            ok, err = type(e).__name__ == "BudgetExceeded", f"{type(e).__name__}: {e}"
        check("9.2 stop 直接抛 BudgetExceeded", ok, err)

        # 配了备用模型也不能绕过：预算检查在降级链之前
        st.set_fallbacks([{"provider": "p1", "model": "cheap"}])
        n0 = len(calls)
        try:
            st.chat([{"role": "user", "content": "hi"}])
            ok, err = False, "没报错"
        except Exception as e:                                    # noqa: BLE001
            ok, err = type(e).__name__ == "BudgetExceeded", type(e).__name__
        check("9.3 有备用模型也绕不过去", ok, err)
        check("9.4 被拦时一次请求都没发出去", len(calls) == n0, str(len(calls) - n0))

        L.set_budget_hook(lambda: {"allow": True, "action": "cheap",
                                   "provider": "p1", "model": "cheap"})
        seen = []
        L._open_url = lambda req, timeout=0: (seen.append(req.data) or fake_open(req))
        st.chat([{"role": "user", "content": "hi"}])
        check("9.5 cheap 换掉了这次请求的模型",
              b'"model": "cheap"' in (seen[0] if seen else b""), str(seen[:1])[:120])
        check("9.6 只改这一次，不改配置", st.active()["model"] == "m1", str(st.active()))

        # 便宜模型挂了：不能退回去用全价模型重试
        st.set_agent("", "")
        L.set_budget_hook(lambda: {"allow": True, "action": "cheap",
                                   "provider": "p1", "model": "nope"})

        def picky(req, timeout=0):
            calls.append(req.full_url)
            if b'"model": "nope"' in (req.data or b""):
                raise L.urllib.error.HTTPError(req.full_url, 400, "bad model", {}, None)
            return FakeResp('{"choices":[{"message":{"content":"ok"}}],'
                            '"usage":{"prompt_tokens":1,"completion_tokens":1,'
                            '"total_tokens":2}}')

        L._open_url = picky
        n1 = len(calls)
        try:
            st.chat([{"role": "user", "content": "hi"}])
        except Exception:                                         # noqa: BLE001
            pass
        check("9.8 便宜模型失败也不退回降级链（全价模型不会被偷偷用上）",
              len(calls) - n1 == 1, str(len(calls) - n1))
        L._open_url = fake_open

        # 便宜供应商被删/改名：不能降级（否则 allow_fallback=False 会让每次调用都失败）
        L.set_budget_hook(lambda: {"allow": True, "action": "cheap",
                                   "provider": "ghost", "model": "cheap"})
        seen_g = []
        L._open_url = lambda req, timeout=0: (seen_g.append(req.data) or fake_open(req))
        out_g = st.chat([{"role": "user", "content": "hi"}])
        check("9.8b 便宜供应商不存在就按主模型继续（不能直接失败）", out_g == "ok", out_g)
        check("9.8c 用的还是主模型",
              b'"model": "m1"' in (seen_g[0] if seen_g else b""), str(seen_g[:1])[:120])

        # 带 tools / 图片的请求不降级（便宜文本模型会 4xx，再按全价重试更亏）
        L.set_budget_hook(lambda: {"allow": True, "action": "cheap",
                                   "provider": "p1", "model": "cheap"})
        seen2 = []
        L._open_url = lambda req, timeout=0: (seen2.append(req.data) or fake_open(req))
        st.complete_raw([{"role": "user", "content": "hi"}], tools=[{"type": "function"}],
                        allow_fallback=False)
        check("9.9 带工具的请求不降级",
              b'"model": "m1"' in (seen2[0] if seen2 else b""), str(seen2[:1])[:120])
        seen3 = []
        L._open_url = lambda req, timeout=0: (seen3.append(req.data) or fake_open(req))
        st.complete_raw([{"role": "user", "content": [
            {"type": "text", "text": "看图"}, {"type": "image_url",
                                               "image_url": {"url": "data:x"}}]}],
                        allow_fallback=False)
        check("9.10 带图片的请求不降级",
              b'"model": "m1"' in (seen3[0] if seen3 else b""), str(seen3[:1])[:120])

        def boom():
            raise RuntimeError("钩子坏了")

        L.set_budget_hook(boom)
        try:
            out = st.chat([{"role": "user", "content": "hi"}])
            ok = out == "ok"
        except Exception as e:                                    # noqa: BLE001
            ok = False
        check("9.7 钩子自己坏了不能把聊天弄死", ok)
    finally:
        L._open_url, L._BUDGET_HOOK = old_open, old_hook
        L.set_usage_hook(None)

    section("10. server 接线")
    import server as SV
    old_meter, old_cfg = SV._BUDGET_METER, SV.BUDGET
    try:
        SV._BUDGET_METER = B.Meter(now)
        SV._BUDGET_METER.seed(0, 0, now)
        SV.BUDGET = B.clamp({"on": True, "daily_tokens": 100, "action": "stop"})
        v = SV._budget_view()
        check("10.1 视图带用量", v["used_day"] == 0 and v["pct_day"] == 0.0, str(v)[:120])
        SV._BUDGET_METER.add(60, time.time())
        hook = SV._budget_hook()
        check("10.2 没超时 hooks 放行", hook["allow"] is True, str(hook))
        SV._BUDGET_METER.add(100, time.time())
        hook = SV._budget_hook()
        check("10.3 超了 hooks 拦下", hook["allow"] is False and "上限" in hook["reason"],
              str(hook))
        SV.BUDGET = B.clamp({"on": True, "daily_tokens": 100, "action": "cheap",
                             "cheap_provider": "workbuddy", "cheap_model": "flash"})
        hook = SV._budget_hook()
        check("10.4 cheap 时带出便宜模型",
              hook["action"] == "cheap" and hook["model"] == "flash", str(hook))
    finally:
        SV._BUDGET_METER, SV.BUDGET = old_meter, old_cfg

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
