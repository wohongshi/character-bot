"""好感度 & 签到：陪可莉聊天会涨好感，每天可以签到领个小礼物。

- 被动：每条消息 +1（每人每天最多 60 条计入）
- .签到 每天一次：+8~15，附赠等级进度
- .好感度 查看数值 / 等级称号 / 进度条
- 存 state/affinity.json（跨重启不丢）
"""
import asyncio
import datetime
import json
import logging
import os
import random
import sys
import time
from pathlib import Path

# _cardimg.py 与插件同目录（兜底 sys.path，见 gacha.py 同一段说明）
_HERE = str(Path(__file__).resolve().parent)
if _HERE not in sys.path:
    sys.path.append(_HERE)
import _cardimg  # noqa: E402

logger = logging.getLogger(__name__)

NAME = "好感度"
DESCRIPTION = "和可莉的友情值：聊天涨好感、每天可签到"
VERSION = "1.0"

# 角色限定：只有这些角色启用；空 = 通用插件（可在面板关闭）
CHARACTERS = ('klee',)

def _state_root() -> "Path":
    """状态目录：`KLEE_STATE_DIR` 可覆盖（干跑测试台靠它把状态写到临时目录）。"""
    base = os.environ.get("KLEE_STATE_DIR")
    return Path(base) if base else Path(__file__).resolve().parent / "state"


BASE = _state_root()
STATE_FILE = BASE / "affinity.json"
DAILY_MSG_CAP = 60

LEVELS = [(0, "路过蒙德的旅人"), (50, "眼熟的路人"), (150, "聊得来的朋友"),
          (350, "骑士团的好朋友"), (700, "蒙德城的老熟人"), (1200, "火花骑士的挚友"),
          (2000, "嘟嘟可的守护者")]


def _today() -> str:
    return datetime.date.today().isoformat()


def _load() -> dict:
    try:
        d = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except FileNotFoundError:
        return {}                                    # 首次运行：本来就没有
    except (OSError, ValueError) as e:
        # 读不动/半损/手改坏：别静默当空表（下次 _save 会把旧数据整个覆盖）。
        # 先把坏文件挪成备份，再照旧返回空表继续跑。
        logger.warning("好感度：状态读取失败（%s: %s）→ 备份后重来", type(e).__name__, e)
        try:
            STATE_FILE.replace(STATE_FILE.with_name(f"affinity.corrupt-{int(time.time())}.json"))
        except OSError:
            pass
        return {}


def _save(d: dict) -> None:
    BASE.mkdir(parents=True, exist_ok=True)
    # 临时文件名带 pid+时间：多进程/多实例并发写也不会互相踩（固定 `.tmp` 会交错写坏）
    tmp = STATE_FILE.with_name(f"{STATE_FILE.name}.{os.getpid()}-{time.time_ns() % 1_000_000}.tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    tmp.replace(STATE_FILE)


def _level(exp: int):
    """→ (等级号, 称号, 本级起点, 下一级门槛|None)"""
    idx = 0
    for i, (need, _) in enumerate(LEVELS):
        if exp >= need:
            idx = i
    nxt = LEVELS[idx + 1][0] if idx + 1 < len(LEVELS) else None
    return idx + 1, LEVELS[idx][1], LEVELS[idx][0], nxt


def _bar(exp: int, lo: int, hi) -> str:
    if not hi:
        return "▰" * 10
    p = min(1.0, max(0.0, (exp - lo) / (hi - lo)))
    done = int(p * 10)
    return "▰" * done + "▱" * (10 - done)


def render_sign_card(name: str, gain: int, exp: int, lv: int, title: str,
                     lo: int, nxt, out_path) -> bool:
    """签到小卡片（560×420）；任何环节失败返回 False（调用方回退文本）。"""
    try:
        Image, ImageDraw, ImageFont = _cardimg.load_pil()
        f_head = _cardimg.font_cute(40)
        f_sub = _cardimg.font_cjk(18)
        f_gain = _cardimg.font_cute(72)
        f_lv = _cardimg.font_cjk(22)
        f_foot = _cardimg.font_cjk(16)
    except Exception:                                # noqa: BLE001
        return False
    try:
        W, H = 560, 420
        cream, paper, ink = (255, 246, 232), (255, 253, 248), (74, 52, 42)
        red, gold, line = (232, 72, 79), (238, 168, 44), (242, 220, 196)
        im = Image.new("RGB", (W, H), cream)
        d = ImageDraw.Draw(im)
        # 顶栏
        d.rounded_rectangle([18, 16, 542, 96], radius=24, fill=paper, outline=line, width=3)
        d.text((280, 46), "可莉的友情手账", font=f_head, fill=red, anchor="mm")
        d.text((280, 78), f"—— {_cardimg.safe_name(name, 12)} 的签到 ——", font=f_sub,
               fill=(148, 116, 90), anchor="mm")
        # 好感 +N
        d.rounded_rectangle([28, 112, 532, 236], radius=22, fill=(255, 247, 221),
                            outline=gold, width=4)
        d.text((280, 174), f"+{int(gain)}", font=f_gain, fill=(214, 140, 10), anchor="mm")
        d.text((280, 214), "点好感，收好啦！", font=f_sub, fill=(186, 116, 8), anchor="mm")
        # 等级与进度
        d.text((280, 262), f"Lv.{lv} {_cardimg.short(title, 14)} · 现在 {int(exp)} 点",
               font=f_lv, fill=ink, anchor="mm")
        x0, x1, y0, y1 = 90, 470, 288, 306
        d.rounded_rectangle([x0, y0, x1, y1], radius=9, fill=(246, 236, 220))
        p = 1.0 if not nxt else max(0.0, min(1.0, (int(exp) - lo) / (nxt - lo)))
        if p > 0:
            d.rounded_rectangle([x0, y0, x0 + int((x1 - x0) * p), y1], radius=9, fill=red)
        foot = (f"再攒 {nxt - int(exp)} 点到下一级～" if nxt
                else "已经是最高级啦，可莉把你当最好的朋友！")
        d.text((280, 336), foot, font=f_sub, fill=(148, 116, 90), anchor="mm")
        d.text((280, H - 34), f"{datetime.date.today():%Y-%m-%d} · 可莉给你盖了个章",
               font=f_foot, fill=(186, 156, 126), anchor="mm")
        _cardimg.save_img(im, out_path)
        return True
    except Exception:                                # noqa: BLE001
        return False


def _exp_of(v) -> int:
    """安全取某个用户的 exp（手改/半损状态不炸整条命令）。"""
    try:
        return int(v.get("exp", 0)) if isinstance(v, dict) else 0
    except (TypeError, ValueError):
        return 0


def register(api):
    def _entry(d: dict, uid: str) -> dict:
        u = d.get(uid)
        if not isinstance(u, dict):                              # 手改/半损：坏条目就地重置
            u = {"exp": 0, "day": "", "day_msgs": 0, "signed": "", "streak": 0}
            d[uid] = u
        if u.get("day") != _today():
            u["day"], u["day_msgs"] = _today(), 0
        return u

    async def passive(ctx):
        try:
            d = _load()
            u = _entry(d, ctx.user_id)
            dm = u.get("day_msgs")
            dm = dm if isinstance(dm, int) else 0
            if dm < DAILY_MSG_CAP:
                u["day_msgs"] = dm + 1
                u["exp"] = _exp_of(u) + 1
                _save(d)
        except Exception as e:                                   # noqa: BLE001
            # 热路径，但「一直失败却毫无声响」更危险（磁盘满/权限错时好感度会静默全丢）
            logger.warning("好感度：被动+1失败（%s: %s）", type(e).__name__, e)
        return None

    async def sign_cmd(ctx):
        d = _load()
        u = _entry(d, ctx.user_id)
        if u.get("signed") == _today():
            lv, title, _, _ = _level(_exp_of(u))
            st = u.get("streak")
            st = st if isinstance(st, int) and st > 0 else 0
            await ctx.reply(f"诶嘿，今天已经签过到啦～{ctx.user_name} 的好感度是 "
                            f"{u['exp']}（Lv.{lv} {title}）"
                            + (f"\n🔥 现在是连签第 {st} 天，明天也要来哦！" if st >= 2 else ""))
            return
        # 连签：昨天签过 → +1；断签 → 回到 1
        prev = u.get("signed") or ""
        yesterday = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
        streak = u.get("streak")
        streak = streak if isinstance(streak, int) and streak > 0 else 0
        streak = streak + 1 if prev == yesterday else 1
        gain = random.randint(8, 15)
        bonus = min(streak, 7) if streak >= 2 else 0     # 连签奖励：第 2 天 +2 … 第 7 天起 +7
        gain += bonus
        u["signed"] = _today()
        u["streak"] = streak
        u["exp"] = _exp_of(u) + gain
        _save(d)
        lv, title, lo, nxt = _level(u["exp"])
        tail = (f"离「{LEVELS[lv][1] if lv < len(LEVELS) else title}」还差 {nxt - u['exp']} 点～"
                if nxt else "已经是最高级啦，可莉把你当最好的朋友！")
        streak_line = ""
        if streak >= 2:
            streak_line = f"🔥 连签第 {streak} 天！"
            if streak in (7, 14, 30, 60, 100):
                streak_line += "可莉给你撒花！🎉"
        name = _cardimg.safe_name(ctx.user_name or "你", 12)
        gain_txt = f"+{gain}" + (f"（连签奖励 +{bonus}）" if bonus else "")
        try:
            out = _cardimg.card_path(
                _cardimg.cards_dir(dict(getattr(api, "settings", None) or {}), BASE), "sign", ctx.user_id)
        except Exception as e:                                   # noqa: BLE001
            logger.warning("签到：卡片目录不可用（%s）→ 回退文本", e)
            out = None
        if out is not None and await asyncio.to_thread(
                render_sign_card, name, gain, u["exp"], lv, title, lo, nxt, out):
            caption = (f"叮！{name} 今天的好感 {gain_txt}，收下可莉的印章吧～"
                       + (f"（连签 ×{streak}）" if streak > 1 else ""))
            if await _cardimg.send_card(ctx, out, caption, pattern="sign-*.png"):
                return
        await ctx.reply(f"叮！{name} 今天的好感 {gain_txt}，现在 {u['exp']} 点"
                        f"（Lv.{lv} {title}）。{streak_line}{tail}[表情:呲牙]"
                        .replace("。。", "。"))

    async def query_cmd(ctx):
        d = _load()
        exp = _exp_of(d.get(ctx.user_id))
        lv, title, lo, nxt = _level(exp)
        if nxt:
            await ctx.reply(f"{ctx.user_name} 和可莉的友情：{exp} 点（Lv.{lv} {title}）\n"
                            f"{_bar(exp, lo, nxt)}  再攒 {nxt - exp} 点到下一级～")
        else:
            await ctx.reply(f"{ctx.user_name} 和可莉的友情：{exp} 点（Lv.{lv} {title}）\n"
                            f"{'▰' * 10}  满啦！可莉最最喜欢你了！")

    async def rank_cmd(ctx):
        d = _load()
        rows = sorted(((_exp_of(v), str(uid))
                       for uid, v in d.items() if _exp_of(v) > 0),
                      key=lambda x: (-x[0], x[1]))
        if not rows:
            await ctx.reply("排行榜还空空的呢～和大家聊聊天就能涨好感啦！")
            return
        names: dict = {}
        if getattr(ctx, "is_group", False) and getattr(ctx, "group_id", ""):
            fn = getattr(ctx.adapter, "_member_names", None)
            if fn:
                try:
                    names = await fn(ctx.group_id)
                except Exception:                            # noqa: BLE001
                    names = {}

        def disp(uid: str) -> str:
            nm = names.get(str(uid))
            return str(nm)[:12] if nm else f"旅行者·{uid[-4:]}"

        medals = ("🥇", "🥈", "🥉")
        lines = []
        for i, (exp, uid) in enumerate(rows[:10]):
            mark = medals[i] if i < 3 else f"{i + 1}."
            me = "（你）" if uid == str(ctx.user_id) else ""
            lines.append(f"{mark} {disp(uid)}{me} —— {exp}")
        mine = next(((i + 1, exp) for i, (exp, uid) in enumerate(rows)
                     if uid == str(ctx.user_id)), None)
        tail = ""
        if mine and mine[0] > 10:
            tail = f"\n……你的名次：第 {mine[0]} 名（{mine[1]} 点）"
        await ctx.reply("🏆 可莉的好感度排行榜：\n" + "\n".join(lines) + tail)

    api.on_message(passive, priority=95)
    api.on_command("签到", sign_cmd, cooldown=3, help=".签到 —— 每天来领可莉的礼物")
    api.on_command("好感度", query_cmd, aliases=("亲密度",), cooldown=3,
                   help=".好感度 —— 看看你和可莉的友情值")
    api.on_command("排行榜", rank_cmd, aliases=("排行", "榜单"), cooldown=10,
                   help=".排行榜 —— 好感度前十名（群里会显示大家的名字）")
