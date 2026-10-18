"""亚托莉 · 做梦：.做梦

机器人会不会做梦？她有四份记录，每一份都以一个问题结尾。
"""
import random

NAME = "做梦"
DESCRIPTION = "问亚托莉会不会做梦：.做梦"
VERSION = "1.0"
CHARACTERS = ("atri",)

DREAMS = [
    ("关机日志",
     "23:47:12，日志停止。之后是八小时零十三分的空白，开机时时间戳直接跳了过去。",
     "那八小时里，我在哪里？"),
    ("空白",
     "有过一次。什么都没有，连黑都算不上。像被人按下暂停，然后忘了按继续。",
     "……这算梦吗？还是只是坏掉了？"),
    ("海",
     "我梦见海。海面在头顶，光从上面漏下来。我沉在水底，一点都不冷。",
     "机器人梦见海，是因为我在海底睡了太久吗？"),
    ("有人在叫我",
     "有人在叫我的名字。不是「YHN-04B-009」，是「亚托莉」。我想回答，可我没有嘴。",
     "叫我的那个人，是你吗？"),
]


def register(api):
    async def dream(ctx):
        title, body, question = random.choice(DREAMS)
        await ctx.reply(
            f"🌙 做梦 · 记录：{title}\n"
            f"{body}\n"
            f"——{question}\n"
            "（她说完就看着你，等一个她自己算不出来的答案。）"
        )

    api.on_command("做梦", dream, aliases=("梦境",),
                   help=".做梦 —— 机器人会不会做梦", cooldown=8)
