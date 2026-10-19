"""24 点：``.24点 3 3 8 8`` 求表达式，``.24点`` 随机出题。

通用插件。纯离线。

用 ``Fraction`` 而不是 float：24 点里大量出现 1/3、7/3 这种分数，
float 下 (8/3)*9 会算出 23.999999999999996 —— 明明解对了却判错。
"""
from __future__ import annotations

import random
from fractions import Fraction

NAME = "24点"
NAME_EN = "24 game"
DESCRIPTION = "给四个数，用 + - × ÷ 和括号凑出 24。"
DESCRIPTION_EN = "Make 24 from four numbers with + - * / and brackets."
VERSION = "1.0"


def raw_args(ctx) -> str:
    t = str(getattr(ctx, "text", "") or "").strip()
    body = t[1:].strip() if t[:1] in "./／" else t
    parts = body.split(None, 1)
    return parts[1] if len(parts) > 1 else ""


def solve24(nums: list) -> str | None:
    """返回一个等于 24 的表达式，或 None。"""
    def rec(items):
        if len(items) == 1:
            val, expr = items[0]
            return expr if val == 24 else None
        n = len(items)
        for i in range(n):
            for j in range(n):
                if i == j:
                    continue
                a, ea = items[i]
                b, eb = items[j]
                rest = [items[k] for k in range(n) if k != i and k != j]
                cands = [(a + b, f"({ea}+{eb})"),
                         (a * b, f"({ea}×{eb})"),
                         # 减法/除法不满足交换律 —— 但 (i,j) 和 (j,i) **都会**被枚举到，
                         # 所以这里固定写 a-b / a÷b 就已经覆盖了两种顺序。
                         # （以前按 i<j 翻转成 b-a，等于把顺序又翻回去，于是
                         #   [10,10,4,4] 这种「大数减小数」的解法永远找不到。）
                         (a - b, f"({ea}-{eb})")]
                if b != 0:
                    cands.append((a / b, f"({ea}÷{eb})"))
                for val, expr in cands:
                    got = rec(rest + [(val, expr)])
                    if got:
                        return got
        return None

    return rec([(Fraction(x), str(x)) for x in nums])


def deal(rng=None) -> list[int]:
    """随机发四张牌，保证有解（否则用户会以为她算错了）。"""
    rng = rng or random
    for _ in range(400):
        nums = [rng.randint(1, 10) for _ in range(4)]
        if solve24(nums):
            return nums
    return [3, 3, 8, 8]


def register(api):
    async def cmd(ctx):
        raw = raw_args(ctx).strip()
        if not raw:
            nums = deal()
            await ctx.reply(f"🃏 {' '.join(map(str, nums))}\n用这四个数凑 24（想不出就 .24点 {' '.join(map(str, nums))}）")
            return
        parts = raw.replace(",", " ").replace("，", " ").split()
        try:
            nums = [int(p) for p in parts]
        except ValueError:
            await ctx.reply("四个数要用数字～例：.24点 3 3 8 8")
            return
        if len(nums) != 4:
            await ctx.reply(f"要正好四个数（你给了 {len(nums)} 个）")
            return
        if any(n < 1 or n > 99 for n in nums):
            await ctx.reply("数取 1~99 吧")
            return
        expr = solve24(nums)
        if not expr:
            await ctx.reply(f"😵 {' '.join(map(str, nums))} 凑不出 24（我每种组合都试过了）")
            return
        # 去掉最外层括号，好看一点
        if expr.startswith("(") and expr.endswith(")"):
            expr = expr[1:-1]
        await ctx.reply(f"✅ {' '.join(map(str, nums))} →\n{expr} = 24")

    api.on_command("24点", cmd, aliases=("24",), cooldown=3,
                   help=".24点 [四个数] —— 凑 24，不给数就随机出题")
