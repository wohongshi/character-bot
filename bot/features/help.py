"""帮助：``.help`` / ``.帮助`` —— 命令表，发一张「长图」（渲染失败回退纯文本）。

- 分组表 ``_GROUPS`` 是**手写**的：新插件记得到场（兜底的「其他」堆一涨，测试会喊）。
- 版面：560 宽自适应长图（星空底 + 金框，和祈愿/抽卡同一个家族）：糖果色小组牌 →
  每条命令一行（长说明换行缩进）+ 页脚一句。
- 图里**不画 emoji**（PIL 画不出彩色 emoji，会变方块）；文字版回退照旧带 emoji。
- ``settings.help.card=false`` 只发文字；渲染/发送任何失败都静默回退文字（fail-soft）。
"""
from __future__ import annotations

import asyncio
import logging
import os
import random
from pathlib import Path

import _cardimg

NAME = "帮助"
DESCRIPTION = "帮助菜单：.help —— 看看本角色都会什么（一张长图）"
VERSION = "1.2"

BASE = Path(__file__).resolve().parent
logger = logging.getLogger("klee.help")

# (文字菜单 emoji, 长图小组牌, 牌颜色, 命令名...)
_GROUPS = [
    ("🎲", "小游戏", (244, 152, 188), ("抽卡", "十连", "猜数字", "猜", "放弃", "接龙", "接", "认输",
                                      "猜拳", "投票", "投", "计票", "开票", "谜语", "猜谜", "谜底", "r",
                                      "帮我选", "选择", "帮我决定", "决定", "选")),
    ("🔮", "掐指一算", (172, 148, 236), ("运势", "一言", "天气", "塔罗")),
    ("🏵️", "原神", (232, 196, 106), ("练度", "每日素材", "随机角色", "角色卡", "命座", "天赋", "生日", "祈愿", "祈愿十连",
                                       "祈愿武器", "祈愿常驻", "定轨", "祈愿统计", "祈愿概率", "祈愿排行", "祈愿记录")),
    ("⏰", "请她帮忙", (118, 198, 168), ("提醒", "提醒列表", "取消提醒", "说", "唱", "画画", "二维码",
                                       "纪念日", "倒数日", "纪念")),
    ("💛", "陪聊互动", (244, 176, 128), ("签到", "好感度", "排行榜", "赞我", "夸夸", "摸摸头", "歇后语", "可莉",
                                        "语录", "角色介绍", "介绍")),
    ("🎀", "角色专属", (236, 168, 196), (
        # 可莉
        "炸弹", "炸鱼", "藏宝图", "挖宝", "优惠券",
        # 胡桃
        "打油诗", "蝴蝶签", "往生堂", "超度",
        # 茜特菈莉
        "烟雾签", "熬夜", "观星", "星图", "小酌", "干杯",
        # 昔莲
        "追更", "书架", "存档", "记忆", "写书", "翻书", "轮回", "轮回数", "归零",
        "回声", "放下", "迷迷", "迷迷档案", "摇篮曲", "档案", "重逢", "想念", "雪", "约定",
        # 亚托莉
        "自检", "日志", "倒计时", "重置倒计时", "潜水", "火箭拳", "充能", "伊甸", "信标",
        "电量", "充电", "维修", "上油", "上学", "课表", "高性能", "性能对比", "眼泪", "抱抱",
        # 胡桃（第二批）
        "幽灵", "收留", "卜卦", "恶作剧", "吓人", "禁忌", "夜游", "生死簿", "纸扎", "斗诗",
        # 茜特菈莉（第二批）
        "解梦", "解酒", "酒馆", "唠叨", "收藏", "藏品", "打盹", "兆头", "烟形",
        # 昔莲（第二批）
        "借阅", "照片", "写信", "点灯", "列车", "碎片", "今天", "胶囊",
        # 亚托莉（第二批）
        "做饭", "作业", "做梦", "校准", "哼歌", "下雨", "四十五天", "礼物",
        # 涂山红红
        "红线", "姻缘", "护短", "狐火", "尾巴", "涂山", "判词", "苦情树", "守夜", "契",
        "试爪", "妖盟令", "来客", "喝酒", "旧伤", "取名", "山雨", "教一招", "月下", "续缘")),
    ("🧰", "小工具", (150, 154, 190), ("help", "features", "角色", "本群角色", "绑角色")),
]

# —— 长图配色（与祈愿卡同族）——
HELP_CACHE_V = 1            # 长图版式版本：改设计就 +1（渲染缓存 key 的一部分）
W = 560
SKY_TOP, SKY_BOTTOM = (26, 28, 58), (48, 44, 96)
GOLD, GOLD_DIM = (232, 196, 106), (150, 124, 66)
LIGHT, MUTED = (232, 228, 246), (152, 144, 190)
DARK = (30, 30, 52)
NAME_C = (240, 226, 190)


def _name_of(entry: str) -> str:
    """从「.抽卡 <…> —— 说明」取命令名：抽卡。空条目 → ''（落进「其他」组，不炸）。"""
    tokens = (entry or "").split(maxsplit=1)
    if not tokens:          # "" / "   " → split 返回空列表（OCR 二轮 #8）
        return ""
    return tokens[0].lstrip(".／").split("<")[0].strip()


def _split_entry(entry: str) -> tuple[str, str]:
    """「.抽卡 —— 可莉牌十连开箱」→ ('.抽卡', '可莉牌十连开箱')；没有分隔就整条当名字。"""
    name, sep, desc = (entry or "").partition(" —— ")
    return (name.strip(), desc.strip() if sep else "")


def _opt_bool(v, default: bool = True) -> bool:
    """settings 里读开关：缺省/坏值走 default，只有明确「假」才关（与 wish 同口径）。"""
    if v is None:
        return default
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    if isinstance(v, str):
        return v.strip().lower() not in ("false", "0", "no", "off", "关", "关闭", "不")
    return default


def _build_blocks(cmds: list[str]) -> list[tuple[str, str, tuple, list[tuple[str, str]]]]:
    """命令表 → 分组块：[(emoji, 牌名, 牌色, [(命令, 说明), ...]), ...]"""
    rest = list(cmds)
    blocks = []
    for emoji, chip, color, names in _GROUPS:
        rows = []
        for entry in list(rest):
            if _name_of(entry) in names:
                rows.append(_split_entry(entry))
                rest.remove(entry)
        if rows:
            blocks.append((emoji, chip, color, rows))
    if rest:
        blocks.append(("🧩", "其他", (150, 154, 190), [_split_entry(e) for e in rest]))
    return blocks


def _fallback_text(blocks, name: str = "可莉") -> str:
    head = f"🌸 {name}的小本领（点点它们就好，不用 @）："
    tail = f"（其他时候{name}就是陪大家聊天呀）"
    body = []
    for emoji, chip, _color, rows in blocks:
        lines = [f" · {name}" + (f" —— {desc}" if desc else "") for name, desc in rows]
        body.append(f"{emoji} {chip}\n" + "\n".join(lines))
    return head + "\n\n" + "\n\n".join(body) + "\n\n" + tail


def _klee_avatar() -> Path | None:
    """可莉头像（只读本地缓存，命令里绝不起下载）；找不到就只是没头像。"""
    roots = []
    env = os.environ.get("KLEE_STATE_DIR")
    if env:
        roots.append(Path(env) / "genshin_cache")
    roots += [BASE / "state" / "genshin_cache", BASE / "genshin_cache"]
    for r in roots:
        try:
            p = r / "UI_AvatarIcon_Klee.png"
            if p.exists() and p.stat().st_size > 1024:
                return p
        except OSError:
            continue
    return None


def _wrap(text: str, font, limit: float) -> list[str]:
    """按像素宽贪心折行（中文逐字；命令名/说明都短，不省略）。"""
    lines, cur = [], ""
    for ch in text:
        if cur and font.getlength(cur + ch) > limit:
            lines.append(cur)
            cur = ch
        else:
            cur += ch
    if cur:
        lines.append(cur)
    return lines or [""]


def render_help(blocks, out_path, name: str = "可莉") -> bool:
    """画长图并落盘。任何异常 → False（调用方回退文字）。"""
    try:
        Image, ImageDraw, ImageFont = _cardimg.load_pil()
        f_title = _cardimg.font_cute(42)
        f_sub = _cardimg.font_cjk(18)
        f_chip = _cardimg.font_cute(25)
        f_name = _cardimg.font_cute(24)
        f_desc = _cardimg.font_cjk(17)
        f_foot = _cardimg.font_cjk(17)
        f_brand = _cardimg.font_cute(16)
    except Exception as e:                                # noqa: BLE001
        logger.warning("帮助：字体加载失败（%s: %s）→ 回退文本", type(e).__name__, e)
        return False
    try:
        LEFT = 34
        name_x = LEFT + 20
        desc_indent = name_x + 26

        # —— 先量高度（换行/同行判定和下面画法同一套公式）——
        layout = []                                        # [(chip, color, [(name, nl, desc, dl, same), ...])]
        H = 172 + 150                                      # 头部 + 页脚区
        for _emoji, chip, color, rows in blocks:
            group_rows = []
            H += 52
            for name, desc in rows:
                nl = _wrap(name, f_name, W - name_x - LEFT)
                same = False
                dl: list[str] = []
                if desc:
                    if len(nl) == 1 and (name_x + f_name.getlength(name) + 16
                                         + f_desc.getlength(desc)) <= W - LEFT - 20:
                        same = True
                    else:
                        dl = _wrap(desc, f_desc, W - desc_indent - LEFT)
                group_rows.append((name, nl, desc, dl, same))
                H += (38 if same else 30 * len(nl) + 25 * len(dl) + 8)
            layout.append((chip, color, group_rows))

        # —— 底：星空渐变 + 星点 + 双线金框 ——
        im = Image.new("RGB", (W, H), SKY_TOP)
        d = ImageDraw.Draw(im)
        for yy in range(H):
            t = yy / max(1, H - 1)
            d.line([(0, yy), (W, yy)],
                   fill=tuple(int(a + (b - a) * t) for a, b in zip(SKY_TOP, SKY_BOTTOM)))
        srng = random.Random(20261016)
        for _ in range(int(H * 0.012)):
            sx = srng.randint(24, W - 24)
            sy = srng.randint(155, H - 60)
            r = srng.choice((1, 1, 2))
            v = srng.randint(70, 150)
            d.ellipse([sx - r, sy - r, sx + r, sy + r], fill=(v, v, min(255, v + 30)))
        d.rounded_rectangle([14, 14, W - 14, H - 14], radius=26, outline=GOLD, width=3)
        d.rounded_rectangle([25, 25, W - 25, H - 25], radius=22, outline=GOLD_DIM, width=1)

        # —— 头部 ——
        d.text((LEFT, 52), f"{name}的小本领", font=f_title, fill=GOLD)
        d.text((LEFT, 108), "点点它们就好，不用 @", font=f_sub, fill=MUTED)
        av = _klee_avatar()
        if av is not None:
            if _cardimg.paste_icon(im, str(av), W - 88, 58, 82, radius=41, log=logger) is not None:
                d.ellipse([W - 129, 58, W - 47, 140], outline=(244, 152, 188), width=3)
        d.line([LEFT, 152, W - LEFT, 152], fill=GOLD_DIM, width=1)

        # —— 分组 ——
        y = 172
        for chip, color, group_rows in layout:
            cw = 148                                          # 固定宽：分隔线起点才齐
            d.rounded_rectangle([LEFT, y, LEFT + cw, y + 38], radius=19, fill=color)
            d.text((LEFT + cw / 2, y + 20), chip, font=f_chip, fill=DARK, anchor="mm")
            d.text((LEFT + cw + 12, y + 20), f"×{len(group_rows)}", font=f_sub, fill=MUTED, anchor="lm")
            d.line([LEFT + cw + 60, y + 19, W - LEFT, y + 19], fill=GOLD_DIM, width=1)
            y += 52
            for name, nl, desc, dl, same in group_rows:
                d.ellipse([LEFT + 2, y + 9, LEFT + 10, y + 17], fill=color)
                for i, ln in enumerate(nl):
                    d.text((name_x, y + i * 30), ln, font=f_name, fill=NAME_C)
                if same:
                    d.text((name_x + f_name.getlength(name) + 16, y + 8), desc,
                           font=f_desc, fill=MUTED)
                    y += 38
                else:
                    y += 30 * len(nl)
                    for ln in dl:
                        d.text((desc_indent, y - 2), ln, font=f_desc, fill=MUTED)
                        y += 25
                    y += 8

        # —— 页脚 ——
        y += 12
        d.line([LEFT, y, W - LEFT, y], fill=GOLD_DIM, width=1)
        d.text((W / 2, y + 36), f"（其他时候{name}就是陪大家聊天呀）", font=f_foot,
               fill=MUTED, anchor="mm")
        d.text((W / 2, y + 68), f"{name} · CHARACTER BOT", font=f_brand,
               fill=GOLD_DIM, anchor="mm")
        _cardimg.save_img(im, out_path)
        return True
    except Exception as e:                                # noqa: BLE001
        logger.warning("帮助：长图渲染失败（%s: %s）→ 回退文本", type(e).__name__, e)
        return False


def register(api):
    settings = dict(getattr(api, "settings", None) or {})
    card_on = _opt_bool(settings.get("card"), True)        # settings.card=false → 只发文字

    # 不要在 register 时把名字钉死：通用插件在会话绑定到别的角色时也要说对话
    # （原来这里读一次 api.character，绑定胡桃的群里会写成「可莉的小本领」）。
    def _me() -> str:
        return str((getattr(api, "character", {}) or {}).get("name") or "可莉")

    async def help_cmd(ctx):
        me = _me()
        cmds = api.commands_help(getattr(ctx, "character", "") or api.character_id)
        if not cmds:
            await ctx.reply(f"{me}现在只会聊天呀～")
            return
        blocks = _build_blocks(cmds)
        fallback = _fallback_text(blocks, me)
        if not card_on:
            await ctx.reply(fallback)
            return
        try:
            out = _cardimg.card_path(_cardimg.cards_dir(settings, BASE), "help", ctx.user_id)
        except OSError as e:
            logger.warning("帮助：卡片目录不可用（%s）→ 回退文本", e)
            out = None
        if out is not None:
            key = _cardimg.rcache_key("help", HELP_CACHE_V, *cmds)
            if await asyncio.to_thread(_cardimg.rcache_render, settings, BASE, "help", key, out,
                                       lambda: render_help(blocks, out)):
                if await _cardimg.send_card(ctx, out, f"🌸 {me}的小本领（点点它们就好，不用 @）",
                                            pattern="help-*.png"):
                    return
        await ctx.reply(fallback)

    api.on_command("help", help_cmd, aliases=("帮助", "菜单", "命令", "commands"),
                   help=".help —— 看看本角色会什么（按用途分组，一张长图）")
