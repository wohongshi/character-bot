"""角色长期记忆：面板里「记住这句」和 agent 的 remember 工具共用的那一份。

为什么单独一份、不塞进知识库
--------------------------
知识库（klee-kb）是**只读的向量索引**，改一条要重建整份 embedding（274 块，几十秒）。
而「记住」是高频、单条、立刻要生效的操作 —— 所以存在这里：
`state/memories/<角色>.jsonl`，一行一条，注入到系统提示里。

注入策略
--------
不是把 200 条全塞进去（那是每条消息都要付的输入成本），而是：
按这条消息的相关度挑几条，再补最近的几条，总共 `limit` 条。
相关度用 2-gram 重合（和 skills.hint 同一个思路），中文短句上够用。
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from pathlib import Path

log = logging.getLogger("klee.bot.memory")

MAX_ITEMS = 200          # 每个角色最多留这么多条（超了丢最旧的）
MAX_TEXT = 300           # 单条上限
_LOCK = threading.Lock()


def _root(state_dir: Path) -> Path:
    d = Path(state_dir) / "memories"
    d.mkdir(parents=True, exist_ok=True)
    return d


def path_for(state_dir: Path, character: str) -> Path:
    cid = re.sub(r"[^a-z0-9_\-]", "", str(character or "").strip().lower())[:32] or "default"
    return _root(state_dir) / f"{cid}.jsonl"


def _read(path: Path) -> list[dict]:
    out = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                if isinstance(e, dict) and str(e.get("text") or "").strip():
                    out.append(e)
    except OSError:
        return []
    return out


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", str(s or "")).strip().lower()


def add(state_dir: Path, character: str, text: str, *,
        source: str = "panel", who: str = "") -> dict | None:
    """加一条记忆。同一条（归一化后相同）不重复加，只把 ts 刷新。"""
    t = re.sub(r"\s+", " ", str(text or "")).strip()[:MAX_TEXT]
    if not t:
        return None
    path = path_for(state_dir, character)
    key = _norm(t)
    with _LOCK:
        items = _read(path)
        for e in items:
            if _norm(e.get("text")) == key:
                e["ts"] = time.time()
                e["hits"] = int(e.get("hits") or 0) + 1
                _write(path, items)
                return e
        entry = {"ts": time.time(), "text": t, "source": str(source or "panel")[:16],
                 "who": str(who or "")[:16], "hits": 0}
        items.append(entry)
        items = items[-MAX_ITEMS:]
        _write(path, items)
        return entry


def _write(path: Path, items: list[dict]) -> None:
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            for e in items:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")
        tmp.replace(path)
    except OSError as e:
        log.warning("记忆写不进去：%s", e)
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def all_items(state_dir: Path, character: str) -> list[dict]:
    return sorted(_read(path_for(state_dir, character)), key=lambda e: -float(e.get("ts") or 0))


def remove(state_dir: Path, character: str, ts: float) -> int:
    path = path_for(state_dir, character)
    with _LOCK:
        items = _read(path)
        keep = [e for e in items if abs(float(e.get("ts") or 0) - float(ts)) > 1e-6]
        removed = len(items) - len(keep)
        if removed:
            _write(path, keep)
    return removed


def clear(state_dir: Path, character: str) -> int:
    path = path_for(state_dir, character)
    with _LOCK:
        n = len(_read(path))
        if n:
            _write(path, [])
    return n


# ---------------------------------------------------------------- 注入
def _grams(s: str) -> set[str]:
    t = _norm(s)
    return {t[i:i + 2] for i in range(max(0, len(t) - 1))} if len(t) > 1 else ({t} if t else set())


def pick(state_dir: Path, character: str, query: str = "", limit: int = 8) -> list[dict]:
    """挑要注入的那几条：先按相关度，再按最近，去重。"""
    items = all_items(state_dir, character)
    if not items:
        return []
    q = _grams(query)
    scored = []
    for e in items:
        g = _grams(str(e.get("text") or ""))
        score = len(q & g) if q else 0
        scored.append((score, float(e.get("ts") or 0), e))
    scored.sort(key=lambda x: (-x[0], -x[1]))
    out: list[dict] = []
    seen: set[str] = set()
    for score, _ts, e in scored:
        if score <= 0:
            break
        k = _norm(e.get("text"))
        if k in seen:
            continue
        seen.add(k)
        out.append(e)
        if len(out) >= limit:
            return out
    # 相关的不够：按时间补最近的
    for e in items:
        k = _norm(e.get("text"))
        if k in seen:
            continue
        seen.add(k)
        out.append(e)
        if len(out) >= limit:
            break
    return out


def render(state_dir: Path, character: str, query: str = "", limit: int = 8) -> str:
    """给系统提示用的一段文字；没有记忆就返回空串。"""
    items = pick(state_dir, character, query, limit)
    if not items:
        return ""
    lines = []
    for e in items:
        ts = float(e.get("ts") or 0)
        when = time.strftime("%m-%d", time.localtime(ts)) if ts else ""
        src = "我" if e.get("source") == "panel" else "你自己"
        lines.append(f"- {e['text']}" + (f"（{when}，{src}记的）" if when else ""))
    return "【你记得的事】这些是你和主人之间真实发生过/说过的，聊天时自然用上，别逐条复述：\n" + "\n".join(lines)
