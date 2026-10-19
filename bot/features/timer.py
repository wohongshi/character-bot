"""倒计时器：``.计时 25m 泡面`` / ``.计时列表`` / ``.取消计时 2``。

通用插件。和「.提醒」的区别：
- `.提醒 21:30 开会` 是**日历时间**；`.计时 25m` 是**从现在起算的时长**，适合泡面、番茄钟；
- 到期只响一次，不重复。

实现上和 remind 同一个套路（自己的后台循环 + 锁文件防重载叠加），但状态文件独立，
互不干扰：关掉提醒插件，计时照样能用。
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import time
import uuid
from pathlib import Path

NAME = "倒计时器"
NAME_EN = "Timer"
DESCRIPTION = "从现在起算的倒计时：泡面、番茄钟、蒸蛋。" 
DESCRIPTION_EN = "Countdown timers from now (noodle, pomodoro, eggs)."
VERSION = "1.0"

logger = logging.getLogger(__name__)
MAX_PENDING = 10
MAX_SECONDS = 24 * 3600

_UNIT = {"s": 1, "秒": 1, "m": 60, "分": 60, "分钟": 60, "h": 3600, "小时": 3600,
         "d": 86400, "天": 86400}


def raw_args(ctx) -> str:
    t = str(getattr(ctx, "text", "") or "").strip()
    body = t[1:].strip() if t[:1] in "./／" else t
    parts = body.split(None, 1)
    return parts[1] if len(parts) > 1 else ""


def _root() -> Path:
    base = os.environ.get("KLEE_STATE_DIR")
    d = Path(base) if base else Path(__file__).resolve().parent / "state"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _state() -> Path:
    return _root() / "timers.json"


def _lock() -> Path:
    return _root() / "timers.lock"


def _load() -> dict:
    import json
    try:
        d = json.loads(_state().read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(d: dict) -> None:
    import json
    p = _state()
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)


def parse_duration(s: str) -> int | None:
    """'25m' / '90秒' / '1h30m' / '300'（默认秒）→ 秒数；认不出返回 None。"""
    t = str(s or "").strip().lower().replace("　", "")
    if not t:
        return None
    if re.fullmatch(r"\d+", t):
        return int(t)
    total = 0
    for num, unit in re.findall(r"(\d+)\s*(s|秒|m|分|分钟|h|小时|d|天)", t):
        total += int(num) * _UNIT[unit]
    if total <= 0:
        return None
    return total


def human(sec: int) -> str:
    sec = int(sec)
    if sec < 60:
        return f"{sec} 秒"
    m, s = divmod(sec, 60)
    if m < 60:
        return f"{m} 分" + (f" {s} 秒" if s else "")
    h, m = divmod(m, 60)
    return f"{h} 小时" + (f" {m} 分" if m else "")


RETRY_AFTER_S = 60.0       # 响铃发送失败后多久重投
MAX_AGE_S = 1800           # 超过半小时就不再追（别在恢复后刷一堆过时闹钟）
MAX_RETRIES = int(MAX_AGE_S / RETRY_AFTER_S)


def _ok(res) -> bool:
    if res is None:
        return True
    ok = getattr(res, "success", None)
    return True if ok is None else bool(ok)


async def fire_due(api) -> int:
    """检查到期；返回响了几条。测试直接调它。"""
    d = _load()
    now = time.time()
    due = [it for it in d.get("items", []) if float(it.get("due", 0)) <= now]
    if not due:
        return 0
    d["items"] = [it for it in d.get("items", []) if float(it.get("due", 0)) > now]
    _save(d)                                                  # 先弹出再发，防重复
    fired = 0
    retry: list = []
    for it in due:
        at = f"[CQ:at,qq={it['user_id']}] " if str(it.get("chat_id", "")).startswith("group:") else ""
        text = it.get("text") or "时间到"
        try:
            res = await api.send(it["chat_id"], f"{at}⏳ 叮——{human(int(it.get('seconds') or 0))}到了：{text}")
            if not _ok(res):
                raise RuntimeError("发送未成功")
            fired += 1
        except Exception as e:                                   # noqa: BLE001
            # 发送失败（掉线/风控）不能把计时器丢掉：这是「先删后发」，
            # 一秒钟的连接抖动就等于闹钟凭空消失。按提醒那套排 60 秒后重投，
            # 最多追 RETRY_AFTER_S*MAX_RETRIES（≈30 分钟）。
            tries = int(it.get("retries") or 0)
            age = time.time() - float(it.get("due", 0))
            if tries < MAX_RETRIES and age < MAX_AGE_S:
                again = dict(it)
                again["due"] = time.time() + RETRY_AFTER_S
                again["retries"] = tries + 1
                retry.append(again)
                logger.warning("计时器响铃失败（第 %d 次，稍后重投）：%s: %s",
                               tries + 1, type(e).__name__, e)
            else:
                logger.warning("计时器放弃：晚了 %.0f 分钟 / 重投 %d 次 —「%s」",
                               age / 60.0, tries, text)
    if retry:
        fresh = _load()                                       # 重读，别覆盖这期间新加的
        fresh["items"] = list(fresh.get("items") or []) + retry
        _save(fresh)
    return fired


def register(api):
    my_token = uuid.uuid4().hex
    try:
        _lock().write_text(my_token, encoding="utf-8")
    except OSError:
        pass

    async def add(ctx):
        parts = raw_args(ctx).strip().split(None, 1)
        if not parts:
            await ctx.reply("用法：.计时 25m 泡面（支持 90s / 1h30m / 300）\n"
                            ".计时列表 · .取消计时 <编号>")
            return
        sec = parse_duration(parts[0])
        if not sec:
            await ctx.reply(f"看不懂这个时长：「{parts[0]}」～写 25m / 90s / 1h30m")
            return
        if sec > MAX_SECONDS:
            await ctx.reply("最长 24 小时～更久的用 .提醒 吧")
            return
        text = (parts[1] if len(parts) > 1 else "").strip()[:100] or "时间到"
        d = _load()
        mine = [it for it in d.get("items", []) if it.get("chat_id") == ctx.chat_id]
        if len(mine) >= MAX_PENDING:
            await ctx.reply(f"这个会话已经有 {len(mine)} 个计时了，先等它们响完吧")
            return
        tid = max((int(it.get("id") or 0) for it in d.get("items", [])), default=0) + 1
        d.setdefault("items", []).append({
            "id": tid, "chat_id": ctx.chat_id, "user_id": ctx.user_id,
            "text": text, "seconds": sec, "due": time.time() + sec,
        })
        _save(d)
        await ctx.reply(f"⏳ 好的，{human(sec)}后叫你：{text}")

    async def listing(ctx):
        items = [it for it in _load().get("items", []) if it.get("chat_id") == ctx.chat_id]
        if not items:
            await ctx.reply("这个会话没有在跑的计时～用 .计时 25m 泡面 开一个")
            return
        rows = []
        for it in sorted(items, key=lambda x: float(x.get("due") or 0)):
            left = max(0, int(float(it.get("due") or 0) - time.time()))
            rows.append(f"{it.get('id')}. {it.get('text')} —— 还剩 {human(left)}")
        await ctx.reply("⏳ 在跑的计时\n" + "\n".join(rows))

    async def cancel(ctx):
        raw = raw_args(ctx).strip()
        if not raw.isdigit():
            await ctx.reply("用法：.取消计时 2（编号看 .计时列表）")
            return
        tid = int(raw)
        d = _load()
        keep = [it for it in d.get("items", [])
                if not (int(it.get("id") or 0) == tid and it.get("chat_id") == ctx.chat_id)]
        if len(keep) == len(d.get("items", [])):
            await ctx.reply("没找到这个编号～用 .计时列表 看看")
            return
        d["items"] = keep
        _save(d)
        await ctx.reply(f"好，#{tid} 那个计时撤掉了。")

    api.on_command("计时", add, aliases=("timer",), cooldown=2,
                   help=".计时 25m 泡面 —— 从现在起算的倒计时")
    api.on_command("计时列表", listing, cooldown=2, help=".计时列表 —— 看还在跑的")
    api.on_command("取消计时", cancel, cooldown=2, help=".取消计时 <编号>")

    async def loop():
        while True:
            await asyncio.sleep(5)
            try:
                if _lock().read_text(encoding="utf-8").strip() != my_token:
                    return                                   # 被新的重载实例接管
            except OSError:
                pass
            try:
                await fire_due(api)
            except Exception:                                 # noqa: BLE001
                continue

    try:
        task = asyncio.get_running_loop().create_task(loop())
        if isinstance(getattr(api, "state", None), dict):
            api.state["_task"] = task                        # 强引用防 GC
    except RuntimeError:
        pass                                                 # 没事件循环就静默降级
