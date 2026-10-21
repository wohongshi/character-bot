"""出站内容闸：她的话在进群之前先过一遍。

解决什么问题
------------
「审核模式」是人工兜底（每条都点一下），只适合一两个重要的群；
几百人的公开群里靠人盯不现实。这一层是**自动的、便宜的**兜底：
模型偶尔会把内部东西说漏嘴 —— 绝对路径、密钥、原始 CQ 码。
这些不是「语气问题」，是事故，所以放在所有出站文字的唯一出口上
（`Runtime.send`：闲聊、命令回复、定时提醒、主动消息都从这里走）。

四个动作
--------
- `off`     ：不检查（默认）
- `log`     ：只记一条，照发（先观察一段时间再加严）
- `mask`    ：命中的片段打码，其余照发
- `replace` ：整条换成 `guard_replace` 那句（默认「这条被拦下了」）

两类规则
--------
- **内置**（`guard_builtin`，默认开）：绝对路径、`sk-` 密钥、`Bearer` 令牌、
  `[CQ:...]` 原始协议码。都是**不该出现在闲聊里**的东西，误报率极低。
  「系统提示词」这类词故意不查 —— 用户问她「什么是提示词」是真会问的。
- **自定义**：`guard_words`（逗号/换行分隔的词）+ `guard_patterns`（一行一条正则）。

正则写错**不能静默跳过**：那等于你以为拦住了、其实一条都没拦。
`compile_rules()` 把错误原样带回，面板直接显示红字。
"""

from __future__ import annotations

import functools
import json
import os
import re
import threading
import time
from pathlib import Path

ACTIONS = ("off", "log", "mask", "replace")
ACTIONS_CN = {"off": "不检查", "log": "只记录", "mask": "打码后发", "replace": "整条换掉"}
MASK = "***"
REPLACE_DEFAULT = "（这句话不能说，换个话题好不好）"

# 注意 `\b` 在这里是陷阱：Python 的 `\w` 包含汉字，`密钥是sk-…` 里
# 「是」和「s」之间**没有**词边界，规则会整条漏掉。所以用「前面不是 ASCII
# 标识符字符」的负向后顾。
BUILTIN = (
    # 常见系统目录；路径段允许非 ASCII（`/home/张三/x.txt`），到空白或中文标点为止
    # 前面不能是 ASCII 的主机名/协议字符：`https://example.com/data/report` 里那段
    # `/data/report` 是 URL 路径，不是本机路径（打成 `https://example.com***`
    # 等于把一条正常链接改坏）。中文紧贴（`在/root/x`）仍然命中 —— 汉字不在
    # 这个字符类里。
    ("本地路径", r"(?<![A-Za-z0-9_:/.])"
                r"/(?:root|home|etc|var|opt|srv|tmp|usr|data|mnt|media)"
                r"(?:/[^\s/，。！？；：、\"'（）【】]+)+"),
    ("疑似密钥", r"(?<![A-Za-z0-9_-])(?:sk|pk|rk)-[A-Za-z0-9._-]{16,}"),
    ("Bearer 令牌", r"(?i)(?<![A-Za-z0-9])bearer\s+[A-Za-z0-9._~+/-]{16,}"),
    ("CQ 码", r"\[CQ:[^\]]{1,80}\]"),
)


def split_words(raw: str) -> list[str]:
    """逗号 / 顿号 / 换行 都当分隔符；去空白、保序去重。

    **不按空格切**：词里可能本来就有空格（`fuck you` 这种英文短语），
    切开就变成两个词，命中范围完全不同。面板的提示也是这么写的。
    """
    parts = re.split(r"[,，、\n\r]+", str(raw or ""))
    out, seen = [], set()
    for p in parts:
        w = p.strip()
        if w and w not in seen:
            seen.add(w)
            out.append(w)
    return out


def compile_rules(words: str = "", patterns: str = "", *,
                  builtin: bool = True) -> tuple[list[tuple[str, re.Pattern]], list[str]]:
    """→ ([(标签, 正则)], [错误...])。词用 `escape`（当字面量），正则原样编译。"""
    rules: list[tuple[str, re.Pattern]] = []
    errors: list[str] = []
    if builtin:
        for label, pat in BUILTIN:
            try:
                rules.append((label, re.compile(pat)))
            except re.error as e:                     # 理论上不会发生，兜底不要抛
                errors.append(f"内置规则 {label}：{e}")
    for w in split_words(words):
        try:
            rules.append((f"词:{w[:20]}", re.compile(re.escape(w), re.IGNORECASE)))
        except re.error as e:
            errors.append(f"词 {w}：{e}")
    for i, line in enumerate(str(patterns or "").split("\n"), 1):
        pat = line.strip()
        if not pat:
            continue
        try:
            rx = re.compile(pat)
            # 能匹配空串的正则等于「每条消息都命中」：`mask` 会在每个字之间
            # 插一个 ***，`log` 会让统计全是假的。必须在编译期拒掉。
            if rx.search(""):
                raise re.error("这个正则能匹配空字符串（会在每个字之间打码）")
            rules.append((f"正则#{i}", rx))
        except re.error as e:
            errors.append(f"第 {i} 行正则：{e}")
    return rules, errors


@functools.lru_cache(maxsize=8)
def _compile_cached(words: str, patterns: str, builtin: bool):
    """按配置缓存编译结果。`send()` 是热路径，每条都重编译正则没必要。

    键是三个字符串：面板改了配置就是新键，旧的自然被 LRU 挤掉。
    """
    return compile_rules(words, patterns, builtin=builtin)


def inspect(text: str, *, words: str = "", patterns: str = "",
            builtin: bool = True) -> dict:
    """只查不改：命中了哪些规则（面板的「试一下」用这个）。"""
    # 不截断：`apply()`（真正生效的那条路）也不截断，两边看的必须是同一段文字，
    # 否则贴一段长日志进来会显示「不会命中」，实际发出去却被打码
    body = str(text or "")
    rules, errors = _compile_cached(words, patterns, bool(builtin))
    labels = [label for label, rx in rules if rx.search(body)]
    return {"ok": True, "hit": bool(labels), "labels": labels, "errors": errors,
            "rules": len(rules)}


def apply(text: str, *, action: str = "mask", replace_text: str = REPLACE_DEFAULT,
          words: str = "", patterns: str = "", builtin: bool = True) -> dict:
    """按动作返回最终要发的文本。

    `action` 不合法时按 `log` 处理（宁可放过，也不能因为配置写错而静默吞消息）。
    """
    body = str(text or "")
    act = str(action or "").strip().lower()
    if act not in ACTIONS:
        act = "log"
    if act == "off" or not body.strip():
        return {"action": act, "text": body, "labels": [], "errors": [],
                "hit": False, "replaced": False}
    rules, errors = _compile_cached(words, patterns, bool(builtin))
    labels, out = [], body
    for label, rx in rules:
        # 判定看**原文**：前一条规则把片段换成 `***` 之后，落在那个片段里的
        # 后续规则就再也匹配不到了 —— 面板「试一下」显示 3 条命中、日志里只
        # 记 1 条，审计时对不上。打码仍然只作用在 `out` 上。
        if not rx.search(body):
            continue
        labels.append(label)
        if act == "mask":
            out = rx.sub(MASK, out)
    replaced = bool(labels) and act == "replace"
    if replaced:
        out = str(replace_text or REPLACE_DEFAULT)[:200]
    return {"action": act, "text": out, "labels": labels, "errors": errors,
            "hit": bool(labels), "replaced": replaced}


def log_row(chat: str, verdict: dict, before: str, *, now: float | None = None) -> dict:
    """写进 `state/guard_log.jsonl` 的一条（面板「最近命中」读它）。"""
    return {"ts": time.time() if now is None else float(now),
            "chat": str(chat or ""),
            "action": verdict.get("action") or "",
            "labels": list(verdict.get("labels") or [])[:6],
            "before": str(before or "")[:200],
            "after": str(verdict.get("text") or "")[:200]}


# ---------- 命中日志（并发安全） ----------
LOG_FILE = "guard_log.jsonl"
LOG_MAX_BYTES = 2 * 1024 * 1024
LOG_KEEP_LINES = 1000

_log_lock = threading.Lock()
_log_n = 0


def append_log(path: Path, row: dict, *, max_bytes: int = LOG_MAX_BYTES,
               keep: int = LOG_KEEP_LINES, every: int = 200) -> bool:
    """追加一条命中记录；顺带按大小轮转。

    `Runtime._guard_log` 是 `asyncio.to_thread` 并发跑的（每个群每条命中一个
    线程），所以计数器和「读全文 → 覆盖」都必须在一把锁里 —— 否则
    A 线程正在写轮转后的内容，B 线程刚 append 的行会跟着旧文件一起消失
    （`kbgaps.append_row` 踩过同一个坑）。轮转用 `os.replace`，不留半截文件。
    """
    global _log_n
    try:
        body = json.dumps(row, ensure_ascii=False) + "\n"
    except (TypeError, ValueError):
        return False
    with _log_lock:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as f:
                f.write(body)
        except OSError:
            return False
        _log_n += 1
        if _log_n % max(1, int(every)):
            return True
        try:
            if path.stat().st_size <= max_bytes:
                return True
            lines = [ln for ln in path.read_text(encoding="utf-8", errors="replace")
                     .split("\n") if ln.strip()][-keep:]
            tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
            tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
            os.replace(tmp, path)
        except OSError:
            return False
    return True
