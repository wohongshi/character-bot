"""猜数字：``.猜数字`` 开一局（1~100，七次机会）、``.猜 <数字>`` 猜、``.放弃`` 认输。

状态存在 api.state（内存），按聊天（群/私聊各自独立）；群里大家都能一起猜。
超过 2 小时没动静的残局会在下次开局时自动作废。
"""

import random
import time

NAME = "猜数字"
DESCRIPTION = "小游戏：.猜数字 开一局，再用 .猜 <数字>"
VERSION = "1.0"

TRIES = 7
STALE_AFTER = 2 * 3600


def _new_target() -> int:
    return random.randint(1, 100)


def _games(api) -> dict:
    return api.state.setdefault("guess.games", {})


def register(api):
    async def start_cmd(ctx):
        games = _games(api)
        g = games.get(ctx.chat_id)
        now = time.time()
        if g and now - float(g.get("ts") or 0) > STALE_AFTER:
            g = None                                     # 残局作废
        if g:
            await ctx.reply(f"诶，这一局还没结束哦——还剩 {g['left']} 次机会。"
                            f"继续 .猜 <数字> 嘛，或者 .放弃")
            return
        games[ctx.chat_id] = {"target": _new_target(), "left": TRIES,
                              "ts": now, "starter": ctx.user_id}
        await ctx.reply(f"来猜数字啦！她心里想了一个 1~100 的数字，"
                        f"给大家 {TRIES} 次机会～\n用「.猜 42」这样告诉我！")

    async def guess_cmd(ctx):
        games = _games(api)
        g = games.get(ctx.chat_id)
        if not g:
            await ctx.reply("还没有开始哦，先 .猜数字 开一局嘛～")
            return
        raw = (ctx.arg_text or "").strip()
        if not raw.isdigit():
            await ctx.reply("诶诶，要发数字呀，比如 .猜 42")
            return
        n = int(raw)
        if not 1 <= n <= 100:
            await ctx.reply("要猜 1~100 之间的数字哦～")
            return
        g["ts"] = time.time()
        if n == g["target"]:
            used = TRIES - g["left"] + 1
            del games[ctx.chat_id]
            praise = ("天呐！你也太准了吧！！" if used <= 3 else
                      "哇，好厉害嘛，这么快就猜中啦！" if used <= 5 else
                      "嘿嘿，猜中啦！")
            await ctx.reply(f"🎉 {praise}就是「{n}」，只用了 {used} 次～")
            return
        g["left"] -= 1
        if g["left"] <= 0:
            ans = g["target"]
            del games[ctx.chat_id]
            await ctx.reply(f"呜……机会用完了，其实是「{ans}」啦。下次再来嘛～")
            return
        hint = "大了大了！" if n > g["target"] else "小了小了！"
        await ctx.reply(f"{hint}还剩 {g['left']} 次～")

    async def giveup_cmd(ctx):
        games = _games(api)
        g = games.pop(ctx.chat_id, None)
        if not g:
            await ctx.reply("现在没有正在进行的猜数字哦～想玩就 .猜数字 呀")
            return
        await ctx.reply(f"好嘛，不猜啦就不猜啦～答案是「{g['target']}」哦！")

    api.on_command("猜数字", start_cmd, aliases=("猜数",), cooldown=3,
                   help=".猜数字 —— 开一局 1~100 猜数字（七次机会）")
    api.on_command("猜", guess_cmd, cooldown=1,
                   help=".猜 <数字> —— 猜一下！")
    api.on_command("放弃", giveup_cmd, aliases=("投降", "不猜了"), cooldown=3,
                   help=".放弃 —— 认输并揭晓答案")
