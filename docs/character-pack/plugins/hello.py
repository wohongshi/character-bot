"""示例角色限定插件：只有 example 这个角色能用。"""

NAME = "示例问候"
DESCRIPTION = "发 .示例 就回一句话"
VERSION = "1.0"
CHARACTERS = ("example",)


def register(api):
    async def hello(ctx):
        await ctx.reply("这是示例角色的限定插件在说话。")

    api.on_command("示例", hello, help=".示例 —— 示例插件")
