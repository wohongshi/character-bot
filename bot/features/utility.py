"""小工具 —— 给 agent 的 ``now`` / ``calc``。

通用插件：所有角色都能用，也可以在面板里单独关掉（关掉后模型不知道现在几点、
也不能算数，只能自己编 —— 所以默认开）。
"""
from __future__ import annotations

NAME = "时间与计算"
NAME_EN = "Time & math"
DESCRIPTION_EN = "Current time (any timezone) and arithmetic."
DESCRIPTION = "看当前时间（可指定时区）、算算术表达式。"
VERSION = "1.0"

_NOW_PARAMS = {
    "type": "object",
    "properties": {
        "tz": {"type": "string", "description": "IANA 时区，如 Asia/Shanghai；默认本地"},
    },
}

_CALC_PARAMS = {
    "type": "object",
    "properties": {"expr": {"type": "string", "description": "如 (3+5)*2.5"}},
    "required": ["expr"],
}


def register(api):
    from bot.agent import tool_calc, tool_now

    api.add_tool(
        "now",
        description="获取当前日期和时间（可指定时区）。",
        parameters=_NOW_PARAMS,
        handler=tool_now,
    )
    api.add_tool(
        "calc",
        description="计算算术表达式（+ - * / // % ** 和括号）。",
        parameters=_CALC_PARAMS,
        handler=tool_calc,
    )
