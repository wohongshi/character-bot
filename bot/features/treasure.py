"""可莉 · 藏宝图：.藏宝图 / .挖宝 B3

每张图按「会话 + 用户」保存一个 5×5 的藏宝点；挖中一次就换新图。
"""
import random

from _plugstore import chat_key, db_get, db_set, state_path

NAME = "藏宝图"
DESCRIPTION = "她画藏宝图，你来挖：.藏宝图、.挖宝 B3"
VERSION = "1.0"
CHARACTERS = ("klee",)

FILE = state_path("klee_treasure")
COLS = "ABCDE"


def _key(ctx) -> str:
    return f"{chat_key(ctx)}:{ctx.user_id}"


def _render(dug: list[str], found: str = "") -> str:
    lines = ["　 A B C D E"]
    for r in range(1, 6):
        row = []
        for c in COLS:
            cell = f"{c}{r}"
            row.append("💎" if cell == found else ("🕳" if cell in dug else "·"))
        lines.append(f"{r} {' '.join(row)}")
    return "\n".join(lines)


def register(api):
    async def draw(ctx):
        spot = random.choice([f"{c}{r}" for c in COLS for r in range(1, 6)])
        db_set(FILE, _key(ctx), {"spot": spot, "dug": []})
        await ctx.reply(
            "🗺 可莉画好藏宝图啦！宝藏就埋在下面某一格：\n"
            f"{_render([])}\n"
            "用 .挖宝 B3 这样告诉我挖哪儿～（只有 6 次机会哦）"
        )

    async def dig(ctx):
        cell = (ctx.arg_text or "").strip().upper().replace(" ", "")
        if len(cell) != 2 or cell[0] not in COLS or cell[1] not in "12345":
            await ctx.reply("诶？格子要写成 B3 这样呀，字母 A-E + 数字 1-5。")
            return
        key = _key(ctx)
        g = db_get(FILE, key, None)
        if not isinstance(g, dict) or not g.get("spot"):
            await ctx.reply("还没有藏宝图呢，先发 .藏宝图 让可莉画一张！")
            return
        dug = list(g.get("dug") or [])
        if cell in dug:
            await ctx.reply(f"这里已经挖过啦（{cell}），换个地方嘛～")
            return
        dug.append(cell)
        if cell == g["spot"]:
            db_set(FILE, key, {"spot": "", "dug": []})
            await ctx.reply(
                f"💎 哇！！{cell} 挖到啦！\n{_render(dug, cell)}\n"
                "是可莉藏的亮晶晶小石头！分你一半～（新图可以再发 .藏宝图）"
            )
            return
        if len(dug) >= 6:
            db_set(FILE, key, {"spot": "", "dug": []})
            await ctx.reply(
                f"🕳 {cell} 是空的……六次用完啦，宝藏还在地里。\n{_render(dug)}\n"
                f"偷偷告诉你：其实在 {g['spot']}。下次再来！"
            )
            return
        db_set(FILE, key, {"spot": g["spot"], "dug": dug})
        hint = "有点热" if _near(cell, g["spot"]) else "凉凉的"
        await ctx.reply(f"🕳 {cell} 挖开——什么都没有。\n{_render(dug)}\n"
                        f"可莉凑过来闻了闻：这里{hint}……还剩 {6 - len(dug)} 次！")

    def _near(a: str, b: str) -> bool:
        return abs(COLS.index(a[0]) - COLS.index(b[0])) <= 1 and abs(int(a[1]) - int(b[1])) <= 1

    api.on_command("藏宝图", draw, aliases=("宝藏图",), help=".藏宝图 —— 让可莉画一张 5×5 藏宝图", cooldown=4)
    api.on_command("挖宝", dig, aliases=(), help=".挖宝 B3 —— 在藏宝图上挖一格")
