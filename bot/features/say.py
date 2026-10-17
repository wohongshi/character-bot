"""说给你听：.说 <文本> —— 用可莉的声音念一句（MIMO TTS，走适配器 [语音:] 管线）。"""

NAME = "语音说说"
DESCRIPTION = "让可莉用语音念一句话"
VERSION = "1.0"

CHARACTERS = ('klee',)


def register(api):
    async def say_cmd(ctx):
        text = ctx.arg_text.strip()[:80]
        if not text:
            await ctx.reply("用法：.说 <想听的话>，比如 .说 可莉最喜欢炸鱼啦")
            return
        if not getattr(ctx.adapter, "tts_enabled", False):
            await ctx.reply("语音功能现在没开哦（tts_enabled=false）")
            return
        await ctx.adapter.send(ctx.chat_id, f"[语音:{text}]")

    api.on_command("说", say_cmd, aliases=("语音",), cooldown=20,
                   help=".说 <文本> —— 让可莉用语音念出来")

    async def sing_cmd(ctx):
        text = ctx.arg_text.strip()[:60]
        if not text:
            await ctx.reply("用法：.唱 <想听的词>，比如 .唱 一闪一闪亮晶晶")
            return
        if not getattr(ctx.adapter, "tts_enabled", False):
            await ctx.reply("语音功能现在没开哦（tts_enabled=false）")
            return
        await ctx.adapter.send(ctx.chat_id, f"[唱歌:{text}]")

    api.on_command("唱", sing_cmd, aliases=("唱歌",), cooldown=30,
                   help=".唱 <文本> —— 让可莉唱给你听（试作版）")
