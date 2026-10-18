"""掷骰子：``.r 2d6+1`` / ``.roll 3d6`` / ``.骰子 1d100``"""

import random
import re

NAME = "掷骰子"
DESCRIPTION = "掷骰子：.r 2d6+1（最多 20 颗、单颗最多 10000 面）"
VERSION = "1.0"

_EXPR_FULL = re.compile(r"^(\d{0,3})\s*[dD]\s*(\d{1,5})(?:\s*([+-])\s*(\d{1,5}))?$")
_EXPR_HEAD = re.compile(r"^\s*(\d{0,3})\s*[dD]\s*(\d{1,5})(?:\s*([+-])\s*(\d{1,5}))?")


def register(api):
    async def roll(ctx):
        expr = (ctx.arg_text or "").strip()
        norm = (expr.replace("＋", "+").replace("－", "-").replace("—", "-")
                .replace("Ｄ", "d").replace("ｄ", "d").replace("　", " "))
        m = _EXPR_FULL.match(norm) if norm else _EXPR_FULL.match("1d6")
        if not m and norm:
            m = _EXPR_HEAD.match(norm)          # 「.r 2d6 来一发」：认开头的表达式，尾巴忽略
        if not m:
            await ctx.reply("诶，格式她看不懂啦——写成「.r 2d6+1」好不好？（2 颗六面骰再加 1）")
            return
        count = max(1, min(int(m.group(1) or 1), 20))
        sides = max(2, min(int(m.group(2)), 10000))
        bonus = int(m.group(4)) if m.group(3) else 0
        if m.group(3) == "-":
            bonus = -bonus
        rolls = [random.randint(1, sides) for _ in range(count)]
        detail = " + ".join(str(r) for r in rolls)
        bonus_s = f"{bonus:+d}" if bonus else ""
        if bonus:
            detail += f" {'+' if bonus > 0 else '−'} {abs(bonus)}"
        total = sum(rolls) + bonus
        await ctx.reply(f"🎲 她帮你摇：{count}d{sides}{bonus_s} → {detail} = 【{total}】")

    api.on_command("r", roll, aliases=("roll", "骰子", "dice"), help=".r 2d6+1 —— 掷骰子")
