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
import random
import time
from pathlib import Path

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
    return out


def load(state_dir: Path | str) -> dict:
    try:
        d = json.loads(_path(state_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"chats": {}}
    chats = d.get("chats") if isinstance(d, dict) else None
    return {"chats": chats if isinstance(chats, dict) else {}}


def save(state_dir: Path | str, data: dict) -> None:
    p = _path(state_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)


def get_chat(state_dir: Path | str, chat_id: str) -> dict:
    return load(state_dir)["chats"].get(str(chat_id)) or {}


def set_chat(state_dir: Path | str, chat_id: str, *, on=None, per_day=None,
             min_gap_h=None, idle_h=None, chance=None, note=None) -> dict:
    """按字段合并：面板一次只改一个字段，整条替换会把别的设置悄悄恢复默认。"""
    chat_id = str(chat_id or "").strip()
    if not chat_id:
        raise ValueError("缺少会话 id")
    data = load(state_dir)
    chats = data["chats"]
    cur = _clamp(chats.get(chat_id) or {})
    for k, v in (("on", on), ("per_day", per_day), ("min_gap_h", min_gap_h),
                 ("idle_h", idle_h), ("chance", chance), ("note", note)):
        if v is None:
            continue
        cur[k] = v
    cur = _clamp(cur)
    if not cur.get("on") and not any(cur.get(k) != DEFAULTS[k] for k in
                                     ("per_day", "min_gap_h", "idle_h", "chance", "note")):
        chats.pop(chat_id, None)          # 全是默认值 → 不占位
    else:
        if len(chats) >= MAX_CHATS and chat_id not in chats:
            raise ValueError(f"最多 {MAX_CHATS} 个会话")
        chats[chat_id] = cur
    save(state_dir, data)
    return chats.get(chat_id) or {}


def remove_chat(state_dir: Path | str, chat_id: str) -> bool:
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
    if sent_today >= int(c.get("per_day") or 0):
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


def pick_intent(now: float, rng=None) -> dict:
    """按小时挑一个「为什么找她」的角度（早上问候 / 晚上催睡…）。"""
    rng = rng or random
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


def mark_fail(state_dir: Path | str, chat_id: str, error: str, *, now: float | None = None) -> dict:
    now = time.time() if now is None else now
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
    """追加日志并封顶（直接读全文再截断：文件到 5000 行也就 1MB 级）。"""
    p = Path(state_dir) / "proactive_log.jsonl"
    try:
        lines = p.read_text(encoding="utf-8").splitlines() if p.exists() else []
    except OSError:
        lines = []
    lines.append(json.dumps(row, ensure_ascii=False))
    if len(lines) > LOG_MAX_LINES:
        lines = lines[-LOG_MAX_LINES:]
    try:
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
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


def last_user_ts(path: Path | str, chat_id: str, *, limit: int = 4000) -> float:
    """从消息流尾部找这个会话最后一次「用户说话」的时间（找不到返回 0）。"""
    from collections import deque

    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            tail = deque(f, maxlen=max(1, int(limit or 4000)))
    except OSError:
        return 0.0
    chat_id = str(chat_id or "")
    for line in reversed(tail):
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if (isinstance(row, dict) and row.get("role") == "user"
                and str(row.get("chat_id") or "") == chat_id):
            try:
                return float(row.get("ts") or 0)
            except (TypeError, ValueError):
                return 0.0
    return 0.0
