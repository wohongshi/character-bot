"""知识库缺口：她答不上来的问题。

从哪来
------
`_kb_context` 每检索一次就往 `state/kb_hits.jsonl` 写一行（带命中块和分数）。
**没命中也要写**（`hits: []`）—— 那些空行就是「她脑子里没有这块」的直接证据。
以前只在有命中时才记，于是「她不懂什么」永远查不到。

怎么用
------
面板列出出现次数最多、最近又出现过的那些问题，两种处理：

- **补一句**：把「问 → 答」写进她的知识库（走 klee-kb `/add`，来源记「缺口补录」）；
- **忽略**：这问题是闲聊/不该答的（比如「在吗」），踢出清单但保留记录。

两种决定都存在 `state/kb_gaps.json`（按规范化后的 key 记，改一个字算新问题）：

    {"ignored": ["在吗"], "added": ["你喜欢吃什么"]}

为什么按 key 而不是按原文：她会被问「你还记得我吗」「你还记得我吗？」——
标点不同是同一个问题。`norm()` 去掉空白与标点，只留字。
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path

MIN_LEN = 4                  # 太短的都是「在吗」「嗯」，进清单只会淹没真问题
MAX_ITEMS = 500              # 忽略/已补录名单上限（超了从最旧的丢）
MAX_KEY = 60
FILE = "kb_gaps.json"
HITS = "kb_hits.jsonl"
ROTATE_BYTES = 12 * 1024 * 1024     # 命中日志超过这个大小就轮转（避免无限长）
ROTATE_KEEP_LINES = 4000            # 轮转时保留最近这么多行

_LOCK = threading.RLock()
_WS = re.compile(r"[\W_]+", re.UNICODE)      # 只在 Unicode 意义上算「非字」：CJK 不会被吃掉


def norm(text: str) -> str:
    """规范化成比较用的 key：小写、去空白与标点、截断。"""
    return _WS.sub("", str(text or "").lower())[:MAX_KEY]


def path_for(state_dir: Path | str) -> Path:
    return Path(state_dir) / FILE


def hits_path(state_dir: Path | str) -> Path:
    return Path(state_dir) / HITS


# ---------- 忽略 / 已补录 名单 ----------

def load_state(state_dir: Path | str) -> dict:
    p = path_for(state_dir)
    try:
        raw = json.loads(p.read_text(encoding="utf-8") or "{}")
    except (OSError, ValueError):
        return {"ignored": [], "added": []}
    out = {"ignored": [], "added": []}
    if isinstance(raw, dict):
        for k in ("ignored", "added"):
            seen = set()
            for v in (raw.get(k) or []):
                key = str(v or "").strip()
                if key and key not in seen:
                    seen.add(key)
                    out[k].append(key)
    return out


def save_state(state_dir: Path | str, data: dict) -> dict:
    """原子写；两个名单都**保序**（顺序就是「谁先被忽略的」）。"""
    p = path_for(state_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    # 满了从**最旧的**丢：名单是「后加的更重要」，[:N] 保的是最旧的那批，
    # 结果新 key 一写就被截掉、面板点了「忽略」却毫无变化（真的踩过）
    body = {"ignored": list(dict.fromkeys(str(x) for x in (data.get("ignored") or []) if str(x)))[-MAX_ITEMS:],
            "added": list(dict.fromkeys(str(x) for x in (data.get("added") or []) if str(x)))[-MAX_ITEMS:]}
    with _LOCK:
        tmp = p.with_name(f"{p.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_text(json.dumps(body, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, p)
    return body


def _skey(cid: str, key: str) -> str:
    """名单里的存储键：`角色\t问题`。

    不按角色分的话，给亚托莉补过的问题在胡桃的清单里也会消失（同一个问题
    两个人可能都需要），而且「忽略」会在全局生效 —— 面板是按角色看的。
    """
    cid = str(cid or "").strip().lower()
    return f"{cid}\t{key}" if cid else key


def set_status(state_dir: Path | str, kind: str, key: str, on: bool = True,
               *, cid: str = "") -> dict:
    """把某个 key 标成 ignored / added（`on=False` 撤销）。读-改-写整段持锁。"""
    if kind not in ("ignored", "added"):
        raise ValueError("kind 只能是 ignored / added")
    key = str(key or "").strip()
    if not key:
        raise ValueError("缺问题内容")
    key = _skey(cid, key)
    with _LOCK:
        data = load_state(state_dir)
        other = "added" if kind == "ignored" else "ignored"
        cur = [x for x in data[kind] if x != key]
        if on:
            cur.append(key)
            data[other] = [x for x in data[other] if x != key]   # 两边互斥
        data[kind] = cur
        return save_state(state_dir, data)


# ---------- 读命中日志 ----------

def read_rows(path: Path, *, max_bytes: int = 6 * 1024 * 1024) -> list[dict]:
    """只读文件尾部（命中日志会很长，整份读进来没必要）。

    按 `\\n` 手工切：`str.splitlines()` 还会在 `\\x85` / `\\u2028` 处断行，
    一条消息被劈成两半就再也解析不出来（中文里这些字符是能出现的）。
    """
    try:
        size = path.stat().st_size
        with path.open("rb") as f:
            if size > max_bytes:
                f.seek(size - max_bytes)
                f.readline()                       # 丢掉半行
            raw = f.read().decode("utf-8", "replace")
    except OSError:
        return []
    out = []
    for line in raw.split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            out.append(row)
    return out


def scan(rows: list[dict], *, days: int = 30, limit: int = 30,
         min_len: int = MIN_LEN, now: float | None = None,
         ignored=(), added=(), weak: float = 0.0, cid: str = "",
         skip_via: tuple[str, ...] = ("panel",)) -> dict:
    """把「没命中 / 只是勉强沾边」的查询聚合成清单（纯函数，方便测）。

    `weak`：最高相似度低于它的也算缺口。**光看「零命中」不够** ——
    大语料角色的阈值是 0.4，向量模型又偏弱，实际几乎每次都能刮到 0.41 的
    边角料，于是「她不懂什么」永远是空的。实测这个库里 5% 分位就是 0.41。

    `skip_via`：面板自己的「试检索」（`via: panel`）不算 —— 那是管理员在测，
    不是群里有人问她。不排除的话清单第一名永远是测试用的探针字符串。
    """
    now = time.time() if now is None else float(now)
    since = now - days * 86400 if days and days > 0 else 0.0
    ign = {x for x in (ignored or ())}
    add = {x for x in (added or ())}
    agg: dict[str, dict] = {}
    scanned = miss = faint = skipped = 0
    for row in rows or ():
        if not isinstance(row, dict):
            continue
        try:
            ts = float(row.get("ts") or 0)
        except (TypeError, ValueError):
            continue
        if ts <= 0:
            continue                 # 没有时间戳 = 排不出先后，别混进「最近」里
        if ts < since:
            continue
        q = str(row.get("query") or "").strip()
        if not q:
            continue
        if str(row.get("via") or "").strip().lower() in (skip_via or ()):
            skipped += 1
            continue
        scanned += 1
        hits = row.get("hits") or []
        try:
            best = max([float(h.get("dense") or 0) for h in hits if isinstance(h, dict)] or [0.0])
        except (TypeError, ValueError):
            best = 0.0
        if hits and not (weak and best < weak):
            continue                                   # 命中得还行：不是缺口
        if hits:
            faint += 1                                 # 勉强沾边
        else:
            miss += 1
        if len(q) < min_len:
            continue
        key = norm(q)
        if not key:
            continue
        cur = agg.get(key)
        if cur is None:
            agg[key] = {"key": key, "text": q[:200], "count": 1,
                        "first": ts, "last": ts, "best": round(best, 3),
                        "character": str(row.get("character") or "").strip().lower()}
        else:
            cur["count"] += 1
            cur["first"] = min(cur["first"], ts)
            cur["last"] = max(cur["last"], ts)
            cur["best"] = min(cur.get("best", best), round(best, 3))   # 取最差的那次
    open_items, n_ign, n_add = [], 0, 0
    for row in agg.values():
        row_key = _skey(cid, row["key"])
        if row_key in add:
            row["status"], n_add = "added", n_add + 1
        elif row_key in ign:
            row["status"], n_ign = "ignored", n_ign + 1
        else:
            row["status"] = "open"
            open_items.append(row)
    open_items.sort(key=lambda r: (-r["count"], -r["last"]))
    return {"items": open_items[:max(1, int(limit or 30))],
            "counts": {"scanned": scanned, "miss": miss, "weak": faint,
                       "skipped": skipped, "keys": len(agg),
                       "open": len(open_items), "ignored": n_ign, "added": n_add}}


def append_row(path: Path, row: dict) -> bool:
    """追加一条命中记录。

    走本模块的锁：`maybe_rotate` 是「读全文 → os.replace」，
    这期间别人 append 进旧 inode 的行会跟着旧文件一起消失。
    """
    try:
        body = json.dumps(row, ensure_ascii=False) + "\n"
    except (TypeError, ValueError):
        return False
    with _LOCK:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as f:
                f.write(body)
        except OSError:
            return False
    return True


def maybe_rotate(path: Path, *, max_bytes: int = ROTATE_BYTES,
                 keep: int = ROTATE_KEEP_LINES) -> bool:
    """命中日志太长就截到最近 `keep` 行（不换文件：读的人都只看这一个路径）。"""
    try:
        if path.stat().st_size <= max_bytes:
            return False
    except OSError:
        return False
    with _LOCK:
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").split("\n")
        except OSError:
            return False
        tail = [ln for ln in lines if ln.strip()][-keep:]
        try:
            tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
            tmp.write_text("\n".join(tail) + "\n", encoding="utf-8")
            os.replace(tmp, path)
        except OSError:
            return False
    return True
