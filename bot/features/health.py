"""身体小账本：``.bmi`` / ``.bmr`` / ``.喝水``。

通用插件。纯离线。

口径都写明来源，别让人拿它当医学结论：
- BMI 用**中国成人**标准（24 超重、28 肥胖），不是 WHO 的 25/30；
- 基础代谢用 Mifflin-St Jeor（目前临床上最常用的一版）；
- 饮水量按「体重 × 30~35 ml」，并且明确写出「运动/出汗要加」。
"""
from __future__ import annotations

NAME = "身体计算"
NAME_EN = "Body metrics"
DESCRIPTION = "BMI、基础代谢、每日饮水量。"
DESCRIPTION_EN = "BMI, basal metabolic rate and daily water target."
VERSION = "1.0"

MAX_WEIGHT = 400.0
MAX_HEIGHT = 260.0


def raw_args(ctx) -> str:
    t = str(getattr(ctx, "text", "") or "").strip()
    body = t[1:].strip() if t[:1] in "./／" else t
    parts = body.split(None, 1)
    return parts[1] if len(parts) > 1 else ""


def bmi_of(height_cm: float, weight_kg: float) -> float:
    h = height_cm / 100.0
    return weight_kg / (h * h)


def bmi_label(v: float) -> str:
    """中国成人标准。"""
    if v < 18.5:
        return "偏瘦"
    if v < 24:
        return "正常"
    if v < 28:
        return "超重"
    return "肥胖"


def ideal_range(height_cm: float) -> tuple[float, float]:
    """BMI 18.5~24 对应的体重区间。"""
    h = height_cm / 100.0
    return 18.5 * h * h, 24 * h * h


def bmr_of(sex: str, age: int, height_cm: float, weight_kg: float) -> float:
    """Mifflin-St Jeor。"""
    base = 10 * weight_kg + 6.25 * height_cm - 5 * age
    return base + (5 if sex == "m" else -161)


def water_ml(weight_kg: float) -> tuple[int, int]:
    return int(weight_kg * 30), int(weight_kg * 35)


def _sex(token: str) -> str:
    t = str(token or "").lower()
    if t in ("男", "m", "male", "男生"):
        return "m"
    if t in ("女", "f", "female", "女生"):
        return "f"
    raise ValueError("性别写「男」或「女」")


def register(api):
    async def bmi(ctx):
        parts = raw_args(ctx).split()
        if len(parts) < 2:
            await ctx.reply("用法：.bmi 身高cm 体重kg，例：.bmi 170 60")
            return
        try:
            h, w = float(parts[0]), float(parts[1])
        except ValueError:
            await ctx.reply("身高体重都要是数字～例：.bmi 170 60")
            return
        if not (50 <= h <= MAX_HEIGHT) or not (10 <= w <= MAX_WEIGHT):
            await ctx.reply("这两个数看着不像身高/体重…例：.bmi 170 60")
            return
        v = bmi_of(h, w)
        lo, hi = ideal_range(h)
        await ctx.reply(f"⚖️ BMI {v:.1f} —— {bmi_label(v)}\n"
                        f"（中国标准：18.5~24 正常，24~28 超重，≥28 肥胖）\n"
                        f"按你的身高，正常体重区间是 {lo:.1f} ~ {hi:.1f} kg")

    async def bmr(ctx):
        parts = raw_args(ctx).split()
        if len(parts) < 4:
            await ctx.reply("用法：.bmr 男 18 175 65（性别 年龄 身高cm 体重kg）")
            return
        try:
            sex = _sex(parts[0])
            age = int(parts[1])
            h, w = float(parts[2]), float(parts[3])
            if not (5 <= age <= 120):
                raise ValueError("年龄写 5~120 吧")
            base = bmr_of(sex, age, h, w)
        except ValueError as e:
            await ctx.reply(f"看不懂：{e}")
            return
        rows = [f"🔥 基础代谢约 {base:.0f} kcal/天（躺着不动）",
                "按活动量估每日消耗：",
                f"  久坐：{base * 1.2:.0f} kcal",
                f"  轻度活动（每周 1~3 次）：{base * 1.375:.0f} kcal",
                f"  中度活动（每周 3~5 次）：{base * 1.55:.0f} kcal",
                f"  高强度：{base * 1.725:.0f} kcal"]
        await ctx.reply("\n".join(rows))

    async def water(ctx):
        parts = raw_args(ctx).split()
        if not parts:
            await ctx.reply("用法：.喝水 65（体重 kg）")
            return
        try:
            w = float(parts[0])
        except ValueError:
            await ctx.reply("体重要是数字～例：.喝水 65")
            return
        if not (10 <= w <= MAX_WEIGHT):
            await ctx.reply("这个体重看着不像…")
            return
        lo, hi = water_ml(w)
        cups = round(hi / 250)
        await ctx.reply(f"💧 每天约 {lo}~{hi} ml（{lo / 1000:.1f}~{hi / 1000:.1f} L）\n"
                        f"相当于 {cups} 杯 250 ml。运动、出汗、天热要再加。")

    api.on_command("bmi", bmi, cooldown=2, help=".bmi 170 60 —— BMI 与正常区间")
    api.on_command("bmr", bmr, cooldown=2, help=".bmr 男 18 175 65 —— 基础代谢")
    api.on_command("喝水", water, cooldown=2, help=".喝水 65 —— 每日建议饮水量")
