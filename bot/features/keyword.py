"""关键词自动回复：config.json → settings.keyword.rules

规则格式（在 ``onebot_features/config.json`` 里改）::

    "keyword": {
      "rules": [
        {"pattern": "早上好", "reply": "早呀！今天也要开心哦", "mode": "contains", "scope": "group", "cooldown": 600},
        {"pattern": "^打卡$", "reply": "打卡成功！", "mode": "regex", "scope": "both", "cooldown": 0}
      ]
    }

mode：contains（包含，默认）/ exact（完全相等）/ regex（正则）
scope：both（默认）/ group / dm；cooldown：秒（同一个规则在这段时间内不重复触发）
命中后直接回复并**拦下**该消息（不会再去打扰本体）。
"""

import re
import time

NAME = "关键词回复"
DESCRIPTION = "关键词自动回复：有人说到这个词，她就回一句。在「插件 → 设置」里配规则"
VERSION = "1.0"


def register(api):
    rules = []
    for r in (api.settings.get("rules") or []):
        try:
            pattern = str(r.get("pattern") or "")
            reply = str(r.get("reply") or "")
            if not pattern or not reply:
                continue
            rule = {
                "pattern": pattern,
                "reply": reply,
                "mode": str(r.get("mode") or "contains").lower(),
                "scope": str(r.get("scope") or "both").lower(),
                "cooldown": float(r.get("cooldown") or 0),
                "rx": None,
            }
            if rule["mode"] == "regex":
                rule["rx"] = re.compile(pattern, re.I)
            rules.append(rule)
        except Exception as e:                                # noqa: BLE001
            api.log("规则解析失败：%s", e)
    if not rules:
        api.log("没有配置规则，跳过钩子注册")
        return

    async def match(ctx):
        for i, r in enumerate(rules):
            if r["scope"] == "group" and not ctx.is_group:
                continue
            if r["scope"] == "dm" and ctx.is_group:
                continue
            if r["mode"] == "regex":
                hit = r["rx"].search(ctx.text) is not None
            elif r["mode"] == "exact":
                hit = ctx.text.strip() == r["pattern"]
            else:
                hit = r["pattern"].lower() in (ctx.text or "").lower()
            if not hit:
                continue
            if r["cooldown"] > 0:
                key = f"kw{i}"
                now = time.monotonic()
                if now < api.state.get(key, 0.0):
                    continue
                api.state[key] = now + r["cooldown"]
            await ctx.reply(r["reply"])
            return api.BLOCK
        return None

    api.on_message(match, priority=60)
    api.log("启用，%d 条规则", len(rules))
