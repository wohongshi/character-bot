"""可莉一言：``.一言`` —— 从 hitokoto 拿一句今日名言（无需 key，失败走内置兜底）。"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import urllib.request

logger = logging.getLogger(__name__)

NAME = "一言"
DESCRIPTION = "随机来一句名言：.一言（hitokoto，断网有兜底）"
VERSION = "1.0"

# 角色限定：只有这些角色启用；空 = 通用插件（可在面板关闭）
CHARACTERS = ('klee',)

API = "https://v1.hitokoto.cn/?encode=json&max_length=30"
TIMEOUT = 8

FALLBACK = [
    "蹦蹦炸弹虽然会炸，但可莉会负责任地收拾干净！",
    "鱼很好吃，所以值得去努力——哪怕要绕开琴团长。",
    "嘟嘟可说：今天也要开开心心地活着哦。",
    "可莉的古书告诉我们：勇敢的人不会害怕明天。",
    "风吹过奔狼领，蒲公英会记得每一个许愿的人。",
    "可莉最喜欢的地方，是有你在的地方。",
    "被关禁闭也没关系，反正可莉总有办法跑出来。",
    "冒险家要出发啦，今天的宝箱一定藏在最亮的地方！",
]


def fetch_quote(url: str = API, timeout: int = TIMEOUT):
    """同步拿一句话，返回 (text, source)；失败抛异常。"""
    req = urllib.request.Request(url, headers={"User-Agent": "klee-onebot/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:      # noqa: S310
        data = json.loads(resp.read().decode("utf-8"))
    text = str((data or {}).get("hitokoto") or "").strip()
    if not text:
        raise RuntimeError("empty-hitokoto")
    source = str(data.get("from") or "").strip()
    who = str(data.get("from_who") or "").strip()
    if who:
        source = f"{who}《{source}》" if source else who
    return text, source


def register(api):
    settings = dict(getattr(api, "settings", None) or {})

    async def one_word(ctx):
        try:
            text, source = await asyncio.to_thread(fetch_quote, settings.get("api") or API,
                                                   int(settings.get("timeout") or TIMEOUT))
            line = f"「{text}」"
            if source:
                line += f"\n—— {source}"
            await ctx.reply(line)
        except Exception as e:                                   # noqa: BLE001
            logger.warning("OneBot quote: 取一句话失败 — %s", e)
            await ctx.reply("「%s」\n—— 可莉的小脑袋（网线好像松了）" % random.choice(FALLBACK))

    api.on_command("一言", one_word, aliases=("hitokoto", "一句话"), cooldown=5,
                   help=".一言 —— 随机来一句名言（可莉念给你听）")
