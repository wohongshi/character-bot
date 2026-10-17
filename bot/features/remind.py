"""定时提醒：到点提醒你一件事。

用法：.提醒 10m 喝水 / .提醒 21:30 开会 / .提醒列表 / .取消提醒 3

- 后台循环每 15 秒检查到期项；到期在群里 @ 提醒、私聊直接发
- 存 state/reminders.json（跨重启不丢）；锁文件防热重载时新旧循环叠加
"""
import asyncio
import datetime
import json
import logging
import re
import time
import uuid
from pathlib import Path
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

NAME = "定时提醒"
DESCRIPTION = "到点提醒你一件事"
VERSION = "1.2"

def _state_root() -> "pathlib.Path":
    """状态目录：`KLEE_STATE_DIR` 可覆盖（干跑测试台靠它把状态写到临时目录）。"""
    import os
    base = os.environ.get("KLEE_STATE_DIR")
    from pathlib import Path as _P
    return _P(base) if base else _P(__file__).resolve().parent / "state"


BASE = _state_root()
STATE_FILE = BASE / "reminders.json"
LOCK_FILE = BASE / "reminders.lock"
MAX_PENDING = 20

_REL = re.compile(r"^(\d{1,4})\s*(s|m|h|d|秒|分|分钟|小时|天)$", re.I)
_CLOCK = re.compile(r"^(\d{1,2})[:：](\d{2})$")
_DAILY = re.compile(r"^每天\s*(\d{1,2})[:：](\d{2})$")
_WEEKLY = re.compile(r"^每周\s*([一二三四五六日天])\s*(\d{1,2})[:：](\d{2})$")
_WEEKDAY = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}
_WEEKDAY_CN = "一二三四五六日"


def parse_rule(s: str) -> Optional[dict]:
    """'每天8:00' / '每周一 8:00' → 重复规则；不是重复写法 → None。"""
    s = (s or "").strip()
    m = _DAILY.match(s)
    if m:
        hh, mm = int(m.group(1)), int(m.group(2))
        if hh <= 23 and mm <= 59:
            return {"kind": "daily", "at": f"{hh:02d}:{mm:02d}"}
        return None
    m = _WEEKLY.match(s)
    if m:
        hh, mm = int(m.group(2)), int(m.group(3))
        if hh <= 23 and mm <= 59:
            return {"kind": "weekly", "weekday": _WEEKDAY[m.group(1)], "at": f"{hh:02d}:{mm:02d}"}
        return None
    return None


def next_due(rule: dict, now: Optional[float] = None) -> float:
    """按规则算下一次到期时间戳（永远严格在未来）。周一=weekday 0，跟 datetime 一致。"""
    base = datetime.datetime.fromtimestamp(now if now is not None else time.time())
    hh, mm = (int(x) for x in rule["at"].split(":"))
    target = base.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if rule["kind"] == "daily":
        if target <= base:
            target += datetime.timedelta(days=1)
    else:
        target += datetime.timedelta(days=(int(rule["weekday"]) - target.weekday()) % 7)
        if target <= base:
            target += datetime.timedelta(days=7)
    return target.timestamp()


def describe_rule(rule: dict) -> str:
    """'🔁每天 08:00' / '🔁每周一 08:00'（列表和回执里用）。"""
    at = rule["at"]
    if rule["kind"] == "daily":
        return f"🔁每天 {at}"
    return f"🔁每周{_WEEKDAY_CN[int(rule['weekday'])]} {at}"


def _load() -> dict:
    try:
        d = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {"items": []}
    except (OSError, ValueError):
        return {"items": []}


def _save(d: dict) -> None:
    BASE.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(STATE_FILE)


def parse_when(s: str) -> Optional[float]:
    """'10m' / '2h' / '30秒' / '21:30' → 到期时间戳；解析不了 → None。"""
    s = (s or "").strip()
    m = _REL.match(s)
    if m:
        n = int(m.group(1))
        unit = m.group(2).lower()
        mult = {"s": 1, "秒": 1, "m": 60, "分": 60, "分钟": 60,
                "h": 3600, "小时": 3600, "d": 86400, "天": 86400}[unit]
        return time.time() + max(10, n * mult)
    m = _CLOCK.match(s)
    if m:
        hh, mm = int(m.group(1)), int(m.group(2))
        if hh > 23 or mm > 59:
            return None
        now = datetime.datetime.now()
        target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if target <= now:
            target += datetime.timedelta(days=1)
        return target.timestamp()
    return None


RETRY_AFTER_S = 60.0      # 发失败后多久重投（掉线时别丢提醒）
MAX_AGE_S = 2 * 3600      # 提醒「过期」多久就不再追了（掉线一整天后别一恢复就刷一堆过时提醒）
MAX_RETRIES = int(MAX_AGE_S / RETRY_AFTER_S)   # 重投次数上限（≈2 小时 / 60 秒）


def _send_result_ok(res) -> bool:
    """适配器的 SendResult 兼容判定：``success=False`` → 失败；None/True/无该字段 → 成功。

    掉线时适配器**不抛异常**，只返回 ``SendResult(success=False, error="OneBot WS 未连接")``
    —— 不看返回值就当发送成功，提醒会静默蒸发（这是本次修的另一个真 bug）。
    """
    if res is None:
        return True
    ok = getattr(res, "success", None)
    return True if ok is None else bool(ok)


async def _deliver(api, chat_id: str, text: str) -> None:
    """主动发一条。优先框架的 ``api.send()``；只有老测试桩才落到 ``api.adapter.send()``。

    历史坑：``api.adapter`` 在真框架（FeatureAPI）上**不存在**，只有 MsgContext 才有；
    测试桩偏偏给了 ``.adapter``，于是「到点提醒」在生产里一直是 AttributeError 被吞掉、从未发出去过。
    """
    send = getattr(api, "send", None)
    if callable(send):
        res = await send(chat_id, text)
    else:                                                 # 兼容旧桩
        res = await api.adapter.send(chat_id, text)
    if not _send_result_ok(res):
        raise RuntimeError(f"发送未成功：{getattr(res, 'error', '') or res!r}")


async def check_once(api) -> int:
    """检查一次到期项并发送；返回触发条数（后台循环与测试共用）。"""
    d = _load()
    now = time.time()
    due: List[dict] = []
    rest: List[dict] = []
    for it in d["items"]:
        (due if float(it.get("due", 0)) <= now else rest).append(it)
    if not due:
        return 0
    d["items"] = rest
    for it in due:                                        # 重复提醒：算好下次到期，不丢
        rule = it.get("repeat")
        if isinstance(rule, dict):
            nxt = dict(it)
            nxt["due"] = next_due(rule)
            nxt["fired"] = int(it.get("fired") or 0) + 1
            d["items"].append(nxt)
    _save(d)                                              # 先弹出（+重排）再发，防重复
    fired = 0
    retry: List[dict] = []
    for it in due:
        at = (f"[CQ:at,qq={it['user_id']}] "
              if str(it.get("chat_id", "")).startswith("group:") else "")
        try:
            await _deliver(api, it["chat_id"],
                           f"{at}⏰ 叮——我来提醒你：「{it['text']}」啦！")
            fired += 1
        except Exception as e:                            # noqa: BLE001
            tries = int(it.get("retries") or 0)
            age = time.time() - float(it.get("due", 0))    # 这条提醒已经晚多久了
            if tries < MAX_RETRIES and age < MAX_AGE_S:
                # 掉线/发送失败 → 排到 60 秒后重投，别让提醒凭空消失（最长追 MAX_AGE_S）
                again = dict(it)
                again["due"] = time.time() + RETRY_AFTER_S
                again["retries"] = tries + 1
                retry.append(again)
                logger.warning("提醒发送失败（第 %d 次，稍后重投）— %s: %s",
                               tries + 1, type(e).__name__, e)
            else:
                logger.warning("提醒放弃：晚了 %.0f 分钟 / 重投 %d 次 —「%s」",
                               age / 60.0, tries, it.get("text"))
    if retry:
        fresh = _load()                                   # 重读，别覆盖这期间用户新加的
        fresh["items"].extend(retry)
        _save(fresh)
    return fired


def register(api):
    BASE.mkdir(parents=True, exist_ok=True)
    my_token = uuid.uuid4().hex
    try:
        LOCK_FILE.write_text(my_token, encoding="utf-8")        # 新版占锁，旧循环会自退
    except OSError:
        pass

    async def add_cmd(ctx):
        parts = ctx.arg_text.strip().split(maxsplit=1)
        if len(parts) < 2:
            await ctx.reply("用法：.提醒 10m 喝水 / .提醒 21:30 开会\n"
                            "重复：.提醒 每天8:00 起床 / .提醒 每周一 7:30 上学\n"
                            "（10m=十分钟后，21:30=今晚或明晚九点半；.提醒列表 看全部）")
            return
        rule = parse_rule(parts[0])
        if rule:
            due = next_due(rule)
        else:
            due = parse_when(parts[0])
        if not due:
            await ctx.reply(f"看不懂这个时间：「{parts[0]}」～试试 10m / 2h / 30秒 / 21:30 / 每天8:00 "
                            f"/ 每周一 7:30 这类写法")
            return
        what = parts[1].strip()[:200]
        d = _load()
        mine = [it for it in d["items"]
                if it.get("user_id") == ctx.user_id and it.get("chat_id") == ctx.chat_id]
        if len(mine) >= MAX_PENDING:
            await ctx.reply(f"你已经有 {len(mine)} 条待提醒啦，先等它们响完吧～")
            return
        rid = max((it.get("id", 0) for it in d["items"]), default=0) + 1
        item = {"id": rid, "chat_id": ctx.chat_id, "user_id": ctx.user_id,
                "user_name": ctx.user_name, "due": due, "text": what,
                "created": time.time()}
        if rule:
            item["repeat"] = rule
        d["items"].append(item)
        _save(d)
        if rule:
            when_str = f"{describe_rule(rule)}（下次 {datetime.datetime.fromtimestamp(due):%m-%d %H:%M}）"
        else:
            when_str = datetime.datetime.fromtimestamp(due).strftime("%m-%d %H:%M")
        await ctx.reply(f"好嘞！记在小本本上了：{when_str} 提醒你「{what[:50]}」[表情:呲牙]")

    async def list_cmd(ctx):
        d = _load()
        mine = sorted((it for it in d["items"] if it.get("chat_id") == ctx.chat_id),
                      key=lambda x: x["due"])
        if not mine:
            await ctx.reply("现在没有待提醒的事情哦～")
            return
        lines = ["我的小本本·待提醒："]
        for it in mine[:MAX_PENDING]:
            rule = it.get("repeat")
            if isinstance(rule, dict):
                t = f"{describe_rule(rule)}（下次 {datetime.datetime.fromtimestamp(it['due']):%m-%d %H:%M}）"
            else:
                t = datetime.datetime.fromtimestamp(it["due"]).strftime("%m-%d %H:%M")
            lines.append(f"  #{it['id']}  {t}  {it['text'][:30]}")
        await ctx.reply("\n".join(lines))

    async def cancel_cmd(ctx):
        arg = ctx.arg_text.strip().lstrip("#")
        if not arg.isdigit():
            await ctx.reply("用法：.取消提醒 3（编号看 .提醒列表）")
            return
        d = _load()
        keep = [it for it in d["items"]
                if not (it.get("id") == int(arg) and it.get("chat_id") == ctx.chat_id)]
        if len(keep) == len(d["items"]):
            await ctx.reply("没找到这个编号～用 .提醒列表 看看？")
            return
        d["items"] = keep
        _save(d)
        await ctx.reply(f"好，#{arg} 那条提醒撤掉了。")

    api.on_command("提醒", add_cmd, aliases=("remind",), cooldown=5,
                   help=".提醒 <时间> <内容> —— 到点提醒你（10m / 2h / 21:30）")
    api.on_command("提醒列表", list_cmd, cooldown=3, help=".提醒列表 —— 看全部待提醒")
    api.on_command("取消提醒", cancel_cmd, cooldown=3, help=".取消提醒 <编号> —— 撤销一条")

    async def loop():
        while True:
            await asyncio.sleep(15)
            try:
                if LOCK_FILE.read_text(encoding="utf-8").strip() != my_token:
                    return                                        # 被新的重载实例接管
            except OSError:
                pass
            try:
                await check_once(api)
            except Exception:                                     # noqa: BLE001
                continue

    try:
        task = asyncio.get_running_loop().create_task(loop())
        if isinstance(getattr(api, "state", None), dict):
            api.state["_task"] = task                             # 强引用防 GC
    except RuntimeError:
        pass                                                       # 没事件循环就跑不了定时，静默降级
