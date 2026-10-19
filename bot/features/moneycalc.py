"""买买买算术：``.折扣`` / ``.涨跌`` / ``.分摊`` / ``.复利``。

通用插件。纯离线。这类计算人人都会，但心算错的概率也不低 —— 尤其是
「打八折再满 300 减 50」和「AA 分账差一分钱」这两种。

金额一律按**分**算（``Decimal`` + 四舍五入到分），不用 float 累加：
199.9 * 0.85 在 float 下会得到 169.91499999999998，写进账单就变成笑话了。
"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

NAME = "折扣与账"
NAME_EN = "Money math"
DESCRIPTION = "打折、涨跌幅、AA 分账、复利。"
DESCRIPTION_EN = "Discounts, percentage change, bill splitting and compound interest."
VERSION = "1.0"

CENT = Decimal("0.01")


def raw_args(ctx) -> str:
    t = str(getattr(ctx, "text", "") or "").strip()
    body = t[1:].strip() if t[:1] in "./／" else t
    parts = body.split(None, 1)
    return parts[1] if len(parts) > 1 else ""


def money(x) -> Decimal:
    """金额 → 分。非数字统一翻译成 ValueError（人话）。

    以前 Decimal 的 InvalidOperation 会一路冒到处理器外面，四个命令各写一遍
    `except (ValueError, InvalidOperation)` 还都直接 `str(e)` ——
    用户看到的是 `[<class 'decimal.ConversionSyntax'>]`。收在这里处理一次。
    """
    try:
        return Decimal(str(x)).quantize(CENT, rounding=ROUND_HALF_UP)
    except InvalidOperation:
        raise ValueError(f"「{x}」不是数字，金额要写成 199 或 199.9") from None


def parse_rate(token: str) -> Decimal:
    """'8折' / '0.8' / '80%' / '8.5折' → 0.80 / 0.80 / 0.80 / 0.85。"""
    t = str(token or "").strip().replace("％", "%")
    try:
        if t.endswith("折"):
            v = Decimal(t[:-1])
            return (v / Decimal(10)) if v > 0 else Decimal(0)
        if t.endswith("%"):
            return Decimal(t[:-1]) / Decimal(100)
        v = Decimal(t)
        return v / Decimal(10) if v > 1 else v
    except InvalidOperation:
        raise ValueError(f"看不懂这个折扣：{token}") from None


def discount(price, rate) -> tuple[Decimal, Decimal]:
    """返回 (实付, 省下)。"""
    p = money(price)
    r = parse_rate(rate)
    if r < 0 or r > 1:
        raise ValueError("折扣要在 0~10 折之间")
    pay = money(p * r)
    return pay, money(p - pay)


def change(old, new) -> tuple[Decimal, Decimal]:
    """返回 (差额, 百分比)。百分比可能为负。"""
    a, b = money(old), money(new)
    if a == 0:
        raise ValueError("原价是 0，算不了百分比")
    diff = money(b - a)
    pct = (diff / a * 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return diff, pct


def split(total, people: int) -> list[Decimal]:
    """AA：尽量平分，余下的按分发给前几个人，保证加起来刚好等于总额。"""
    total = money(total)
    if people < 1:
        raise ValueError("人数至少 1")
    base = (total / people).quantize(CENT, rounding=ROUND_HALF_UP)
    shares = [base] * people
    drift = money(total - base * people)
    step = CENT if drift >= 0 else -CENT
    i = 0
    while drift != 0 and i < people * 200:
        shares[i % people] = money(shares[i % people] + step)
        drift = money(drift - step)
        i += 1
    return shares


def compound(principal, rate_pct, years: int, times: int = 1) -> Decimal:
    p = money(principal)                       # 顺带做「不是数字」的翻译
    try:
        r = Decimal(str(rate_pct)) / Decimal(100) / Decimal(times)
    except InvalidOperation:
        raise ValueError(f"「{rate_pct}」不是数字，利率要写成 3 或 3.5（百分数）") from None
    return money(p * (1 + r) ** (years * times))


def register(api):
    async def disc(ctx):
        parts = raw_args(ctx).split()
        if len(parts) < 2:
            await ctx.reply("用法：.折扣 199 8折（也认 0.8 / 80%）")
            return
        try:
            pay, save = discount(parts[0], parts[1])
        except InvalidOperation:
            # Decimal(str(x)) 对非数字抛的是 decimal.InvalidOperation，不是 ValueError；
            # 它的 str() 是 `[<class 'decimal.ConversionSyntax'>]` 这种内部标记，
            # 直接回给用户等于乱码。
            await ctx.reply("价格要写数字，比如 .折扣 199 8折")
            return
        except ValueError as e:
            await ctx.reply(str(e))
            return
        await ctx.reply(f"🛒 原价 ¥{money(parts[0])}\n实付 ¥{pay}\n省下 ¥{save}"
                        f"（{parts[1]}）")

    async def rise(ctx):
        parts = raw_args(ctx).split()
        if len(parts) < 2:
            await ctx.reply("用法：.涨跌 100 120（原价 → 现价）")
            return
        try:
            diff, pct = change(parts[0], parts[1])
        except (ValueError, InvalidOperation) as e:
            await ctx.reply(str(e))
            return
        arrow = "📈 涨了" if diff > 0 else ("📉 跌了" if diff < 0 else "持平")
        await ctx.reply(f"{arrow}\n差额 ¥{abs(diff)}\n幅度 {pct}%")

    async def aa(ctx):
        parts = raw_args(ctx).split()
        if len(parts) < 2:
            await ctx.reply("用法：.分摊 200 3（总额 人数）")
            return
        try:
            people = int(parts[1])
        except (TypeError, ValueError):
            await ctx.reply("人数要写个数字，比如 .分摊 200 3")
            return
        # 先校验人数再分摊：split() 会先分配 [每人金额] * people，
        # `.分摊 200 1000000000` 会先吃掉几百 MB 才轮到「人太多」的判断。
        if not 1 <= people <= 20:
            await ctx.reply("人数要 1~20 人（自己按计算器吧～）")
            return
        try:
            shares = split(parts[0], people)
        except (ValueError, InvalidOperation) as e:
            await ctx.reply(f"算不了：{e}")
            return
        lines = [f"🧾 总额 ¥{money(parts[0])} · {people} 人"]
        uniq: dict[str, int] = {}
        for s in shares:
            uniq[str(s)] = uniq.get(str(s), 0) + 1
        for amount, n in uniq.items():
            lines.append(f"  ¥{amount} × {n} 人")
        lines.append(f"（合计 ¥{sum(shares)}，不会差一分钱）")
        await ctx.reply("\n".join(lines))

    async def interest(ctx):
        parts = raw_args(ctx).split()
        if len(parts) < 3:
            await ctx.reply("用法：.复利 10000 5 10（本金 年利率% 年数 [每年复利次数]）\n"
                            "例：.复利 10000 3 5 12 = 按月复利")
            return
        try:
            years = int(parts[2])
            times = int(parts[3]) if len(parts) > 3 else 1
            if years < 0 or years > 100 or times < 1 or times > 365:
                raise ValueError("年数 0~100，复利次数 1~365")
            end = compound(parts[0], parts[1], years, times)
        except (ValueError, InvalidOperation) as e:
            await ctx.reply(f"算不了：{e}")
            return
        start = money(parts[0])
        await ctx.reply(f"💰 本金 ¥{start}\n年利率 {parts[1]}% · {years} 年"
                        f"{'（每年复利 ' + str(times) + ' 次）' if times > 1 else ''}\n"
                        f"到期 ¥{end}\n利息 ¥{money(end - start)}")

    api.on_command("折扣", disc, aliases=("discount",), cooldown=2,
                   help=".折扣 199 8折 —— 实付和省的")
    api.on_command("涨跌", rise, aliases=("change",), cooldown=2,
                   help=".涨跌 100 120 —— 涨跌幅")
    api.on_command("分摊", aa, aliases=("aa",), cooldown=2,
                   help=".分摊 200 3 —— AA 分账（精确到分）")
    api.on_command("复利", interest, cooldown=3,
                   help=".复利 10000 5 10 [每年次数] —— 复利终值")
