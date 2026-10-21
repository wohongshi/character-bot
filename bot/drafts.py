"""待审草稿：审核模式下的回复先落盘，管理员在面板点「发送」才真的发出去。

为什么要有
----------
她平时是「说完就算」的：模型一开口就直接进群。可有些群不允许这样 ——
几百人的公开群里说错一句话，撤回也来不及（有人已经截图了）。所以给会话
一个「先审后发」的开关：回复照常生成，但停在队列里等你看一眼。

这里只做存储，不管发送（发送在 `BotRuntime.send_draft`，要过限流/分句/引用）。

存放：`state/drafts.json`

    {"items": [{"id": "d1792550855000", "ts": 1792550855.0, "chat": "group:123",
                "character": "atri", "user": "原话", "text": "她的回复",
                "images": ["/root/…/sticker.webp"]}]}

约定
----
- **只留待审的**：发出去/丢掉的立刻删。这是队列不是日志，剩下的每一条都是
  「你还没看过的」。
- 上限 `MAX_ITEMS`：超了从**最旧的**丢（新的更可能是你现在想看的），并把丢掉的
  条数返回给调用方 —— 静默丢消息比丢数据本身更糟。
- 读-改-写整段持锁；写用 tmp + `os.replace`：面板连点两下不会写坏，也不会出现
  两条一模一样的草稿（`pop` 是原子的，谁先拿到谁发）。
- 时间倒序返回（新的在前）。
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

MAX_ITEMS = 100          # 队列上限（条）
MAX_TEXT = 4000          # 单条回复上限（字符）
MAX_USER = 600           # 触发它的那句用户消息，只留开头
MAX_IMAGES = 3           # 一条草稿最多挂几张图
# 引用用的消息 id：QQ 官方（`ROBOT1.0_...`）和飞书（`om_...`）都超过 32 字符，
# 截短会引错消息、或者被平台当非法被动回复直接发失败
MAX_REPLY_TO = 128
FILE = "drafts.json"

_LOCK = threading.RLock()


def path_for(state_dir: Path | str) -> Path:
    return Path(state_dir) / FILE


def _clean_item(raw) -> dict | None:
    """一条草稿的规范化；坏行返回 None（读的时候直接扔掉，不让它继续传播）。"""
    if not isinstance(raw, dict):
        return None
    did = str(raw.get("id") or "").strip()
    chat = str(raw.get("chat") or "").strip()
    text = str(raw.get("text") or "").strip()
    images = [str(p).strip() for p in (raw.get("images") or []) if str(p or "").strip()]
    if not did or not chat:
        return None
    if not text and not images:
        return None                       # 空草稿没有意义
    try:
        ts = float(raw.get("ts") or 0)
    except (TypeError, ValueError):
        ts = 0.0
    if ts <= 0:
        ts = time.time()
    return {"id": did, "ts": ts, "chat": chat,
            "character": str(raw.get("character") or "").strip().lower(),
            "user": str(raw.get("user") or "")[:MAX_USER],
            "text": text[:MAX_TEXT], "images": images[:MAX_IMAGES],
            # 触发它的那条消息：审批可能过几分钟，那时候 `_last_inbound` 早就
            # 换成别人了 —— 不带上的话，引用/@ 都会指错人
            "reply_to": str(raw.get("reply_to") or "")[:MAX_REPLY_TO]}


def _load(state_dir: Path | str) -> list[dict]:
    p = path_for(state_dir)
    try:
        raw = json.loads(p.read_text(encoding="utf-8") or "{}")
    except (OSError, ValueError):
        return []
    rows = raw.get("items") if isinstance(raw, dict) else None
    out, seen = [], set()
    for row in (rows or []):
        item = _clean_item(row)
        if item and item["id"] not in seen:
            seen.add(item["id"])
            out.append(item)
    return out


def _save(state_dir: Path | str, items: list[dict]) -> None:
    """原子写（必须在 `_LOCK` 里调用，见各写入函数的注释）。"""
    p = path_for(state_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps({"items": list(items)}, ensure_ascii=False, indent=1)
    tmp = p.with_name(f"{p.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(body, encoding="utf-8")
    os.replace(tmp, p)


def _new_id(items: list[dict], now: float) -> str:
    """`d<毫秒>`，同一毫秒里再加 `-2`/`-3`（批量入队时会发生）。"""
    base = f"d{int(now * 1000)}"
    used = {i["id"] for i in items}
    if base not in used:
        return base
    n = 2
    while f"{base}-{n}" in used:
        n += 1
    return f"{base}-{n}"


def add(state_dir: Path | str, *, chat: str, character: str = "", user: str = "",
        text: str = "", images=None, reply_to: str = "",
        now: float | None = None) -> dict:
    """入队一条。返回 `{item, count, dropped}`（`dropped` = 因为超上限被挤掉的条数）。"""
    chat = str(chat or "").strip()
    text = str(text or "").strip()
    imgs = [str(p).strip() for p in (images or []) if str(p or "").strip()][:MAX_IMAGES]
    if not chat:
        raise ValueError("缺会话 ID")
    if not text and not imgs:
        raise ValueError("草稿是空的")
    now = time.time() if now is None else float(now)
    with _LOCK:
        items = _load(state_dir)
        item = {"id": _new_id(items, now), "ts": now, "chat": chat,
                "character": str(character or "").strip().lower(),
                "user": str(user or "")[:MAX_USER],
                "text": text[:MAX_TEXT], "images": imgs,
                "reply_to": str(reply_to or "")[:MAX_REPLY_TO]}
        items.append(item)
        dropped = 0
        while len(items) > MAX_ITEMS:
            items.pop(0)
            dropped += 1
        _save(state_dir, items)
        return {"item": item, "count": len(items), "dropped": dropped}


def _order(item: dict) -> tuple:
    """排序键：时间 + 同一毫秒内的**数字**序号。

    按 id 字符串排的话 `…-10` 会排在 `…-2` 前面（`"1" < "2"`），
    同一毫秒里入队的 10 条以上会显示成倒序 —— `_new_id` 正是为这种批量准备的。
    """
    base, _, seq = item["id"].partition("-")
    return (item["ts"], int(seq) if seq.isdigit() else 1)


def list_items(state_dir: Path | str, *, chat: str = "", limit: int = 50) -> list[dict]:
    """时间倒序；`chat` 给了就只看那个会话。"""
    chat = str(chat or "").strip()
    n = max(1, min(int(limit or 50), MAX_ITEMS))
    items = _load(state_dir)
    if chat:
        items = [i for i in items if i["chat"] == chat]
    items.sort(key=_order, reverse=True)
    return items[:n]


def count(state_dir: Path | str, *, chat: str = "") -> int:
    chat = str(chat or "").strip()
    items = _load(state_dir)
    return sum(1 for i in items if not chat or i["chat"] == chat)


def get(state_dir: Path | str, did: str) -> dict | None:
    did = str(did or "").strip()
    for i in _load(state_dir):
        if i["id"] == did:
            return i
    return None


def pop(state_dir: Path | str, did: str) -> dict | None:
    """取出并删除（原子的「占位」操作）。

    发送前必须**先 pop 再发**：面板连点两下、或者 App 和网页同时开着，
    读到同一条各发一次 —— 群里就会看到她说两遍。发失败时调用方负责 `restore`。
    """
    did = str(did or "").strip()
    with _LOCK:
        items = _load(state_dir)
        for i, row in enumerate(items):
            if row["id"] == did:
                items.pop(i)
                _save(state_dir, items)
                return row
        return None


def restore(state_dir: Path | str, item: dict) -> dict | None:
    """发送失败把草稿放回去（放回队尾，时间戳不变）。

    返回 `{item, dropped}`：`dropped` 是因为队列满被挤掉的**别的**草稿条数。
    和 `add()` 一样必须报出来 —— 发一条失败的消息，代价不能是另一条待审的
    草稿悄悄消失。
    """
    clean = _clean_item(item)
    if not clean:
        return None
    with _LOCK:
        items = _load(state_dir)
        if any(i["id"] == clean["id"] for i in items):
            return {"item": clean, "dropped": 0}   # 已经在了，别搞出两份
        items.append(clean)
        dropped = 0
        while len(items) > MAX_ITEMS:
            items.pop(0)
            dropped += 1
        _save(state_dir, items)
        return {"item": clean, "dropped": dropped}


def set_text(state_dir: Path | str, did: str, text: str) -> dict | None:
    """改一句：审核最常见的就是「这个说法不合适，换掉」。改完还留在队列里。"""
    did = str(did or "").strip()
    text = str(text or "").strip()
    if not text:
        raise ValueError("回复不能是空的（不想发就用「丢弃」）")
    with _LOCK:
        items = _load(state_dir)
        for row in items:
            if row["id"] == did:
                row["text"] = text[:MAX_TEXT]
                _save(state_dir, items)
                return row
        return None


def drop(state_dir: Path | str, did: str) -> bool:
    return pop(state_dir, did) is not None


def drop_all(state_dir: Path | str, *, chat: str = "") -> int:
    """清空（`chat` 给了就只清那个会话）。返回删掉的条数。"""
    chat = str(chat or "").strip()
    with _LOCK:
        items = _load(state_dir)
        keep = [i for i in items if chat and i["chat"] != chat]
        removed = len(items) - len(keep)
        if removed:
            _save(state_dir, keep)
        return removed


def summary(state_dir: Path | str) -> dict:
    """面板顶部那行数字：共几条、最早一条多久了。"""
    items = _load(state_dir)
    oldest = min((i["ts"] for i in items), default=0.0)
    return {"count": len(items), "oldest": oldest,
            "chats": len({i["chat"] for i in items})}
