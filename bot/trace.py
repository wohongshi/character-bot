"""消息决策追踪：这条消息为什么没回？

群里最常见的疑问是「她怎么不理我」。真实原因可能有十几种：没 @ 她、静默时段、
会话被设成「完全不理」、被插件钩子拦下、被节流、预算拦下、模型报错、
审核模式在等你点发送……这些分支散在 `Runtime._handle` / `_chat` 里，
每处只写一行 logger —— 想弄清楚得翻 journalctl 一条条拼。

这里只做一件事：**每个分支留一条结构化记录**（内存 ring，重启清空），
面板按时间倒序列出来。看完就知道「最后 50 条全是 not_mentioned = 没人 @ 她」。

设计上刻意不做持久化：这是排障用的，攒一星期反而看不清现在。
"""

from __future__ import annotations

import collections
import time

# stage → (中文, 语气)
#   ok   = 正常处理（回了/执行了命令）
#   held = 有意压住（等确认 / 合并窗口里），不是故障
#   miss = 没处理（策略/没叫到）
#   err  = 出错
STAGES: dict[str, tuple[str, str]] = {
    "replied":      ("已回复", "ok"),
    "command":      ("执行命令", "ok"),
    "rule_reply":   ("规则回了", "ok"),
    "draft":        ("等确认", "held"),
    "coalesce":     ("攒着等合并", "held"),
    "self":         ("自己发的", "miss"),
    "empty":        ("没有文字", "miss"),
    "disabled":     ("会话关了", "miss"),
    "hook":         ("插件拦下", "miss"),
    "rule_ignore":  ("规则：不理", "miss"),
    "not_mentioned": ("没被叫到", "miss"),
    "quiet":        ("静默时段", "miss"),
    "throttle":     ("被节流", "miss"),
    "budget":       ("预算拦下", "miss"),
    "image":        ("图片没看懂", "miss"),
    "error":        ("出错", "err"),
}

MAX_ROWS = 200


class Trace:
    """定长 ring。`note()` 只 append，`rows()` 返回拷贝（面板不能改到实际数据）。"""

    def __init__(self, maxlen: int = MAX_ROWS):
        self._q: collections.deque = collections.deque(maxlen=max(1, int(maxlen)))

    def note(self, chat: str, text: str, stage: str, detail: str = "",
             *, user: str = "", character: str = "", now: float | None = None) -> dict:
        stage = str(stage or "").strip() or "error"
        row = {
            "ts": time.time() if now is None else float(now),
            "chat": str(chat or ""),
            "stage": stage,
            "text": str(text or "").replace("\n", " ")[:120],
            "detail": str(detail or "")[:160],
            "user": str(user or "")[:40],
            "character": str(character or "")[:20],
        }
        self._q.append(row)
        return row

    def rows(self, *, chat: str = "", limit: int = 60, stage: str = "") -> list[dict]:
        """时间倒序（新的在前）。`chat` / `stage` 给了就过滤。"""
        chat = str(chat or "").strip()
        stage = str(stage or "").strip()
        n = max(1, min(int(limit or 60), MAX_ROWS))
        out = []
        for row in reversed(self._q):
            if chat and row["chat"] != chat:
                continue
            if stage and row["stage"] != stage:
                continue
            item = dict(row)
            label, tone = STAGES.get(row["stage"], (row["stage"], "miss"))
            item["label"], item["tone"] = label, tone
            out.append(item)
            if len(out) >= n:
                break
        return out

    def counts(self, *, chat: str = "", limit: int = MAX_ROWS) -> dict:
        """各 stage 的条数（给面板画一行小标签：已回复 12 / 没叫到 30 …）。"""
        chat = str(chat or "").strip()
        seen = 0
        out: dict[str, int] = {}
        for row in reversed(self._q):
            if chat and row["chat"] != chat:
                continue
            out[row["stage"]] = out.get(row["stage"], 0) + 1
            seen += 1
            if seen >= max(1, min(int(limit or MAX_ROWS), MAX_ROWS)):
                break
        return out

    def clear(self) -> int:
        n = len(self._q)
        self._q.clear()
        return n

    def __len__(self) -> int:
        return len(self._q)


def labels() -> dict:
    """面板下拉/图例用：stage → 中文。"""
    return {k: v[0] for k, v in STAGES.items()}
