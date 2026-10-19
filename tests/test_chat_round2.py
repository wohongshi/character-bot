#!/usr/bin/env python3
"""两轮功能增强的回归：流式回复 / 引用回复 / 会话置顶 / 一键备份。

判断标准：
- 流式解析吃的是**真的 SSE 格式**（含 tool_calls 增量、坏行、[DONE]），
  并且服务端不支持流式时要能自己退回非流式；
- 引用必须落到数据里、并且真的拼进了模型看到的那一轮文本；
- 置顶要改顺序（置顶 > 主对话 > 其余最近），旧格式 sessions.json 要能读；
- 备份是**能打开的 zip**，带 manifest，且**不含**密钥 / sqlite / 日志。
"""
from __future__ import annotations

import io
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PASS, FAIL = [], []


def check(name, ok, extra=""):
    (PASS if ok else FAIL).append(name)
    print(f"  {'✓' if ok else '✗'} {name}" + (f"   [{extra}]" if extra and not ok else ""))


BASE = "http://127.0.0.1:8798"
H = {"X-Klee-Studio": "1"}


def get_raw(path, headers=None):
    req = urllib.request.Request(BASE + path, headers=H if headers is None else headers)
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            return r.status, r.headers, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers, e.read()


def get(path):
    st, _h, body = get_raw(path)
    try:
        return st, json.loads(body.decode("utf-8", "replace") or "{}")
    except ValueError:
        return st, {}


def post(path, body, headers=None):
    req = urllib.request.Request(
        BASE + path, data=json.dumps(body).encode(),
        headers={**(H if headers is None else headers), "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8", "replace") or "{}")
        except ValueError:
            return e.code, {}


# ---------------------------------------------------------------- 假 SSE
def chunk(d):
    return ("data: " + json.dumps(d, ensure_ascii=False) + "\n").encode()


class FakeSSE:
    def __init__(self, chunks):
        self.chunks = chunks

    def __iter__(self):
        for c in self.chunks:
            yield c


class FakeResp:
    """冒充 urllib 的响应：既能迭代（流式），也能 read()（非流式）。"""

    def __init__(self, body: bytes):
        self._body = body
        self._sent = False

    def __iter__(self):
        for line in self._body.splitlines(True):
            yield line

    def read(self, *_a):
        if self._sent:
            return b""
        self._sent = True
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def main() -> int:
    import server as S
    from bot import agent as A
    from bot import llm as L

    _st, ch = get("/api/characters")
    char = str(ch.get("active") or "klee")
    sess = f"t-r2-{int(time.time())}"

    print("[1] SSE 解析：真的按 OpenAI 流式格式吃")
    got = []
    r = L._read_sse(FakeSSE([
        chunk({"choices": [{"delta": {"content": "你"}}]}),
        chunk({"choices": [{"delta": {"content": "好呀"}}]}),
        b"data: [DONE]\n",
    ]), on_delta=lambda t: got.append(t))
    check("1.1 纯正文拼起来", r and r["text"] == "你 好呀".replace(" ", ""), str(r))
    check("1.2 每段都回调了", got == ["你", "你好呀"], str(got))
    check("1.3 finish_reason 取到", r.get("finish_reason") == "" or isinstance(r.get("finish_reason"), str))

    r = L._read_sse(FakeSSE([
        chunk({"choices": [{"delta": {"reasoning_content": "先想想"}}]}),
        chunk({"choices": [{"delta": {"content": "答案"}, "finish_reason": "stop"}]}),
        b"data: [DONE]\n",
    ]))
    check("1.4 只有推理时用推理当正文", r["text"] == "答案", str(r))
    r = L._read_sse(FakeSSE([
        chunk({"choices": [{"delta": {"reasoning_content": "只有推理"}}]}),
        b"data: [DONE]\n",
    ]))
    check("1.5 纯推理回复不丢", r["text"] == "只有推理", str(r))

    r = L._read_sse(FakeSSE([
        chunk({"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c1",
                                                     "function": {"name": "now", "arguments": "{\"a\""}}]}}]}),
        chunk({"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": ":1}"}}]}}]}),
        b"data: [DONE]\n",
    ]))
    check("1.6 工具调用增量拼成完整 arguments",
          r["tool_calls"] == [{"id": "c1", "name": "now", "arguments": '{"a":1}'}], str(r))

    r = L._read_sse(FakeSSE([b"data: not-json\n", chunk({"choices": [{"delta": {"content": "ok"}}]}),
                             b"data: [DONE]\n"]))
    check("1.7 坏行跳过不影响后续", r and r["text"] == "ok", str(r))
    check("1.8 完全不是 SSE → 交回 None（调用方退回非流式）",
          L._read_sse(FakeSSE([b'{"choices":[]}\n'])) is None)

    print("\n[2] complete_raw：流式 / 非流式 / 服务端不支持流式")
    store = L.ModelStore(str(ROOT / "state" / "models.json"))
    real_open = L._open_url
    calls = {"n": 0}

    def fake_open(req, timeout):
        calls["n"] += 1
        if calls["n"] == 1:      # 第一次：真 SSE
            body = b"".join([
                chunk({"choices": [{"delta": {"content": "流式"}}]}),
                chunk({"choices": [{"delta": {"content": "正常"}, "finish_reason": "stop"}]}),
                b"data: [DONE]\n",
            ])
        else:                    # 第二次：服务端不认 stream，直接吐整段 JSON
            body = json.dumps({"choices": [{"message": {"content": "整段回来"},
                                            "finish_reason": "stop"}]}, ensure_ascii=False).encode()
        return FakeResp(body)

    try:
        L._open_url = fake_open
        seen = []
        r = store.complete_raw([{"role": "user", "content": "hi"}], stream=True,
                               on_delta=lambda t: seen.append(t))
        check("2.1 流式走通", r["text"] == "流式正常", str(r))
        check("2.2 回调拿到了增量", seen and seen[-1] == "流式正常", str(seen))
        r2 = store.complete_raw([{"role": "user", "content": "hi"}], stream=True)
        check("2.3 服务端不支持流式时自动退回非流式", r2["text"] == "整段回来", str(r2))
    finally:
        L._open_url = real_open

    print("\n[3] agent 循环：on_delta 一路传到模型层")
    fired: list[str] = []

    def complete(messages, **kw):
        cb = kw.get("on_delta")
        if cb:
            cb("半句")            # 模拟模型层的增量回调
            cb("半句话说完")
        return {"text": "半句话说完", "tool_calls": [], "finish_reason": "stop"}

    res = A.run(None, [{"role": "user", "content": "说点什么"}], max_steps=2,
                complete=complete, on_delta=lambda t: fired.append(t))
    check("3.1 模型层的增量一路冒到调用方", fired == ["半句", "半句话说完"], str(fired))
    check("3.1b 最终文本仍然是完整的", res["text"] == "半句话说完", res["text"])
    seen_kw = {}

    def spy(messages, **kw):
        seen_kw.update(kw)
        return {"text": "好", "tool_calls": [], "finish_reason": "stop"}

    A.run(None, [{"role": "user", "content": "hi"}], max_steps=1, complete=spy,
          on_delta=lambda t: None)
    check("3.2 stream/on_delta 真的传下去了",
          seen_kw.get("stream") is True and callable(seen_kw.get("on_delta")),
          str({k: v for k, v in seen_kw.items() if k != "on_delta"}))
    seen_kw.clear()
    A.run(None, [{"role": "user", "content": "hi"}], max_steps=1, complete=spy)
    check("3.3 不传 on_delta 时不带 stream（老路径不变）", "stream" not in seen_kw, str(seen_kw.keys()))

    print("\n[4] 半成品文本的投递（_delta_sink）")
    S._chat_state["running"] = {"session": sess, "text": "x", "started": time.time(), "partial": ""}
    sink = S._delta_sink({"session": sess})
    sink("第一段")
    check("4.1 写进 running.partial", S._chat_state["running"]["partial"] == "第一段",
          str(S._chat_state["running"]))
    sink("同一毫秒内的更新")            # 节流：80ms 内不重复写
    other = S._delta_sink({"session": "别的会话"})
    other("不该写进去")
    check("4.2 别的会话收不到", S._chat_state["running"]["partial"] == "第一段",
          str(S._chat_state["running"]))
    time.sleep(0.1)
    sink("很久之后")
    check("4.3 过了节流窗口会更新", S._chat_state["running"]["partial"] == "很久之后",
          str(S._chat_state["running"]))
    S._chat_state["running"] = None
    sink("收尾后不再写")
    check("4.4 没有在跑的任务时什么也不做", S._chat_state["running"] is None)

    print("\n[5] 引用：落到数据里，并拼进模型看到的那一轮")
    check("5.1 有引用时前缀带谁说了什么",
          S._apply_quote("这句什么意思", {"who": "亚托莉", "text": "时间不够用"})
          == "（引用 亚托莉：「时间不够用」）\n这句什么意思")
    check("5.2 没引用时原样返回", S._apply_quote("hi", None) == "hi")
    check("5.3 空引用当没有", S._apply_quote("hi", {"who": "x", "text": "  "}) == "hi")
    check("5.4 超长引用被截到 300",
          len(S._apply_quote("hi", {"who": "x", "text": "字" * 900})) < 340)

    st, r = post("/api/chat", {"session": sess, "text": "这句什么意思",
                               "quote": {"who": char, "text": "被引用的原话"},
                               "character": char})
    check("5.5 发一条带引用的消息 → 200", st == 200, str(r)[:120])
    rows = [e for e in _rows(sess, char)]
    hit = [e for e in rows if e.get("quote")]
    check("5.6 引用写进了用户那条消息", bool(hit) and hit[0]["quote"]["text"] == "被引用的原话",
          str(hit[:1])[:200])
    check("5.7 引用里只留 who/text 两个字段", set(hit[0]["quote"]) == {"who", "text"},
          str(hit[0]["quote"]))
    post("/api/chat", {"session": sess, "text": "没有引用", "quote": {"who": "x", "text": ""},
                       "character": char})
    rows = _rows(sess, char)
    check("5.8 空引用不写进数据", all("quote" not in e for e in rows if e.get("text") == "没有引用"))
    _wait_idle(sess, char)

    print("\n[6] 会话：置顶 / 排序 / 旧格式兼容")
    sess2 = sess + "-b"
    post("/api/chat", {"session": sess2, "text": "第二个会话", "character": char})
    _wait_idle(sess2, char)
    st, d = get(f"/api/chat/sessions?character={char}")
    check("6.1 列表里有 pins 字段", "pins" in d, str(list(d))[:120])
    check("6.2 默认主对话在最前", (d.get("sessions") or [""])[0] == "studio-main",
          str((d.get("sessions") or [])[:3]))
    st, r = post("/api/chat/session/pin", {"session": sess2, "pinned": True})
    check("6.3 置顶成功", st == 200 and sess2 in (r.get("pins") or []), str(r))
    st, d = get(f"/api/chat/sessions?character={char}")
    check("6.4 置顶会话排第一", (d.get("sessions") or [""])[0] == sess2,
          str((d.get("sessions") or [])[:3]))
    check("6.5 pins 回传", sess2 in (d.get("pins") or []), str(d.get("pins")))
    post("/api/chat/session/pin", {"session": sess2, "pinned": False})
    st, d = get(f"/api/chat/sessions?character={char}")
    check("6.6 取消置顶后回到原顺序", (d.get("sessions") or [""])[0] == "studio-main",
          str((d.get("sessions") or [])[:3]))
    st, r = post("/api/chat/session/pin", {"session": "", "pinned": True})
    check("6.7 缺 session → 400", st == 400, str(r)[:100])

    meta_file = ROOT / "state" / "sessions.json"
    saved = meta_file.read_text(encoding="utf-8") if meta_file.is_file() else None
    try:
        meta_file.write_text(json.dumps({"legacy-key": "老名字"}, ensure_ascii=False), encoding="utf-8")
        check("6.8 旧格式（平铺 dict）仍能读成 labels",
              S._session_meta()["labels"] == {"legacy-key": "老名字"}, str(S._session_meta()))
        meta_file.write_text(json.dumps({"labels": {"a": "甲"}, "pins": ["a"]}, ensure_ascii=False),
                             encoding="utf-8")
        m = S._session_meta()
        check("6.9 新格式读 labels + pins", m["labels"] == {"a": "甲"} and m["pins"] == ["a"], str(m))
        check("6.10 排序：置顶 > 主对话 > 其余",
              S._session_order(["studio-main", "a", "b"], {"labels": {}, "pins": ["a"]})
              == ["a", "studio-main", "b"],
              str(S._session_order(["studio-main", "a", "b"], {"labels": {}, "pins": ["a"]})))
    finally:
        if saved is None:
            meta_file.unlink(missing_ok=True)
        else:
            meta_file.write_text(saved, encoding="utf-8")

    print("\n[7] 一键备份")
    st, hdrs, blob = get_raw("/api/backup")
    check("7.1 200 + zip", st == 200 and blob[:2] == b"PK", f"{st} {blob[:4]!r}")
    check("7.2 是附件下载", "attachment" in str(hdrs.get("Content-Disposition", "")),
          str(hdrs.get("Content-Disposition")))
    z = zipfile.ZipFile(io.BytesIO(blob))
    names = z.namelist()
    man = json.loads(z.read("manifest.json"))
    check("7.3 有 manifest 且格式号是 1", man.get("app") == "klee-studio" and man.get("format") == 1,
          str(man)[:120])
    check("7.4 聊天记录在里面", "state/chat.jsonl" in names)
    check("7.5 角色 SOUL 在里面", any(n.startswith("state/characters/") and n.endswith("SOUL.md")
                                      for n in names), str(names[:6]))
    check("7.6 六个角色都记进 manifest", len(man.get("characters") or []) >= 6,
          str(man.get("characters")))
    check("7.7 chat_rows 大于 0", (man.get("chat_rows") or 0) > 0, str(man.get("chat_rows")))
    bad = [n for n in names if "token" in n.lower() or n.endswith((".db", ".db-wal", ".db-shm", ".log"))
           or ".env" in n]
    check("7.8 不含密钥 / sqlite / 日志", not bad, str(bad[:5]))
    check("7.9 体积合理（< 4MB）", len(blob) < 4 * 1024 * 1024, f"{len(blob) / 1024:.0f}KB")
    st, _h, body = get_raw("/api/backup", headers={})
    check("7.10 没有面板头 → 403", st in (401, 403), str(st))

    print("\n[8] 清理")
    for s in (sess, sess2):
        post("/api/chat/delete", {"session": s, "character": char})
    left = get(f"/api/chat/sessions?character={char}")[1].get("sessions") or []
    check("8.1 测试会话已删干净", sess not in left and sess2 not in left, str(left[:4]))

    print(f"\nPASS {len(PASS)} / {len(PASS) + len(FAIL)}")
    if FAIL:
        print("失败：" + "、".join(FAIL))
    return 1 if FAIL else 0


def _rows(sess: str, char: str) -> list[dict]:
    return list(get(f"/api/chat?session={urllib.parse.quote(sess)}&character={char}")[1]
                .get("messages") or [])


def _wait_idle(sess: str, char: str, timeout: float = 90.0) -> None:
    end = time.time() + timeout
    while time.time() < end:
        d = get(f"/api/chat?session={urllib.parse.quote(sess)}&character={char}")[1]
        if not d.get("running"):
            return
        time.sleep(1.5)


if __name__ == "__main__":
    sys.exit(main())
