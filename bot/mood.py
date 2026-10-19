"""角色心情：让她有「今天状态」——不是每句话都精神饱满。

三个维度，各自独立，都能被一句人话说清楚：

- **精神**（energy）：聊太多、半夜聊天会掉；睡一觉会回来。
- **心情**（mood）：被夸、被道谢会上来；被冷落、被凶会下去。
- **亲近**（closeness）：只在相处里涨，几乎不掉 —— 这是「认识多久了」的量。

为什么不直接用模型判情绪：那要每轮多一次调用，还不可复现。
这里是**确定性**的：关键词 + 时长 + 衰减，同样的一串消息算出来永远是同一个数，
所以能写回归，也能在面板上解释「为什么她现在是这个状态」。

状态存在 `state/mood.json`，按角色分。**懒衰减**：读的时候按经过的时间算，
不起后台线程（这台机器只有 4GB，能省一个线程是一个）。
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from pathlib import Path

log = logging.getLogger("klee.bot.mood")

#: 三个维度的基线（0–100）。掉到 0 不是「坏掉」，是「很累/很低落」。
BASE = {"energy": 70.0, "mood": 62.0, "closeness": 40.0}

#: 多久回到一半差距（秒）。精神靠睡，心情靠时间，亲近基本不回落。
HALF_LIFE = {"energy": 10 * 3600, "mood": 6 * 3600, "closeness": 120 * 3600}

#: 单轮的最大增减，防止一句话把状态顶到极端又弹回来。
STEP_CAP = 6.0

POSITIVE = ("谢谢", "谢啦", "喜欢你", "开心", "哈哈", "可爱", "厉害", "好棒", "太棒",
            "爱你", "抱抱", "辛苦了", "真好", "不错", "好评", "么么", "乖", "好可爱")
NEGATIVE = ("讨厌", "闭嘴", "滚", "烦", "笨", "垃圾", "生气", "难过", "失望", "无语",
            "不想理", "别说了", "走开", "恨", "stupid", "shut up")
#: 敷衍：字数极少且没有信息量的回复。这类话会让她有点失落（小幅）。
COLD = ("嗯", "哦", "噢", "额", "呃", "啊", "行", "好", "在", "？", "?", "。", "...", "…")
#: 分享自己：会让她觉得被当成朋友（亲近 +）。
SHARE = ("我今天", "我刚", "我跟你说", "我觉得", "我最近", "我喜欢", "我讨厌",
         "我的", "我梦到", "我好", "我在想", "我怕", "我希望")

LATE_NIGHT = (0, 6)          # 这两个小时还在聊：精神掉得快
MAX_CHARS = 200              # 只扫前 200 字（再长也不改变判断）

_LOCK = threading.Lock()


def _path(state_dir: Path | str) -> Path:
    return Path(state_dir) / "mood.json"


def _clamp(v, default: float = 50.0) -> float:
    """钳到 0~100；脏数据（字符串/None/NaN/超大整数）回落到调用方给的默认值。"""
    try:
        f = float(v)
    except (TypeError, ValueError, OverflowError):
        # json 里一个 1000 位的大整数会让 float() 抛 OverflowError
        return float(default)
    if f != f:                      # NaN
        return float(default)
    return max(0.0, min(100.0, f))


def _num(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError, OverflowError):
        return 0.0


def _blank(now: float) -> dict:
    return {**BASE, "ts": now, "n": 0, "last_user_ts": 0.0, "last_touch": 0.0}


def load_all(state_dir: Path | str) -> dict:
    try:
        d = json.loads(_path(state_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def get(state_dir: Path | str, cid: str, now: float | None = None) -> dict:
    """读一个角色的状态（**已经按时间衰减过**）。"""
    now = time.time() if now is None else float(now)
    cid = str(cid or "klee").strip().lower()
    row = load_all(state_dir).get(cid)
    if not isinstance(row, dict):
        row = _blank(now)
    return decay(row, now)


def decay(row: dict, now: float) -> dict:
    """按经过的时间把三个维度往基线拉。只算，不写盘。"""
    out = {k: _clamp(row.get(k, BASE[k]), BASE[k]) for k in BASE}
    ts = _num(row.get("ts")) or now
    dt = max(0.0, float(now) - ts)
    # 不活动超过 3 天就别再按秒算了：直接给基线，省得指数算出一堆零
    if dt > 3 * 86400:
        out.update(BASE)
    else:
        for k, hl in HALF_LIFE.items():
            frac = 0.5 ** (dt / hl)
            out[k] = _clamp(BASE[k] + (out[k] - BASE[k]) * frac)
    try:
        n = int(row.get("n") or 0)
    except (TypeError, ValueError):
        n = 0
    out.update(ts=float(now), n=n,
               last_user_ts=_num(row.get("last_user_ts")),
               last_touch=_num(row.get("last_touch")))
    return out


def analyse(user_text: str, reply_text: str = "", now: float | None = None) -> dict:
    """一句话对三个维度的净影响。纯函数，好测。"""
    now = time.time() if now is None else float(now)
    t = str(user_text or "")[:MAX_CHARS]
    r = str(reply_text or "")[:MAX_CHARS]
    d = {"energy": -0.6, "mood": 0.0, "closeness": 0.35}

    low = t.lower()
    pos = sum(1 for w in POSITIVE if w in low)
    neg = sum(1 for w in NEGATIVE if w in low)
    d["mood"] += 1.3 * pos - 2.4 * neg
    d["closeness"] += 0.6 * pos

    stripped = t.strip(" 　\t\n")
    # 空正文（只发了一张图、或者纯标点）不该按「冷淡」扣分：
    # 以前 `len("") <= 2` 成立，发图片也被算成敷衍。
    if stripped and (stripped in COLD or
                     (len(stripped) <= 2 and not re.search(r"[\u4e00-\u9fff]{2}", stripped))):
        # 一个字、或者干脆一个「？」——她会在意，但只一点点
        d["mood"] -= 1.0
        d["closeness"] -= 0.15

    if any(w in t for w in SHARE):
        d["closeness"] += 1.1
        d["mood"] += 0.4
    if len(stripped) >= 120:                 # 愿意打这么多字，是在认真聊
        d["mood"] += 0.8
        d["closeness"] += 0.5

    hour = time.localtime(now).tm_hour
    if LATE_NIGHT[0] <= hour < LATE_NIGHT[1]:
        d["energy"] -= 2.2
    elif 8 <= hour < 22:
        d["energy"] += 0.2                   # 白天聊聊是「有人陪」，不掉那么快

    if "!" in r or "！" in r:                # 她自己说得起劲
        d["mood"] += 0.3
    if len(r) >= 200:                        # 说了一大段，费神
        d["energy"] -= 0.4

    for k in d:
        d[k] = round(max(-STEP_CAP, min(STEP_CAP, d[k])), 2)
    return d


def apply_turn(state_dir: Path | str, cid: str, user_text: str,
               reply_text: str = "", now: float | None = None) -> dict:
    """记一轮对话：先衰减到「现在」，再加上这一轮的影响，落盘。

    返回新的状态（调用方可以直接拿去注入提示）。
    """
    now = time.time() if now is None else float(now)
    cid = str(cid or "klee").strip().lower()
    delta = analyse(user_text, reply_text, now)
    with _LOCK:
        allrows = load_all(state_dir)
        row = allrows.get(cid)
        row = decay(row if isinstance(row, dict) else _blank(now), now)
        for k in BASE:
            row[k] = _clamp(row.get(k, BASE[k]) + delta[k], BASE[k])
        row["n"] = int(row.get("n") or 0) + 1
        row["last_user_ts"] = now
        row["last_touch"] = now
        row["ts"] = now
        allrows[cid] = row
        _save(state_dir, allrows)
    return row


def _save(state_dir: Path | str, data: dict) -> None:
    p = _path(state_dir)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(f"{p.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, p)
    except OSError as e:
        log.info("心情状态写入失败（不影响回复）：%s", e)


def reset(state_dir: Path | str, cid: str) -> dict:
    cid = str(cid or "klee").strip().lower()
    with _LOCK:
        allrows = load_all(state_dir)
        allrows.pop(cid, None)
        _save(state_dir, allrows)
    return get(state_dir, cid)


def label(v: float) -> tuple:
    """(表情, 中文)。给面板和提示用同一套词，免得两边说法不一样。"""
    v = float(v)
    if v >= 85:
        return "🤩", "超有精神"
    if v >= 70:
        return "😊", "挺开心"
    if v >= 55:
        return "🙂", "还不错"
    if v >= 40:
        return "😐", "平平"
    if v >= 25:
        return "😔", "有点低落"
    return "😞", "很低落"


def energy_label(v: float) -> tuple:
    v = float(v)
    if v >= 80:
        return "⚡", "满电"
    if v >= 60:
        return "🔋", "有劲"
    if v >= 40:
        return "🪫", "一般"
    if v >= 20:
        return "😪", "有点累"
    return "🥱", "困死了"


def closeness_label(v: float) -> tuple:
    v = float(v)
    if v >= 80:
        return "💞", "很亲近"
    if v >= 60:
        return "🤝", "熟"
    if v >= 40:
        return "🙂", "还不错"
    if v >= 20:
        return "👋", "刚认识"
    return "🫥", "还很生"


def summary(state_dir: Path | str, cid: str, now: float | None = None) -> dict:
    """面板用的一份完整描述。"""
    st = get(state_dir, cid, now)
    me, ml = label(st["mood"])
    ee, el = energy_label(st["energy"])
    ce, cl = closeness_label(st["closeness"])
    return {"ok": True, "character": str(cid).strip().lower(),
            "energy": round(st["energy"], 1), "mood": round(st["mood"], 1),
            "closeness": round(st["closeness"], 1),
            "emoji": me, "mood_text": ml,
            "energy_emoji": ee, "energy_text": el,
            "closeness_emoji": ce, "closeness_text": cl,
            "turns": int(st.get("n") or 0),
            "ts": st.get("ts"), "base": dict(BASE)}


def hint(state_dir: Path | str, cid: str, now: float | None = None) -> str:
    """给模型的一句话。**只在真的偏离基线时给**，正常状态不加噪声。"""
    st = get(state_dir, cid, now)
    bits = []
    if st["energy"] <= 38:
        bits.append("你现在有点累了，说话会更短、更懒一点，别硬撑着长篇大论")
    elif st["energy"] >= 88:
        bits.append("你现在精神很好，说话会更有活力")
    if st["mood"] <= 35:
        bits.append("你心情不太好，语气会淡一点、不那么热情，但不会迁怒对方")
    elif st["mood"] >= 85:
        bits.append("你今天心情很好，会主动多说两句")
    if st["closeness"] <= 22:
        bits.append("你们还不太熟，别太自来熟")
    elif st["closeness"] >= 78:
        bits.append("你们已经很熟了，可以像老朋友一样说话")
    if not bits:
        return ""
    return "【你现在的状态】" + "；".join(bits) + "。（这是状态，不是让你念出来的设定）"
