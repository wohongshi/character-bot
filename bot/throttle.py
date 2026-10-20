"""每个会话的回复节流：防刷屏、防「有人一句话一句地问」把额度烧光。

和「用量预算」的区别
--------------------
预算管的是**钱**（token），这里管的是**频率**：一个群里三个人连珠炮问，预算还有余额，
但一分钟回 40 条既不像人、也贵。两道闸叠加生效，互不替代。

规则
----
- 滑动窗口（默认 60 秒），每个会话独立计数；
- 超了三种处置：`drop` 静默不答（默认，群里最像人）、`notice` 回一句「慢点」再说、
  `queue` 不适用（回复是异步的，排队只会更糟）—— 所以只有 `drop` / `notice`；
- 只限制**聊天**，命令照常（`.待办` 这类不该被节流卡住）；
- 例外：被 @ 的那条仍然计数，但**不单独豁免**（否则「@ 一下就能刷」）；
- 计数在内存里（重启归零），不落盘：这是「当下别刷」而不是额度。

上限按「分钟」和「小时」两条线，任意一条超了就拦。
"""
from __future__ import annotations

import threading
import time
from collections import deque

DEFAULTS = {"on": False, "per_min": 6, "per_hour": 60, "action": "drop"}
ACTIONS = ("drop", "notice")
ACTIONS_CN = {"drop": "静默不答", "notice": "回一句「慢点」"}
LIMITS = {"per_min": (0, 120), "per_hour": (0, 5000)}
MAX_CHATS = 500


def clamp_cfg(raw: dict | None) -> dict:
    """配置收敛：类型不对/超范围都夹回合法值（坏配置等同于默认）。"""
    raw = raw if isinstance(raw, dict) else {}
    out = dict(DEFAULTS)
    out["on"] = bool(raw.get("on", False))
    for k, (lo, hi) in LIMITS.items():
        try:
            v = int(raw.get(k, DEFAULTS[k]))
        except (TypeError, ValueError):
            v = DEFAULTS[k]
        out[k] = max(lo, min(hi, v))
    act = str(raw.get("action") or DEFAULTS["action"]).strip().lower()
    out["action"] = act if act in ACTIONS else DEFAULTS["action"]
    return out


def decide(cfg: dict, *, used_min: int, used_hour: int) -> dict:
    """纯函数：这次该不该放行。返回 {allow, level, reason}。

    `level` 给面板/日志用：`ok` / `min` / `hour` / `off`。
    """
    c = clamp_cfg(cfg)
    if not c["on"]:
        return {"allow": True, "level": "off", "reason": ""}
    if c["per_min"] and used_min >= c["per_min"]:
        return {"allow": False, "level": "min",
                "reason": f"一分钟内已经回了 {used_min} 条（上限 {c['per_min']}）"}
    if c["per_hour"] and used_hour >= c["per_hour"]:
        return {"allow": False, "level": "hour",
                "reason": f"这一小时已经回了 {used_hour} 条（上限 {c['per_hour']}）"}
    return {"allow": True, "level": "ok", "reason": ""}


class Window:
    """按会话的滑动窗口计数（线程安全；运行时与面板会并发读）。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._hits: dict[str, deque] = {}

    def _trim(self, dq: deque, now: float, span: float) -> None:
        floor = now - span
        while dq and dq[0] < floor:
            dq.popleft()

    def counts(self, chat_id: str, *, now: float | None = None) -> tuple[int, int]:
        """(这一分钟, 这一小时) 已经记了多少条。"""
        now = time.time() if now is None else now
        with self._lock:
            dq = self._hits.get(str(chat_id))
            if not dq:
                return 0, 0
            self._trim(dq, now, 3600.0)
            hour = len(dq)
            min_floor = now - 60.0
            minute = sum(1 for t in dq if t >= min_floor)
            return minute, hour

    def hit(self, chat_id: str, *, now: float | None = None) -> None:
        """记一条（放行之后才记）。顺手清掉整小时都没动静的会话，防长跑膨胀。"""
        now = time.time() if now is None else now
        cid = str(chat_id)
        with self._lock:
            dq = self._hits.setdefault(cid, deque())
            dq.append(now)
            if len(self._hits) > MAX_CHATS:
                dead = [k for k, v in self._hits.items()
                        if not v or now - v[-1] > 3600.0]
                for k in dead[: max(0, len(dead) - MAX_CHATS // 2)]:
                    self._hits.pop(k, None)

    def reset(self, chat_id: str | None = None) -> None:
        with self._lock:
            if chat_id is None:
                self._hits.clear()
            else:
                self._hits.pop(str(chat_id), None)

    def snapshot(self, limit: int = 20) -> list[dict]:
        """面板用：最近比较活跃的几个会话（计数高的在前）。"""
        now = time.time()
        with self._lock:
            rows = []
            for cid, dq in self._hits.items():
                self._trim(dq, now, 3600.0)
                if not dq:
                    continue
                minute = sum(1 for t in dq if t >= now - 60.0)
                rows.append({"chat": cid, "min": minute, "hour": len(dq),
                             "last": round(dq[-1], 1)})
        rows.sort(key=lambda r: (-r["min"], -r["hour"]))
        return rows[:limit]
