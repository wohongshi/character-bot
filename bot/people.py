"""群友画像：这个群里谁最活跃、她最常回谁、最近谁不说话了。

数据来源是面板自己的消息流（`messages.jsonl`）。老行里没有 `user_id`/`name`
（那之前没记），统一归到「未记录」——**不要**按文本去猜是谁说的。

判定「她回了谁」用的是时间顺序：一条助手消息，算作**它前面最近一条用户消息**
的那个人的一次回复，前提是两条间隔不超过 `REPLY_WINDOW` 秒。这不需要任何
启发式猜测，也不需要模型。

纯函数：`summarize(rows, ...)` 只吃行、只吐统计，测试直接喂字典。
"""

from __future__ import annotations

import time

REPLY_WINDOW = 180.0      # 超过 3 分钟就不算「回应这句」了
UNKNOWN = "未记录"         # 老行没有说话人字段
MAX_PEOPLE = 200          # 一次最多看这么多人（防脏数据把内存吃满）


def _num(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _blank(name: str, uid: str, chat: str = "") -> dict:
    # `chat` 只在「没有说话人」的行上有意义：面板要能显示成「未记录（群 xxx）」，
    # 否则六个群的人全叫「未记录」，看不出谁是谁
    return {"user_id": uid, "name": name, "chat": chat, "n": 0, "chars": 0,
            "first": 0.0, "last": 0.0, "replied": 0, "max_len": 0}


def summarize(rows: list, *, now: float | None = None, days: int = 0,
              chat_id: str = "", top: int = 20, include_web: bool = False) -> dict:
    """按人统计。`days>0` 时只看最近这么多天；`chat_id` 空 = 全部会话。

    `web:` 是**面板自己的会话**，不是群里的人 —— 默认排除（几百个面板会话
    会把「群友」列表挤满、还全是「未记录」）。要看就把 `include_web=True`。
    """
    now = time.time() if now is None else now
    floor = now - days * 86400 if days and days > 0 else 0.0
    want_chat = str(chat_id or "")
    seq: list = []
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        ts = _num(r.get("ts"))
        if ts <= 0 or ts < floor:
            continue
        cid = str(r.get("chat_id") or "")
        if not cid:                 # 没有会话的消息没法归属，直接跳过
            continue
        if want_chat and cid != want_chat:
            continue
        if not include_web and cid.startswith("web:") and not want_chat:
            continue
        role = str(r.get("role") or "")
        if role not in ("user", "assistant"):
            continue
        seq.append((ts, role, cid, str(r.get("text") or ""),
                    str(r.get("user_id") or ""), str(r.get("name") or "")))
    seq.sort(key=lambda x: x[0])

    people: dict = {}
    replies_total = 0
    last_user = None            # (ts, cid, uid, name)
    for ts, role, cid, text, uid, name in seq:
        if role == "user":
            key = uid or (f"name:{name}" if name else f"anon:{cid}")
            p = people.get(key)
            if p is None:
                if len(people) >= MAX_PEOPLE:
                    # 溢出桶：这些人共用一条统计，但「回了谁」必须记得**桶的 key**，
                    # 否则下面 people.get(ukey) 拿真实 key 找不到，桶的 replied 永远是 0
                    key = "__overflow__"
                    p = people.setdefault(key, _blank("……（更多）", ""))
                else:
                    p = people[key] = _blank(name or uid or UNKNOWN, uid,
                                             cid if not (uid or name) else "")
            p["n"] += 1
            p["chars"] += len(text)
            p["max_len"] = max(p["max_len"], len(text))
            if not p["first"] or ts < p["first"]:
                p["first"] = ts
            if ts > p["last"]:
                p["last"] = ts
            if name and not p["name"]:
                p["name"] = name
            last_user = (ts, cid, key)
            continue
        # 助手消息：算作「回了前面那个人」
        if last_user is None:
            continue
        uts, ucid, ukey = last_user
        if ucid == cid and 0 <= ts - uts <= REPLY_WINDOW:
            p = people.get(ukey)
            if p is not None:
                p["replied"] += 1
                replies_total += 1
        last_user = None        # 一句话只算回一次

    items = []
    for p in people.values():
        avg = round(p["chars"] / p["n"], 1) if p["n"] else 0.0
        items.append({**p, "avg_len": avg,
                      "reply_rate": round(p["replied"] / p["n"], 3) if p["n"] else 0.0,
                      "silent_h": round((now - p["last"]) / 3600, 1) if p["last"] else 0.0})
    items.sort(key=lambda x: (-x["n"], x["name"]))
    total_msgs = sum(x["n"] for x in items)
    return {
        "chat_id": want_chat,
        "days": int(days or 0),
        "people": items[:max(1, int(top or 20))],
        "total": len(items),
        "total_msgs": total_msgs,
        "replies": replies_total,
        "busiest": items[0]["name"] if items else "",
        "window": seq[0][0] if seq else 0.0,
        "ts": now,
    }


def chats(rows: list, *, include_web: bool = False) -> list:
    """有哪些会话（按最近活跃排序），给面板的下拉用。"""
    seen: dict = {}
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        cid = str(r.get("chat_id") or "")
        if not cid or (not include_web and cid.startswith("web:")):
            continue
        ts = _num(r.get("ts"))
        cur = seen.get(cid) or {"id": cid, "last": 0.0, "n": 0}
        # n 只数**用户**消息：面板把它显示成「群 X（N）」，而画像里那个「条」
        # 也是用户消息数 —— 口径不一致会在同一页上出现两个不同的「条」
        if str(r.get("role") or "") == "user":
            cur["n"] += 1
        if ts > cur["last"]:
            cur["last"] = ts
        seen[cid] = cur
    return sorted(seen.values(), key=lambda x: -x["last"])
