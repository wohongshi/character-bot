#!/usr/bin/env python3
"""第七轮（模型降级 / 上下文压缩 / 关键词规则）的回归。

判据都是行为，不是「有这个函数」：
- 降级：主模型真的挂了才换，换的时候用量行要记下「之前失败过」；
  已经吐过字的流式**不许**换（换 = 把前半句再念一遍）；
- 压缩：超预算才压、压完缓存、新消息只压增量、脏提要宁可丢掉；
- 规则：四种匹配方式、三种动作、范围过滤、第一条命中为准、改完立刻生效。
"""
from __future__ import annotations

import json
import os
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


def models_file(tmp: Path, active=("good", "m1"), extra_providers=()) -> Path:
    p = tmp / "models.json"
    provs = [
        {"id": "good", "name": "好的", "api": "http://127.0.0.1:9/v1", "key_env": "", "models": ["m1", "m2"]},
        {"id": "good2", "name": "第二家", "api": "http://127.0.0.1:9/v1", "key_env": "", "models": ["x1"]},
    ]
    provs.extend(extra_providers)
    p.write_text(json.dumps({"active": {"provider": active[0], "model": active[1]},
                             "providers": provs}), encoding="utf-8")
    return p


def section(title):
    print(f"\n== {title}")


# ---------------------------------------------------------------- 1. 备用模型
def t_fallback():
    from bot import llm

    section("1. 备用模型")
    tmp = Path(tempfile.mkdtemp())
    ms = llm.ModelStore(models_file(tmp))

    check("1.1 默认没有备用", ms.fallbacks() == [])

    ms.set_fallbacks([{"provider": "good2", "model": "x1"}])
    check("1.2 写入后能读出来", ms.fallbacks() == [{"provider": "good2", "model": "x1"}])

    try:
        ms.set_fallbacks([{"provider": "good", "model": "m1"}])
        check("1.3 拒绝「备用=主模型」", False)
    except ValueError as e:
        check("1.3 拒绝「备用=主模型」", "主模型" in str(e), str(e))

    try:
        ms.set_fallbacks([{"provider": "nope", "model": "z"}])
        check("1.4 拒绝不存在的 provider", False)
    except ValueError:
        check("1.4 拒绝不存在的 provider", True)

    try:
        ms.set_fallbacks([{"provider": "good2", "model": "x1"}] * 5)
        check("1.5 最多 4 个", False)
    except ValueError:
        check("1.5 最多 4 个", True)

    ms.set_fallbacks([{"provider": "good2", "model": "x1"}])
    ms.set_active("good2", "x1")           # 主模型变成原来的备用
    check("1.6 主模型变了以后，重复项被跳过", ms.fallbacks() == [])

    # --- 真的降级：主模型抛错，备用接过 ---
    tmp2 = Path(tempfile.mkdtemp())
    ms2 = llm.ModelStore(models_file(tmp2))
    ms2.set_fallbacks([{"provider": "good2", "model": "x1"}])
    calls = []
    real = ms2._complete_once

    def fake(messages, **kw):
        # provider=None 表示「用 current active」，也就是主模型 good/m1
        prov = kw.get("provider") or "good"
        calls.append((prov, kw.get("model") or "m1"))
        if prov == "good":
            raise RuntimeError("模型接口 HTTP 500: boom")
        # 模拟真实现的用量回调：`complete_raw` 必须把 _fails 透传下来
        llm._emit_usage(prov, kw.get("model") or "", {"prompt_tokens": 10,
                        "completion_tokens": 2, "total_tokens": 12}, 1.0, "chat",
                        int(kw.get("_fails") or 0))
        return {"text": "备用答的", "tool_calls": [], "finish_reason": "stop",
                "provider": prov, "model": kw.get("model") or "",
                "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12}}

    ms2._complete_once = fake
    rows = []
    llm.set_usage_hook(rows.append)
    try:
        r = ms2.complete_raw([{"role": "user", "content": "在吗"}], timeout=5)
    finally:
        llm.set_usage_hook(None)
        ms2._complete_once = real

    check("1.7 主模型挂了会自动换", r.get("text") == "备用答的", str(r))
    check("1.8 结果标明用了备用", r.get("fallback") is True and r.get("model") == "x1", str(r.get("model")))
    check("1.9 失败链记了下来", [f["provider"] for f in r.get("fails") or []] == ["good"])
    check("1.10 用量行带 fails", bool(rows) and rows[-1].get("fails") == 1, str(rows[-1:]))
    check("1.11 只调了两个模型", calls == [("good", "m1"), ("good2", "x1")], str(calls))

    # --- 流式已经吐过字：不许换 ---
    tmp3 = Path(tempfile.mkdtemp())
    ms3 = llm.ModelStore(models_file(tmp3))
    ms3.set_fallbacks([{"provider": "good2", "model": "x1"}])
    tried = []

    def fake_stream(messages, **kw):
        tried.append(kw.get("provider") or "good")
        cb = kw.get("on_delta")
        if cb:
            cb("前半句")
        raise RuntimeError("连接断了")

    ms3._complete_once = fake_stream
    raised = False
    try:
        ms3.complete_raw([{"role": "user", "content": "x"}], stream=True, on_delta=lambda t: None)
    except RuntimeError:
        raised = True
    check("1.12 已经出过字就不换模型", raised and tried == ["good"], str(tried))

    # --- 没有备用时原样抛出 ---
    tmp4 = Path(tempfile.mkdtemp())
    ms4 = llm.ModelStore(models_file(tmp4))
    ms4._complete_once = lambda m, **kw: (_ for _ in ()).throw(RuntimeError("挂了"))
    raised = False
    try:
        ms4.complete_raw([{"role": "user", "content": "x"}], timeout=3)
    except RuntimeError:
        raised = True
    check("1.13 没配就照原样报错", raised)

    # --- 角色专属模型 ---
    tmp5 = Path(tempfile.mkdtemp())
    ms5 = llm.ModelStore(models_file(tmp5))
    check("1.14 没配时跟全局", ms5.for_character("klee") == {**ms5.active(), "own": False})
    ms5.set_char_model("klee", "good2", "x1")
    f = ms5.for_character("klee")
    check("1.15 角色专属生效", f["provider"] == "good2" and f["model"] == "x1" and f["own"])
    check("1.16 别的角色不受影响", ms5.for_character("atri")["own"] is False)
    check("1.17 工具循环也听角色的", ms5.for_agent("klee")["model"] == "x1")
    ms5.set_char_model("klee", "", "")
    check("1.18 传空 = 恢复跟全局", ms5.for_character("klee")["own"] is False)

    ms5.data["characters"] = {"klee": {"provider": "已删除", "model": "x"}}
    check("1.19 坏配置静默回退", ms5.for_character("klee")["own"] is False)


# ---------------------------------------------------------------- 2. 上下文
def t_context():
    from bot import context as ctx

    section("2. 上下文预算与摘要")

    msgs = [{"role": "user" if i % 2 == 0 else "assistant", "content": "字" * 120,
             "ts": 1000 + i} for i in range(50)]
    check("2.1 字符数算得对", ctx.size(msgs) == 6000, str(ctx.size(msgs)))

    pl = ctx.plan(msgs, budget=100000)
    check("2.2 没超预算就不切", pl["over"] is False and len(pl["recent"]) == 50)
    pl = ctx.plan(msgs, budget=2000, keep=8)
    check("2.3 超预算才切", pl["over"] is True and len(pl["recent"]) >= 8)
    check("2.4 最近 8 条一定留着", len(pl["older"]) + len(pl["recent"]) == 50
          and pl["recent"][-1]["ts"] == 1049)
    check("2.5 budget=0 = 关掉", ctx.plan(msgs, budget=0)["over"] is False)

    tmp = Path(tempfile.mkdtemp())
    calls = []

    def fake_complete(messages, **kw):
        calls.append(sum(len(str(m.get("content") or "")) for m in messages))
        return {"text": "他们在聊雾港灯塔和三把钥匙。"}

    r1 = ctx.build(msgs, state_dir=tmp, key="klee:s1", complete=fake_complete,
                   budget=2000, keep=8)
    check("2.6 超预算会压出提要", r1["summary"].startswith("他们在聊"), str(r1)[:80])
    check("2.7 记下压了多少条", r1["compressed"] > 0 and r1["cache"] is False)
    check("2.8 提要落盘", bool(ctx.peek(tmp, "klee:s1").get("text")))
    n_calls = len(calls)

    r2 = ctx.build(msgs, state_dir=tmp, key="klee:s1", complete=fake_complete,
                   budget=2000, keep=8)
    check("2.9 同一条历史只压一次", len(calls) == n_calls and r2["cache"] is True)

    # 新来 2 条：不够一批 → 沿用旧提要，不调模型
    more = msgs + [{"role": "user", "content": "字" * 120, "ts": 2000},
                   {"role": "assistant", "content": "字" * 120, "ts": 2001}]
    r3 = ctx.build(more, state_dir=tmp, key="klee:s1", complete=fake_complete,
                   budget=2000, keep=8)
    check("2.10 新消息不够一批就不重压", len(calls) == n_calls and r3["cache"] is True)
    check("2.11 但新消息照样带上", r3["recent"][-1].get("ts") == 2001)

    # 再来 6 条：够一批 → 只压增量
    more6 = more + [{"role": "user", "content": "字" * 120, "ts": 3000 + i} for i in range(6)]
    r4 = ctx.build(more6, state_dir=tmp, key="klee:s1", complete=fake_complete,
                   budget=2000, keep=8)
    check("2.12 攒够一批会增量重压", len(calls) == n_calls + 1 and r4["cache"] is False)
    check("2.13 增量输入比全量小得多", calls[-1] < max(calls[:1] or [0]) * 3, str(calls))

    # 脏提要：模型把内心独白写出来了
    check("2.14 内心独白不算提要", ctx._clean_summary("我们需要回答用户请求：压缩这段") == "")
    check("2.15 前缀「提要：」会被剥掉", ctx._clean_summary("提要：两人在聊灯塔") == "两人在聊灯塔")
    check("2.16 太长的不要", ctx._clean_summary("字" * 500) == "")

    r5 = ctx.build(msgs, state_dir=tmp, key="klee:bad", complete=lambda m, **kw: {"text": "我们需要先思考"},
                   budget=2000, keep=8)
    check("2.17 摘要失败就当没压（不存脏东西）", r5["summary"] == "" and not ctx.peek(tmp, "klee:bad"))

    # 压缩时模型报错不能把整轮弄挂
    def boom(messages, **kw):
        raise RuntimeError("模型挂了")

    r6 = ctx.build(msgs, state_dir=tmp, key="klee:boom", complete=boom, budget=2000, keep=8)
    check("2.18 摘要报错只退化成截断", r6["summary"] == "" and len(r6["recent"]) >= 8)

    ctx.store(tmp, "k" * 10, {"text": "x", "n": 1, "ts": time.time()})
    for i in range(ctx.MAX_ENTRIES + 20):
        ctx.store(tmp, f"key{i}", {"text": "x", "n": 1, "ts": time.time() + i})
    total = len(json.loads((tmp / "summaries.json").read_text(encoding="utf-8")))
    check("2.19 缓存条目有上限", total <= ctx.MAX_ENTRIES, str(total))

    ctx.forget(tmp, "klee:s1")
    check("2.20 忘掉一段", not ctx.peek(tmp, "klee:s1"))


# ---------------------------------------------------------------- 3. 规则
def t_rules():
    from bot import rules as R

    section("3. 关键词规则")
    tmp = Path(tempfile.mkdtemp())

    r = R.add(tmp, {"name": "早安", "match": "contains", "pattern": "早安",
                    "action": "reply", "reply": "早呀{user}"})
    check("3.1 能加规则并分配 id", r["id"] == "r1" and r["enabled"] is True)
    check("3.2 落盘能读回", len(R.load(tmp)) == 1)
    check("3.3 {user} 占位符", R.render_reply(r, user="小明") == "早呀小明")

    for bad, why in [
        ({"pattern": "", "action": "reply", "reply": "x"}, "空关键词"),
        ({"pattern": "x", "action": "reply", "reply": ""}, "回一句没内容"),
        ({"pattern": "x", "action": "command", "command": ""}, "命令为空"),
        ({"pattern": "(", "match": "regex", "action": "reply", "reply": "x"}, "坏正则"),
        ({"pattern": "x", "action": "nope", "reply": "x"}, "未知动作"),
    ]:
        try:
            R.normalize(bad)
            check(f"3.4 拒绝非法规则（{why}）", False)
        except ValueError:
            check(f"3.4 拒绝非法规则（{why}）", True)

    R.add(tmp, {"name": "广告", "match": "regex", "pattern": r"[广廣]告|加微信",
                "action": "ignore", "scope": "group"})
    R.add(tmp, {"name": "骰子", "match": "equals", "pattern": "掷骰",
                "action": "command", "command": ".骰子 1d6"})
    R.add(tmp, {"name": "私聊问候", "match": "prefix", "pattern": "你好",
                "action": "reply", "reply": "你好呀", "scope": "dm"})
    items = R.load(tmp)
    check("3.5 四条规则都在", len(items) == 4, str([x["id"] for x in items]))

    hit = R.match(items, "大家早安", is_group=True)
    check("3.6 包含匹配", hit and hit["name"] == "早安")
    hit = R.match(items, "掷骰", is_group=True)
    check("3.7 等于匹配 → 当命令", hit and hit["action"] == "command")
    hit = R.match(items, "有人在发广告", is_group=True)
    check("3.8 正则匹配", hit and hit["name"] == "广告")
    check("3.9 群规则在私聊不生效", R.match(items, "发广告", is_group=False) is None)
    hit = R.match(items, "你好呀", is_group=False)
    check("3.10 私聊规则在私聊生效", hit and hit["name"] == "私聊问候")
    check("3.11 群里的「你好」不命中私聊规则", R.match(items, "你好", is_group=True) is None)
    check("3.12 都不命中返回 None", R.match(items, "今天天气不错", is_group=True) is None)

    # 第一条命中为准
    R.add(tmp, {"name": "早安2", "match": "contains", "pattern": "早安", "action": "ignore"})
    items = R.load(tmp)
    check("3.13 按顺序取第一条", R.match(items, "早安", is_group=True)["name"] == "早安")

    # 关掉就不生效
    R.update(tmp, "r1", {"enabled": False})
    items = R.load(tmp)
    check("3.14 暂停后跳过", R.match(items, "早安", is_group=True)["name"] == "早安2")
    R.update(tmp, "r1", {"enabled": True})

    # 指定会话
    R.update(tmp, "r1", {"chat": "group:123"})
    items = R.load(tmp)
    # 限定群号之后，别的群就轮到下一条「早安2」了
    check("3.15 限定群号", R.match(items, "早安", is_group=True, chat_id="group:999")["name"] == "早安2"
          and R.match(items, "早安", is_group=True, chat_id="group:123")["name"] == "早安")
    R.update(tmp, "r1", {"chat": ""})

    # 命中计数（3 秒内不重复落盘）
    before = R.load(tmp)[0]["hits"]
    R.bump(tmp, "r1")
    h1 = R.load(tmp)[0]["hits"]
    R.bump(tmp, "r1")
    h2 = R.load(tmp)[0]["hits"]
    check("3.16 命中会记数", h1 == before + 1)
    check("3.17 3 秒内不重复写盘", h2 == h1)

    check("3.18 删除", R.delete(tmp, "r1") is True and R.delete(tmp, "r1") is False)
    raised = False
    try:
        R.update(tmp, "zz", {"reply": "x"})
    except ValueError:
        raised = True
    check("3.19 更新不存在的规则会报错", raised)

    # 上限
    tmp2 = Path(tempfile.mkdtemp())
    for i in range(R.MAX_RULES):
        R.add(tmp2, {"pattern": f"k{i}", "action": "reply", "reply": "x"})
    try:
        R.add(tmp2, {"pattern": "overflow", "action": "reply", "reply": "x"})
        check("3.20 规则条数有上限", False)
    except ValueError:
        check("3.20 规则条数有上限", True)

    # 坏规则不连累好规则
    p = tmp2 / "rules.json"
    d = json.loads(p.read_text(encoding="utf-8"))
    d["items"].insert(0, {"id": "bad", "pattern": "(", "match": "regex", "action": "reply", "reply": "x"})
    p.write_text(json.dumps(d), encoding="utf-8")
    check("3.21 坏规则被跳过", all(x["id"] != "bad" for x in R.load(tmp2)))


# ---------------------------------------------------------------- 4. 接口与页面
def t_wiring():
    section("4. 接线（接口 / 页面 / 配置）")
    srv = (ROOT / "server.py").read_text(encoding="utf-8")
    page = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    rt = (ROOT / "bot" / "runtime.py").read_text(encoding="utf-8")
    svc = (ROOT / "bot" / "service.py").read_text(encoding="utf-8")

    check("4.1 /api/rules GET", 'u.path == "/api/rules"' in srv)
    check("4.2 /api/rules/test", '"/api/rules/test"' in srv)
    check("4.3 /api/chat 带 context", '"context": ctx_info' in srv)
    check("4.4 清提要接口", "/api/chat/context/forget" in srv)
    check("4.5 /api/models 暴露备用与角色模型",
          '"fallbacks": BOT.models.fallbacks()' in srv and '"characters": BOT.models.characters()' in srv)
    check("4.6 面板能保存备用/角色模型",
          'set_fallbacks(body["fallbacks"])' in srv and 'set_char_model(' in srv)
    check("4.7 面板有备用模型卡", "fbList" in page and "fbAdd()" in page)
    check("4.8 面板有角色专属模型", "cmList" in page and "cmSave(" in page)
    check("4.9 面板有规则卡与试跑", "ruleList" in page and "ruleTest()" in page)
    check("4.10 对话页有前情标", "ctxChip" in page and "showCtx()" in page)
    check("4.11 QQ 侧先命令后规则", rt.index("handle_command(ctx)") < rt.index("self._rule_hit("))
    check("4.12 规则缓存按 mtime", "_RULE_CACHE" in rt)
    check("4.13 配置里有 context_budget", '"context_budget"' in svc)
    check("4.14 面板能改 context_budget", 'k:"context_budget"' in page)

    from bot.service import DEFAULT_CONFIG, coerce_config
    check("4.15 预算是数字型", isinstance(DEFAULT_CONFIG.get("context_budget"), int))
    check("4.16 面板传字符串也能收敛",
          coerce_config({"context_budget": "3000"})["context_budget"] == 3000)

    print(f"\nPASS {len(PASS)} / {len(PASS) + len(FAIL)}")
    if FAIL:
        print("失败：" + "、".join(FAIL))
    return 1 if FAIL else 0


def main():
    t_fallback()
    t_context()
    t_rules()
    return t_wiring()


if __name__ == "__main__":
    sys.exit(main())
