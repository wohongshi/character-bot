"""主动消息：她自己开个话头。

原则（写在这里是为了以后别手滑放宽）：
- **默认全关**，一个会话都没配置就什么都不会发。
- 只在「很久没聊」之后才考虑，且受 `per_day` / `min_gap_h` 双重限制。
- 会话策略说「完全不理」或正在静默时段 → 不发（复用 chatpolicy 的判定）。
- 连续失败 3 次就停，面板上要能看到原因 —— 别对着一个掉线的账号每 5 分钟重试。
- 每一条都进 `state/proactive_log.jsonl`（封顶 5000 行），发了什么可查。

`decide()` 是纯函数：不读文件、不发消息、不动时间，测试直接喂参数。
"""

from __future__ import annotations

import json
import os
import random
import threading
import time
from pathlib import Path

_LOCK = threading.RLock()      # 巡检线程与 HTTP 线程都会读改写同一个文件

MAX_CHATS = 50
LOG_MAX_LINES = 5000
FAIL_STOP = 3                  # 连续失败这么多次就自动停
TICK_S = 300                   # 巡检间隔（面板上「概率」是按这个间隔算的）

DEFAULTS = {
    "on": False,
    "per_day": 2,              # 每天最多几条
    "min_gap_h": 6.0,          # 两条之间至少隔多久
    "idle_h": 12.0,            # 对方多久没说话才考虑
    "chance": 0.35,            # 每次巡检的命中概率（5 分钟一次）
    "note": "",
}

# 面板能改的范围（防止手填 999 条/天）
LIMITS = {
    "per_day": (0, 20), "min_gap_h": (0.5, 168.0),
    "idle_h": (0.5, 720.0), "chance": (0.0, 1.0),
}

INTENTS = {
    "morning": ("早上", "刚醒，问问对方今天什么安排"),
    "noon": ("中午", "提一句吃饭，或者分享你在做什么"),
    "afternoon": ("下午", "随便聊一句，别问问题堆成审问"),
    "evening": ("晚上", "问问今天过得怎么样"),
    "night": ("深夜", "催对方睡觉，或者自己也困了"),
}


def _path(state_dir: Path | str) -> Path:
    return Path(state_dir) / "proactive.json"


def _clamp(cfg: dict) -> dict:
    out = dict(DEFAULTS)
    for k, (lo, hi) in LIMITS.items():
        try:
            v = float(cfg.get(k, DEFAULTS[k]))
        except (TypeError, ValueError):
            v = float(DEFAULTS[k])
        out[k] = max(lo, min(hi, v))
    out["per_day"] = int(round(out["per_day"]))
    out["on"] = bool(cfg.get("on"))
    out["note"] = str(cfg.get("note") or "")[:40]
    # 运行期字段原样带过来（不参与 clamp）
    for k in ("last_sent_ts", "sent_day", "sent_today", "fails", "last_error", "last_ts"):
        if k in cfg:
            out[k] = cfg[k]
    # 跨天就把「今天发了多少」归零：面板读的是 _clamp() 的结果，
    # 不在这里归零的话，过了零点还会显示「今天 2/2 条」——正是那种
    # 「为什么她不说话」的误导信号。
    if out.get("sent_day") != _day(time.time()):
        out["sent_today"] = 0
    return out


def load(state_dir: Path | str) -> dict:
    with _LOCK:
        try:
            d = json.loads(_path(state_dir).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"chats": {}}
    chats = d.get("chats") if isinstance(d, dict) else None
    return {"chats": chats if isinstance(chats, dict) else {}}


def save(state_dir: Path | str, data: dict) -> None:
    """整份写回。临时文件名带 pid+线程号：两个线程共用 `.json.tmp` 时，
    一个可能把另一个还没写完的文件搬走（→ 半截 JSON → 读成空表 → 配置全没）。"""
    p = _path(state_dir)
    with _LOCK:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(f"{p.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        try:
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(p)
        finally:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass


def get_chat(state_dir: Path | str, chat_id: str) -> dict:
    """读一个会话的配置（**已经过 `_clamp`**：跨天计数归零、参数范围也夹过）。

    直接返回磁盘原文会让调用方看到「昨天发了 2 条」而 decide() 看到 0。
    """
    with _LOCK:
        raw = load(state_dir)["chats"].get(str(chat_id)) or {}
    return _clamp(raw) if raw else {}


def set_chat(state_dir: Path | str, chat_id: str, *, on=None, per_day=None,
             min_gap_h=None, idle_h=None, chance=None, note=None) -> dict:
    """按字段合并：面板一次只改一个字段，整条替换会把别的设置悄悄恢复默认。"""
    chat_id = str(chat_id or "").strip()
    if not chat_id:
        raise ValueError("缺少会话 id")
    with _LOCK:                       # 整段读-改-写：否则并发保存会互相覆盖
        return _set_chat_locked(state_dir, chat_id, on=on, per_day=per_day,
                               min_gap_h=min_gap_h, idle_h=idle_h, chance=chance,
                               note=note)


def _set_chat_locked(state_dir, chat_id, *, on=None, per_day=None, min_gap_h=None,
                     idle_h=None, chance=None, note=None) -> dict:
    data = load(state_dir)
    chats = data["chats"]
    existed = chat_id in chats
    cur = _clamp(chats.get(chat_id) or {})
    for k, v in (("on", on), ("per_day", per_day), ("min_gap_h", min_gap_h),
                 ("idle_h", idle_h), ("chance", chance), ("note", note)):
        if v is None:
            continue
        cur[k] = v
    cur = _clamp(cur)
    # **已经存在的行永远不自动删**：面板的「○ 关」和逐字段保存都不能顺手删配置
    # （以前全默认就 pop —— 点一下开关、或把一个数字改回默认值，整行就没了）。
    # 只有「本来没有、且这次全是默认值」才不占位；删除走 remove_chat()。
    if (not existed and not cur.get("on")
            and not any(cur.get(k) != DEFAULTS[k] for k in
                        ("per_day", "min_gap_h", "idle_h", "chance", "note"))):
        chats.pop(chat_id, None)
    else:
        if len(chats) >= MAX_CHATS and not existed:
            raise ValueError(f"最多 {MAX_CHATS} 个会话")
        chats[chat_id] = cur
    save(state_dir, data)
    return chats.get(chat_id) or {}


def remove_chat(state_dir: Path | str, chat_id: str) -> bool:
    with _LOCK:
        data = load(state_dir)
        hit = data["chats"].pop(str(chat_id), None) is not None
        if hit:
            save(state_dir, data)
        return hit


def _day(now: float) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(now))


def decide(cfg: dict, *, now: float, last_user_ts: float = 0.0,
           blocked: str = "", rng=None) -> dict:
    """该不该发。返回 `{send, reason, sent_today}`；`reason` 是给面板看的人话。"""
    rng = rng or random
    c = _clamp(cfg or {})
    if not c.get("on"):
        return {"send": False, "reason": "没开", "sent_today": 0}
    if blocked:
        return {"send": False, "reason": blocked, "sent_today": 0}
    if int(c.get("fails") or 0) >= FAIL_STOP:
        return {"send": False, "reason": f"连续失败 {int(c['fails'])} 次，已暂停", "sent_today": 0}
    today = _day(now)
    sent_today = int(c.get("sent_today") or 0) if c.get("sent_day") == today else 0
    per_day = int(c.get("per_day") or 0)
    if per_day <= 0:
        return {"send": False, "reason": "每天上限为 0（已关闭）", "sent_today": sent_today}
    if sent_today >= per_day:
        return {"send": False, "reason": f"今天已经发了 {sent_today} 条", "sent_today": sent_today}
    last_sent = float(c.get("last_sent_ts") or 0)
    gap = float(c.get("min_gap_h") or 0) * 3600
    if last_sent and now - last_sent < gap:
        left = int((gap - (now - last_sent)) / 60) + 1
        return {"send": False, "reason": f"距离上一条还差 {left} 分钟", "sent_today": sent_today}
    idle = float(c.get("idle_h") or 0) * 3600
    if last_user_ts and now - last_user_ts < idle:
        return {"send": False, "reason": "刚聊过，不用她主动找", "sent_today": sent_today}
    if last_user_ts <= 0:
        return {"send": False, "reason": "还没有对话记录，不主动", "sent_today": sent_today}
    if rng.random() > float(c.get("chance") or 0):
        return {"send": False, "reason": "这次没轮到", "sent_today": sent_today}
    return {"send": True, "reason": "发", "sent_today": sent_today}


def pick_intent(now: float) -> dict:
    """按小时挑一个「为什么找她」的角度（早上问候 / 晚上催睡…）。

    **纯函数按小时决定**，没有随机：以前留了个 `rng` 参数但根本没用，
    调用方以为有随机化。
    """
    h = time.localtime(now).tm_hour
    if 5 <= h < 11:
        key = "morning"
    elif 11 <= h < 14:
        key = "noon"
    elif 14 <= h < 18:
        key = "afternoon"
    elif 18 <= h < 23:
        key = "evening"
    else:
        key = "night"
    label, hint = INTENTS[key]
    return {"key": key, "label": label, "hint": hint}


def build_messages(persona: str, *, intent: dict, name: str = "", recent: str = "",
                   now: float | None = None) -> list:
    """主动开口的提示词：人格 + 「这次是你先说话」+ 最近聊到哪。"""
    now = time.time() if now is None else now
    when = time.strftime("%m月%d日 %H:%M", time.localtime(now))
    sys_text = (str(persona or "") +
                "\n\n【QQ 聊天规则】直接说话，别用 Markdown；一次回复 1~3 句短句，"
                "像在手机上聊天；不知道就直说不知道。")
    sys_text += (f"\n【这次是你先开口】现在是{when}（{intent.get('label')}）。"
                 "没有新消息，是你自己想找对方说话。"
                 f"{intent.get('hint') or ''}。"
                 "只说 1 句，别提「主动」「好久没聊」这类元信息，也别解释为什么突然找。")
    if recent:
        sys_text += "\n【最近的对话（接着聊，不要复述）】\n" + str(recent)[:800]
    if name:
        sys_text += f"\n对方昵称：{name}"
    return [{"role": "system", "content": sys_text},
            {"role": "user", "content": "（没有新消息。你想说点什么？）"}]


def mark_sent(state_dir: Path | str, chat_id: str, text: str, *, now: float | None = None,
              intent: str = "") -> dict:
    now = time.time() if now is None else now
    with _LOCK:
        return _mark_sent_locked(state_dir, chat_id, text, now=now, intent=intent)


def _mark_sent_locked(state_dir, chat_id, text, *, now: float, intent: str) -> dict:
    data = load(state_dir)
    cur = _clamp(data["chats"].get(str(chat_id)) or {})
    today = _day(now)
    if cur.get("sent_day") != today:
        cur["sent_today"] = 0
    cur.update({"sent_day": today, "sent_today": int(cur.get("sent_today") or 0) + 1,
                "last_sent_ts": now, "fails": 0, "last_error": "", "last_ts": now})
    data["chats"][str(chat_id)] = cur
    save(state_dir, data)
    _log(state_dir, {"ts": now, "chat_id": str(chat_id), "text": str(text or "")[:400],
                     "event": "sent", "intent": str(intent or "")})
    return cur


def mark_unknown(state_dir: Path | str, chat_id: str, note: str,
                 *, now: float | None = None) -> dict:
    """结果未知（比如发送超时）：占住「刚发过」的名额，但**不算失败**。

    否则会在最小间隔内再发一条（可能重复），或者记成失败把功能自动暂停。
    """
    now = time.time() if now is None else now
    with _LOCK:
        data = load(state_dir)
        cur = _clamp(data["chats"].get(str(chat_id)) or {})
        cur.update({"last_sent_ts": now, "last_error": str(note or "")[:200], "last_ts": now})
        data["chats"][str(chat_id)] = cur
        save(state_dir, data)
        _log(state_dir, {"ts": now, "chat_id": str(chat_id), "event": "unknown",
                         "error": str(note or "")[:200]})
        return cur


def mark_fail(state_dir: Path | str, chat_id: str, error: str, *, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    with _LOCK:                      # 整段读-改-写（只锁 load 等于没锁）
        data = load(state_dir)
        cur = _clamp(data["chats"].get(str(chat_id)) or {})
        cur.update({"fails": int(cur.get("fails") or 0) + 1,
                    "last_error": str(error or "")[:200], "last_ts": now})
        data["chats"][str(chat_id)] = cur
        save(state_dir, data)
        _log(state_dir, {"ts": now, "chat_id": str(chat_id), "event": "fail",
                         "error": str(error or "")[:200]})
        return cur


def _log(state_dir: Path | str, row: dict) -> None:
    """追加日志并封顶。

    读写都在 `_LOCK` 里：巡检线程和 HTTP 线程都会写，各写各的会互相覆盖。
    每次发送都重写整个文件（5000 行 ≈ 1MB 级，一天也就几次），
    换来的是一条都不会丢。
    """
    p = Path(state_dir) / "proactive_log.jsonl"
    with _LOCK:
        try:
            lines = p.read_text(encoding="utf-8").splitlines() if p.exists() else []
        except OSError:
            lines = []
        lines.append(json.dumps(row, ensure_ascii=False))
        if len(lines) > LOG_MAX_LINES:
            lines = lines[-LOG_MAX_LINES:]
        tmp = p.with_name(f"{p.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
            tmp.replace(p)
        except OSError:
            pass
        finally:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass


def read_log(state_dir: Path | str, limit: int = 20) -> list:
    p = Path(state_dir) / "proactive_log.jsonl"
    try:
        lines = p.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in lines[-max(1, int(limit or 20)):]:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            out.append(row)
    return list(reversed(out))


def last_user_map(path: Path | str, *, tail_bytes: int = 8_388_608) -> dict:
    """一次扫完文件尾部，返回 `{chat_id: 最后一次用户发言的 ts}`。

    巡检要问 50 个会话，逐个 `last_user_ts()` 等于把同一个文件读 50 遍；
    而且 `deque(f, maxlen=n)` 并不会少读 —— 它照样从头读到尾。

    窗口默认 8 MB（本机一天流水也就几百 KB）：窗口太小的话，「很久没聊」的会话
    会被别人刷出窗口 → 取不到时间 → 被判成「没有对话记录」→ 主动消息恰好对
    最该发的那批人失效。文件比窗口还大时宁可漏判（不发），也不猜。
    """
    try:
        size = os.path.getsize(path)
    except OSError:
        return {}
    out: dict = {}
    try:
        with open(path, "rb") as f:
            start = max(0, size - max(4096, int(tail_bytes or 0)))
            f.seek(start)
            blob = f.read()
    except OSError:
        return {}
    text = blob.decode("utf-8", "replace")
    if start > 0:                       # 第一行可能被切掉一半
        nl = text.find("\n")
        text = text[nl + 1:] if nl >= 0 else ""
    for line in text.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict) or row.get("role") != "user":
            continue
        cid = str(row.get("chat_id") or "")
        if not cid:
            continue
        try:
            ts = float(row.get("ts") or 0)
        except (TypeError, ValueError):
            continue
        if ts > float(out.get(cid) or 0):
            out[cid] = ts
    return out


def last_user_ts(path: Path | str, chat_id: str, *, tail_bytes: int = 8_388_608) -> float:
    """这个会话最后一次「用户说话」的时间（找不到返回 0）。"""
    return float(last_user_map(path, tail_bytes=tail_bytes).get(str(chat_id or "")) or 0)
