#!/usr/bin/env python3
"""Agent 能力测试：工具循环 / 工具边界 / 模型配置（不联网、不碰生产状态）。"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

STUDIO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(STUDIO))

TMP = Path(tempfile.gettempdir()) / "klee_agent_test"
if TMP.exists():
    shutil.rmtree(TMP)
TMP.mkdir(parents=True)

from bot import agent as A                                    # noqa: E402
from bot.llm import ModelStore                                # noqa: E402
from bot import llm as L                                      # noqa: E402

PASS, FAIL = [], []


def check(name, ok, extra=""):
    (PASS if ok else FAIL).append(name)
    print(f"  {'✓' if ok else '✗'} {name}" + (f"  [{extra}]" if extra and not ok else ""))


def script(*rounds):
    """按顺序返回预设响应；记录每次调用的参数。"""
    rounds = list(rounds)
    calls = []

    def complete(messages, **kw):
        calls.append({"messages": [dict(m) for m in messages], **kw})
        if not rounds:
            raise AssertionError("script 用完了")
        r = rounds.pop(0)
        if isinstance(r, Exception):
            raise r
        return r
    complete.calls = calls
    return complete


def tc(name, args="{}", cid="c1"):
    return [{"id": cid, "name": name, "arguments": args}]


def ctx(**kw):
    # character 必须给：长期记忆按角色分文件，没有角色就等于写不进去
    return A.ToolContext(kb_url=kw.get("kb_url", ""), state_dir=kw.get("state_dir"),
                         scope=kw.get("scope", "test"), timeout=kw.get("timeout", 5),
                         character=kw.get("character", "klee"))


def main():
    print("[1] 工具注册表")
    check("1.1 默认工具齐全", A.resolve_tools(None) == A.DEFAULT_TOOLS, str(A.resolve_tools(None)))
    check("1.2 逗号字符串也认", A.resolve_tools("now, calc ,bad") == ["now", "calc"],
          str(A.resolve_tools("now, calc ,bad")))
    check("1.3 显式配置全非法 → 空（fail-closed，不回退全集）", A.resolve_tools(["nope"]) == [])
    check("1.4 specs 结构合法", all(s["type"] == "function" and s["function"]["name"]
                                  for s in A.specs(["now", "calc"])))
    check("1.5 每个工具都有 handler", all(n in A.TOOLS and callable(A.TOOLS[n]["handler"])
                                     for n in A.ALL_TOOL_NAMES))

    print("[2] 工具循环")
    c = script({"text": "", "tool_calls": tc("now")},
               {"text": "现在是下午三点～", "tool_calls": []})
    res = A.run(None, [{"role": "user", "content": "几点了"}], max_steps=3, complete=c,
                state_dir=TMP)
    check("2.1 调一次工具后给最终回答", res["text"] == "现在是下午三点～" and res["steps"] == 1,
          json.dumps(res, ensure_ascii=False)[:200])
    check("2.2 工具名有记录", res["tool_calls"] == ["now"], str(res["tool_calls"]))
    check("2.3 第二轮带 tool 消息", c.calls[1]["messages"][-1]["role"] == "tool"
          and c.calls[1]["messages"][-1]["tool_call_id"] == "c1")
    check("2.4 第一轮带 tools 定义 + tool_choice=auto",
          bool(c.calls[0]["tools"]) and c.calls[0]["tool_choice"] == "auto")
    check("2.5 assistant 消息带 tool_calls",
          c.calls[1]["messages"][-2]["role"] == "assistant"
          and c.calls[1]["messages"][-2]["tool_calls"][0]["function"]["name"] == "now")

    print("[3] 边界")
    c = script({"text": "", "tool_calls": tc("nope", cid="x1")},
               {"text": "好吧", "tool_calls": []})
    res = A.run(None, [{"role": "user", "content": "试试"}], max_steps=3, complete=c)
    tool_msg = c.calls[1]["messages"][-1]["content"]
    check("3.1 未知工具 → 回灌错误而不是崩", "没有工具" in tool_msg and res["text"] == "好吧", tool_msg[:120])
    c = script({"text": "", "tool_calls": tc("nope", cid="x2")},
               {"text": "还是我", "tool_calls": []})
    c2 = script({"text": "", "tool_calls": tc("calc", args="{坏 json", cid="x3")},
                {"text": "算不出来", "tool_calls": []})
    res2 = A.run(None, [{"role": "user", "content": "1+1"}], max_steps=3, complete=c2)
    check("3.2 坏 JSON 参数 → 工具报错、循环继续", res2["text"] == "算不出来")
    c = script({"text": "", "tool_calls": tc("now")},
               {"text": "", "tool_calls": tc("now", cid="c2")})
    res = A.run(None, [{"role": "user", "content": "一直查"}], max_steps=1, complete=c)
    check("3.3 步数用完 → error 且不假装有答案",
          res["text"] == "" and "步数用完" in res["error"], json.dumps(res, ensure_ascii=False)[:200])
    check("3.4 最后一轮强制 tool_choice=none 且不带 tools",
          c.calls[-1]["tool_choice"] == "none" and not c.calls[-1].get("tools"))
    c = script(RuntimeError("网络炸了"))
    try:
        A.run(None, [{"role": "user", "content": "hi"}], max_steps=2, complete=c)
        ok, err = False, ""
    except A.AgentError as e:
        ok, err = True, str(e)
    check("3.5 模型异常 → AgentError（供 runtime 回退）", ok and "网络炸了" in err, err)

    # 收尾那轮（final_answer）被预算拦下：必须原样冒出去。
    # 吞掉的话上层会当成「agent 没给回复」→ 一次正常限流被记成故障
    from bot.llm import BudgetExceeded as _BE
    c = script({"text": "", "tool_calls": tc("now")}, _BE("今天到上限了"))
    try:
        A.run(None, [{"role": "user", "content": "查时间"}], max_steps=1, complete=c)
        ok5, err5 = False, "没有抛出"
    except Exception as e:                                        # noqa: BLE001
        # run() 会把底层异常包成 AgentError，但必须保留 __cause__ ——
        # runtime 就是靠它把「预算拦下」和真故障分开的
        ok5 = isinstance(e.__cause__, _BE) and "上限" in str(e)
        err5 = f"{type(e).__name__}: {e} | cause={type(e.__cause__).__name__}"
    check("3.5b 收尾轮被预算拦下 → 异常带 __cause__（不被吞）", ok5, err5)

    calls5 = [{"id": f"c{i}", "name": "now", "arguments": "{}"} for i in range(5)]
    c = script({"text": "", "tool_calls": calls5},
               {"text": "收尾", "tool_calls": []})
    res5 = A.run(None, [{"role": "user", "content": "五个"}], max_steps=2, complete=c)
    last_msgs = c.calls[1]["messages"]
    declared = last_msgs[-1 - len([m for m in last_msgs if m["role"] == "tool"])
                        ]["tool_calls"]
    tool_ids = {m["tool_call_id"] for m in last_msgs if m["role"] == "tool"}
    check("3.6 模型一次给 5 个调用 → 只声明/执行 4 个，tool 响应不缺",
          len(declared) == 4 and tool_ids == {x["id"] for x in declared}
          and res5["text"] == "收尾", f"{len(declared)}/{tool_ids}")

    c = script({"text": "<｜｜DSML｜｜ calls><｜｜DSML｜｜ invoke name=\"now\">", "tool_calls": []})
    try:
        A.run(None, [{"role": "user", "content": "x"}], max_steps=1, complete=c)
        ok, err = False, ""
    except A.AgentError as e:
        ok, err = True, str(e)
    check("3.7 正文只有工具标记 → AgentError（交给 runtime 回退）", ok, err)
    c = script({"text": "<｜｜DSML｜｜ calls> 乱码后的一句话", "tool_calls": []})
    res = A.run(None, [{"role": "user", "content": "x"}], max_steps=1, complete=c)
    check("3.8 标记夹着正文 → 清掉标记保留正文",
          "<｜｜" not in res["text"] and "乱码后的一句话" in res["text"], res["text"])

    print("[4] 工具实现")
    check("4.1 calc 优先级", A.tool_calc({"expr": "2+3*4"}, ctx()) == "2+3*4 = 14",
          A.tool_calc({"expr": "2+3*4"}, ctx()))
    check("4.2 calc 拒绝代码", "不支持" in A.tool_calc({"expr": "__import__('os')"}, ctx())
          or "错误" in A.tool_calc({"expr": "__import__('os')"}, ctx()))
    check("4.3 calc 拒绝大指数", "指数太大" in A.tool_calc({"expr": "9**99"}, ctx()))
    check("4.3b 嵌套幂也被拦（9**9**9）", "错误" in A.tool_calc({"expr": "9**9**9"}, ctx()),
          A.tool_calc({"expr": "9**9**9"}, ctx()))
    check("4.3c (9**9)**9 结果超限被拦", "错误" in A.tool_calc({"expr": "(9**9)**9"}, ctx()),
          A.tool_calc({"expr": "(9**9)**9"}, ctx()))
    check("4.3d 正常幂仍可算", A.tool_calc({"expr": "2**10"}, ctx()) == "2**10 = 1024")
    check("4.4 calc 非法语法", "语法" in A.tool_calc({"expr": "1+*2"}, ctx()))
    now_s = A.tool_now({}, ctx())
    check("4.5 now 返回日期", "-" in now_s and ":" in now_s, now_s)
    check("4.6 now 坏时区被拒", "不认识" in A.tool_now({"tz": "Mars/Base"}, ctx()))
    check("4.7 SSRF 回环被拒", "错误" in A.tool_web_fetch({"url": "http://127.0.0.1/x"}, ctx()),
          A.tool_web_fetch({"url": "http://127.0.0.1/x"}, ctx()))
    check("4.8 SSRF 内网被拒", "错误" in A.tool_web_fetch({"url": "http://10.0.0.1/"}, ctx()))
    check("4.9 非 http 被拒", "只支持" in A.tool_web_fetch({"url": "file:///etc/passwd"}, ctx()))
    check("4.10 带账号密码被拒", "账号密码" in A.tool_web_fetch({"url": "http://a:b@example.com/"}, ctx()))
    html_s = "<html><head><style>x{}</style></head><body><p>你好 <b>世界</b></p><script>bad()</script></body></html>"
    clean = A._strip_tags(html_s)
    check("4.11 HTML 去标签/去脚本", "你好" in clean and "世界" in clean and "bad" not in clean and "x{}" not in clean,
          clean)
    check("4.12 Bing 跳转链接解码",
          A._decode_bing_url("https://www.bing.com/ck/a?u=a1aHR0cHM6Ly9leGFtcGxlLmNvbS9h") == "https://example.com/a",
          A._decode_bing_url("https://www.bing.com/ck/a?u=a1aHR0cHM6Ly9leGFtcGxlLmNvbS9h"))

    print("[5] 记事本（隔离目录）")
    st = TMP / "state"
    c1 = ctx(state_dir=st, scope="group:1")
    r1 = A.tool_remember({"text": "主人的生日是 7 月 27 日"}, c1)
    A.tool_remember({"text": "别的群的事"}, ctx(state_dir=st, scope="group:2"))
    check("5.1 写入成功", r1.startswith("记住了"), r1)
    out = A.tool_recall({"query": "生日"}, c1)
    check("5.2 本会话能查到", "7 月 27" in out, out)
    out2 = A.tool_recall({"query": "生日"}, ctx(state_dir=st, scope="group:2"))
    check("5.3 别的会话查不到", "7 月 27" not in out2, out2)
    check("5.4 空 query 返回最近", A.tool_recall({}, c1).startswith("- ["), A.tool_recall({}, c1))
    check("5.5 没目录不硬写", "错误" in A.tool_remember({"text": "x"}, ctx(state_dir=None)))

    print("[6] ModelStore agent 段")
    mp = TMP / "models.json"
    ms = ModelStore(mp)
    check("6.1 默认跟聊天同一个", ms.agent() == ms.active(), json.dumps(ms.agent(), ensure_ascii=False))
    ms.set_agent("", "cn:glm-5.3")
    check("6.2 可单独指定模型", ms.agent()["model"] == "cn:glm-5.3", str(ms.agent()))
    ms.set_agent("", "")
    check("6.3 清空 → 跟聊天", ms.agent()["model"] == ms.active()["model"])
    pid = ms.providers()[0]["id"]
    try:
        ms.set_agent("nope", "x")
        check("6.4 未知 provider 被拒", False)
    except ValueError:
        check("6.4 未知 provider 被拒", True)
    bad = {"id": "../etc", "api": "http://x", "models": ["m"]}
    try:
        ms.upsert_provider(bad)
        check("6.5 provider id 非法被拒", False)
    except ValueError:
        check("6.5 provider id 非法被拒", True)
    try:
        ms.upsert_provider({"id": "ok", "api": "ftp://x", "models": ["m"]})
        check("6.6 api 非 http 被拒", False)
    except ValueError:
        check("6.6 api 非 http 被拒", True)
    ms.upsert_provider({"id": "second", "name": "二号", "api": "https://api.example.com/v1",
                        "key_env": "SECOND_KEY", "models": ["m1", "m2"]})
    check("6.7 新 provider 可加", any(p["id"] == "second" for p in ms.providers()))
    ms.set_active("second", "m1")
    try:
        ms.delete_provider("second")
        check("6.8 正在用的 provider 不能删", False)
    except ValueError as e:
        check("6.8 正在用的 provider 不能删", "正在被" in str(e), str(e))
    first = next(p for p in ms.providers() if p["id"] != "second")
    ms.set_active(first["id"], first["models"][0])
    ms.set_vision("", "")
    ms.set_agent("", "")
    ms.delete_provider("second")
    check("6.9 没用就能删", not any(p["id"] == "second" for p in ms.providers()))

    print("[7] complete_raw 解析")
    class Resp:
        def __init__(self, payload):
            self.payload = payload

        def read(self, n=-1):
            return self.payload

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    orig = L._open_url
    try:
        payload = {"choices": [{"message": {
            "content": "", "reasoning_content": "想一下",
            "tool_calls": [{"id": "call_1", "type": "function",
                            "function": {"name": "now", "arguments": "{}"}}]},
            "finish_reason": "tool_calls"}]}
        L._open_url = lambda *a, **k: Resp(json.dumps(payload).encode())
        got = ms.complete_raw([{"role": "user", "content": "x"}], tools=A.specs(["now"]))
        check("7.1 content 空时回退 reasoning", got["text"] == "想一下", str(got))
        check("7.2 tool_calls 归一化", got["tool_calls"] == [{"id": "call_1", "name": "now",
                                                         "arguments": "{}"}], str(got))
        check("7.3 finish_reason 保留", got["finish_reason"] == "tool_calls")
        payload2 = {"choices": [{"message": {"content": "", "tool_calls": [
            {"id": "c9", "function": {"name": "calc", "arguments": {"expr": "1+1"}}}]}}]}
        L._open_url = lambda *a, **k: Resp(json.dumps(payload2).encode())
        got2 = ms.complete_raw([{"role": "user", "content": "x"}])
        check("7.4 非字符串 arguments 会序列化",
              got2["tool_calls"][0]["arguments"] == '{"expr": "1+1"}', str(got2))
        L._open_url = lambda *a, **k: Resp(json.dumps(
            {"choices": [{"message": {"content": "普通回答"}}]}).encode())
        check("7.5 chat() 仍返回纯文本", ms.chat([{"role": "user", "content": "x"}]) == "普通回答")
    finally:
        L._open_url = orig

    print()
    print(f"PASS {len(PASS)} / {len(PASS) + len(FAIL)}")
    if FAIL:
        print("FAILED:", FAIL)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
