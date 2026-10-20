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


def _num(v, default: float = 0.0) -> float:
    """容错的数值转换：手改/外部导入的 `ts` 可能是字符串，别让整次整理 500。"""
    try:
        return float(v)
    except (TypeError, ValueError, OverflowError):
        return default


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
    rows.sort(key=lambda x: (_num(x.get("ts")), str(x.get("text") or "")))

    groups = []
    dropped: set = set()
    for g in _clusters(rows, th):
        g = sorted(g, key=lambda i: _num(rows[i].get("ts")))
        keep_idx = g[-1]                        # 最新的那条当代表
        keep = dict(rows[keep_idx])
        drop = []
        for i in g:
            if i == keep_idx:
                continue
            # `_merge_scope(old, new)` 让**第二个**参数优先（截断从尾部保）；
            # 要保留的是 keep 那条的会话，所以把它放在第二位
            keep["scope"] = _merge_scope(rows[i].get("scope", ""), keep.get("scope", ""))
            keep["hits"] = int(keep.get("hits") or 0) + int(rows[i].get("hits") or 0)
            drop.append(rows[i])
            # 用 (ts, text) 而不是裸 ts：没有 ts 的老行会全折成 0.0，删多条只算一条
            dropped.add((_num(rows[i].get("ts")), str(rows[i].get("text") or "")))
        keep["merged"] = len(drop)
        groups.append({"keep": keep, "drop": drop, "sim": round(
            similar(str(keep.get("text") or ""), str(drop[0].get("text") or "")), 3),
            "text": str(keep.get("text") or "")[:120],
            "texts": [str(d.get("text") or "")[:120] for d in drop]})

    stale = []
    if int(stale_days or 0) > 0:
        floor = now - int(stale_days) * 86400
        for i, r in enumerate(rows):
            ts = _num(r.get("ts"))
            if ts and ts < floor and (ts, str(r.get("text") or "")) not in dropped:
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
    """复用 memory 的路径规则（它会归一化角色名）。

    自己拼 `memories/<character>.jsonl` 的话，`Klee` / `klee.v2` / `../x` 会去操作
    一个 `memory.add()` 根本不认的文件（整理静默变成 no-op，最坏写到 state 之外）。
    """
    from .memory import path_for
    return path_for(state_dir, character)


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

    合并的计数**按行**算（不是按 key）：文件里可能有完全重复的行，
    按 key 算会少报、还会把 hits 记两遍。
    """
    from . import memory as mem_mod
    path = _path(state_dir, character)

    def _key(r: dict) -> tuple:
        # 只用 ts 当身份会把「没有 ts 的老行」全折成 0.0 —— 删一条就删一片
        return (_num(r.get("ts")), str(r.get("text") or ""))

    lock = getattr(mem_mod, "_LOCK", None)
    ctx = lock if lock is not None else _NullLock()
    with ctx:
        rows = _read(path)
        if not rows:
            return {"before": 0, "after": 0, "merged": 0, "archived": 0,
                    "dropped": 0, "noop": True}
        # 只算一次 key（每行都要 float + 截断字符串，2000 行重扫几十遍太浪费，
        # 而且这段全程持 memory._LOCK，聊天那边会被一起挡住）
        pairs = [(_key(r), r) for r in rows]
        present = {k for k, _ in pairs}
        first_row: dict = {}
        for k, r in pairs:
            first_row.setdefault(k, r)      # 文件里第一次出现的那条（和输出循环的基准一致）
        stale_ts = {_key(r) for r in (p or {}).get("stale") or []}

        # 逐组核账：keep 还在（且不是「要被归档」）且组里至少还有一条别的行才生效
        groups = []          # [(keep_key, keep_row, [本组现存的所有行])]
        group_of = {}        # 行 key → 组下标
        for g in (p or {}).get("groups") or []:
            kk = _key(g.get("keep") or {})
            if kk not in present or kk in stale_ts:
                # keep 被删 / 它自己要被归档：这一组不动（否则 drops 只剩 .bak）
                continue
            keys = {kk} | {_key(d) for d in (g.get("drop") or [])}
            alive = [r for k, r in pairs if k in keys]
            if len(alive) <= 1:
                continue
            gi = len(groups)
            keep_row = first_row[kk]
            groups.append((kk, keep_row, alive))
            for r in alive:
                group_of.setdefault(_key(r), gi)

        if not groups and not stale_ts:
            return {"before": len(rows), "after": len(rows), "merged": 0,
                    "archived": 0, "dropped": 0, "noop": True}

        out, archived = [], []
        written: set = set()
        merged_rows = 0
        for k, r in pairs:
            gi = group_of.get(k)
            if gi is not None:
                kk, keep_row, alive = groups[gi]
                if k != kk:
                    continue        # 组内其余行（含重复行）全部并进 keep 那条
                if gi in written:
                    continue        # keep 自身在文件里重复：只写一份
                written.add(gi)
                # 以**文件里现在这条** keep 为底（计划是锁外算的，ts/source 可能已经变）
                rec = dict(keep_row)
                merged_rows += len(alive) - 1
                rec["hits"] = sum(int(x.get("hits") or 0) for x in alive)
                # `_merge_scope(old, new)` 让第二个参数优先（截断从尾部保）：
                # 先把 keep 自己放进去，再一条条并 drop —— keep 的会话最不容易被截掉
                scope = str(keep_row.get("scope") or "")
                for x in alive:
                    if _key(x) == kk:
                        continue
                    scope = _merge_scope(str(x.get("scope") or ""), scope)
                rec["scope"] = scope
                rec.pop("merged", None)         # 预演用的计数，不落盘
                out.append(rec)
                continue
            if k in stale_ts:
                archived.append(r)
                continue
            out.append(r)

        if len(out) == len(rows) and not archived:
            return {"before": len(rows), "after": len(rows), "merged": 0,
                    "archived": 0, "dropped": 0, "noop": True}
        if path.exists():
            # 备份写不进去就整体作废：没有 .bak 的破坏性整理不能做
            path.with_suffix(path.suffix + ".bak").write_bytes(path.read_bytes())
        if archived:                    # 先归档：失败就整体作废，别丢条目
            ap = path.with_name(f"{path.stem}.archive.jsonl")
            ap.parent.mkdir(parents=True, exist_ok=True)
            merged = _read(ap)
            seen = {_key(r) for r in merged}
            added = 0
            for r in archived:
                if _key(r) not in seen:
                    merged.append(r)
                    seen.add(_key(r))
                    added += 1
            if added:
                _write(ap, merged)
        _write(path, out)
    return {"before": len(rows), "after": len(out), "merged": merged_rows,
            "archived": len(archived), "dropped": merged_rows}


class _NullLock:
    """拿不到 memory._LOCK 时的兜底（不该发生，但别让整理直接崩）。"""

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False
