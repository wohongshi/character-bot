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

import functools
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


# 读缓存：`_build_messages` 是**在事件循环里同步调用**的，每条消息都要
# mkdir + 解析最多 200 行 JSONL。文件没变就不用重复解析。
# 键 = 路径，值 = (mtime, size, items)。返回的 list 是**共享的，不许改**。
_CACHE: dict[str, tuple[float, int, list[dict]]] = {}


def _read(path: Path) -> list[dict]:
    try:
        st = path.stat()
    except OSError:
        _CACHE.pop(str(path), None)
        return []
    hit = _CACHE.get(str(path))
    if hit and hit[0] == st.st_mtime and hit[1] == st.st_size:
        return hit[2]
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
    _CACHE[str(path)] = (st.st_mtime, st.st_size, out)
    return out


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", str(s or "")).strip().lower()


def add(state_dir: Path, character: str, text: str, *,
        source: str = "panel", who: str = "", scope: str = "") -> dict | None:
    """加一条记忆。同一条（归一化后相同）不重复加，只把 ts 刷新。

    `scope` 是「这句话是在哪个会话里记的」（QQ 用 chat_id，网页用 `web:<会话>`）。
    空串 = 全局（面板里手动点的「记住」就是这样：主人自己的记事本，处处都算数）。
    不区分的话，群里某人让 agent 记的事会出现在主人私聊的提示里 —— 那是泄漏。
    """
    t = re.sub(r"\s+", " ", str(text or "")).strip()[:MAX_TEXT]
    if not t:
        return None
    path = path_for(state_dir, character)
    key = _norm(t)
    with _LOCK:
        items = list(_read(path))          # 缓存里的是共享 list，别原地改
        for e in items:
            if _norm(e.get("text")) == key:
                e["ts"] = time.time()
                e["hits"] = int(e.get("hits") or 0) + 1
                return e if _write(path, items) else None
        entry = {"ts": time.time(), "text": t, "source": str(source or "panel")[:16],
                 "who": str(who or "")[:16], "scope": str(scope or "")[:64], "hits": 0}
        items.append(entry)
        items = items[-MAX_ITEMS:]
        return entry if _write(path, items) else None


def _write(path: Path, items: list[dict]) -> bool:
    """写盘；返回**真的写成功了吗**。

    以前这里吞掉异常返回 None，add() 照样把条目交给调用方，面板就显示
    「记住了」然后刷新一下什么都没了。写不进去必须能被上层看见。
    """
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            for e in items:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")
        tmp.replace(path)
        _CACHE.pop(str(path), None)
        return True
    except OSError as e:
        log.warning("记忆写不进去：%s", e)
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return False


def all_items(state_dir: Path, character: str) -> list[dict]:
    return sorted(_read(path_for(state_dir, character)), key=lambda e: -float(e.get("ts") or 0))


def remove(state_dir: Path, character: str, ts: float) -> int:
    path = path_for(state_dir, character)
    with _LOCK:
        items = _read(path)
        keep = [e for e in items if abs(float(e.get("ts") or 0) - float(ts)) > 1e-6]
        removed = len(items) - len(keep)
        if removed and not _write(path, keep):
            return 0                      # 没写进去就不能说删掉了
    return removed


def clear(state_dir: Path, character: str) -> int:
    path = path_for(state_dir, character)
    with _LOCK:
        n = len(_read(path))
        if n and not _write(path, []):
            return 0
    return n


# ---------------------------------------------------------------- 注入
@functools.lru_cache(maxsize=2048)
def _grams(s: str) -> frozenset[str]:
    t = _norm(s)
    return frozenset({t[i:i + 2] for i in range(max(0, len(t) - 1))} if len(t) > 1 else ({t} if t else ()))


def pick(state_dir: Path, character: str, query: str = "", limit: int = 8,
         scope: str | None = None) -> list[dict]:
    """挑要注入的那几条：先按相关度，再按最近，去重。

    `scope=None` = 不过滤（面板列表用）；给字符串时只保留「全局 + 这个会话」的
    —— 和 `tool_recall` 的口径一致。
    """
    items = all_items(state_dir, character)
    if scope is not None:
        want = str(scope or "")
        items = [e for e in items if str(e.get("scope") or "") in ("", want)]
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


def render(state_dir: Path, character: str, query: str = "", limit: int = 8,
           scope: str | None = None) -> str:
    """给系统提示用的一段文字；没有记忆就返回空串。"""
    items = pick(state_dir, character, query, limit, scope=scope)
    if not items:
        return ""
    lines = []
    for e in items:
        ts = float(e.get("ts") or 0)
        when = time.strftime("%m-%d", time.localtime(ts)) if ts else ""
        # 站在角色的角度说话：面板里点「记住」是主人让她记的，
        # 写成「我」会被模型理解成「我自己想起来的」，语气就偏了。
        src = "主人让你记的" if e.get("source") == "panel" else "你自己记的"
        lines.append(f"- {e['text']}" + (f"（{when}，{src}记的）" if when else ""))
    return "【你记得的事】这些是你和主人之间真实发生过/说过的，聊天时自然用上，别逐条复述：\n" + "\n".join(lines)
