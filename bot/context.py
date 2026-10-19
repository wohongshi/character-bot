"""对话上下文预算：历史太长时压成一段「前情提要」。

为什么不直接截断
----------------
长会话（几十上百轮）有两个问题：一是塞进模型的钱和延迟都按字数涨，
二是**截断会让她突然失忆** —— 用户刚说过的设定，下一句就忘了。

所以这里做的是「滚动压缩」：
- 最近 `keep` 条原样保留（说话的语气、刚聊到的事都在）；
- 更早的部分交给模型压成 120~260 字的一段提要；
- 提要落盘缓存，**同一条历史只压一次**。下一次只把「上次提要 + 新增的几条」
  再压一遍，成本是常数级，而不是每轮重压全部历史。

缓存指纹用「覆盖到哪一条消息」而不是「全部内容的哈希」：新消息进来时
前面那段的哈希没变，能直接续上，不用从头再压。
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import os
import threading
import time
from pathlib import Path

log = logging.getLogger("klee.bot.context")

MAX_CHARS = 6000        # 历史部分的字符预算（≈ 3000 token，CJK 按 1 字 1 token 估）
KEEP_RECENT = 8         # 最近这么多条永远原样给模型
MIN_BATCH = 6           # 新增的老消息少于这么多条就不值得再调一次模型
MAX_INPUT_CHARS = 8000  # 一次压缩最多读这么多字（再老的就不进了）
MAX_ENTRIES = 200       # 缓存最多留这么多段会话
# 提要本身只要 200 字，但**推理模型**会先花一大截 token 在思考上：
# 给 260 的时候 content 直接是空的，llm 层只好退回 reasoning_content，
# 于是「提要」里存的其实是它的内心独白（「我们需要回答用户请求：…」）。
SUMMARY_MAX_TOKENS = 900
SUMMARY_MAX_CHARS = 420 # 超过这个长度就不像提要了，按失败处理

_LOCK = threading.Lock()

PROMPT = (
    "把下面这段聊天压缩成一段提要，给之后的对话当前情。要求：\n"
    "- 120~220 字，用第三人称，中文；\n"
    "- 只留会影响后续对话的东西：人物称呼、约定、重要事实、情绪与关系、正在做的事；\n"
    "- 不要复述寒暄，不要评价，不要加标题，直接给正文。\n"
)


def _path(state_dir: Path | str) -> Path:
    return Path(state_dir) / "summaries.json"


def size(msgs: list) -> int:
    """一组消息的字符数（content 长度之和）。"""
    return sum(len(str(m.get("content") or "")) for m in msgs if isinstance(m, dict))


def _ident(e: dict, i: int) -> str:
    """一条消息的稳定标识：优先 id，其次 ts+角色，最后用下标。"""
    for k in ("id", "ts"):
        v = e.get(k)
        if v not in (None, ""):
            return f"{k}:{v}"
    return f"i:{i}"


def fingerprint(msgs: list, base: str = "") -> str:
    """覆盖指纹：`base` 是上一条摘要覆盖到的位置，后面接新增消息的标识。"""
    h = hashlib.sha1(usedforsecurity=False)
    h.update((base or "").encode("utf-8"))
    for i, m in enumerate(msgs):
        h.update(f"|{_ident(m, i)}:{len(str(m.get('content') or ''))}".encode("utf-8"))
    return h.hexdigest()[:16]


def plan(entries: list, budget: int = MAX_CHARS, keep: int = KEEP_RECENT) -> dict:
    """按预算切分：`older` 待压缩，`recent` 原样保留。"""
    msgs = [e for e in entries if isinstance(e, dict) and str(e.get("content") or "").strip()]
    total = size(msgs)
    keep = max(2, int(keep or KEEP_RECENT))
    if budget <= 0 or total <= budget:
        return {"older": [], "recent": msgs, "chars": total, "over": False}
    # 至少留 keep 条；如果 keep 条本身已经超预算，就从末尾往前凑到预算的一半为止
    cut = max(0, len(msgs) - keep)
    while cut > 0 and size(msgs[cut:]) > max(budget * 0.5, 400):
        cut += 1
    cut = min(cut, len(msgs))
    return {"older": msgs[:cut], "recent": msgs[cut:], "chars": total, "over": True}


def _trim_for_input(msgs: list) -> list:
    """压缩输入从**最新**的一端往前取，最多 MAX_INPUT_CHARS 字。"""
    out, used = [], 0
    for m in reversed(msgs):
        t = str(m.get("content") or "")
        if used + len(t) > MAX_INPUT_CHARS and out:
            break
        out.append(m)
        used += len(t)
    out.reverse()
    return out


# 「这不是提要，是它的内心独白」的常见开头。推理模型在 token 不够时会漏出来。
_META_RE = re.compile(r"^(我们需要|用户要求|用户希望|作为(?:一个)?[ ]*AI|我需要|首先要|思考过程)")
_WRAP_RE = re.compile(r"^(?:【?前情提要】?|提要|摘要)[ ]*[:：]?[ ]*")


def _clean_summary(text: str) -> str:
    """把模型输出洗成「一段提要」，不像提要就返回空串（宁可不压，也别存垃圾）。"""
    t = str(text or "").strip()
    t = _WRAP_RE.sub("", t).strip()
    if not t or len(t) > SUMMARY_MAX_CHARS or _META_RE.match(t):
        return ""
    return t


def summarize(msgs: list, complete, *, timeout: float = 45.0) -> str:
    """调一次模型做摘要。`complete(messages, max_tokens=, timeout=)` 由调用方注入。"""
    if not msgs:
        return ""
    body = "\n".join(
        ("" if m.get("role") == "system" else
         ("用户：" if m.get("role") == "user" else "她："))
        + str(m.get("content") or "")[:400]
        for m in _trim_for_input(msgs))
    kw = {"max_tokens": SUMMARY_MAX_TOKENS, "timeout": timeout, "temperature": 0.3}
    try:
        # 让它少想点：摘要不需要推理，思考还会把 max_tokens 吃光
        res = complete([{"role": "system", "content": PROMPT},
                        {"role": "user", "content": body}],
                       reasoning_effort="low", **kw)
    except TypeError:                     # 模型层不认这个参数
        res = complete([{"role": "system", "content": PROMPT},
                        {"role": "user", "content": body}], **kw)
    text = res if isinstance(res, str) else str((res or {}).get("text") or "")
    return _clean_summary(text)


# ---------------------------------------------------------------- 缓存
# 面板每 0.5 秒轮询一次 /api/chat，每次都把整份 summaries.json 解析一遍太浪费
# （200 段会话 ≈ 60 KB）。按 mtime+size 缓存解析结果。
_FILE_CACHE: dict = {}
_FILE_LOCK = threading.Lock()


def _read_all(state_dir: Path | str) -> dict:
    p = _path(state_dir)
    try:
        st = p.stat()
    except OSError:
        return {}
    sig = (str(p), st.st_mtime_ns, st.st_size)
    with _FILE_LOCK:
        hit = _FILE_CACHE.get("sig")
        if hit == sig:
            return _FILE_CACHE.get("data") or {}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        d = d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        d = {}
    with _FILE_LOCK:
        _FILE_CACHE["sig"], _FILE_CACHE["data"] = sig, d
    return d


def load(state_dir: Path | str, key: str) -> dict:
    v = _read_all(state_dir).get(key)
    return v if isinstance(v, dict) else {}


def store(state_dir: Path | str, key: str, rec: dict) -> None:
    p = _path(state_dir)
    with _LOCK:
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            d = d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            d = {}
        d[key] = rec
        if len(d) > MAX_ENTRIES:                      # 只留最近的（按写入时间）
            keep = sorted(d.items(), key=lambda kv: float((kv[1] or {}).get("ts") or 0))[-MAX_ENTRIES:]
            d = dict(keep)
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_name(f"{p.name}.{os.getpid()}.{threading.get_ident()}.tmp")
            tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
            os.replace(tmp, p)
        except OSError as e:
            log.info("摘要缓存写入失败（不影响回复）：%s", e)


def build(entries: list, *, state_dir: Path | str, key: str, complete=None,
          budget: int = MAX_CHARS, keep: int = KEEP_RECENT) -> dict:
    """给这一轮准备历史：返回 `{recent, summary, compressed, cache, chars}`。

    `complete` 为 None 时只做「截断」不做摘要（测试/无模型场景）。
    """
    pl = plan(entries, budget, keep)
    msgs = pl["recent"] if not pl["over"] else pl["older"] + pl["recent"]
    if not pl["over"]:
        return {"recent": pl["recent"], "summary": "", "compressed": 0,
                "cache": False, "chars": pl["chars"]}
    older = pl["older"]
    ids = [_ident(m, i) for i, m in enumerate(msgs)]
    rec = load(state_dir, key)
    last = str(rec.get("last") or "")

    # 上次覆盖到哪里了？在 older 里能找到 = 只需要压「之后新增的」
    hi = ids.index(last) if last in ids else -1
    if hi >= 0 and hi < len(older) and (len(older) - hi - 1) < MIN_BATCH and rec.get("text"):
        # 新增的不够一批：沿用旧提要，把这几条原样塞进 recent
        keep_from = hi + 1
        return {"recent": msgs[keep_from:], "summary": str(rec.get("text") or ""),
                "compressed": int(rec.get("n") or 0), "cache": True,
                "chars": pl["chars"]}

    delta = msgs[hi + 1:len(older)] if hi >= 0 else older
    if not delta:
        return {"recent": pl["recent"], "summary": str(rec.get("text") or ""),
                "compressed": int(rec.get("n") or 0), "cache": True, "chars": pl["chars"]}
    if complete is None:
        # 没有模型可用：只能丢老的，别把整轮搞挂
        return {"recent": pl["recent"], "summary": str(rec.get("text") or ""),
                "compressed": 0, "cache": True, "chars": pl["chars"]}

    payload = list(delta)
    if hi >= 0 and rec.get("text"):
        payload = [{"role": "system",
                    "content": "【之前已经压过的提要】\n" + str(rec.get("text"))}] + payload
    try:
        text = summarize(payload, complete)
    except Exception as e:                                     # noqa: BLE001
        log.info("压缩历史失败（这一轮先截断）：%s", e)
        return {"recent": pl["recent"], "summary": str(rec.get("text") or ""),
                "compressed": 0, "cache": True, "chars": pl["chars"]}
    if not text:
        return {"recent": pl["recent"], "summary": str(rec.get("text") or ""),
                "compressed": 0, "cache": True, "chars": pl["chars"]}
    last_id = _ident(older[-1], len(older) - 1)
    n = int(rec.get("n") or 0) + len(delta) if hi >= 0 else len(older)
    store(state_dir, key, {"fp": fingerprint(older), "last": last_id, "text": text,
                           "n": n, "ts": time.time()})
    return {"recent": pl["recent"], "summary": text, "compressed": n,
            "cache": False, "chars": pl["chars"]}


def peek(state_dir: Path | str, key: str) -> dict:
    """面板用：这个会话当前有没有提要（只读，不触发压缩）。"""
    rec = load(state_dir, key)
    if not rec.get("text"):
        return {}
    return {"text": str(rec["text"]), "n": int(rec.get("n") or 0),
            "ts": float(rec.get("ts") or 0)}


def forget(state_dir: Path | str, key: str) -> None:
    p = _path(state_dir)
    with _LOCK:
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if isinstance(d, dict) and key in d:
            d.pop(key, None)
            try:
                p.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
            except OSError:
                pass
