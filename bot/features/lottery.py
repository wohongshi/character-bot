"""抽奖：``.抽奖 3 张三 李四 王五 赵六`` —— 公平抽几个，不重复。

通用插件。纯离线。

- 用 ``random.SystemRandom``（不是可预测的伪随机）；
- 抽 N 个**不重复**，顺序也随机（抽奖顺序本身就是奖品顺序时很重要）；
- 名单去重、去空、上限 200 人，避免一条消息里塞几百个名字。

和 `.帮我选` 的区别：那个是「从几个里挑一个」，这个是「从一堆人里抽 N 个，
并且告诉你是谁」。
"""
from __future__ import annotations

import random
import re

NAME = "抽奖"
NAME_EN = "Lottery pick"
DESCRIPTION = "从一串名单里随机抽几个，不重复。"
DESCRIPTION_EN = "Draw N distinct names from a list at random."
VERSION = "1.0"

MAX_PEOPLE = 200
_SPLIT = re.compile(r"[\s,，、;；/|]+")


def raw_args(ctx) -> str:
    t = str(getattr(ctx, "text", "") or "").strip()
    body = t[1:].strip() if t[:1] in "./／" else t
    parts = body.split(None, 1)
    return parts[1] if len(parts) > 1 else ""


def parse(raw: str) -> tuple[int, list[str]]:
    """'3 张三 李四…' → (3, [名字…])；第一个词不是数字就默认抽 1 个。"""
    tokens = [t for t in _SPLIT.split(str(raw or "").strip()) if t]
    n = 1
    if tokens and tokens[0].isdigit():
        n = max(1, int(tokens[0]))
        tokens = tokens[1:]
    seen, out = set(), []
    for t in tokens:
        k = t.strip()[:32]
        if not k or k in seen:
            continue
        seen.add(k)
        out.append(k)
    return n, out[:MAX_PEOPLE]


def draw(names: list[str], n: int, rng=None) -> list[str]:
    rng = rng or random.SystemRandom()
    n = max(1, min(n, len(names)))
    return rng.sample(names, n)


def register(api):
    async def cmd(ctx):
        n, names = parse(raw_args(ctx))
        if not names:
            await ctx.reply("用法：.抽奖 3 张三 李四 王五 赵六\n"
                            "（第一个数字是抽几个；名字用空格/逗号/顿号隔开）")
            return
        if len(names) == 1:
            await ctx.reply("名单里只有一个人…再填几个吧")
            return
        if n > len(names):
            await ctx.reply(f"要抽 {n} 个，但名单里只有 {len(names)} 个人")
            return
        picked = draw(names, n)
        if n == 1:
            await ctx.reply(f"🎲 从 {len(names)} 个人里抽中：{picked[0]}")
            return
        rows = "\n".join(f"{i}. {name}" for i, name in enumerate(picked, 1))
        await ctx.reply(f"🎲 从 {len(names)} 个人里抽 {n} 个：\n{rows}")

    # 别名别用 draw —— 那是画画插件的键，撞了整条命令会被静默丢掉
    api.on_command("抽奖", cmd, aliases=("lottery",), cooldown=3,
                   help=".抽奖 <个数> <名字…> —— 随机抽不重复的 N 个")
