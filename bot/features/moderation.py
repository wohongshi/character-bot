"""屏蔽词：``settings.bad_words`` 命中 → 回一句 + 拦下消息（不给 agent）

settings 可配：``bad_words``（列表）、``reply``（回复文案）、``cooldown``（秒，默认 60，
冷却期内静默拦截不回复，防刷屏）。

**词表 ≥ 64 时用 Rust 的 Aho-Corasick**（见 docs/POLYGLOT.md 的交叉点实测），
更小就留在 Python —— ctypes 一次调用有 6 µs 底噪，词少时反而更慢。
两条路径**必须走同一套拦截逻辑**，否则「换个词量就换了个行为」。
"""

import time

NAME = "屏蔽词"
DESCRIPTION = "屏蔽词过滤：命中就拦下来不回。词表空着就等于没开"
VERSION = "1.1"

# 面板「插件 → 设置」按这个 schema 自动出表单（AstrBot _conf_schema 风格）
CONFIG_SCHEMA = {
    "bad_words": {"type": "list", "default": [], "description": "屏蔽词，一行一个（空 = 不启用）"},
    "reply": {"type": "str", "default": "这些不聊啦～换个话题吧",
              "description": "命中后回复的文案", "max_len": 200},
    "cooldown": {"type": "int", "default": 60, "min": 0, "max": 3600,
                 "description": "冷却（秒）：冷却期内静默拦截，不回复；0 = 不冷却"},
}

DEFAULT_REPLY = "这些不聊啦～换个话题吧"


def register(api):
    words = [str(w).strip() for w in (api.settings.get("bad_words") or []) if str(w).strip()]
    if not words:
        api.log("没有配置 bad_words，跳过钩子注册")
        return

    reply = str(api.settings.get("reply") or DEFAULT_REPLY)
    # 不能用 `x or 60`：面板上 0 是合法值（意思是「不冷却」），`0 or 60` 会把它变成 60
    raw_cd = api.settings.get("cooldown", 60)
    try:
        cooldown = 60.0 if raw_cd is None else float(raw_cd)
    except (TypeError, ValueError):
        cooldown = 60.0

    use_native = len(words) >= 64
    if use_native:
        api.log("启用 Rust 扫描器（%d 个词）", len(words))

    async def _block(ctx):
        """拦截的统一出口：冷却判定 + 回复 + BLOCK。

        必须把 ctx 传进来 —— 之前写成闭包零参数，`ctx` 在 register 作用域里不存在，
        于是命中屏蔽词时抛 NameError：小词表下消息**根本没被拦住**，
        大词表下因为进过一次冷却反而「静默拦截」（连回复都没有）。
        两套表现都是错的，而且都不报错。
        """
        if cooldown > 0:
            now = time.monotonic()
            if now < api.state.get("last", 0.0):
                return api.BLOCK                    # 冷却中：静默拦截，不刷屏
            api.state["last"] = now + cooldown
        await ctx.reply(reply)
        return api.BLOCK

    async def check(ctx):
        text = ctx.text or ""
        if use_native:
            try:
                from bot import native as _native
                # native.scan 自己会在 .so 缺失/出错时回落 Python，
                # 所以它「返回空列表」= 真的没命中，**不能**再跑下面的全量循环 ——
                # 以前这里没有 return，于是一条干净消息要跑 Aho-Corasick
                # 加一遍 O(词数×长度) 的 Python 扫描，比纯 Python 还慢（OCR 抓到）。
                if not _native.scan(text, words):
                    return None
                return await _block(ctx)
            except Exception as e:                  # noqa: BLE001
                api.log("原生扫描失败，本次退回 Python：%s", e)
        low = text.lower()
        for w in words:
            if w.lower() in low:
                return await _block(ctx)
        return None

    api.on_message(check, priority=10)
    api.log("启用，%d 个词（冷却 %ds）", len(words), int(cooldown))
