"""单位换算：``.换算 5 km mi`` / ``.换算 100 F C`` / ``.换算 2 GB MB``。

通用插件。纯离线。

三个容易做错的地方，这里都按规矩来：
- **温度不是比例换算**（0°C ≠ 0°F），单独走一套公式；
- **字节有两种进位**：1000（KB/MB/GB，硬盘厂用）和 1024（KiB/MiB/GiB，内存用），
  混着算出来的数字会差 5%~10%，所以两种都收；
- **中文市制**（斤/两/里/尺/寸）按中国大陆现行标准，1 斤 = 500 g。
"""
from __future__ import annotations

NAME = "单位换算"
NAME_EN = "Unit converter"
DESCRIPTION = "长度、重量、温度、容量、数据、速度、时间互转。"
DESCRIPTION_EN = "Convert length, mass, temperature, volume, data, speed and time."
VERSION = "1.0"

# 类别 → {单位: 换算到基准单位的系数}
TABLES: dict[str, dict[str, float]] = {
    "长度": {
        "m": 1.0, "km": 1000.0, "cm": 0.01, "mm": 0.001, "um": 1e-6,
        "mi": 1609.344, "yd": 0.9144, "ft": 0.3048, "in": 0.0254,
        "里": 500.0, "尺": 1 / 3, "寸": 1 / 30, "海里": 1852.0, "光年": 9.4607e15,
    },
    "重量": {
        "kg": 1.0, "g": 0.001, "mg": 1e-6, "t": 1000.0, "吨": 1000.0,
        "lb": 0.45359237, "磅": 0.45359237, "oz": 0.028349523125, "盎司": 0.028349523125,
        "斤": 0.5, "两": 0.05, "钱": 0.005, "stone": 6.35029318,
    },
    "容量": {
        "L": 1.0, "升": 1.0, "mL": 0.001, "毫升": 0.001, "m3": 1000.0,
        "gal": 3.785411784, "加仑": 3.785411784, "qt": 0.946352946,
        "pt": 0.473176473, "cup": 0.2365882365, "floz": 0.0295735296,
    },
    "数据": {
        "B": 1.0, "KB": 1000.0, "MB": 1e6, "GB": 1e9, "TB": 1e12, "PB": 1e15,
        "KiB": 1024.0, "MiB": 1024.0 ** 2, "GiB": 1024.0 ** 3, "TiB": 1024.0 ** 4,
        "bit": 0.125, "kbit": 125.0, "Mbit": 125000.0,
    },
    "速度": {
        "m/s": 1.0, "km/h": 1 / 3.6, "kmh": 1 / 3.6, "mph": 0.44704,
        "节": 0.514444, "knot": 0.514444, "马赫": 340.3,
    },
    "时间": {
        "s": 1.0, "秒": 1.0, "min": 60.0, "分": 60.0, "h": 3600.0, "小时": 3600.0,
        "d": 86400.0, "天": 86400.0, "周": 604800.0, "年": 31557600.0,
    },
    "面积": {
        "m2": 1.0, "平方米": 1.0, "km2": 1e6, "cm2": 1e-4, "公顷": 10000.0,
        "亩": 666.6666667, "acre": 4046.8564224, "ft2": 0.09290304,
    },
}

TEMP = {"C", "F", "K", "℃", "℉"}
# 常见别名归一
ALIAS = {"c": "C", "°c": "C", "celsius": "C", "摄氏": "C",
         "f": "F", "°f": "F", "fahrenheit": "F", "华氏": "F",
         "k": "K", "开": "K", "开尔文": "K"}


def raw_args(ctx) -> str:
    t = str(getattr(ctx, "text", "") or "").strip()
    body = t[1:].strip() if t[:1] in "./／" else t
    parts = body.split(None, 1)
    return parts[1] if len(parts) > 1 else ""


def _find_unit(tok: str) -> tuple[str, str, float] | None:
    """'5km' → (类别, 单位, 5)；'km' → (类别, 单位, 1)。认不出返回 None。"""
    raw = str(tok or "").strip()
    num_part = ""
    i = 0
    while i < len(raw) and (raw[i].isdigit() or raw[i] in "+-."):
        num_part += raw[i]
        i += 1
    unit = raw[i:].strip()
    value = float(num_part) if num_part not in ("", "+", "-", ".") else 1.0
    key = unit.lower()
    if key in ALIAS:
        return ("温度", ALIAS[key], value)
    if unit in TEMP:
        return ("温度", unit, value)
    for cat, table in TABLES.items():
        if unit in table:
            return (cat, unit, value)
        for u in table:
            if u.lower() == key:
                return (cat, u, value)
    return None


def _temp(v: float, a: str, b: str) -> float:
    c = v if a in ("C", "℃") else (v - 32) * 5 / 9 if a in ("F", "℉") else v - 273.15
    if b in ("C", "℃"):
        return c
    if b in ("F", "℉"):
        return c * 9 / 5 + 32
    return c + 273.15


def convert(value: float, a: str, b: str) -> float:
    """a → b。单位必须同类，否则抛 ValueError。"""
    fa = _find_unit(a)
    fb = _find_unit(b)
    if not fa or not fb:
        raise ValueError(f"认不出单位：{' '.join(x for x, ok in ((a, not fa), (b, not fb)) if ok)}")
    if fa[0] != fb[0]:
        raise ValueError(f"{fa[1]} 是{fa[0]}，{fb[1]} 是{fb[0]}，不是一类")
    if fa[0] == "温度":
        return _temp(value, fa[1], fb[1])
    return value * TABLES[fa[0]][fa[1]] / TABLES[fa[0]][fb[1]]


def _fmt(x: float) -> str:
    if x == int(x) and abs(x) < 1e15:
        return f"{int(x)}"
    if abs(x) >= 1e6 or (abs(x) < 1e-4 and x):
        return f"{x:.6g}"
    return f"{x:.4f}".rstrip("0").rstrip(".")


def register(api):
    async def cmd(ctx):
        raw = raw_args(ctx).strip()
        if not raw:
            cats = "、".join(TABLES)
            await ctx.reply("用法：.换算 5 km mi\n"
                            f"支持：{cats}、温度（C/F/K）\n"
                            "例：.换算 100 F C / .换算 2 GB MB / .换算 1 斤 g")
            return
        parts = raw.split()
        if len(parts) == 3:
            val_s, a, b = parts
            try:
                value = float(val_s)
            except ValueError:
                await ctx.reply(f"「{val_s}」不是数字呀")
                return
        elif len(parts) == 2:
            # 支持「5km mi」和「100F C」这种连写
            head = _find_unit(parts[0])
            if not head:
                await ctx.reply("看不懂这个单位～写成 5 km mi 这样？")
                return
            value, a, b = head[2], head[1], parts[1]
        else:
            await ctx.reply("用法：.换算 <数量> <原单位> <目标单位>，例：.换算 5 km mi")
            return
        try:
            out = convert(value, a, b)
        except ValueError as e:
            await ctx.reply(str(e))
            return
        await ctx.reply(f"📐 {_fmt(value)} {a} = {_fmt(out)} {b}")

    api.on_command("换算", cmd, aliases=("convert",), cooldown=2,
                   help=".换算 5 km mi —— 长度/重量/温度/容量/数据/速度")

    async def tool_convert(args: dict, ctx=None) -> str:
        """给 agent 用的版本：直接返回一行结果。"""
        try:
            v = float(args.get("value") or 0)
            return f"{_fmt(v)} {args.get('from')} = {_fmt(convert(v, args.get('from'), args.get('to')))} {args.get('to')}"
        except (TypeError, ValueError) as e:
            return f"换算失败：{e}"

    api.add_tool(
        "convert_unit",
        description="单位换算（长度/重量/温度/容量/数据/速度/时间/面积）。",
        parameters={"type": "object",
                    "properties": {"value": {"type": "number", "description": "数量"},
                                   "from": {"type": "string", "description": "原单位，如 km"},
                                   "to": {"type": "string", "description": "目标单位，如 mi"}},
                    "required": ["value", "from", "to"]},
        handler=tool_convert,
    )
