"""记事本 —— 给 agent 的 ``remember`` / ``recall``。

通用插件。写的是 `state/agent_notes.json`（按 scope 隔离：群 / 私聊不串），
所以关掉它只是「不再记新事」，旧记录还在文件里。
"""
from __future__ import annotations

NAME = "记事本"
NAME_EN = "Notes"
DESCRIPTION_EN = "Remember small things per conversation, and look them up later."
DESCRIPTION = "把值得记住的小事记下来，以后能翻出来。按会话隔离，群和私聊不串。"
VERSION = "1.0"

_REMEMBER_PARAMS = {
    "type": "object",
    "properties": {"text": {"type": "string", "description": "要记住的内容"}},
    "required": ["text"],
}

_RECALL_PARAMS = {
    "type": "object",
    "properties": {"query": {"type": "string", "description": "关键词；空 = 最近几条"}},
}


def register(api):
    from bot.agent import tool_recall, tool_remember

    api.add_tool(
        "remember",
        description="把一件值得记住的小事写进本会话的记事本（以后能 recall）。",
        parameters=_REMEMBER_PARAMS,
        handler=tool_remember,
    )
    api.add_tool(
        "recall",
        description="从本会话记事本里翻找以前记下的事。",
        parameters=_RECALL_PARAMS,
        handler=tool_recall,
    )
