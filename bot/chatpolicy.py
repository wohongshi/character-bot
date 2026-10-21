"""按会话的策略：每个群/私聊可以单独设「多主动」和「几点闭嘴」。

为什么不能只有一个全局开关
--------------------------
`group_policy` 是全局的：设成 mention，所有群都得 @ 才应；设成 open，
所有群都变成话痨。实际使用里这两种群是同时存在的 —— 一个只有自己和朋友的
小群想让她自然加入，一个几百人的大群只该在被叫到时说话。所以策略要按会话走。

另外「静默时段」是刚需：半夜三点群里有人说话，不该把整段对话跑一遍模型
（既吵又费 token）。静默期间**命令照常**（那是明确的意图），只是不聊天。

存放：`state/chat_policy.json`

    {"chats": {"group:123456": {"mode": "open", "quiet": ["23:30", "07:00"],
                                "quiet_mention": false, "review": true,
                                "note": "小群"}}}

- `mode`：inherit（跟全局）/ open / mention / disabled
- `quiet`：`["HH:MM","HH:MM"]`，跨零点也支持（from > to）
- `quiet_mention`：静默时被 @ 还回不回（默认不回 —— 否则「静默」没意义）
- `review`：这个会话的回复先存草稿等人工确认（不写 = 跟全局 `review_on`；
  显式 `false` 就是「这个群不用审」—— 所以它必须能和三态区分开）

全是纯函数 + 一个原子写，`resolve()` 不做 I/O 之外的副作用。
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path

MODES = ("inherit", "open", "mention", "disabled")
MODES_CN = {"inherit": "跟随全局", "open": "群里每条都看", "mention": "被 @ 才应", "disabled": "完全不理"}
MAX_CHATS = 200

_LOCK = threading.RLock()
FILE = "chat_policy.json"
_HHMM = re.compile(r"^(\d{1,2}):(\d{2})$")


def path_for(state_dir: Path | str) -> Path:
    return Path(state_dir) / FILE


def _minutes(s: str) -> int | None:
    m = _HHMM.match(str(s or "").strip())
    if not m:
        return None
    hh, mm = int(m.group(1)), int(m.group(2))
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        return None
    return hh * 60 + mm


def _clean(row: dict) -> dict:
    if not isinstance(row, dict):
        return {}
    out: dict = {}
    mode = str(row.get("mode") or "inherit").strip().lower()
    out["mode"] = mode if mode in MODES else "inherit"
    quiet = row.get("quiet")
    if isinstance(quiet, (list, tuple)) and len(quiet) == 2:
        a, b = _minutes(quiet[0]), _minutes(quiet[1])
        if a is not None and b is not None and a != b:
            out["quiet"] = [f"{a // 60:02d}:{a % 60:02d}", f"{b // 60:02d}:{b % 60:02d}"]
    if "quiet_mention" in row:
        out["quiet_mention"] = bool(row.get("quiet_mention"))
    if "review" in row:
        out["review"] = bool(row.get("review"))
    note = str(row.get("note") or "").strip()[:40]
    if note:
        out["note"] = note
    return out


def _is_default(row: dict) -> bool:
    """整条都是默认值 = 等于没设（读和写用同一个判据，别只有一边过滤）。

    否则「清空」会留下一个 `{"mode": "inherit", "quiet_mention": false}` 的空壳：
    面板上看着还在，实际上什么都没设。
    """
    return (not row
            or (row.get("mode", "inherit") == "inherit"
                and not row.get("quiet")
                and not row.get("quiet_mention")
                and "review" not in row
                and not row.get("note")))


def load(state_dir: Path | str) -> dict:
    p = path_for(state_dir)
    try:
        d = json.loads(p.read_text(encoding="utf-8") or "{}")
    except (OSError, ValueError):
        return {"chats": {}}
    chats = d.get("chats") if isinstance(d, dict) else None
    out = {"chats": {}}
    if isinstance(chats, dict):
        for k, v in chats.items():
            clean = _clean(v)
            if not _is_default(clean):
                out["chats"][str(k)] = clean
    return out


def save(state_dir: Path | str, data: dict) -> dict:
    p = path_for(state_dir)
    chats = (data or {}).get("chats") if isinstance(data, dict) else {}
    chats = {str(k): _clean(v) for k, v in (chats or {}).items() if not _is_default(_clean(v))}
    if len(chats) > MAX_CHATS:
        raise ValueError(f"最多给 {MAX_CHATS} 个会话单独设策略")
    p.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        tmp = p.with_name(f"{p.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_text(json.dumps({"chats": chats}, ensure_ascii=False, indent=1),
                       encoding="utf-8")
        os.replace(tmp, p)
    return {"chats": chats}


def set_chat(state_dir: Path | str, chat_id: str, **patch) -> dict:
    """设一条（`mode=""` / `quiet=[]` = 清掉这一项）。"""
    chat_id = str(chat_id or "").strip()
    if not chat_id:
        raise ValueError("缺会话 ID")
    # 读-改-写整体串行（同 cmdpolicy：并发保存会丢别人的策略）
    with _LOCK:
        return _set_chat_locked(state_dir, chat_id, patch)


def _set_chat_locked(state_dir: Path | str, chat_id: str, patch: dict) -> dict:
    data = load(state_dir)
    row = dict(data["chats"].get(chat_id) or {})
    if "mode" in patch:
        mode = str(patch.get("mode") or "inherit").strip().lower()
        if mode not in MODES:
            raise ValueError(f"模式只能是 {'/'.join(MODES)}")
        row["mode"] = mode
    if "quiet" in patch:
        q = patch.get("quiet")
        if q in (None, "", [], ()):
            row.pop("quiet", None)
        else:
            cleaned = _clean({"quiet": q, "mode": row.get("mode", "inherit")})
            if "quiet" not in cleaned:
                raise ValueError("静默时段要写成 [\"23:30\", \"07:00\"]")
            row["quiet"] = cleaned["quiet"]
    if "quiet_mention" in patch:
        row["quiet_mention"] = bool(patch.get("quiet_mention"))
    if "review" in patch:
        q = patch.get("review")
        if q in (None, ""):
            row.pop("review", None)          # 回到「跟全局」
        else:
            row["review"] = bool(q)
    if "note" in patch:
        row["note"] = str(patch.get("note") or "").strip()[:40]
    if _is_default(_clean(row)):
        data["chats"].pop(chat_id, None)
    else:
        data["chats"][chat_id] = row
    return save(state_dir, data)


def in_quiet(quiet, now: float | None = None) -> bool:
    """现在是不是落在静默时段里（支持跨零点）。"""
    if not quiet or len(quiet) != 2:
        return False
    a, b = _minutes(quiet[0]), _minutes(quiet[1])
    if a is None or b is None:
        return False
    t = time.localtime(time.time() if now is None else now)
    cur = t.tm_hour * 60 + t.tm_min
    if a < b:
        return a <= cur < b
    return cur >= a or cur < b        # 跨零点：23:30 → 07:00


def blocks_all(pol: dict, *, is_group: bool) -> bool:
    """这条消息是否**完全不处理**（连命令都不接）。

    规则只有一条，容器（runtime）和面板（simulator）共用：
    - 群里：`disabled` 就是完全不理；
    - 私聊：只有**显式**给这个会话设过 `disabled` 才认 ——
      全局 `group_policy` 的名字和文档都是「群聊策略」，把它设成 disabled
      是想关掉群聊、保留私聊，不能连私聊一起哑掉。
    """
    if pol.get("mode") != "disabled":
        return False
    return bool(is_group) or pol.get("configured", "inherit") != "inherit"


def resolve(state_dir: Path | str, chat_id: str, base_mode: str = "mention",
            now: float | None = None, data: dict | None = None) -> dict:
    """这个会话现在该用什么策略。

    返回 `{mode, global_mode, quiet, quiet_mention, why}`：
    - `mode` 是**生效**的模式（inherit 会被换成全局值）；
    - `quiet` 表示此刻在静默时段内；
    - `why` 是给面板/模拟器看的一句话。
    """
    row = ((data or load(state_dir)).get("chats") or {}).get(str(chat_id or "")) or {}
    gm = str(base_mode or "mention").lower()
    mode = str(row.get("mode") or "inherit")
    eff = gm if mode in ("inherit", "") else mode
    quiet = in_quiet(row.get("quiet"), now)
    why = []
    if mode != "inherit":
        why.append("单独设成「" + MODES_CN.get(eff, eff) + "」")
    if quiet:
        why.append("静默时段 " + "~".join(row["quiet"]))
    if "review" in row:
        why.append("回复先审后发" if row["review"] else "不用审")
    return {"mode": eff, "global_mode": gm, "configured": mode,
            "quiet": quiet, "quiet_win": list(row.get("quiet") or []),
            "quiet_mention": bool(row.get("quiet_mention")),
            "review": row.get("review"),
            "note": str(row.get("note") or ""), "why": "；".join(why)}
