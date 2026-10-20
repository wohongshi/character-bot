"""记忆整理：把「说过好几遍的同一件事」并成一条。

为什么需要：记忆是每条消息都会被拼进提示词的（最贵的那部分）。`memory.add()`
只挡**完全一样**的文本，换个说法就会又存一条 —— 实测里同一个「谢谢你陪我」
被记了三遍，措辞只差几个字。时间一长，提示词里全是重复内容，既费 token
又让模型反复念叨同一件事。

这里只做两件不需要模型的事：

1. **合并近似重复**：按字符二元组的 Jaccard 相似度聚类（`>= threshold` 就算
   同一件事），每组保留最新的一条，作用域取并集（记忆在原来的会话里仍然可见）。
2. **归档陈旧条目**：超过 N 天没再被记过的，移到 `memories/<角色>.archive.jsonl`，
   而不是删掉 —— 想找回来还在。

`plan()` 是纯函数：只吃条目列表、只吐计划，测试直接喂假数据。
"""

from __future__ import annotations

import difflib
import json
import os
import threading
import time
from pathlib import Path

DEFAULT_THRESHOLD = 0.45     # 见 similar()：Jaccard 与 difflib 比率取大
MAX_ITEMS = 2000             # 一次最多处理这么多条（防脏数据）
DEFAULT_STALE_DAYS = 0       # 0 = 不归档（默认只做去重，行为最保守）

_HEAD_KEEP = 12              # 太短的句子（「好的」）不参与相似度判断


def _norm(s: str) -> str:
    return "".join(str(s or "").lower().split())


def grams(s: str) -> frozenset:
    t = _norm(s)
    if len(t) < 2:
        return frozenset([t]) if t else frozenset()
    return frozenset(t[i:i + 2] for i in range(len(t) - 1))


SHORT_SIM = 0.75             # 短句要更像才合并（「主人怕黑」和「主人怕狗」不能并）


def _sim_pre(na: str, nb: str, ga: frozenset, gb: frozenset) -> float:
    """`similar()` 的内核；`_clusters` 复用同一份，免得两处规则漂移。"""
    if not na or not nb or not ga or not gb:
        return 0.0
    inter = len(ga & gb)
    if not inter:
        return 0.0
    jac = inter / len(ga | gb)
    if min(len(na), len(nb)) < _HEAD_KEEP:
        # 短句只有两三个二元组，0.5 就可能完全是两件事
        # （「主人怕黑」vs「主人怕狗」共享「主人」「人怕」）→ 要更像才算
        return jac if jac >= SHORT_SIM else jac * 0.5
    if jac < 0.12:                  # 快筛：连二元组都几乎不共享，不可能同一件事
        return jac
    return max(jac, difflib.SequenceMatcher(None, na, nb).ratio())


def similar(a: str, b: str) -> float:
    """像不像「同一件事」。

    只用二元组 Jaccard 会漏：同一件事换个说法时句子会变长，Jaccard 的分母
    （并集）跟着涨，实测同一件事只有 0.26。所以取 `max(Jaccard, difflib 比率)`：

    - 短句（< 12 字）只认 Jaccard（「好的」「晚安」这种不能靠模糊比）；
    - Jaccard 先把明显无关的挡掉（CJK 里真正近似的句子一定共享二元组）；
    - 剩下的再用 difflib 的字符级比率兜一次。
    """
    return _sim_pre(_norm(a), _norm(b), grams(a), grams(b))


def _clusters(items: list, threshold: float) -> list:
    """并查集聚类：A~B、B~C 会让 A/B/C 落进同一组（传递性）。"""
    n = len(items)
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i, j):
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[rj] = ri

    # 预计算：gram/长度只算一次（不然 O(n²) 里每个 pair 都重建一遍 frozenset）
    texts = [str(x.get("text") or "") for x in items]
    norms = [_norm(t) for t in texts]
    grs = [grams(t) for t in texts]
    for i in range(n):
        # 短句也要参与比较：`_sim_pre` 里对短句用的是更严的 SHORT_SIM，
        # 整条跳过会让「谢谢你陪我」这种短句永远并不到一起
        for j in range(i + 1, n):
            li, lj = len(norms[i]), len(norms[j])
            if li and lj and max(li, lj) > min(li, lj) * 2.2:
                continue
            if _sim_pre(norms[i], norms[j], grs[i], grs[j]) >= threshold:
                union(i, j)
    groups: dict = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    return [g for g in groups.values() if len(g) > 1]


def _merge_scope(a: str, b: str) -> str:
    """作用域取并集。直接复用 `memory._merge_scope`：它按**整条**作用域丢，
    绝不按字符截断（截出 `web:studio-ma` 这种半截 id 永远匹配不上任何会话）。"""
    from .memory import _merge_scope as _mem_merge
    return _mem_merge(a, b)


def plan(items: list, *, threshold: float = DEFAULT_THRESHOLD,
         stale_days: int = DEFAULT_STALE_DAYS, now: float | None = None) -> dict:
    """给出「哪几条要合并 / 哪几条要归档」。

    返回 `{groups, stale, keep, drop, counts}`：
    - `groups[i] = {keep: item, drop: [item...], sim, text}`
    - `stale` = 超过 `stale_days` 没更新的条目（`stale_days=0` 时永远为空）
    """
    now = time.time() if now is None else now
    try:
        th = max(0.3, min(float(threshold), 0.99))
    except (TypeError, ValueError):
        th = DEFAULT_THRESHOLD
    rows = [x for x in (items or []) if isinstance(x, dict) and str(x.get("text") or "").strip()]
    rows = rows[:MAX_ITEMS]
    rows.sort(key=lambda x: float(x.get("ts") or 0))

    groups = []
    dropped: set = set()
    for g in _clusters(rows, th):
        g = sorted(g, key=lambda i: float(rows[i].get("ts") or 0))
        keep_idx = g[-1]                        # 最新的那条当代表
        keep = dict(rows[keep_idx])
        drop = []
        for i in g:
            if i == keep_idx:
                continue
            keep["scope"] = _merge_scope(keep.get("scope", ""), rows[i].get("scope", ""))
            keep["hits"] = int(keep.get("hits") or 0) + int(rows[i].get("hits") or 0)
            drop.append(rows[i])
            dropped.add(float(rows[i].get("ts") or 0))
        keep["merged"] = len(drop)
        groups.append({"keep": keep, "drop": drop, "sim": round(
            similar(str(keep.get("text") or ""), str(drop[0].get("text") or "")), 3),
            "text": str(keep.get("text") or "")[:120],
            "texts": [str(d.get("text") or "")[:120] for d in drop]})

    stale = []
    if int(stale_days or 0) > 0:
        floor = now - int(stale_days) * 86400
        for i, r in enumerate(rows):
            ts = float(r.get("ts") or 0)
            if ts and ts < floor and ts not in dropped:
                stale.append(r)

    keep_n = len(rows) - len(dropped) - len(stale)
    return {
        "groups": groups,
        "stale": stale,
        "counts": {"total": len(rows), "groups": len(groups),
                   "merged": len(dropped), "stale": len(stale),
                   "keep": max(0, keep_n)},
        "params": {"threshold": th, "stale_days": int(stale_days or 0)},
    }


def _path(state_dir: Path | str, character: str) -> Path:
    return Path(state_dir) / "memories" / f"{character}.jsonl"


def _read(path: Path) -> list:
    out = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if isinstance(r, dict):
                    out.append(r)
    except FileNotFoundError:
        return []
    # 其它 OSError（权限 / 瞬时 I/O）**不能**当成空文件：apply() 拿到空列表
    # 就会把真实文件覆盖成空的
    return out


def _write(path: Path, rows: list) -> None:
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        tmp.replace(path)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def apply(state_dir: Path | str, character: str, p: dict) -> dict:
    """把计划落到磁盘：合并的被丢掉、陈旧的进归档、其余原样写回。

    三件事按顺序做，任何一步失败都不该让记忆「凭空少一截」：

    1. 先留 `.bak`（这次整理是**破坏性**的：合并不可能完美）；
    2. **先写归档再重写主线** —— 反过来的话，归档那步一失败，陈旧的条目
       既不在主线也不在归档，只剩一个下次就会被覆盖的 `.bak`；
    3. 状态目录里没有变化就一个字节都不写。

    整个过程持 `memory._LOCK`：聊天那侧 `memory.add()` 也拿这把锁，
    不互斥的话「整理期间的读 → 聊天写入 → 整理回写旧快照」会丢新记忆。
    """
    from . import memory as mem_mod
    path = _path(state_dir, character)

    drop_ts = set()
    for g in (p or {}).get("groups") or []:
        for d in g.get("drop") or []:
            drop_ts.add(float(d.get("ts") or 0))
    stale_ts = {float(r.get("ts") or 0) for r in (p or {}).get("stale") or []}
    if not drop_ts and not stale_ts:
        return {"before": 0, "after": 0, "merged": 0, "archived": 0, "dropped": 0,
                "noop": True}

    merged_by_keep = {}
    for g in (p or {}).get("groups") or []:
        k = g.get("keep") or {}
        merged_by_keep[float(k.get("ts") or 0)] = k

    lock = getattr(mem_mod, "_LOCK", None)
    ctx = lock if lock is not None else _NullLock()
    with ctx:
        rows = _read(path)
        if not rows:
            return {"before": 0, "after": 0, "merged": 0, "archived": 0, "dropped": 0,
                    "noop": True}
        out, archived = [], []
        for r in rows:
            ts = float(r.get("ts") or 0)
            if ts in drop_ts:
                continue
            if ts in stale_ts:
                archived.append(r)
                continue
            out.append({**merged_by_keep[ts]} if ts in merged_by_keep else r)

        if path.exists():
            try:
                path.with_suffix(path.suffix + ".bak").write_bytes(path.read_bytes())
            except OSError:
                pass
        if archived:                    # 先归档：失败就整体作废，别丢条目
            ap = path.with_name(f"{character}.archive.jsonl")
            ap.parent.mkdir(parents=True, exist_ok=True)
            with open(ap, "a", encoding="utf-8") as f:
                for r in archived:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
        _write(path, out)
    return {"before": len(rows), "after": len(out), "merged": len(drop_ts),
            "archived": len(archived), "dropped": len(drop_ts)}


class _NullLock:
    """拿不到 memory._LOCK 时的兜底（不该发生，但别让整理直接崩）。"""

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False
