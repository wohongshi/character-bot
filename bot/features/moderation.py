"""屏蔽词：``settings.bad_words`` 命中 → 回一句 + 拦下消息（不给 agent）

settings 可配：``bad_words``（列表）、``reply``（回复文案）、``cooldown``（秒，默认 60，
冷却期内静默拦截不回复，防刷屏）。
"""

import time

NAME = "屏蔽词"
DESCRIPTION = "屏蔽词过滤：命中就拦下来不回。词表空着就等于没开"
VERSION = "1.0"

# 面板「插件 → 设置」按这个 schema 自动出表单（AstrBot _conf_schema 风格）
CONFIG_SCHEMA = {
    "bad_words": {"type": "list", "default": [], "description": "屏蔽词，一行一个（空 = 不启用）"},
    "reply": {"type": "str", "default": "这些不聊啦～换个话题吧",
              "description": "命中后回复的文案", "max_len": 200},
    "cooldown": {"type": "int", "default": 60, "min": 0, "max": 3600,
                 "description": "冷却（秒）：冷却期内静默拦截，不回复"},
}

DEFAULT_REPLY = "这些不聊啦～换个话题吧"


def register(api):
    words = [str(w).strip() for w in (api.settings.get("bad_words") or []) if str(w).strip()]
    if not words:
        api.log("没有配置 bad_words，跳过钩子注册")
        return

    reply = str(api.settings.get("reply") or DEFAULT_REPLY)
    cooldown = float(api.settings.get("cooldown") or 60)

    # 词表规模决定用哪套实现（实测在 docs/POLYGLOT.md）：
    #   ≤ 64 词：Python 的 `w in text` 走 str 快搜，比 ctypes 调用还快（1.9 µs vs 6.4 µs 底噪）
    #   ≥ 64 词：Rust 的 Aho-Corasick 一次扫描搞定，500 词时快 1.7×，且随词表增大差距继续拉
    # 两套结果一致（都返回命中词），所以这里只切路径，不动语义。
    use_native = len(words) >= 64
    if use_native:
        api.log("启用 Rust 扫描器（%d 个词）", len(words))

    async def check(ctx):
        if use_native:
            try:
                from bot import native as _native
                hit = _native.scan(ctx.text or "", words)
                if hit:
                    return await _block()
            except Exception:                        # noqa: BLE001
                pass                                  # 原生层出问题就退回 Python 版
        low = (ctx.text or "").lower()
        for w in words:
            if w.lower() in low:
                return await _block()
        return None

    async def _block():
        if cooldown > 0:
            now = time.monotonic()
            if now < api.state.get("last", 0.0):
                return api.BLOCK                    # 冷却中：静默拦截，不刷屏
            api.state["last"] = now + cooldown
        await ctx.reply(reply)
        return api.BLOCK

    api.on_message(check, priority=10)
    api.log("启用，%d 个词（冷却 %ds）", len(words), int(cooldown))
