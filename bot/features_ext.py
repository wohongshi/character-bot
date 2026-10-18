"""运行时新功能：图片理解 / 语音回复 / 撤回与限流。

这些方法由 BotRuntime 继承混入（避免 runtime.py 无限膨胀）。
"""
from __future__ import annotations

import asyncio
import inspect
import logging
import re
import time
from pathlib import Path

from .tts import synthesize

logger = logging.getLogger("klee.bot.features")

_VOICE_TAG = re.compile(r"\[语音[:：]([^\]]{1,200})\]")


class RuntimeFeatures:
    """Mixin：需在 BotRuntime.__init__ 后调用 setup_extras()。"""

    def setup_extras(self, config: dict, *, preserve_buckets: bool = False) -> None:
        self.tts_enabled = bool(config.get("tts_enabled", False))
        self.tts_voice = str(config.get("tts_voice") or "冰糖")
        self.tts_max_chars = int(config.get("tts_max_chars") or 80)
        self.rate_limit_per_min = float(config.get("rate_limit_per_min") or 0)
        self.rate_limit_burst = float(config.get("rate_limit_burst") or 10)
        self._buckets: dict = self.__dict__.get("_buckets", {}) if preserve_buckets else {}
        self.image_dir = self.state_dir / "images"
        self.tts_cache = self.state_dir / "tts_cache"
        self.stats.setdefault("recalls", 0)
        self.stats.setdefault("rate_limited", 0)
        self.stats.setdefault("tts_out", 0)
        self.stats.setdefault("vision_in", 0)

    # ---------------- 适配器调用 ----------------
    async def _adapter_call(self, name: str, *args, **kwargs):
        """调适配器的一个动词，**同步实现和 async 实现都认**。

        `docs/FRAMEWORK.md` 承诺「同步 / async 都行」，之前 `maybe_await()`
        从来没被调用过 —— 换成 async 适配器后 `to_thread()` 拿到的是协程对象，
        消息不发、还只留一句 "coroutine was never awaited"。这里把承诺兑现：

          · 同步方法 → `asyncio.to_thread`（urllib 那种阻塞调用不能占住事件循环）
          · 协程函数 → 直接 await（它自己就该是非阻塞的）
        """
        # 走 `self.ob`（传输句柄）而不是 `self.adapter`：
        # ① 老代码/测试都是把 FakeOB 挂到 rt.ob 上做隔离的，绕过它就变成真发网络请求；
        # ② 切换适配器时两者一起换（apply_config 里），不会分叉。
        target = getattr(self, "ob", None) or self.adapter
        fn = getattr(target, name, None)
        if not callable(fn):
            from .adapters.base import AdapterError
            raise AdapterError(f"{getattr(target, 'platform', '?')} 没有实现 {name}()")
        if inspect.iscoroutinefunction(fn):
            return await fn(*args, **kwargs)
        res = await asyncio.to_thread(fn, *args, **kwargs)
        if inspect.isawaitable(res):        # 返回协程的同步包装（少见，但别再漏）
            return await res
        return res

    # ---------------- 语音 ----------------
    @staticmethod
    def extract_voice_tags(text: str) -> tuple[str, list]:
        """剥出 [语音:…] 标记 → (清理后的文本, [语音文本…])。"""
        tags = [m.group(1).strip() for m in _VOICE_TAG.finditer(text or "")]
        clean = _VOICE_TAG.sub("", text or "").strip()
        return clean, [t for t in tags if t]

    async def send_voice(self, chat_id: str, text: str) -> bool:
        if not self.tts_enabled or not text.strip():
            return False
        try:
            path = await asyncio.to_thread(
                synthesize, text.strip()[: self.tts_max_chars],
                voice=self.tts_voice, cache_dir=self.tts_cache)
            if path is None:
                return False
            async with self._chat_locks.setdefault(chat_id, asyncio.Lock()):
                await self._adapter_call("send_voice", chat_id, str(path))
            self.stats["tts_out"] += 1
            return True
        except Exception as e:                                     # noqa: BLE001
            logger.warning("发语音失败（%s）：%s", chat_id, e)
            return False

    # ---------------- 限流 ----------------
    def allow_send(self, chat_id: str) -> bool:
        """每会话令牌桶；rate_limit_per_min<=0 表示不限。"""
        if self.rate_limit_per_min <= 0:
            return True
        now = time.monotonic()
        cap = max(1.0, self.rate_limit_burst)
        rate = self.rate_limit_per_min / 60.0
        if len(self._buckets) > 500:                              # 清闲置桶，防无限增长
            refill_s = (cap / rate) if rate > 0 else 0
            self._buckets = {k: v for k, v in self._buckets.items()
                             if now - v[1] < refill_s * 2}
        tokens, last = self._buckets.get(chat_id, (cap, now))
        tokens = min(cap, tokens + (now - last) * rate)
        if tokens < 1.0:
            self.stats["rate_limited"] += 1
            return False
        self._buckets[chat_id] = (tokens - 1.0, now)
        return True
