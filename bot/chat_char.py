"""每个会话固定的角色：哪个群跟哪个角色说话，各自钉一个。

为什么不能塞进现有文件
----------------------
- `state/characters.json` 的 `active` 是**全局**的（面板 🎭 页切换的那个）。
  把会话绑定写进去，面板每切一次角色就会把所有群的绑定一起改掉。
- `state/chat_policy.json` 是「会话策略」（闭嘴 / 仅 @ / 静默时段），面板会
  整份重写它。多塞一层语义，两边迟早互相清空。

所以单独一个文件 `state/chat_chars.json`：

    {"chats": {"group:1077339356": {"id": "hutao", "ts": 1745000000.0,
                                    "by": "2100950993"}}}

规则
----
- **只有显式绑定的会话才在里面**；没写的 = 跟随全局当前角色（行为不变）；
- 指向已删除角色的绑定 = 当作没绑（读的时候校验，`bot/runtime.py` 会记一条日志）；
- 文件坏了 / 读不了 → 全空（= 全跟随全局）。**绝不能因为一个坏文件让 bot 不说话**；
- 最多 500 个会话，超了不再新增（防长跑膨胀）。

ContextVar
----------
`current()` / `push()` / `pop()`：一条消息从进门到回复结束都在同一个 asyncio
上下文里。运行时在入口处把「本会话生效的角色」放进去，SOUL / 知识库 / 记忆 /
情绪 / 模型 / 角色限定插件就都能读到它 —— 不用把 cid 顺着二十个函数签名叫下去
（那样必然漏一处，而漏掉的那处会安静地用错人格）。
"""
from __future__ import annotations

import contextvars
import json
import logging
import os
import re
import threading
import time
from pathlib import Path

log = logging.getLogger("klee.bot.chat_char")

FILE = "chat_chars.json"
MAX_CHATS = 500

# 角色 id 的形态（和 characters.py 的 _ID_RE 一致；这里不 import，模块保持独立）
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
# 会话 id：`group:123` / `private:456` / 别的适配器可能给 `web:xxx`
_CHAT_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*:[^\s\x00-\x1f\x7f]{1,64}$")

_LOCK = threading.RLock()
# (路径, mtime_ns, size) → {"chats": {...}}。绑定文件很小，按 stat 失效就够，
# 不需要 TTL：写一次 mtime 就变，读一次 stat 而已。
_CACHE: dict = {}


class BindError(ValueError):
    """绑定参数不合法（消息直接给人看）。"""


def path_for(state_dir: Path | str) -> Path:
    return Path(state_dir) / FILE


def clean_chat(chat) -> str:
    """会话 id 规范化：不合法 → ""（调用方按「没绑定」处理）。"""
    v = str(chat or "").strip()
    return v if _CHAT_RE.match(v) else ""


# 面板里的对话（`web:<session>`）不算「会话绑定」：它有自己的角色选择器，
# 而且走的是服务端另一条回复路径（不进 handle_event）。把它绑上等于让面板
# 显示的角色和实际回话的角色对不上 —— 干脆不收。
_SKIP_PREFIX = ("web:",)


def is_bindable(chat) -> bool:
    c = clean_chat(chat)
    return bool(c) and not c.startswith(_SKIP_PREFIX)


def clean_id(cid) -> str:
    v = str(cid or "").strip().lower()
    return v if _ID_RE.match(v) else ""


def _decode(raw: bytes) -> dict:
    """字节 → {"chats": {...}}；任何问题都返回空表（fail-open 到「跟随全局」）。"""
    try:
        d = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return {"chats": {}}
    if not isinstance(d, dict):
        return {"chats": {}}
    src = d.get("chats")
    if not isinstance(src, dict):
        return {"chats": {}}
    out = {}
    for chat, rec in src.items():
        c = clean_chat(chat)
        if not c or not isinstance(rec, dict) or not is_bindable(c):
            continue
        cid = clean_id(rec.get("id"))
        if not cid:
            continue
        try:
            ts = float(rec.get("ts") or 0)
        except (TypeError, ValueError):
            ts = 0.0
        out[c] = {"id": cid, "ts": ts, "by": str(rec.get("by") or "")[:24]}
    return {"chats": out}


def load(state_dir: Path | str) -> dict:
    """读全部绑定（带 stat 缓存）。返回 `{chat_id: {id, ts, by}}` 的副本。"""
    p = path_for(state_dir)
    try:
        st = p.stat()
        sig = (st.st_mtime_ns, st.st_size)
    except OSError:
        sig = None
    key = str(p)
    with _LOCK:
        hit = _CACHE.get(key)
        if sig is None:
            if hit and hit["sig"] is None:
                return dict(hit["data"])
            _CACHE[key] = {"sig": None, "data": {}}
            return {}
        if hit and hit["sig"] == sig:
            return dict(hit["data"])
    try:
        raw = p.read_bytes()
    except OSError as e:
        log.info("会话角色绑定读取失败（按全跟随全局）：%s", e)
        raw = b"{}"
    data = _decode(raw)["chats"]
    with _LOCK:
        _CACHE[key] = {"sig": sig, "data": data}
    return dict(data)


def get(state_dir: Path | str, chat) -> str:
    """这个会话绑定的角色 id；没绑（或 id 不合法）→ ""。"""
    c = clean_chat(chat)
    if not c:
        return ""
    return str((load(state_dir).get(c) or {}).get("id") or "")


def _write(state_dir: Path | str, chats: dict) -> None:
    p = path_for(state_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps({"chats": chats}, ensure_ascii=False, indent=2) + "\n"
    tmp = p.with_name(f"{p.name}.{os.getpid()}.tmp")
    with _LOCK:
        try:
            # 留一份上一版（写坏了还能看）。复制而不是改名：改名会让原文件在
            # 「改名」和「写新文件」之间**不存在**，那段时间读到的就是「全跟随全局」。
            if p.exists():
                p.with_name(p.name + ".bak").write_bytes(p.read_bytes())
        except OSError:
            pass
        tmp.write_text(body, encoding="utf-8")
        os.replace(tmp, p)
        # 写完立刻把缓存换成新值：同一进程里紧接着读到的必须是刚写的，
        # 不能等下一次 stat（有些文件系统 mtime 精度是秒）。
        try:
            st = p.stat()
            _CACHE[str(p)] = {"sig": (st.st_mtime_ns, st.st_size), "data": dict(chats)}
        except OSError:
            _CACHE.pop(str(p), None)


def set_binding(state_dir: Path | str, chat, cid: str, *, by: str = "",
                valid_ids=None) -> dict:
    """把会话钉到某个角色上。`valid_ids`（可迭代）给了就校验角色存在。"""
    c = clean_chat(chat)
    if not c:
        raise BindError("会话 id 不合法")
    if not is_bindable(c):
        raise BindError("面板里的对话不参与固定（它跟着面板上选的角色）")
    k = clean_id(cid)
    if not k:
        raise BindError("角色 id 不合法")
    if valid_ids is not None and k not in {str(x).strip().lower() for x in valid_ids}:
        raise BindError(f"没有角色 {k!r}")
    # 读-改-写整段持锁：面板连点两下「固定」时，两次 load 拿到同一份旧表，
    # 后写的那次会把先写的那条覆盖掉（丢绑定，而且毫无报错）。
    with _LOCK:
        chats = load(state_dir)
        if c not in chats and len(chats) >= MAX_CHATS:
            raise BindError(f"绑定数已到上限（{MAX_CHATS} 个），先取消几个")
        chats[c] = {"id": k, "ts": time.time(), "by": str(by or "")[:24]}
        _write(state_dir, chats)
        return dict(chats[c])


def clear(state_dir: Path | str, chat) -> bool:
    """取消绑定（= 跟随全局）。返回原来有没有绑。"""
    c = clean_chat(chat)
    if not c:
        return False
    with _LOCK:
        chats = load(state_dir)
        if c not in chats:
            return False
        chats.pop(c, None)
        _write(state_dir, chats)
        return True


def prune(state_dir: Path | str, valid_ids) -> list:
    """删掉指向已不存在角色的绑定；返回被删掉的会话 id 列表。"""
    ok = {str(x).strip().lower() for x in (valid_ids or [])}
    if not ok:
        # 角色库读失败时「没有一个合法 id」不等于「所有绑定都失效」：
        # 这时删了就是真丢数据，而且返回值会撒谎（说删了、其实没写盘）。
        return []
    with _LOCK:
        chats = load(state_dir)
        dead = [c for c, r in chats.items() if str(r.get("id") or "") not in ok]
        if dead and ok:
            for c in dead:
                chats.pop(c, None)
            _write(state_dir, chats)
        return dead


# ---------- 运行时的「此刻是谁」 ----------
_CUR: contextvars.ContextVar[str] = contextvars.ContextVar("klee_chat_char", default="")


def current() -> str:
    """当前 asyncio 上下文里生效的角色 id；不在处理消息时是 ""。"""
    try:
        return str(_CUR.get() or "")
    except LookupError:                     # 理论上不会：有 default
        return ""


def set_value(cid: str) -> None:
    _CUR.set(str(cid or ""))


def push(cid: str):
    """设置并返回 token，交给 `pop(token)` 还原（用 try/finally）。"""
    return _CUR.set(str(cid or ""))


def pop(token) -> None:
    try:
        _CUR.reset(token)
    except (ValueError, RuntimeError):
        # token 已经用过了 / 跨上下文 reset：退化成清空，别让 finally 里再抛一次
        _CUR.set("")
