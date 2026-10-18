"""复读机：群里连续同样的消息 → 跟着复读一次（最后一次，排列在审核/关键词之后）。

- 只认群聊；2~40 字纯文本；命令/含占位符的不复读
- priority=80：排在 moderation(10) / 关键词(60) 之后，坏话和拦截词先被处理
- 配置（config.json 的 settings.repeater）：
    threshold  3     连续几条触发
    cooldown   180   同群冷却（秒）
    chance     0.9   触发概率
"""
import random
import time

NAME = "复读机"
DESCRIPTION = "群里连续同样的消息，她会跟着说一遍"
VERSION = "1.0"

_DEFAULTS = {"threshold": 3, "cooldown": 180.0, "chance": 0.9}

CONFIG_SCHEMA = {
    "threshold": {"type": "int", "default": 3, "min": 2, "max": 10,
                  "description": "连续几条相同的消息才复读"},
    "cooldown": {"type": "int", "default": 180, "min": 0, "max": 3600,
                 "description": "同群冷却（秒）"},
    "chance": {"type": "float", "default": 0.9, "min": 0, "max": 1,
               "description": "触发概率（0~1）"},
}


def register(api):
    cfg = {**_DEFAULTS, **(api.settings or {})}
    threshold = max(2, int(cfg["threshold"]))
    cooldown = max(0.0, float(cfg["cooldown"]))
    chance = min(1.0, max(0.0, float(cfg["chance"])))

    last_text, count, last_fire = {}, {}, {}

    def clean(text):
        return " ".join((text or "").split()).strip()

    async def watch(ctx):
        if not ctx.is_group or not ctx.text:
            return None
        text = clean(ctx.text)
        if not (2 <= len(text) <= 40):
            return None
        if text[0] in "/.。．!！" or "[" in text:
            return None
        gid = ctx.group_id
        if text == last_text.get(gid):
            count[gid] = count.get(gid, 1) + 1
        else:
            last_text[gid] = text
            count[gid] = 1
        if count.get(gid, 0) < threshold:
            return None
        now = time.monotonic()
        if now - last_fire.get(gid, -99999) < cooldown:
            return None
        last_fire[gid] = now
        count[gid] = 0
        if random.random() > chance:
            return None
        try:
            await ctx.reply(text)
        except Exception:                                        # noqa: BLE001
            return None
        return api.BLOCK

    api.on_message(watch, priority=80)
