"""技能盒子 —— 给 agent 的 ``list_skills`` / ``read_skill`` / ``use_skill``。

插件即技能：这个盒子本身不提供任何玩法，它是「让模型看见并调用别的插件」
的那扇门。关掉它，agent 就只能说话、查知识库、上网，不会主动用 `.抽卡` 这类命令。
"""
from __future__ import annotations

NAME = "技能盒子"
NAME_EN = "Skill box"
DESCRIPTION_EN = "Let her discover and call other plugins (gacha, tarot, builds…) as skills."
DESCRIPTION = "让她能发现并主动调用别的插件（抽卡、塔罗、练度……）当技能用。"
VERSION = "1.0"

_LIST_PARAMS = {
    "type": "object",
    "properties": {"query": {"type": "string", "description": "关键词；空 = 全部"}},
}

_READ_PARAMS = {
    "type": "object",
    "properties": {"name": {"type": "string", "description": "技能名，如 genshin"}},
    "required": ["name"],
}

_USE_PARAMS = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "description": "技能名，如 genshin"},
        "text": {"type": "string", "description": "命令 + 参数，如 '练度 甘雨'"},
    },
    "required": ["name"],
}


def register(api):
    from bot.agent import tool_list_skills, tool_read_skill, tool_use_skill

    api.add_tool(
        "list_skills",
        description="列出可用技能（其实就是插件：查设定、抽卡、塔罗、角色卡……），可按关键词过滤。",
        parameters=_LIST_PARAMS,
        handler=tool_list_skills,
    )
    api.add_tool(
        "read_skill",
        description="查看某个技能的说明和命令用法（决定怎么调用它之前先读）。",
        parameters=_READ_PARAMS,
        handler=tool_read_skill,
    )
    api.add_tool(
        "use_skill",
        description="执行某个技能的命令（例如查角色练度、抽卡、看每日素材）。text 是命令和参数，如 '练度 甘雨'。",
        parameters=_USE_PARAMS,
        handler=tool_use_skill,
    )
