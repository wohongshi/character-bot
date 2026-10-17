"""可莉的语音合成（MIMO TTS）—— 面板版（纯标准库，同步；调用方放线程池）。

- 缓存：按 (音色, 文本) 哈希存 state/tts_cache/*.wav，同一句不重复合成
- 配置：MIMO_TTS_API_KEY（studio .env 或环境变量）、voice 默认 冰糖
- 一切失败返回 None，绝不阻塞文字消息
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import logging
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

logger = logging.getLogger("klee.bot.tts")

API_URL = "https://api.xiaomimimo.com/v1/chat/completions"
MODEL = "mimo-v2.5-tts"
DEFAULT_VOICE = "冰糖"
SEED_TEXT = "你好呀，我是可莉，哒哒哒！"
MAX_CHARS = 120


def synthesize(text: str, *, voice: str = "", style: str = "", cache_dir: Path,
               timeout: float = 40.0) -> Path | None:
    text = (text or "").strip()[:MAX_CHARS]
    if not text:
        return None
    voice = str(voice or "").strip() or str(os.environ.get("ONEBOT_TTS_VOICE") or "").strip() \
        or DEFAULT_VOICE
    key = (os.environ.get("MIMO_TTS_API_KEY") or "").strip()
    if not key:
        logger.warning("TTS: 缺 MIMO_TTS_API_KEY，跳过")
        return None

    cache_dir = Path(cache_dir)
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        fname = hashlib.sha1(f"{voice}\n{style}\n{text}".encode()).hexdigest()[:16] + ".wav"
        cached = cache_dir / fname
        if cached.exists() and cached.stat().st_size > 1000:
            return cached
    except OSError as e:
        logger.warning("TTS: 缓存目录不可用：%s", e)
        return None

    content = (f"<style>{style}</style>" if style else "") + text
    body = {
        "model": MODEL,
        "messages": [
            {"role": "user", "content": SEED_TEXT},
            {"role": "assistant", "content": content},
        ],
        "audio": {"format": "wav", "voice": voice},
    }
    req = urllib.request.Request(
        API_URL, data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {key}"}, method="POST")
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:      # noqa: S310
            raw_resp = r.read(8 * 1024 * 1024 + 1)
            if len(raw_resp) > 8 * 1024 * 1024:
                logger.warning("TTS: 响应过大")
                return None
            data = json.loads(raw_resp.decode("utf-8", "replace") or "{}")
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", "replace")[:200]
        except (OSError, AttributeError):
            pass
        logger.warning("TTS: HTTP %s — %s", e.code, detail)
        return None
    except (OSError, ValueError, urllib.error.URLError) as e:
        logger.warning("TTS: 合成失败 — %s: %s", type(e).__name__, e)
        return None
    try:
        payload = (((data.get("choices") or [{}])[0].get("message")) or {}).get("audio", {}).get("data")
    except (AttributeError, IndexError, TypeError):
        payload = None
    if not payload:
        logger.warning("TTS: 无 audio 数据 — %s", str(data)[:200])
        return None
    if len(str(payload)) > 8 * 1024 * 1024 * 4 // 3:
        logger.warning("TTS: audio payload 过大")
        return None
    try:
        raw = base64.b64decode(payload, validate=True)
    except (ValueError, TypeError, binascii.Error):
        logger.warning("TTS: audio base64 解码失败")
        return None
    if len(raw) < 1000 or raw[:4] != b"RIFF" or raw[8:12] != b"WAVE":
        logger.warning("TTS: 返回不是 WAV（%d 字节）", len(raw))
        return None
    tmp = cached.with_name(f"{cached.name}.{os.getpid()}.{time.time_ns()}.part")
    try:
        tmp.write_bytes(raw)
        os.replace(tmp, cached)
    except OSError as e:
        logger.warning("TTS: 写缓存失败：%s", e)
        return None
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
    logger.info("TTS: 合成 %d 字 → %d KB（%.1fs）", len(text), len(raw) // 1024,
                time.monotonic() - t0)
    return cached
