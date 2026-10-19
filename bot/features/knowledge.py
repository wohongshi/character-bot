"""知识库检索 —— 给 agent 的 ``kb_search``。

通用插件：所有角色共用一个入口，但查询时带 `ctx.character`，
每个角色查自己的库（klee-kb 按 character 分索引）。
"""
from __future__ import annotations

NAME = "知识库"
NAME_EN = "Knowledge base"
DESCRIPTION_EN = "Look up this character's settings, lines and corpus."
DESCRIPTION = "按角色查设定、台词、语料。关掉它，回答设定类问题时她就只能靠上下文和人格。"
VERSION = "1.0"

_KB_PARAMS = {
    "type": "object",
    "properties": {
        "query": {"type": "string", "description": "要查的内容"},
        "k": {"type": "integer", "description": "条数 1-6，默认 4"},
    },
    "required": ["query"],
}


def register(api):
    from bot.agent import tool_kb_search

    api.add_tool(
        "kb_search",
        description="检索这个角色的知识库（设定、台词、语料）。回答设定类问题时先查它。",
        parameters=_KB_PARAMS,
        handler=tool_kb_search,
    )
