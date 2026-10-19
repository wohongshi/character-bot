"""数字的几种写法：``.罗马 1994`` / ``.大写 1024`` / ``.进制 255 16``。

通用插件。纯离线。

- 罗马数字：1~3999（再大古典写法就不规范了，这里直接说清楚）；
- 人民币大写：写支票/报销单要用的那种「壹仟零贰拾肆元整」；
- 进制：2~36 互转，顺带把 2/8/16 都列出来 —— 调色、权限、掩码时省事。
"""
from __future__ import annotations

import re

NAME = "数字写法"
NAME_EN = "Numerals"
DESCRIPTION = "罗马数字、人民币大写、任意进制互转。"
DESCRIPTION_EN = "Roman numerals, Chinese uppercase amounts, base conversion."
VERSION = "1.0"

ROMAN = ((1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"),
         (50, "L"), (40, "XL"), (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I"))
_ROMAN_VAL = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}

CN_DIGIT = "零壹贰叁肆伍陆柒捌玖"
CN_UNIT = ("", "拾", "佰", "仟")
CN_SECTION = ("", "万", "亿", "万亿")
CN_SMALL = "零一二三四五六七八九"


def raw_args(ctx) -> str:
    t = str(getattr(ctx, "text", "") or "").strip()
    body = t[1:].strip() if t[:1] in "./／" else t
    parts = body.split(None, 1)
    return parts[1] if len(parts) > 1 else ""


def to_roman(n: int) -> str:
    if n <= 0 or n >= 4000:
        raise ValueError("罗马数字只规整地表示 1~3999")
    out = []
    for v, sym in ROMAN:
        while n >= v:
            out.append(sym)
            n -= v
    return "".join(out)


def from_roman(s: str) -> int:
    t = str(s or "").upper().strip()
    if not t or any(ch not in _ROMAN_VAL for ch in t):
        raise ValueError("这不是罗马数字")
    total = 0
    for i, ch in enumerate(t):
        v = _ROMAN_VAL[ch]
        nxt = _ROMAN_VAL[t[i + 1]] if i + 1 < len(t) else 0
        total += -v if v < nxt else v
    if to_roman(total) != t:
        raise ValueError(f"写法不规范（{total} 应该是 {to_roman(total)}）")
    return total


def cjk_upper(amount: float) -> str:
    """人民币大写：1234.56 → 壹仟贰佰叁拾肆元伍角陆分。"""
    if amount < 0:
        return "负" + cjk_upper(-amount)
    amount = round(float(amount), 2)
    yuan = int(amount)
    cents = int(round((amount - yuan) * 100))
    jiao, fen = divmod(cents, 10)
    head = _int_upper(yuan) + "元" if yuan else ""
    tail = ""
    if jiao:
        tail += CN_DIGIT[jiao] + "角"
    if fen:
        tail += CN_DIGIT[fen] + "分"
    if not head:
        head = "零元" if not tail else ""
    if not tail:
        return head + "整"
    if not jiao:
        tail = "零" + tail
    return head + tail


def _int_upper(n: int) -> str:
    if n == 0:
        return "零"
    sections = []
    idx = 0
    zero_between = False
    while n > 0:
        n, sec = divmod(n, 10000)
        if sec:
            part = _section_upper(sec)
            if zero_between or (sec < 1000 and sections):
                part = "零" + part if not part.startswith("零") else part
            sections.append(part + CN_SECTION[idx])
            zero_between = False
        else:
            zero_between = True if sections else zero_between
        idx += 1
    return "".join(reversed(sections)).replace("零零", "零").strip("零")


def _section_upper(sec: int) -> str:
    out = []
    zero = False
    for i in range(3, -1, -1):
        d = (sec // (10 ** i)) % 10
        if d:
            if zero and out:
                out.append("零")
            out.append(CN_DIGIT[d] + CN_UNIT[i])
            zero = False
        else:
            if out:
                zero = True
    return "".join(out)


def cjk_small(n: int) -> str:
    """普通中文读法：1024 → 一千零二十四。

    只换数字不够 —— 大写单位（仟/佰/拾）也得换成小写（千/百/十），
    否则会读出「一仟零二拾四」这种半吊子。
    """
    s = _int_upper(n)
    s = s.translate(str.maketrans(CN_DIGIT, CN_SMALL))
    return s.replace("仟", "千").replace("佰", "百").replace("拾", "十")


_DIGITS = "0123456789abcdefghijklmnopqrstuvwxyz"


def to_base(n: int, base: int) -> str:
    if not 2 <= base <= 36:
        raise ValueError("进制只能是 2~36")
    if n == 0:
        return "0"
    sign = "-" if n < 0 else ""
    n = abs(n)
    out = []
    while n:
        n, r = divmod(n, base)
        out.append(_DIGITS[r])
    return sign + "".join(reversed(out))


def from_base(s: str, base: int) -> int:
    if not 2 <= base <= 36:
        raise ValueError("进制只能是 2~36")
    t = str(s or "").strip().lower()
    sign = -1 if t.startswith("-") else 1
    t = t.lstrip("+-")
    if not t:
        raise ValueError("没有数字")
    n = 0
    for ch in t:
        if ch not in _DIGITS[:base]:
            raise ValueError(f"「{ch}」不是 {base} 进制的数字")
        n = n * base + _DIGITS.index(ch)
    return sign * n


def register(api):
    async def roman(ctx):
        raw = raw_args(ctx).strip()
        if not raw:
            await ctx.reply("用法：.罗马 1994 → MCMXCIV；.罗马 MCMXCIV → 1994")
            return
        if re.fullmatch(r"-?\d+", raw):
            try:
                await ctx.reply(f"🏛 {raw} → {to_roman(int(raw))}")
            except ValueError as e:
                await ctx.reply(str(e))
            return
        try:
            await ctx.reply(f"🏛 {raw.upper()} → {from_roman(raw)}")
        except ValueError as e:
            await ctx.reply(str(e))

    async def upper(ctx):
        raw = raw_args(ctx).strip().replace(",", "").replace("，", "")
        if not raw:
            await ctx.reply("用法：.大写 1024 → 壹仟零贰拾肆元整")
            return
        try:
            num = float(raw)
        except ValueError:
            await ctx.reply(f"「{raw}」不是数字")
            return
        if abs(num) >= 1e14:
            await ctx.reply("太大了，写不下（上限 1 万亿级）")
            return
        await ctx.reply(f"💰 {cjk_upper(num)}\n（小写读法：{cjk_small(int(num))}）")

    async def base(ctx):
        parts = raw_args(ctx).split()
        if len(parts) < 2:
            await ctx.reply("用法：.进制 <数字> <它现在的进制> [目标进制]\n"
                            "例：.进制 255 10 / .进制 ff 16 / .进制 1010 2 16")
            return
        try:
            src = int(parts[1])
            n = from_base(parts[0], src)
        except ValueError as e:
            await ctx.reply(str(e))
            return
        if len(parts) >= 3:
            try:
                tb = int(parts[2])
                await ctx.reply(f"🔢 {parts[0]}({src}) = {to_base(n, tb)}({tb})")
            except ValueError as e:
                await ctx.reply(str(e))
            return
        rows = [f"🔢 {parts[0]}（{src} 进制）= {n}（十进制）"]
        for b, name in ((2, "二进制"), (8, "八进制"), (16, "十六进制")):
            if b != src:
                rows.append(f"{name}：{to_base(n, b)}")
        await ctx.reply("\n".join(rows))

    api.on_command("罗马", roman, aliases=("roman",), cooldown=2,
                   help=".罗马 1994 —— 阿拉伯数字 ⇄ 罗马数字")
    api.on_command("大写", upper, aliases=("rmb",), cooldown=2,
                   help=".大写 1024 —— 人民币大写")
    api.on_command("进制", base, aliases=("base",), cooldown=2,
                   help=".进制 255 10 [16] —— 进制互转")
