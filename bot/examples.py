"""说话样例：给每个角色放几句「用户说 → 她怎么答」，让新角色的语气立住。

为什么需要
----------
SOUL 写得再好也只是「设定」；模型最容易学的是**例句**。把两三句典型对话附在
人格后面，回答的语气、句长、口头禅会明显靠拢 —— 这比在 SOUL 里加十条形容词管用。

放哪、多大
----------
`state/characters/<cid>/examples.json`（和人格、记忆同级，属于角色内容，不进仓库）。
最多 8 条；每条「用户」≤120 字、「她」≤200 字；注入时整块 ≤1200 字 ——
示例是**每一条消息都要付的输入成本**，不设上限等于每条消息都加价。

几条规则
--------
- 坏文件/坏条目一律跳过，不抛：样例坏了顶多少几句，不该让她不会说话；
- 保存是**整份替换**（面板本来就是编辑完整列表），原子写：写一半崩掉不会留下半份；
- 注入文本只带「照这个语气、别照抄内容」一句，避免模型原样复读示例。
"""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path

log = logging.getLogger("klee.bot.examples")

MAX_ITEMS = 8
MAX_USER = 120
MAX_BOT = 200
MAX_CHARS = 1200          # 注入上限（含标题行）
FILE = "examples.json"

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")


def _clean_id(cid) -> str:
    v = str(cid or "").strip().lower()
    return v if _ID_RE.match(v) else ""


def path_for(state_dir: Path | str, cid: str) -> Path:
    return Path(state_dir) / "characters" / _clean_id(cid) / FILE


def _tidy(items) -> list[dict]:
    """收敛成合法列表：丢掉空/非字符串/超长的条目，最多 MAX_ITEMS 条。"""
    out: list[dict] = []
    if not isinstance(items, (list, tuple)):
        return out
    for it in items:
        if not isinstance(it, dict):
            continue
        u = str(it.get("user") or "").strip()[:MAX_USER]
        b = str(it.get("bot") or "").strip()[:MAX_BOT]
        if not u or not b:
            continue
        out.append({"user": u, "bot": b})
        if len(out) >= MAX_ITEMS:
            break
    return out


def load(state_dir: Path | str, cid: str) -> list[dict]:
    """读样例。文件不在 = 没有；读不懂 = 没有（都只是「少几句话」）。"""
    cid = _clean_id(cid)
    if not cid:
        return []
    p = path_for(state_dir, cid)
    try:
        raw = p.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    except OSError as e:
        log.info("说话样例读取失败（按空处理）：%s", e)
        return []
    try:
        data = json.loads(raw or "{}")
    except ValueError:
        log.info("说话样例不是合法 JSON（按空处理）：%s", p)
        return []
    items = data.get("items") if isinstance(data, dict) else data
    return _tidy(items)


def save(state_dir: Path | str, cid: str, items) -> list[dict]:
    """整份替换。返回落盘后的列表（已被收敛）。"""
    cid = _clean_id(cid)
    if not cid:
        raise ValueError("角色 id 不合法")
    clean = _tidy(items)
    p = path_for(state_dir, cid)
    p.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps({"items": clean}, ensure_ascii=False, indent=2) + "\n"
    tmp = p.with_name(f"{p.name}.{os.getpid()}.tmp")
    tmp.write_text(body, encoding="utf-8")
    os.replace(tmp, p)
    return clean


def add(state_dir: Path | str, cid: str, user: str, bot: str) -> list[dict]:
    """追加一条（满 8 条时抛 ValueError —— 悄悄丢掉一条更让人困惑）。"""
    cur = load(state_dir, cid)
    if len(cur) >= MAX_ITEMS:
        raise ValueError(f"已经有 {MAX_ITEMS} 条了，先删一条")
    u = str(user or "").strip()
    b = str(bot or "").strip()
    if not u or not b:
        raise ValueError("「用户说」和「她答」都要有内容")
    return save(state_dir, cid, cur + [{"user": u[:MAX_USER], "bot": b[:MAX_BOT]}])


def remove(state_dir: Path | str, cid: str, index: int) -> list[dict]:
    """按 1 起的位置删一条。"""
    cur = load(state_dir, cid)
    i = int(index) - 1
    if i < 0 or i >= len(cur):
        raise ValueError(f"没有第 {index} 条（一共 {len(cur)} 条）")
    cur.pop(i)
    return save(state_dir, cid, cur)


def render(state_dir: Path | str, cid: str) -> str:
    """拼成提示词里的一块；没有样例 → ""。"""
    items = load(state_dir, cid)
    if not items:
        return ""
    head = "【说话样例（照这个语气、这个句长说话；不要照抄内容）】"
    lines = [head]
    used = len(head)
    for it in items:
        line = f"用户：{it['user']}\n你：{it['bot']}"
        if used + len(line) > MAX_CHARS:
            break
        lines.append(line)
        used += len(line)
    return "" if len(lines) == 1 else "\n".join(lines)


def summary(state_dir: Path | str, cid: str) -> dict:
    items = load(state_dir, cid)
    return {"ok": True, "character": _clean_id(cid), "items": items,
            "count": len(items), "max": MAX_ITEMS,
            "limits": {"user": MAX_USER, "bot": MAX_BOT, "chars": MAX_CHARS,
                       "items": MAX_ITEMS},
            "chars": len(render(state_dir, cid)),
            "hint": "这几句会附在人格后面（最多 8 条、整块 1200 字）"}
