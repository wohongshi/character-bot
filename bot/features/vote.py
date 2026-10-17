"""群投票：``.投票 火锅 烧烤 日料`` 开一票 → ``.投 2`` → ``.计票`` → ``.开票`` 结算。

- 一个聊天同时只有一票；旧票没结束前不许开新票（先 .开票 或等它 30 分钟过期）
- 每人一票、可以随时改票（只记最后一次）
- 结算限发起人 / 主人 / 群管理；平票会明说
- 状态在 api.state（内存），热重载即清零；残局 30 分钟自动作废
"""

import re
import time

NAME = "投票"
DESCRIPTION = "群投票：.投票 火锅 烧烤 → .投 2，.计票 看票数"
VERSION = "1.0"

MAX_OPTIONS = 8
MAX_OPTION_CHARS = 16
STALE_AFTER = 30 * 60
_SPLIT = re.compile(r"[\s、,，|/]+")


def _games(api) -> dict:
    return api.state.setdefault("vote.games", {})


def _alive(game) -> bool:
    return bool(game) and time.time() - float(game.get("ts") or 0) <= STALE_AFTER


def _drop_stale(api, chat_id: str) -> None:
    """残局（>30 分钟）直接作废；调用方不需要知道清了哪一局。"""
    g = _games(api).get(chat_id)
    if g and not _alive(g):
        _games(api).pop(chat_id, None)


def _tally(game):
    counts = {}
    for idx in game["votes"].values():
        counts[idx] = counts.get(idx, 0) + 1
    return counts


def _bar(frac, width=8):
    n = int(round(frac * width)) if frac > 0 else 0
    return "▰" * n + "▱" * (width - n)


def render(game) -> str:
    counts = _tally(game)
    total = len(game["votes"])
    lines = []
    for i, opt in enumerate(game["options"], 1):
        c = counts.get(i - 1, 0)
        lines.append(f"  {i}. {opt}  {_bar(c / total if total else 0)} {c} 票")
    head = f"🗳️ 投票中（{total} 人已投）："
    tail = "\n用「.投 编号」投票，可以改票；发起人可用「.开票」结算"
    return head + "\n" + "\n".join(lines) + tail


def register(api):
    async def start_cmd(ctx):
        if _alive(_games(api).get(ctx.chat_id)):
            await ctx.reply("诶，这一票还没开完呢——先「.计票」看看，或者发起人「.开票」结算嘛")
            return
        _drop_stale(api, ctx.chat_id)
        options = [o.strip()[:MAX_OPTION_CHARS]
                   for o in _SPLIT.split((ctx.arg_text or "").strip()) if o.strip()]
        if len(options) < 2:
            await ctx.reply("用法：.投票 选项1 选项2 [选项3…]\n"
                            "比如 .投票 火锅 烧烤 日料（最多 8 个选项）")
            return
        if len(options) > MAX_OPTIONS:
            await ctx.reply(f"诶，选项太多啦——最多 {MAX_OPTIONS} 个哦（你给了 {len(options)} 个）。\n"
                            f"精简一下再开票嘛～")
            return
        _games(api)[ctx.chat_id] = {"options": options, "votes": {},
                                    "starter": ctx.user_id, "ts": time.time()}
        lines = "\n".join(f"  {i}. {o}" for i, o in enumerate(options, 1))
        await ctx.reply(f"🗳️ 开票啦！选项如下：\n{lines}\n"
                        f"用「.投 编号」投你的一票吧～（可改票，发起人「.开票」结算）")

    async def vote_cmd(ctx):
        _drop_stale(api, ctx.chat_id)
        game = _games(api).get(ctx.chat_id)
        if not game:
            await ctx.reply("现在没有正在进行的投票哦～想玩就「.投票 选项1 选项2」开一个")
            return
        arg = (ctx.arg_text or "").strip().lstrip("#")
        # isdigit() 对 ² 这类"上标数字"也为真，但 int('²') 会抛 ValueError → 必须用 isdecimal
        if not arg.isdecimal() or not 1 <= int(arg) <= len(game["options"]):
            await ctx.reply(f"要投 1~{len(game['options'])} 之间的编号哦（.计票 可以看选项）")
            return
        idx = int(arg) - 1
        changed = ctx.user_id in game["votes"]
        game["votes"][ctx.user_id] = idx
        game["ts"] = time.time()
        word = "改投" if changed else "投"
        await ctx.reply(f"好嘞，{ctx.user_name}{word}给了「{game['options'][idx]}」！"
                        f"（.计票 看实时票数）")

    async def tally_cmd(ctx):
        _drop_stale(api, ctx.chat_id)
        game = _games(api).get(ctx.chat_id)
        if not game:
            await ctx.reply("现在没有正在进行的投票哦～")
            return
        await ctx.reply(render(game))

    async def close_cmd(ctx):
        _drop_stale(api, ctx.chat_id)
        game = _games(api).get(ctx.chat_id)
        if not game:
            await ctx.reply("现在没有正在进行的投票哦～")
            return
        if ctx.user_id != game["starter"] and not (ctx.is_owner or ctx.role == "admin"):
            await ctx.reply("嘿嘿，结算是发起人的活儿（或者群管理来也行）～")
            return
        counts = _tally(game)
        total = len(game["votes"])
        best = max(counts.values()) if counts else 0
        winners = [game["options"][i] for i in range(len(game["options"]))
                   if counts.get(i, 0) == best and best > 0]
        rows = sorted(((counts.get(i, 0), opt) for i, opt in enumerate(game["options"])),
                      key=lambda x: -x[0])
        lines = "\n".join(f"  {opt} —— {c} 票" for c, opt in rows)
        _games(api).pop(ctx.chat_id, None)
        if not winners:
            await ctx.reply(f"🗳️ 开票！结果是……一张票都没有，大家是真不投呀[表情:偷笑]\n{lines}")
        elif len(winners) > 1:
            await ctx.reply(f"🗳️ 开票！{len(game['votes'])} 人投票，平票啦——"
                            f"{' / '.join(winners)} 并列最高（各 {best} 票）！\n{lines}")
        else:
            await ctx.reply(f"🗳️ 开票！{total} 人投票，得票最多的是「{winners[0]}」（{best} 票）！\n{lines}")

    api.on_command("投票", start_cmd, aliases=("发起投票",), cooldown=10,
                   help=".投票 选项1 选项2 […] —— 群里开一票（最多 8 个选项）")
    api.on_command("投", vote_cmd, aliases=("投给",), cooldown=2,
                   help=".投 <编号> —— 给某个选项投一票（可改票）")
    api.on_command("计票", tally_cmd, aliases=("票数", "看票"), cooldown=3,
                   help=".计票 —— 看实时票数")
    api.on_command("开票", close_cmd, aliases=("结束投票", "结算"), cooldown=5,
                   help=".开票 —— 结算并公布结果（发起人/管理员）")
