"""联网工具 —— 给 agent 的 ``web_search`` / ``web_fetch``。

系统插件：出网属于框架能力（要走 SSRF 闸门、要能被运维统一关掉），
不跟任何角色绑定，也不能被「角色限定」覆盖。

实现放在 `bot/agent.py`：那里有 SSRF 闸门（复用 vision 的 `_assert_public_host`）
和统一的结果截断。这里只负责**声明**——工具名、说明、参数 schema，
以及「关掉这个插件，模型就真的看不到这两个工具」这层语义。
"""
from __future__ import annotations

NAME = "联网"
DESCRIPTION = "搜索网页 / 抓取正文。关掉它，她就不会联网，只能靠知识库和自己的记忆。"
VERSION = "1.0"
SYSTEM = True          # 归到「系统」分类（框架能力，不是角色功能）

_SEARCH_PARAMS = {
    "type": "object",
    "properties": {
        "query": {"type": "string", "description": "搜索关键词"},
        "count": {"type": "integer", "description": "结果条数 1-8，默认 5"},
    },
    "required": ["query"],
}

_FETCH_PARAMS = {
    "type": "object",
    "properties": {
        "url": {"type": "string", "description": "http/https 链接"},
        "max_chars": {"type": "integer", "description": "最多返回多少字，默认 3000"},
    },
    "required": ["url"],
}


def register(api):
    from bot.agent import tool_web_fetch, tool_web_search

    api.add_tool(
        "web_search",
        description="用 Bing 搜索公开网页，返回标题、链接和摘要。需要最新消息、资料、攻略时用。",
        parameters=_SEARCH_PARAMS,
        handler=tool_web_search,
    )
    api.add_tool(
        "web_fetch",
        description="打开一个公开网页并提取正文文字。已知链接要看内容时用。",
        parameters=_FETCH_PARAMS,
        handler=tool_web_fetch,
    )
