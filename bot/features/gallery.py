"""可莉画廊：.可莉 —— 随机发一张可莉的官方美图（本地素材，零依赖）。"""
import logging
import random
import time
from pathlib import Path

logger = logging.getLogger(__name__)

NAME = "画廊"
DESCRIPTION = "随机发一张她的图"
VERSION = "1.0"

# 角色限定：只有这些角色启用；空 = 通用插件（可在面板关闭）
CHARACTERS = ('klee',)

# 相对本文件定位：仓库搬到哪都能跑（原来写死了绝对路径）
IMG_DIRS = [Path(__file__).resolve().parents[2] / "static" / "img"]
PATTERNS = ("klee-bday*.jpg", "klee-bday*.webp", "klee-hero.webp",
            "klee-intro.webp", "klee-ava.png")
CUTE_LINES = ["给！这是可莉哦！", "哒哒哒——好看吗？", "可莉最喜欢这张啦！",
              "呐，送给你！", "嘿嘿，被你发现啦！"]


_CACHE: dict = {"ts": 0.0, "files": []}
_CACHE_TTL = 300.0            # 5 分钟：图库基本不变，别每条命令都扫目录


def _collect() -> list:
    now = time.time()
    if _CACHE["files"] and now - _CACHE["ts"] < _CACHE_TTL:
        return _CACHE["files"]
    files = []
    for d in IMG_DIRS:
        for pat in PATTERNS:
            files.extend(sorted(d.glob(pat)))
    files = [f for f in files if f.is_file()]
    _CACHE.update(ts=now, files=files)
    return files


def register(api):
    async def send_pic(ctx):
        files = _collect()
        if not files:
            await ctx.reply("呜……可莉的相册找不到了（图库目录为空）")
            return
        pick = random.choice(files)
        caption = random.choice(CUTE_LINES) if random.random() < 0.7 else None
        # 适配器不会抛异常：失败时返回 success=False。只看异常会让消息静默消失。
        try:
            res = await ctx.adapter.send_image_file(ctx.chat_id, str(pick), caption=caption)
        except Exception as e:                                   # noqa: BLE001
            logger.warning("画廊发图异常（%s）: %s", pick.name, e)
            await ctx.reply("诶……这张发不出去，下次再试试？")
            return
        ok = res if isinstance(res, bool) else getattr(res, "success", True)
        if res is False or (res is not None and not ok):
            logger.warning("画廊发图未被接受（%s）: %s", pick.name,
                           getattr(res, "error", "") or f"success={ok!r}")
            await ctx.reply("诶……这张发不出去（可能是网络），下次再试试？")

    api.on_command("可莉", send_pic, aliases=("来张可莉",), cooldown=15,
                   help=".可莉 —— 随机来一张可莉的美图")
