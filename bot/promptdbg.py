"""提示词调试台：把「这一次真正发给模型的东西」按块摊开。

为什么需要它：模型答得不对时，最难查的不是代码，而是「它到底看到了什么」——
人格被谁覆盖了、记忆是不是空的、知识库那条命中是不是根本没进来、
历史是不是被压缩吃掉了。以前只能靠读源码猜。

设计约束：
- **只存内存**，不落盘（提示词里有聊天记录，没必要长期留着）；留最近
  `MAX_CAPTURES` 条，单个会话留 `PER_CHAT` 条。
- **必须和真实请求同源**：`blocks` 由 `_build_messages` 组装时顺手产出，
  不是拿最终消息再猜一遍。合并后的 `msgs` 与改动前逐字节一致
  （`merge_blocks()` 只做字符串拼接，不加分隔符）。
- 纯标准库、无副作用、可在测试里直接喂假数据。
"""

from __future__ import annotations

import re
import threading
import time

MAX_TEXT = 8000          # 单块最多留这么多字（超了截断并标记）
MAX_CAPTURES = 24        # 全局最多留几条
PER_CHAT = 6             # 单个会话最多留几条

# 块的中文名（面板直接显示；英文 key 只用于程序判断）
LABELS = {
    "persona": "人格（SOUL）",
    "rules": "聊天规则",
    "tools": "工具说明",
    "skills": "技能提示",
    "memory": "长期记忆",
    "mood": "当前心情",
    "stickers": "表情包清单",
    "kb": "知识库命中",
    "summary": "前情提要",
    "hist": "历史对话",
    "who": "群聊身份",
    "scene": "对话场景",
    "user": "本轮输入",
    "system": "系统提示",
    "other": "其它",
}

# 面板侧从最终消息反推块时用：system 里这些标记开头的段落归到哪一类
_MARKS = (
    ("【QQ 聊天规则】", "rules"),
    ("【对话场景】", "scene"),
    ("【工具使用】", "tools"),
    ("【你能用的技能】", "skills"),
    ("【你记得的事】", "memory"),
    ("【你现在的状态】", "mood"),
    ("【表情】", "stickers"),
    ("【相关背景资料", "kb"),
    ("【前情提要", "summary"),
)


def tokens(text: str) -> int:
    """估算 token：中文约 1 字 1 token，其余约 4 字符 1 token（与 llm._estimate 同口径）。"""
    s = str(text or "")
    cjk = sum(1 for ch in s if "\u4e00" <= ch <= "\u9fff")
    n = int(cjk + (len(s) - cjk) / 4.0)
    return max(1, n) if s.strip() else 0     # 非空块别显示成 0（调试器里看着像没算）


_SEG_RE = re.compile(r"(?<!\n)(?=\n\n?【)")   # 别在 "\n\n" 的第二个换行处再切一刀


def block(key: str, role: str, text: str, *, group: str | None = None,
          label: str | None = None) -> dict:
    """一个块。`group` 相同的相邻块在合并时拼成同一条消息（system 分段用）。"""
    t = str(text or "")
    return {
        "key": key,
        "label": label or LABELS.get(key, key),
        "role": role if role in ("system", "user", "assistant") else "system",
        "group": group,
        # `text` 只给调试视图（截断过）；`raw` 是真正要发出去的全文。
        # 只留一份截断版会让「合并结果与实际一致」变成假话 ——
        # 长文粘贴 / 超大 SOUL 会被静默砍掉再发出去。
        "text": t[:MAX_TEXT],
        "raw": t,
        "chars": len(t),
        "tokens": tokens(t),
        "cut": len(t) > MAX_TEXT,
    }


def merge_blocks(blocks: list) -> list:
    """按 `group` 把相邻块拼回消息列表（拼接用空串，保证与手写版本逐字节一致）。

    用 `raw`（全文）而不是 `text`（调试视图的截断版）—— 否则真实请求会被砍。
    """
    out: list = []
    for b in blocks or []:
        if not isinstance(b, dict):
            continue
        full = b.get("raw")
        if full is None:
            full = b.get("text")
        if not str(full or ""):
            continue
        g = b.get("group")
        # 同组**且同 role** 才合并：只比 group 的话，后面万一塞进一条 user 块
        # 会被并进 system 里（看起来正常，实际把人说的话变成了设定）
        if out and g and out[-1].get("group") == g and out[-1].get("role") == b.get("role"):
            out[-1]["content"] += str(full)
            out[-1]["keys"].append(b.get("key"))
            continue
        out.append({"role": b.get("role") or "system",
                    "content": str(full),
                    "group": g, "keys": [b.get("key")]})
    return [{"role": m["role"], "content": m["content"]} for m in out]


def snapshot(blocks: list, *, chat_id: str, character: str, source: str = "qq",
             user_text: str = "", ts: float | None = None) -> dict:
    """一次组装的快照。`blocks` 是细粒度块，`msgs` 是合并后的真实请求体。"""
    bs = [b for b in (blocks or []) if isinstance(b, dict) and str(b.get("text") or "")]
    msgs = merge_blocks(bs)
    # 快照里不留 raw：内存只存展示用的截断版（真实请求已经发完了）
    bs = [{k: v for k, v in b.items() if k != "raw"} for b in bs]
    chars = sum(int(b.get("chars") or 0) for b in bs)
    return {
        "ts": float(ts if ts is not None else time.time()),
        "chat_id": str(chat_id or ""),
        "character": str(character or ""),
        "source": str(source or "qq"),
        "user_text": str(user_text or "")[:400],
        "blocks": bs,
        "msgs": msgs,
        "chars": chars,
        "tokens": sum(int(b.get("tokens") or 0) for b in bs),
        "messages": len(msgs),
    }


class Store:
    """最近若干次组装的内存环形缓冲（线程安全：事件循环与面板线程都会碰）。"""

    def __init__(self, max_captures: int = MAX_CAPTURES, per_chat: int = PER_CHAT):
        self.max_captures = max(1, int(max_captures))
        self.per_chat = max(1, int(per_chat))
        self._lock = threading.Lock()
        self._items: list = []          # 旧 → 新

    def add(self, snap: dict) -> dict:
        if not isinstance(snap, dict):
            return {}
        with self._lock:
            self._items.append(snap)
            # 单会话封顶：**先在集合里标出要丢的，再一次性重建**。
            # 边算下标边 del 会让后面每个下标都前移一位（删掉的是「新的」，留下的反而是旧的）；
            # 多会话交错时甚至会删到别的会话的快照。
            same = lambda x: (x.get("chat_id") == snap.get("chat_id")
                              and x.get("character") == snap.get("character"))
            mine = [i for i, x in enumerate(self._items) if same(x)]
            drop = set(mine[:-self.per_chat])
            kept = [x for i, x in enumerate(self._items) if i not in drop]
            self._items = kept[-self.max_captures:]
        return snap

    def latest(self, chat_id: str | None = None, character: str | None = None) -> dict:
        with self._lock:
            for x in reversed(self._items):
                if chat_id and x.get("chat_id") != chat_id:
                    continue
                if character and x.get("character") != character:
                    continue
                return x
        return {}

    def recent(self, limit: int = 24) -> list:
        """摘要列表（不含块正文，面板列表用）。"""
        n = max(1, min(int(limit or 24), self.max_captures))
        with self._lock:
            items = list(self._items)[-n:]
        return [{
            "ts": x.get("ts"), "chat_id": x.get("chat_id"),
            "character": x.get("character"), "source": x.get("source"),
            "chars": x.get("chars"), "tokens": x.get("tokens"),
            "messages": x.get("messages"), "user_text": x.get("user_text"),
            "blocks": len(x.get("blocks") or []),
        } for x in reversed(items)]

    def clear(self) -> int:
        with self._lock:
            n = len(self._items)
            self._items = []
        return n

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)


def split_system(text: str) -> list:
    """把一条 system 消息按段落标记切成若干段（面板侧反推用）。

    只在「换行 + 【…】」处切；切点处不丢字符，`"".join(段文本) == 原文`。
    """
    s = str(text or "")
    if not s:
        return []
    segs = _SEG_RE.split(s)          # 段首带着分隔换行
    out: list = []
    for i, seg in enumerate(segs):
        if not seg:
            continue
        key = "persona" if i == 0 else "system"
        probe = seg.lstrip("\n")
        for mark, k in _MARKS:
            if probe.startswith(mark):
                key = k
                break
        out.append(block(key, "system", seg))
    return out


def split_msgs(msgs: list) -> list:
    """从最终消息反推块（拿不到组装过程时用；块边界比真实组装粗，但不会错位）。"""
    out: list = []
    seq = [m for m in (msgs or []) if isinstance(m, dict)]
    for i, m in enumerate(seq):
        role = str(m.get("role") or "system")
        text = str(m.get("content") or "")
        if not text:
            continue
        if role == "system":
            out.extend(split_system(text))
            continue
        last = i == len(seq) - 1
        out.append(block("user" if (role == "user" and last) else "hist", role, text))
    return out


def diff(old: dict, new: dict) -> list:
    """两次组装的块级差异（试跑对比用）：只看字符数变化。"""
    def _idx(blocks):
        # 按「key + 第几次出现」配对，而不是按全局下标：插/删一个块（比如关掉知识库）
        # 不会让后面每一块都移位，diff 才不会满屏假变化
        seen: dict = {}
        out: dict = {}
        for b in blocks or []:
            if not isinstance(b, dict):
                continue
            k = str(b.get("key") or "")
            seen[k] = seen.get(k, 0) + 1
            out[f"{k}#{seen[k]}"] = b
        return out

    a = _idx((old or {}).get("blocks"))
    b = _idx((new or {}).get("blocks"))
    rows = []
    for k in list(a) + [k for k in b if k not in a]:
        x, y = a.get(k), b.get(k)
        ca = int((x or {}).get("chars") or 0)
        cb = int((y or {}).get("chars") or 0)
        if x is None:
            rows.append({"key": (y or {}).get("key"), "label": (y or {}).get("label"),
                         "delta": cb, "kind": "added"})
        elif y is None:
            rows.append({"key": x.get("key"), "label": x.get("label"),
                         "delta": -ca, "kind": "removed"})
        elif ca != cb:
            rows.append({"key": x.get("key"), "label": x.get("label"),
                         "delta": cb - ca, "kind": "changed"})
    return rows
