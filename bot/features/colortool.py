"""颜色工具：``.颜色 #e8484f`` / ``.颜色 #fff #000``。

通用插件。纯离线。

对比度按 WCAG 2.1 的公式算（相对亮度 + 加马），阈值 4.5:1（正文）/ 3:1（大字、
图形）。这不是「看着差不多」—— 同一个颜色在白底和黑底下的读数会差一倍以上，
所以两个都报出来。
"""
from __future__ import annotations

import re

NAME = "颜色"
NAME_EN = "Color tools"
DESCRIPTION = "十六进制 ⇄ RGB/HSL，以及和黑白底的对比度。" 
DESCRIPTION_EN = "Hex to RGB/HSL, plus contrast ratios against white and black."
VERSION = "1.0"

_NAMED = {"白": "#ffffff", "黑": "#000000", "红": "#ff0000", "绿": "#008000",
          "蓝": "#0000ff", "黄": "#ffff00", "灰": "#808080", "橙": "#ffa500",
          "紫": "#800080", "粉": "#ffc0cb"}


def raw_args(ctx) -> str:
    t = str(getattr(ctx, "text", "") or "").strip()
    body = t[1:].strip() if t[:1] in "./／" else t
    parts = body.split(None, 1)
    return parts[1] if len(parts) > 1 else ""


def parse_color(s: str) -> tuple[int, int, int]:
    t = str(s or "").strip().lower()
    if t in _NAMED:
        t = _NAMED[t]
    m = re.fullmatch(r"#?([0-9a-f]{3})", t)
    if m:
        h = m.group(1)
        return tuple(int(c * 2, 16) for c in h)          # type: ignore[return-value]
    m = re.fullmatch(r"#?([0-9a-f]{6})", t)
    if m:
        h = m.group(1)
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    # alpha 可以带也可以不带（rgba(1,2,3,.5) 这种粘贴过来很常见，透明度这里不用）
    m = re.fullmatch(r"rgba?\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})"
                     r"(?:\s*,\s*[\d.]+%?)?\s*\)", t)
    if m:
        vals = tuple(int(g) for g in m.groups())
        if all(0 <= v <= 255 for v in vals):
            return vals                                  # type: ignore[return-value]
    raise ValueError(f"认不出这个颜色：{s}")


def to_hex(rgb: tuple[int, int, int]) -> str:
    return "#%02x%02x%02x" % rgb


def to_hsl(rgb: tuple[int, int, int]) -> tuple[float, float, float]:
    r, g, b = (v / 255 for v in rgb)
    mx, mn = max(r, g, b), min(r, g, b)
    light = (mx + mn) / 2
    if mx == mn:
        return 0.0, 0.0, light * 100
    d = mx - mn
    sat = d / (2 - mx - mn) if light > 0.5 else d / (mx + mn)
    if mx == r:
        hue = ((g - b) / d) % 6
    elif mx == g:
        hue = (b - r) / d + 2
    else:
        hue = (r - g) / d + 4
    return hue * 60, sat * 100, light * 100


def _lin(c: int) -> float:
    v = c / 255
    return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4


def luminance(rgb: tuple[int, int, int]) -> float:
    r, g, b = rgb
    return 0.2126 * _lin(r) + 0.7152 * _lin(g) + 0.0722 * _lin(b)


def contrast(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    la, lb = luminance(a), luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def _verdict(ratio: float) -> str:
    if ratio >= 7:
        return "AAA（正文也够）"
    if ratio >= 4.5:
        return "AA（正文够）"
    if ratio >= 3:
        return "只够大字/图形"
    return "不够（换色吧）"


def register(api):
    async def cmd(ctx):
        parts = raw_args(ctx).split()
        if not parts:
            await ctx.reply("用法：.颜色 #e8484f\n"
                            "或两个颜色看对比度：.颜色 #fff #333")
            return
        try:
            a = parse_color(parts[0])
            b = parse_color(parts[1]) if len(parts) > 1 else None
        except ValueError as e:
            await ctx.reply(str(e))
            return
        if b is not None:
            r = contrast(a, b)
            await ctx.reply(f"🎨 {to_hex(a)} vs {to_hex(b)}\n"
                            f"对比度 {r:.2f}:1 —— {_verdict(r)}\n"
                            f"（WCAG：正文 ≥4.5、大字/图形 ≥3）")
            return
        h, s, light = to_hsl(a)
        r_white = contrast(a, (255, 255, 255))
        r_black = contrast(a, (0, 0, 0))
        await ctx.reply(
            f"🎨 {to_hex(a)}\n"
            f"RGB（{a[0]}, {a[1]}, {a[2]}）\n"
            f"HSL（{h:.0f}°, {s:.0f}%, {light:.0f}%）\n"
            f"亮度 {luminance(a):.3f}\n"
            f"白底对比 {r_white:.2f}:1 —— {_verdict(r_white)}\n"
            f"黑底对比 {r_black:.2f}:1 —— {_verdict(r_black)}\n"
            f"建议：把白底/黑底里读数高的那个当文字色。"
        )

    api.on_command("颜色", cmd, aliases=("color",), cooldown=2,
                   help=".颜色 #e8484f [另一个色] —— RGB/HSL/对比度")
