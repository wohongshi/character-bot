"""关键词规则：在「交给模型」之前先过一遍「看到什么就怎么办」。

为什么需要它
------------
群里九成的消息不需要模型：打招呼、问营业时间、发广告、复读。
每一条都过一次大模型既慢又贵，还容易答得不对味。规则是**确定性**的那一层：

- `reply`   —— 固定回一句（可以带 `{user}` 占位）；
- `ignore`  —— 干脆不回（比"回一句我不想聊"更自然，也省一次模型调用）；
- `command` —— 当作某条插件命令来处理（`.骰子 2d6`）。

规则只看**外部平台进来的消息**（QQ/飞书/微信/Webhook）。面板里你直接对她说话
不走规则 —— 那里"不回"看起来就是个 bug。
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from pathlib import Path

log = logging.getLogger("klee.bot.rules")

MAX_RULES = 60
MAX_PATTERN = 200
MAX_REPLY = 500
MATCHES = ("contains", "equals", "prefix", "regex")
ACTIONS = ("reply", "ignore", "command")
SCOPES = ("all", "group", "dm")

# 可重入：save() 自己会拿这把锁，外层还得包住整个「读-改-写」。
# 用普通 Lock 会自锁死（bump → save → 再拿一次）。
_LOCK = threading.RLock()


def _path(state_dir: Path | str) -> Path:
    return Path(state_dir) / "rules.json"


def _num(v, cast):
    try:
        return cast(v or 0)
    except (TypeError, ValueError):
        return cast(0)


def normalize(rule: dict, *, rid: str = "") -> dict:
    """校验并补齐一条规则；不合法抛 ValueError（面板要看到具体原因）。"""
    if not isinstance(rule, dict):
        raise ValueError("规则必须是对象")
    match = str(rule.get("match") or "contains").strip().lower()
    if match not in MATCHES:
        raise ValueError(f"match 只能是 {'/'.join(MATCHES)}")
    pattern = str(rule.get("pattern") or "").strip()
    if not pattern:
        raise ValueError("关键词不能为空")
    if len(pattern) > MAX_PATTERN:
        raise ValueError(f"关键词最长 {MAX_PATTERN} 字")
    if match == "regex":
        try:
            re.compile(pattern)
        except re.error as e:
            raise ValueError(f"正则写错了：{e}") from e
    action = str(rule.get("action") or "reply").strip().lower()
    if action not in ACTIONS:
        raise ValueError(f"action 只能是 {'/'.join(ACTIONS)}")
    reply = str(rule.get("reply") or "").strip()
    command = str(rule.get("command") or "").strip()
    if action == "reply" and not reply:
        raise ValueError("「回一句」得填内容")
    if action == "command" and not command:
        raise ValueError("「跑命令」得填命令，比如 .骰子 2d6")
    if len(reply) > MAX_REPLY:
        raise ValueError(f"回复最长 {MAX_REPLY} 字")
    scope = str(rule.get("scope") or "all").strip().lower()
    if scope not in SCOPES:
        raise ValueError(f"scope 只能是 {'/'.join(SCOPES)}")
    return {
        "id": rid or str(rule.get("id") or "").strip(),
        "name": str(rule.get("name") or "").strip()[:40] or pattern[:20],
        "match": match,
        "pattern": pattern,
        "action": action,
        "reply": reply[:MAX_REPLY],
        "command": command[:MAX_PATTERN],
        "scope": scope,
        "chat": str(rule.get("chat") or "").strip()[:64],
        "enabled": bool(rule.get("enabled", True)),
        # 手改过的 rules.json 里这两个字段可能是字符串/None：int("abc") 抛 TypeError，
        # 而 normalize 的调用方只挡了 ValueError —— 会让整个面板列表 500。
        "hits": _num(rule.get("hits"), int),
        "last_ts": _num(rule.get("last_ts"), float),
    }


def load(state_dir: Path | str) -> list:
    try:
        d = json.loads(_path(state_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    items = d.get("items") if isinstance(d, dict) else None
    out = []
    for i, it in enumerate(items if isinstance(items, list) else []):
        try:
            out.append(normalize(it, rid=str(it.get("id") or f"r{i + 1}")))
        except (ValueError, AttributeError):
            continue                     # 坏规则跳过，不要连累其它规则
    return out


def save(state_dir: Path | str, items: list) -> list:
    clean = [normalize(x, rid=str(x.get("id") or "")) for x in (items or [])]
    if len(clean) > MAX_RULES:
        raise ValueError(f"规则最多 {MAX_RULES} 条")
    ids = [x["id"] for x in clean if x["id"]]
    if len(ids) != len(set(ids)):
        raise ValueError("规则 id 重复了")
    p = _path(state_dir)
    with _LOCK:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(f"{p.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_text(json.dumps({"items": clean}, ensure_ascii=False, indent=1),
                       encoding="utf-8")
        os.replace(tmp, p)
    return clean


def _next_id(items: list) -> str:
    used = {str(x.get("id") or "") for x in items}
    for i in range(1, MAX_RULES + 1):
        if f"r{i}" not in used:
            return f"r{i}"
    return f"r{int(time.time())}"


def add(state_dir: Path | str, rule: dict) -> dict:
    # 整个「读 → 改 → 写」要在同一把锁里：命中计数（bump）也在改同一个文件，
    # 中间插进一次 save 就会把这里刚存的规则冲掉。
    with _LOCK:
        items = load(state_dir)
        if len(items) >= MAX_RULES:
            raise ValueError(f"规则最多 {MAX_RULES} 条")
        rec = normalize(rule, rid=_next_id(items))
        items.append(rec)
        save(state_dir, items)
    return rec


def update(state_dir: Path | str, rid: str, patch: dict) -> dict:
    with _LOCK:
        items = load(state_dir)
        for i, it in enumerate(items):
            if it["id"] != rid:
                continue
            merged = {**it, **(patch or {}), "id": rid}
            # hits/last_ts 由运行时维护，面板改内容不该把它们清零
            merged["hits"] = it["hits"]
            merged["last_ts"] = it["last_ts"]
            items[i] = normalize(merged, rid=rid)
            save(state_dir, items)
            return items[i]
    raise ValueError(f"没有这条规则：{rid}")


def delete(state_dir: Path | str, rid: str) -> bool:
    with _LOCK:
        items = load(state_dir)
        left = [x for x in items if x["id"] != rid]
        if len(left) == len(items):
            return False
        save(state_dir, left)
    return True


# 命中计数在内存里攒，最多每 3 秒落一次盘。
# 一条「拦广告」的 ignore 规则在刷屏时会被每条消息命中：每次都 load+save 就是
# 每秒几十次小文件写入，白白磨损 SD 卡，而这个数字只是个参考值。
_BUMP_LOCK = threading.Lock()
_BUMP: dict = {}
BUMP_FLUSH_S = 3.0


def bump(state_dir: Path | str, rid: str) -> None:
    """记一次命中（面板上「命中 N 次」比什么都直观）。失败无所谓。"""
    key = str(_path(state_dir))
    with _BUMP_LOCK:
        rec = _BUMP.setdefault(key, {"n": {}, "at": 0.0})
        rec["n"][rid] = int(rec["n"].get(rid) or 0) + 1
        now = time.time()
        if now - float(rec["at"]) < BUMP_FLUSH_S:
            return
        rec["at"] = now
        counts = dict(rec["n"])
        rec["n"] = {}
    try:
        # 读-改-写必须整段上锁：面板的 add/update/delete 走的是同一条 load→save。
        # 只锁写的那一半，面板在 bump 的 load 和 save 之间存了一条新规则，
        # 就会被这里的 save 覆盖掉（而且用户看到的是「保存了但没生效」）。
        with _LOCK:
            items = load(state_dir)
            changed = False
            for it in items:
                c = int(counts.get(it["id"]) or 0)
                if c:
                    it["hits"] = int(it.get("hits") or 0) + c
                    it["last_ts"] = time.time()
                    changed = True
            if changed:
                save(state_dir, items)
    except Exception as e:                                        # noqa: BLE001
        log.info("规则命中计数失败：%s", e)


def match(items: list, text: str, *, is_group: bool, chat_id: str = "") -> dict | None:
    """按顺序找第一条命中的规则。没有就返回 None（继续走模型）。"""
    t = str(text or "").strip()
    if not t:
        return None
    for r in items or []:
        if not r.get("enabled", True):
            continue
        scope = r.get("scope") or "all"
        if scope == "group" and not is_group:
            continue
        if scope == "dm" and is_group:
            continue
        if r.get("chat") and r["chat"] not in (chat_id, ""):
            continue
        pat = str(r.get("pattern") or "")
        kind = r.get("match") or "contains"
        hit = False
        if kind == "contains":
            hit = pat in t
        elif kind == "equals":
            hit = t == pat
        elif kind == "prefix":
            hit = t.startswith(pat)
        elif kind == "regex":
            try:
                hit = re.search(pat, t) is not None
            except re.error:
                hit = False
        if hit:
            return r
    return None


def render_reply(rule: dict, *, user: str = "") -> str:
    """把 `{user}` 换掉；不认识的占位符原样留着（别把用户写的花括号吃了）。"""
    return str(rule.get("reply") or "").replace("{user}", str(user or "你"))
