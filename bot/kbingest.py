"""文档导入知识库：把一整篇 txt/md 切块、去重、批量入库。

以前只能一条一条手打（或整段粘贴），一篇 3 万字的设定集根本进不去。
这里只做三件事，每件都能单独测：

1. `split()`  —— 按标题/段落/句子切成 200~600 字的块，块之间留一点重叠，
   免得一句话被切两半、检索时哪块都答不准。
2. `plan()`   —— 给每块算指纹，标出「这篇里重复的」「以前已经入过库的」。
   重复导入是这类工具最容易出的问题：同一个文档导三次，检索里全是同一条。
3. `commit()` —— 逐条交给调用方（真正的写库动作在 server 里），
   失败的单独报，不会因为第 7 块失败就把前面 6 块当成没写。

纯标准库、不联网、不碰文件系统（除了 `seen` 的读写）。
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
from pathlib import Path

MAX_CHUNKS = 400                 # 一次最多切这么多块（再多请拆文件）
MAX_TEXT = 2_000_000             # 2MB 纯文本，超过直接拒绝（不是抓大文件的地方）
MAX_CHARS = 1200                 # 单块上限（参数上限，不是默认值）
DEFAULT_MAX_CHARS = 600
DEFAULT_OVERLAP = 80
DEFAULT_MIN_CHARS = 40
SEEN_MAX = 20_000                # 去重指纹最多记这么多条（FIFO）

_LOCK = threading.RLock()
_HEAD_RE = re.compile(r"^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$")
_SENT_RE = re.compile(r"[^。！？!?；;…\n]*[。！？!?；;…\n]+|[^。！？!?；;…\n]+$")


def norm(text: str) -> str:
    """归一化后再算指纹：空白/全角空格/markdown 记号都不该造成「看起来不同」。"""
    s = str(text or "")
    s = s.replace("\u3000", " ").replace("\r\n", "\n").replace("\r", "\n")
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n{2,}", "\n", s)
    s = re.sub(r"^#{1,6}\s*", "", s, flags=re.M)
    return s.strip().lower()


def fingerprint(text: str) -> str:
    return hashlib.sha1(norm(text).encode("utf-8")).hexdigest()[:16]


def _sentences(par: str) -> list:
    return [x for x in _SENT_RE.findall(par) if x and x.strip()]


def _pack(par: str, max_chars: int, overlap: int) -> list:
    """把一个超长段落按句子打包成若干块，块间用「上一块的尾巴」做重叠。

    超长「句子」（英文段落、表格行、逗号分隔的人名表根本没有句号）
    要**硬切成若干段继续走打包**，不能截断 —— 以前截断等于静默丢内容，
    导入还显示成功。
    """
    queue: list = []
    for sent in _sentences(par):
        while len(sent) > max_chars:
            queue.append(sent[:max_chars])
            sent = sent[max_chars:]
        if sent:
            queue.append(sent)
    out: list = []
    buf = ""
    for s in queue:
        if buf and len(buf) + len(s) > max_chars:
            out.append(buf)
            tail = ""
            if overlap > 0:
                # 重叠取上一块最后一句（不超过 overlap 字），保证下块开头是完整句子
                prev = _sentences(buf)
                if prev and len(prev[-1]) <= overlap:
                    tail = prev[-1]
                else:
                    tail = buf[-overlap:]
                if len(tail) + len(s) > max_chars:      # 别为了重叠反而超长
                    tail = ""
            buf = tail + s
        else:
            buf += s
    if buf.strip():
        out.append(buf)
    return out


def split(text: str, *, max_chars: int = DEFAULT_MAX_CHARS, overlap: int = DEFAULT_OVERLAP,
          min_chars: int = DEFAULT_MIN_CHARS, title: str = "") -> list:
    """切块。返回 `[{idx, title, text, chars}]`（`title` 是所在小节标题）。"""
    t = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    max_chars = max(80, min(int(max_chars or DEFAULT_MAX_CHARS), MAX_CHARS))   # 再小检索就没意义了
    overlap = max(0, min(int(overlap or 0), max_chars // 2))
    min_chars = max(1, min(int(min_chars or DEFAULT_MIN_CHARS), max_chars))
    doc_title = str(title or "").strip()

    # 1) 按 markdown 标题切小节；没有标题就是一节
    sections: list = []
    cur_title, buf = doc_title, []
    for line in t.split("\n"):
        m = _HEAD_RE.match(line)
        if m:
            sections.append((cur_title, "\n".join(buf)))
            cur_title, buf = (m.group(2).strip() or doc_title), []
            continue
        buf.append(line)
    sections.append((cur_title, "\n".join(buf)))

    # 2) 小节内按空行切段，超长段再按句子打包
    chunks: list = []
    for sec_title, body in sections:
        paras = [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]
        for par in paras:
            pieces = [par] if len(par) <= max_chars else _pack(par, max_chars, overlap)
            for piece in pieces:
                piece = piece.strip()
                if not piece:
                    continue
                # 太短的尾块并进上一块（同一小节内），别制造一堆 5 字块
                if (chunks and len(piece) < min_chars
                        and chunks[-1]["title"] == sec_title
                        and chunks[-1]["chars"] + 1 + len(piece) <= max_chars):
                    chunks[-1]["text"] = chunks[-1]["text"] + "\n" + piece
                    chunks[-1]["chars"] = len(chunks[-1]["text"])
                    continue
                chunks.append({"title": sec_title or doc_title, "text": piece,
                               "chars": len(piece)})
    for i, c in enumerate(chunks):
        c["idx"] = i
    return chunks


def _seen_path(state_dir: Path | str) -> Path:
    return Path(state_dir) / "kb_ingest.json"


def load_seen(state_dir: Path | str, character: str) -> list:
    with _LOCK:
        try:
            d = json.loads(_seen_path(state_dir).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        v = d.get(str(character or "")) if isinstance(d, dict) else None
        return [str(x) for x in v] if isinstance(v, list) else []


def save_seen(state_dir: Path | str, character: str, hashes: list) -> None:
    """合并写回（不覆盖别的角色），并按 FIFO 截断。"""
    key = str(character or "")
    with _LOCK:
        p = _seen_path(state_dir)
        p.parent.mkdir(parents=True, exist_ok=True)
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            d = {}
        if not isinstance(d, dict):
            d = {}
        merged = [str(x) for x in (d.get(key) or []) if isinstance(x, str)]
        seen = set(merged)
        for h in hashes:
            if h and h not in seen:
                merged.append(str(h))
                seen.add(str(h))
        d[key] = merged[-SEEN_MAX:]
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        tmp.replace(p)


def plan(text: str, *, state_dir: Path | str | None = None, character: str = "",
         max_chars: int = DEFAULT_MAX_CHARS, overlap: int = DEFAULT_OVERLAP,
         min_chars: int = DEFAULT_MIN_CHARS, title: str = "") -> dict:
    """切块 + 标重复。`dup` 为真表示**不会**再入库（本篇重复 or 以前入过）。"""
    raw = str(text or "")
    if len(raw) > MAX_TEXT:
        raise ValueError(f"文本太大（{len(raw)} 字，上限 {MAX_TEXT}）")
    chunks = split(raw, max_chars=max_chars, overlap=overlap,
                   min_chars=min_chars, title=title)
    if len(chunks) > MAX_CHUNKS:
        raise ValueError(f"切出 {len(chunks)} 块，超过一次 {MAX_CHUNKS} 块的上限，"
                         "请拆成多个文件再导")
    seen = set(load_seen(state_dir, character)) if state_dir else set()
    batch: set = set()
    for c in chunks:
        fp = fingerprint(c["text"])
        c["fp"] = fp
        if fp in seen:
            c["dup"] = "已入过库"
        elif fp in batch:
            c["dup"] = "本篇重复"
        else:
            c["dup"] = ""
        batch.add(fp)
    fresh = [c for c in chunks if not c["dup"]]
    return {
        "chunks": chunks,
        "counts": {"total": len(chunks), "fresh": len(fresh),
                   "dup": len(chunks) - len(fresh),
                   "chars": sum(int(c["chars"]) for c in fresh)},
        "params": {"max_chars": max_chars, "overlap": overlap, "min_chars": min_chars},
    }


def commit(chunks: list, add_fn) -> dict:
    """逐条入库。`add_fn(chunk)` 抛异常算这一条失败，不影响其余（也不回滚）。"""
    added, failed, errors = 0, 0, []
    ok_fps: list = []
    for c in chunks or []:
        if not isinstance(c, dict) or c.get("dup"):
            continue
        try:
            add_fn(c)
        except Exception as e:                                     # noqa: BLE001
            failed += 1
            if len(errors) < 5:
                errors.append(f"第 {int(c.get('idx') or 0) + 1} 块：{type(e).__name__}: {e}")
            continue
        added += 1
        if c.get("fp"):
            ok_fps.append(str(c["fp"]))
    return {"added": added, "failed": failed, "errors": errors, "fps": ok_fps}
