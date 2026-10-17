"""昔莲 · 迷迷：.迷迷 [摸摸/喂/抱/说话]

迷迷是跟着她的那只小家伙，不会说人话，但情绪很准。
"""
import random

from _plugstore import safe_text

NAME = "迷迷"
DESCRIPTION = "逗一逗迷迷：.迷迷 [摸摸/喂/抱/说话]"
VERSION = "1.0"
CHARACTERS = ("cyrene",)

ACTIONS = {
    "摸摸": ["迷迷眯起眼睛，尾巴绕住了你的手腕。", "迷迷把头往你掌心顶了顶，然后假装什么都没发生。"],
    "喂": ["迷迷闻了闻，先看我一眼，才小口吃。", "迷迷吃完了，把空碗推到你面前，意思是还要。"],
    "抱": ["迷迷在你怀里缩成一团，发出很小的呼噜声。", "迷迷挣扎了两下，然后放弃了，把脸埋起来。"],
    "说话": ["迷迷歪头，认真地听，最后「咪」了一声。大概是在附和。", "迷迷叫了一声，我不确定它在替谁说话。"],
}
DEFAULT = ["迷迷绕着你转了两圈，确认你没有恶意，才坐下。",
           "迷迷从桌子底下探出头，看了你一会儿，又缩回去了。",
           "迷迷趴在我膝盖上，尾巴一下一下地拍。"]


def register(api):
    async def mimi(ctx):
        arg = safe_text(ctx.arg_text, 10)
        # 只认「第一个词就是动作」：不然「抱歉」「不想说话」都会误触发
        hit = next((v for k, v in ACTIONS.items() if arg.startswith(k)), None)
        line = random.choice(hit) if hit else random.choice(DEFAULT)
        await ctx.reply(f"🪶 {line}\n（迷迷不会说话。但它记得每一个对它好的人。）")

    async def mimi_name(ctx):
        await ctx.reply(
            "🪶 关于迷迷\n"
            "· 它跟着我走了很多很多次轮回\n"
            "· 它不记得每一次，但它记得我\n"
            "· 它喜欢被摸头，不喜欢被抱太久\n"
            "· 它叫迷迷——名字是我起的，它好像没同意，也没反对"
        )

    api.on_command("迷迷", mimi, help=".迷迷 [摸摸/喂/抱/说话] —— 逗一逗它", cooldown=4)
    api.on_command("迷迷档案", mimi_name, help=".迷迷档案 —— 关于那只小家伙", cooldown=10)
