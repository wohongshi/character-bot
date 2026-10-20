"""用量预算：一天烧到多少 token 就该踩刹车。

为什么需要：这台机器 4 GB、网关按时段计费，而「她一直在群里说话」会在没人看
的时候把额度烧光 —— 等到发现时已经超了。这里给三道闸：

- `warn`：超了只在面板上标红，照常答（默认）；
- `cheap`：超了自动换成一个便宜模型（比如 flash），主模型仍然保留给面板手动用；
- `stop`：超了直接拒绝调用，面板/QQ 都会收到一句人话。

`decide()` 是纯函数：不读文件、不取时间、不调模型 —— 传什么算什么，
测试直接喂数字。配置在 `state/budget.json`，用量从 `usage.jsonl` 汇总。
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path

ACTIONS = ("warn", "cheap", "stop")
ACTIONS_CN = {"warn": "只提醒", "cheap": "换成便宜模型", "stop": "直接停"}

DEFAULTS = {
    "on": False,
    "daily_tokens": 0,        # 0 = 不限
    "hourly_tokens": 0,
    "action": "warn",
    "cheap_provider": "",
    "cheap_model": "",
    "warn_at": 0.8,           # 到 80% 先提醒一次
}

LIMITS = {
    "daily_tokens": (0, 100_000_000),
    "hourly_tokens": (0, 100_000_000),
    "warn_at": (0.1, 1.0),
}

log = logging.getLogger("klee.budget")
_LOCK = threading.RLock()


def _path(state_dir: Path | str) -> Path:
    return Path(state_dir) / "budget.json"


def _num(v, default=0, lo=None, hi=None):
    try:
        n = float(v)
    except (TypeError, ValueError):
        n = float(default)
    if n != n:                     # NaN
        n = float(default)
    if lo is not None:
        n = max(lo, n)
    if hi is not None:
        n = min(hi, n)
    return n


def clamp(cfg: dict) -> dict:
    cfg = cfg if isinstance(cfg, dict) else {}
    out = dict(DEFAULTS)
    out["on"] = bool(cfg.get("on"))
    for k, (lo, hi) in LIMITS.items():
        out[k] = _num(cfg.get(k, DEFAULTS[k]), DEFAULTS[k], lo, hi)
    out["daily_tokens"] = int(out["daily_tokens"])
    out["hourly_tokens"] = int(out["hourly_tokens"])
    act = str(cfg.get("action") or DEFAULTS["action"]).strip().lower()
    out["action"] = act if act in ACTIONS else DEFAULTS["action"]
    out["cheap_provider"] = str(cfg.get("cheap_provider") or "")[:32]
    out["cheap_model"] = str(cfg.get("cheap_model") or "")[:80]
    return out


def load(state_dir: Path | str) -> dict:
    """读配置。

    「文件不存在」= 首次运行，用默认值（全关）。
    「文件存在但读坏了」必须**喊一声**：预算是个成本闸，静默退回「全关」等于
    在磁盘出问题时把闸悄悄拆掉，而这正是它存在的原因。
    """
    with _LOCK:
        try:
            raw = _path(state_dir).read_text(encoding="utf-8")
        except FileNotFoundError:
            return dict(DEFAULTS)
        except (OSError, UnicodeDecodeError) as e:
            # UnicodeDecodeError 不是 OSError：文件被写坏（崩溃/掉电截断）时
            # 会抛它，不接住就会一路冒到 _budget_init 把启动搞挂
            log.warning("用量预算配置读不出来（按默认值=关闭）：%s", e)
            return dict(DEFAULTS)
    try:
        d = json.loads(raw)
    except ValueError as e:
        log.warning("用量预算配置解析失败（按默认值=关闭）：%s", e)
        return dict(DEFAULTS)
    if not isinstance(d, dict):
        # 被别的工具写成 `[]` / `null` 也能解析成功，但 clamp() 会静默当默认值（=关闭）
        log.warning("用量预算配置不是对象（%s，按默认值=关闭）", type(d).__name__)
        return dict(DEFAULTS)
    return clamp(d)


def save(state_dir: Path | str, cfg: dict) -> dict:
    out = clamp(cfg)
    p = _path(state_dir)
    with _LOCK:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(f"{p.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        try:
            tmp.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(p)
        finally:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
    return out


def set_field(state_dir: Path | str, **kw) -> dict:
    """按字段合并（面板一次只改一个；整条替换会把别的设置悄悄恢复默认）。"""
    with _LOCK:
        cur = load(state_dir)
        for k, v in kw.items():
            if v is None:
                continue
            cur[k] = v
        return save(state_dir, cur)


def decide(cfg: dict, *, used_day: int, used_hour: int) -> dict:
    """现在这一次调用允不允许、要不要换模型。

    返回 `{allow, action, reason, pct_day, pct_hour, limit_hit}`：
    `allow=False` 只会在 `action=stop` 且真的超限时出现。
    """
    c = clamp(cfg)
    # 没有 now 参数：日/小时的滚动发生在 `Meter`/`rollup` 里，这里只按数字判定
    # （签名里留一个不用的 now 会让调用方以为它是窗口边界）
    used_day, used_hour = max(0, int(used_day or 0)), max(0, int(used_hour or 0))
    day_lim, hour_lim = int(c["daily_tokens"]), int(c["hourly_tokens"])
    pct_day = (used_day / day_lim) if day_lim else 0.0
    pct_hour = (used_hour / hour_lim) if hour_lim else 0.0
    out = {"allow": True, "action": "ok", "reason": "", "pct_day": round(pct_day, 4),
           "pct_hour": round(pct_hour, 4), "limit_hit": "", "used_day": used_day,
           "used_hour": used_hour, "day_limit": day_lim, "hour_limit": hour_lim,
           "warn_at": c["warn_at"], "on": c["on"]}
    if not c["on"]:
        return out
    # 哪条线先破就按哪条算（小时线更急）
    hit = ""
    if hour_lim and used_hour >= hour_lim:
        hit = "hour"
    elif day_lim and used_day >= day_lim:
        hit = "day"
    if not hit:
        # 还没超：看看是不是该提前提醒
        near = (hour_lim and used_hour >= hour_lim * c["warn_at"]) or \
               (day_lim and used_day >= day_lim * c["warn_at"])
        if near:
            out["action"] = "near"
            out["reason"] = (f"今天已用 {used_day}/{day_lim} token（{pct_day:.0%}）"
                             if day_lim else "")
            if hour_lim and used_hour >= hour_lim * c["warn_at"]:
                out["reason"] = (f"这一小时已用 {used_hour}/{hour_lim} token"
                                 f"（{pct_hour:.0%}）")
        return out
    out["limit_hit"] = hit
    what = "这一小时" if hit == "hour" else "今天"
    lim = hour_lim if hit == "hour" else day_lim
    used = used_hour if hit == "hour" else used_day
    if c["action"] == "stop":
        out["allow"] = False
        out["action"] = "stop"
        out["reason"] = f"{what}已用 {used}/{lim} token，到上限了，先歇一会儿"
    elif c["action"] == "cheap" and c["cheap_model"] and c["cheap_provider"]:
        # provider 也必须配上：只有模型名时 llm 那边会「跳过降级」按全价继续，
        # 而面板却显示「这轮换便宜模型」—— 两边的说法会打架
        out["action"] = "cheap"
        out["reason"] = f"{what}已用 {used}/{lim} token，到上限了，这轮换成 {c['cheap_model']}"
    else:
        # warn，或者 cheap 但没配便宜模型 → 退回「只提醒」
        out["action"] = "warn"
        out["reason"] = f"{what}已用 {used}/{lim} token，到上限了"
    return out


def rollup(rows: list, *, now: float | None = None) -> dict:
    """从用量行汇总「今天 / 这一小时」的 token（面板和启动时的初值都走这里）。"""
    now = time.time() if now is None else now
    lt = time.localtime(now)
    day_start = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))
    # 本地整点，不用 `now - now % 3600`：那是 UTC 对齐的，半小时时区
    # （IST/ACST）会在 :30 归零，面板上「这一小时」和墙上的钟对不上
    hour_start = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, lt.tm_hour, 0, 0, 0, 0, -1))
    used_day = used_hour = calls_day = 0
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        try:
            ts = float(r.get("ts") or 0)
        except (TypeError, ValueError):
            continue
        try:
            tok = int(r.get("total_tokens") or 0)
        except (TypeError, ValueError):
            tok = 0
        if tok <= 0:
            continue
        if ts >= day_start:
            used_day += tok
            calls_day += 1
        if ts >= hour_start:
            used_hour += tok
    return {"used_day": used_day, "used_hour": used_hour, "calls_day": calls_day,
            "day_start": day_start, "hour_start": hour_start, "ts": now}


class Meter:
    """增量计数器：每次调用只加一个数，不重读整个 usage.jsonl。

    跨天/跨小时自动归零 —— 面板上的「今天」不能因为服务跑了两天就一直是昨天那个数。
    """

    def __init__(self, now: float | None = None):
        self._lock = threading.Lock()
        now = time.time() if now is None else now
        self.day = time.strftime("%Y-%m-%d", time.localtime(now))
        self.hour = self._hour_key(now)
        self.used_day = 0
        self.used_hour = 0

    @staticmethod
    def _hour_key(now: float) -> str:
        """本地「年-月-日-时」：和 rollup 的本地整点窗口用同一个边界。"""
        return time.strftime("%Y-%m-%d-%H", time.localtime(now))

    def seed(self, used_day: int, used_hour: int, now: float | None = None) -> None:
        now = time.time() if now is None else now
        with self._lock:
            self.day = time.strftime("%Y-%m-%d", time.localtime(now))
            self.hour = self._hour_key(now)
            self.used_day = max(0, int(used_day or 0))
            self.used_hour = max(0, int(used_hour or 0))

    def _roll(self, now: float) -> None:
        d = time.strftime("%Y-%m-%d", time.localtime(now))
        h = self._hour_key(now)
        if d != self.day:
            self.day, self.used_day = d, 0
        if h != self.hour:
            self.hour, self.used_hour = h, 0

    def add(self, tokens: int, now: float | None = None) -> None:
        now = time.time() if now is None else now
        n = max(0, int(tokens or 0))
        with self._lock:
            self._roll(now)
            self.used_day += n
            self.used_hour += n

    def snapshot(self, now: float | None = None) -> dict:
        now = time.time() if now is None else now
        with self._lock:
            self._roll(now)
            return {"used_day": self.used_day, "used_hour": self.used_hour}
