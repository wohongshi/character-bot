"""纪念日：``.纪念日 12-25 圣诞节`` —— 记一个日子，可莉帮你数着还有几天。

用法：
  ``.纪念日 12-25 圣诞节``        # 每年重复（也认 ``12月25日``）
  ``.纪念日 2024-03-05 认识纪念日``  # 带年份 = 一次性（算已经过了多少天）
  ``.纪念日``                    # 看全部（今天的最前，再按天数排）
  ``.纪念日 删 3`` / ``.纪念日 删 圣诞节``

- 状态存 ``state/anniversary.json``，按聊天分开（``KLEE_STATE_DIR`` 可覆盖，干跑测试用）
- 纯离线、零依赖；参数里的方括号会洗掉（防 ``[CQ:at,qq=all]`` 这类出站富文本注入）
"""

import asyncio
import json
import logging
import os
import re
import time
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

NAME = "纪念日"
DESCRIPTION = "纪念日倒数：.纪念日 12-25 圣诞节 记一个日子，可莉帮你数天数"
VERSION = "1.1"

MAX_PER_CHAT = 30
_NAME_MAX = 24
_LABEL_MAX = 16

# 每天几点起播报「今天到期的纪念日」（同一天同一聊天只播一次）。9 点前不打扰。
ANNOUNCE_HOUR = 9
_TICK_S = 60.0

# 出站会解析 [CQ:at,qq=all] / [CQ:image,…] 之类的富文本标记；名字是用户自己敲的，必须洗。
_UNSAFE = re.compile(r"[\[\]<>]|&#\d+;")

_RE_DASH_Y = re.compile(r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})$")
_RE_DASH = re.compile(r"^(\d{1,2})[-/.](\d{1,2})$")
_RE_CN_Y = re.compile(r"^(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日?$")
_RE_CN = re.compile(r"^(\d{1,2})\s*月\s*(\d{1,2})\s*日?$")
# 长词必须放前面：re 交替取首个可匹配项，`删` 在前会把「删除 3」吃成「除 3」（OCR 三轮 #17）
_RE_DEL = re.compile(r"^(?:删除|移除|去掉|删|del)\s*(.*)$", re.I)

# 非闰年的 2-29 按 2-28 算（最常见做法）；用闰年校验日期合法性，好让 2-29 能存进来。
_LEAP_PROBE = 2024


def _state_root() -> Path:
    """状态目录：``KLEE_STATE_DIR`` 可覆盖（干跑测试台靠它把状态写到临时目录）。"""
    base = os.environ.get("KLEE_STATE_DIR")
    return Path(base) if base else Path(__file__).resolve().parent / "state"


BASE = _state_root()
STATE_FILE = BASE / "anniversary.json"
LOCK_FILE = BASE / "anniversary.lock"      # 热重载后旧播报循环凭它自退（同 remind 的做法）
_TASK_HOLD: list = []                      # 播报循环的模块级强引用（api.state 缺失时兜底防 GC，OCR #19）


def _safe(raw: str, limit: int = _NAME_MAX) -> str:
    """名字/参数里最长的安全片段：干掉方括号与实体转义。"""
    return _UNSAFE.sub("", raw or "").strip()[:limit]


def valid_date(year: int, month: int, day: int) -> bool:
    """真日期校验（闰年/月长都算）。"""
    try:
        date(year, month, day)
        return True
    except ValueError:
        return False


def parse_date(s: str) -> Optional[dict]:
    """``12-25`` / ``12月25日`` / ``2024-03-05`` / ``2024年3月5日`` → dict；看不懂 → None。

    返回 ``{"year": int|None, "month": int, "day": int}``，``year`` 为空表示每年重复。
    """
    s = (s or "").strip()
    for rx, has_year in ((_RE_DASH_Y, True), (_RE_CN_Y, True), (_RE_DASH, False), (_RE_CN, False)):
        m = rx.match(s)
        if not m:
            continue
        g = [int(x) for x in m.groups()]
        year = g[0] if has_year else None
        month, day = (g[1], g[2]) if has_year else (g[0], g[1])
        if not 1 <= month <= 12 or not 1 <= day <= 31:
            return None
        # 无年份：用闰年探一次（2-29 合法）；有年份：按真实年份校验
        if not valid_date(year if year else _LEAP_PROBE, month, day):
            return None
        if year is not None and not (1900 <= year <= 2100):
            return None
        return {"year": year, "month": month, "day": day}
    return None


def resolve_date(year: int, month: int, day: int) -> date:
    """把 (年,月,日) 落成合法 date：2-29 撞上非闰年就退到 2-28。"""
    if valid_date(year, month, day):
        return date(year, month, day)
    if month == 2 and day == 29:
        return date(year, 2, 28)
    raise ValueError(f"bad date {year}-{month}-{day}")


def next_occurrence(item: dict, today: Optional[date] = None) -> date:
    """下一次发生的日子（每年重复的取今年或明年那次；一次性的就取它本身）。"""
    today = today or date.today()
    y, m, d = item.get("year"), int(item["month"]), int(item["day"])
    if y:
        return resolve_date(int(y), m, d)
    cand = resolve_date(today.year, m, d)
    if cand < today:
        cand = resolve_date(today.year + 1, m, d)
    return cand


def status(item: dict, today: Optional[date] = None) -> tuple:
    """→ ``(days, kind)``：``kind`` ∈ today / upcoming / past。

    每年重复：永远按「还有几天」算；一次性：过去的日子给出「已经过了几天」。
    """
    today = today or date.today()
    y, m, d = item.get("year"), int(item["month"]), int(item["day"])
    if y:
        target = resolve_date(int(y), m, d)
        delta = (target - today).days
        if delta == 0:
            return 0, "today"
        if delta > 0:
            return delta, "upcoming"
        return -delta, "past"
    nxt = next_occurrence(item, today)
    delta = (nxt - today).days
    return (0, "today") if delta == 0 else (delta, "upcoming")


def date_label(item: dict) -> str:
    """``12-25``（每年） / ``2024-03-05``（一次性）。"""
    if item.get("year"):
        return f"{int(item['year']):04d}-{int(item['month']):02d}-{int(item['day']):02d}"
    return f"{int(item['month']):02d}-{int(item['day']):02d}"


def describe(item: dict, today: Optional[date] = None) -> str:
    """``🎉 就是今天！`` / ``还有 42 天`` / ``已经过了 108 天``（一次性）。"""
    days, kind = status(item, today)
    if kind == "today":
        return "🎉 就是今天！"
    if kind == "past":
        return f"已经过了 {days} 天"
    return f"还有 {days} 天"


def sort_key(item: dict, today: Optional[date] = None) -> tuple:
    """今天的最前；其后按剩余天数升序；一次性的「已过去」排最后（也按天数升序）。"""
    days, kind = status(item, today)
    rank = {"today": 0, "upcoming": 1, "past": 2}[kind]
    return (rank, days, str(item.get("name", "")))


def _load() -> dict:
    try:
        d = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) and isinstance(d.get("items"), list) else {"items": []}
    except (OSError, ValueError):
        return {"items": []}


def _save(d: dict) -> None:
    BASE.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(STATE_FILE)


def _norm(text: str) -> str:
    return re.sub(r"\s+", "", (text or "").strip()).lower()


def _send_result_ok(res) -> bool:
    """适配器 SendResult 兼容判定：``success=False`` → 失败（掉线时就是这种）。"""
    if res is None:
        return True
    ok = getattr(res, "success", None)
    return True if ok is None else bool(ok)


async def _deliver(api, chat_id: str, text: str) -> None:
    """主动发一条：优先框架的 ``api.send()``（``api.adapter`` 在真框架上并不存在）。"""
    send = getattr(api, "send", None)
    res = await send(chat_id, text) if callable(send) else await api.adapter.send(chat_id, text)
    if not _send_result_ok(res):
        raise RuntimeError(f"发送未成功：{getattr(res, 'error', '') or res!r}")


async def announce_once(api, now: Optional[datetime] = None) -> int:
    """到点就把「今天到期」的日子按聊天各播一条；返回播报条数（给测试用）。

    规矩：``ANNOUNCE_HOUR`` 之前不打扰；同一聊天同一天只播一次（记在 ``announced`` 里，
    发送失败**不记账** → 下一轮还会试）。先算好、await 完再重读文件回写，免得把期间
    用户新加的条目覆盖掉。
    """
    now = now or datetime.now()
    if now.hour < ANNOUNCE_HOUR:
        return 0
    today = now.date()                                        # 日期也从 now 推，测试注入口径一致（OCR #20）
    key = today.isoformat()
    d = _load()
    by_chat: dict = {}
    for it in d["items"]:
        if not isinstance(it, dict):
            continue
        chat = str(it.get("chat_id") or "")
        if not chat:
            continue
        try:
            days, kind = status(it, today)
        except Exception:                                     # noqa: BLE001
            continue                                          # 脏条目跳过
        if kind == "today":
            by_chat.setdefault(chat, []).append(it)
    if not by_chat:
        return 0
    done = d.get("announced") if isinstance(d.get("announced"), dict) else {}
    fired: List[str] = []
    for chat, items in by_chat.items():
        if done.get(chat) == key:
            continue                                          # 今天已经播过
        names = "、".join(f"「{_safe(it.get('name', '?'), _NAME_MAX)}」" for it in items)
        try:
            await _deliver(api, chat, f"🎉 今天就是 {names} 呀！可莉一直记着的，一起过吧[表情:呲牙]")
        except Exception as e:                                # noqa: BLE001
            logger.warning("纪念日：播报 %s 失败（稍后自动重试）— %s: %s",
                           chat, type(e).__name__, e)
            continue                                          # 发失败不记账，下轮再试
        fired.append(chat)
    if fired:
        fresh = _load()                                       # 重读，别覆盖这期间的写入
        if not isinstance(fresh.get("announced"), dict):
            fresh["announced"] = {}
        for chat in fired:
            fresh["announced"][chat] = key
        _save(fresh)
    return len(fired)


_SOON_DAYS = 7          # 7 天内到期的标 ⚡（聊天里一眼看出最近要过哪个日子）

# 「只看最近」的几种说法（`.纪念日 本周` / `.纪念日 7天`）
_SOON_WORDS = {"本周", "这周", "最近", "近期", "近7天", "近七天", "7天", "七天", "一周", "一周内"}


def _usage() -> str:
    return ("用法：.纪念日 12-25 圣诞节（每年）\n"
            "      .纪念日 2024-03-05 认识纪念日（一次性，会算过了多少天）\n"
            "      .纪念日 看全部 / .纪念日 本周 / .纪念日 删 3")


def register(api):
    def mine_of(d: dict, chat_id: str) -> List[dict]:
        return [it for it in d["items"] if it.get("chat_id") == chat_id]

    async def show_list(ctx, within=None):
        d = _load()
        mine = sorted(mine_of(d, ctx.chat_id), key=lambda it: sort_key(it))
        if within is not None:
            mine = [it for it in mine
                    if status(it)[1] == "today" or (status(it)[1] == "upcoming" and status(it)[0] <= within)]
        if not mine:
            if within is not None:
                await ctx.reply(f"最近 {within} 天没有要过的日子哦～"
                                f"（.纪念日 看全部）")
            else:
                await ctx.reply("还没有记着的日子哦～\n" + _usage())
            return
        lines = []
        for it in mine[:MAX_PER_CHAT]:
            days, kind = status(it)
            mark = "⚡ " if kind == "upcoming" and days <= _SOON_DAYS else ""
            # 别用「#3」当编号：适配器的 markdown 清洗会把行首的 # 吃掉（`^\s{0,3}#{1,6}\s*`），
            # QQ 上看到的会是「3 圣诞节…」。用「3.」这种点号编号才是所见即所得。
            lines.append(f"  {int(it.get('id', 0))}. {mark}{it.get('name', '?')}"
                         f"（{date_label(it)}）—— {describe(it)}")
        head = f"📅 可莉的纪念日小本本（最近 {within} 天）：" if within is not None \
            else "📅 可莉的纪念日小本本："
        await ctx.reply(head + "\n" + "\n".join(lines))

    async def add(ctx, raw: str):
        parts = raw.split(maxsplit=1)
        parsed = parse_date(parts[0])
        if not parsed:
            await ctx.reply(f"看不懂这个日期呀：「{_safe(parts[0], _LABEL_MAX)}」\n" + _usage())
            return
        name = _safe(parts[1]) if len(parts) > 1 else ""
        if not name:
            await ctx.reply("再给它起个名字嘛，比如：.纪念日 12-25 圣诞节")
            return
        d = _load()
        mine = mine_of(d, ctx.chat_id)
        if len(mine) >= MAX_PER_CHAT:
            await ctx.reply(f"这个聊天里已经记了 {len(mine)} 个日子啦，先删掉几个吧～"
                            f"（.纪念日 删 <编号>）")
            return
        dup = next((it for it in mine if _norm(it.get("name", "")) == _norm(name)), None)
        if dup:
            await ctx.reply(f"「{name}」已经记过啦（{date_label(dup)}）～"
                            f"想改的话先 .纪念日 删 {int(dup.get('id', 0))}")
            return
        nid = max((int(it.get("id", 0)) for it in d["items"]), default=0) + 1
        item = {"id": nid, "chat_id": ctx.chat_id, "user_id": ctx.user_id,
                "user_name": _safe(getattr(ctx, "user_name", ""), _LABEL_MAX),
                "name": name, "year": parsed["year"],
                "month": parsed["month"], "day": parsed["day"],
                "created": time.time()}
        d["items"].append(item)
        _save(d)
        when = date_label(item) + ("（每年）" if parsed["year"] is None else "（一次性）")
        await ctx.reply(f"记好啦！{when}「{name}」—— {describe(item)}，可莉会帮你数着的[表情:呲牙]")

    async def delete(ctx, arg: str) -> None:
        arg = _safe(arg.strip(), _LABEL_MAX)
        if not arg:
            await ctx.reply("用法：.纪念日 删 <编号> 或 .纪念日 删 <名字>（编号看 .纪念日）")
            return
        # 编号写法都认：「3」「#3」「3.」（列表里显示的是「3. xxx」）
        num = re.match(r"^[#＃]?(\d+)[.、．]?$", arg)
        d = _load()
        mine = mine_of(d, ctx.chat_id)
        if not mine:
            await ctx.reply("这个聊天里还没有记着的日子哦～")
            return
        victim = None
        if num:
            victim = next((it for it in mine if int(it.get("id", 0)) == int(num.group(1))), None)
        else:
            key = _norm(arg)
            hits = [it for it in mine if _norm(it.get("name", "")) == key]
            if not hits and key:
                # 精确没中再退一步找「名字里含这段」（`.纪念日 删 生日` 命中「生日快乐」）
                hits = [it for it in mine if key in _norm(it.get("name", ""))]
            if len(hits) == 1:
                victim = hits[0]
            elif len(hits) > 1:
                ids = "、".join(str(int(it.get("id", 0))) for it in hits)
                await ctx.reply(f"有 {len(hits)} 个都对得上「{arg}」诶（{ids}），用编号指定一下嘛")
                return
        if victim is None:
            await ctx.reply("没找到这个日子～用 .纪念日 看看编号？")
            return
        d["items"] = [it for it in d["items"] if it is not victim]
        _save(d)
        await ctx.reply(f"好，「{victim.get('name', '?')}」那个日子撤掉了。")

    async def cmd(ctx):
        raw = (ctx.arg_text or "").strip()
        if not raw:
            await show_list(ctx)
            return
        m = _RE_DEL.match(raw)
        if m:
            await delete(ctx, m.group(1) or "")
            return
        if _norm(raw) in _SOON_WORDS:
            await show_list(ctx, within=_SOON_DAYS)
            return
        await add(ctx, raw)

    api.on_command("纪念日", cmd, aliases=("倒数日", "纪念", "anniversary"), cooldown=3,
                   help=".纪念日 <日期> <名字> —— 记个日子倒数（.纪念日 看全部 / .纪念日 本周）")

    # 每天播报「今天到期的纪念日」。热重载会再生一个模块实例 → 用锁文件令牌让旧循环自退，
    # 否则同一个日子会被播报两遍（remind.py 同款做法）。
    try:
        BASE.mkdir(parents=True, exist_ok=True)
        my_token = uuid.uuid4().hex
        try:
            LOCK_FILE.write_text(my_token, encoding="utf-8")
        except OSError:
            pass

        async def loop():
            while True:
                await asyncio.sleep(_TICK_S)
                try:
                    if LOCK_FILE.read_text(encoding="utf-8").strip() != my_token:
                        return                                  # 被新的重载实例接管
                except OSError:
                    pass
                try:
                    await announce_once(api)
                except Exception:                               # noqa: BLE001
                    continue

        task = asyncio.get_running_loop().create_task(loop())
        _TASK_HOLD.append(task)                                 # 模块级强引用兜底（OCR #19）
        logger.info("纪念日：每天播报循环已启动（%d 点起、每 %ds 检查一次）", ANNOUNCE_HOUR, int(_TICK_S))
        if isinstance(getattr(api, "state", None), dict):
            api.state["_task"] = task                           # 强引用防 GC
    except RuntimeError:
        pass                                                    # 没有事件循环（干跑静态装载）→ 静默降级
