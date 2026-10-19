"""卡片渲染公共件 —— 给「会出图的插件」共用（抽卡 / 运势 / 签到…）。

原先三处各写一份 PIL 借包、卡片目录、文件名、文本清洗与旧图回收，各修各的；
统一挪到这里，改一处就好（今天 OCR 二轮的 `_load_pil` 加锁只改了 gacha，正是这个坑）。

放这个目录是安全的：`features.py` 的插件 glob **只收非 `_` 开头**的 .py，
所以 `_cardimg.py` 不会被当成插件；插件 `import _cardimg` 即可（插件目录已在 sys.path 上）。
"""

from __future__ import annotations

import asyncio
import contextlib
import glob
import hashlib
import logging
import os
import re
import shutil
import sys
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

_STUDIO_HOME = Path(__file__).resolve().parents[2]
FONT_CUTE = str(_STUDIO_HOME / "static" / "fonts" / "ZCOOLKuaiLe-Regular.ttf")
FONT_CJK = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
CJK_SC_INDEX = 2                      # NotoSansCJK-Regular.ttc 里 SC 面的 index
KEEP_CARDS = 50

# 出站会解析 [CQ:at,qq=all] / [CQ:image,…] 这类富文本标记 → 会进卡片的文本先洗掉方括号
_MARKUP = re.compile(r"[\[\]<>]|&#\d+;")
# 白名单式清洗：允许中英数、常用中文标点与装饰符号，其余一律去掉
_TEXT_OK = re.compile(r"[^0-9A-Za-z\u3000-\u303f\u4e00-\u9fff（）()·—\-…、×]")


def clean(text: str, limit: int = 12) -> str:
    """白名单清洗 + 截断（卡片上的名字/物品名）。"""
    return _TEXT_OK.sub("", text or "").strip()[:limit]


def safe_name(text: str, limit: int = 12) -> str:
    """去方括号/实体转义后截断（昵称、@ 参数等用户可控文本）。"""
    return _MARKUP.sub("", text or "").strip()[:limit]


def short(text: str, limit: int) -> str:
    """按**显示宽度**截断（超长补 …），先走白名单清洗。

    原来是按 `len()` 截：中文一个字占两列，20 个汉字能顶满 40 列，
    而 `len()` 只看 20 —— 卡片上中英混排的名字会被撑出框，或者明明还有空位却被切。
    宽度计算交给 C 扩展（`bot/native.py`，没编 .so 时自动回落纯 Python）。
    """
    t = clean(text, 999)
    if limit <= 0:
        return ""                                   # OCR 六轮 #7：原来 limit<=1 会返回 2 个字符
    try:
        from bot import native as _native
        if _native.width(t) <= limit:
            return t
        return _native.truncate(t, max(1, limit - 1)) + "…"
    except Exception:                               # noqa: BLE001
        return t if len(t) <= limit else t[:limit - 1] + "…"


def safe_token(raw) -> str:
    """文件名里的用户标识：不透明哈希（顺带堵住 `..` / `/` 路径穿越）。"""
    return hashlib.sha1(str(raw).encode("utf-8")).hexdigest()[:12]


def stamp() -> str:
    """同秒并发也不撞名的文件名片段（秒 + 微秒尾数）。"""
    return f"{int(time.time())}-{time.time_ns() % 1_000_000:06d}"


def card_path(directory, prefix: str, user_id) -> Path:
    """`<dir>/<prefix>-<用户哈希>-<秒>-<微秒>.png`。"""
    return Path(directory) / f"{prefix}-{safe_token(user_id)}-{stamp()}.png"


_PIL = None
_PIL_LOCK = threading.Lock()


def load_pil():
    """借 pm-env 的 PIL（网关 python 同为 3.14）。结果缓存 + 加锁：
    渲染跑在 `asyncio.to_thread` 里，多线程同时改全局 `sys.path` 会互相干扰。"""
    global _PIL
    if _PIL is not None:
        return _PIL
    with _PIL_LOCK:
        if _PIL is None:
            # 本机有 Hermes 的 venv 就借它的 Pillow；没有就要求系统装了 Pillow
            for sp in glob.glob("/root/.hermes/installs/*/environments/*/venv/lib/python3*/site-packages"):
                if sp not in sys.path:
                    sys.path.append(sp)      # append：自己的包优先，只借缺失的
            from PIL import Image, ImageDraw, ImageFont
            _PIL = (Image, ImageDraw, ImageFont)
    return _PIL


# ---- 字体缓存：truetype 每次都会重开字体文件（CJK ttc 19MB，实测 ~18ms/次），
#      而且每次新建 Font 对象会把 PIL 的 glyph 栅格缓存全部丢掉——渲染头号热点之一。
#      缓存对象跨线程共享：Pillow 的 FreeType 调用自带内部锁，并发读安全。
_FONT_CACHE: dict = {}


def font_cute(size: int):
    """带缓存的可爱字体（ZCOOLKuaiLe）。"""
    load_pil()                                  # 保持借包/猴补丁语义（测试靠它模拟无 PIL）
    key = ("cute", int(size))
    f = _FONT_CACHE.get(key)
    if f is None:
        _, _, ImageFont = load_pil()
        f = ImageFont.truetype(FONT_CUTE, int(size))
        _FONT_CACHE[key] = f
    return f


def font_cjk(size: int):
    """带缓存的思源黑体（SC 面，ttc index=2）。"""
    load_pil()
    key = ("cjk", int(size))
    f = _FONT_CACHE.get(key)
    if f is None:
        _, _, ImageFont = load_pil()
        f = ImageFont.truetype(FONT_CJK, int(size), index=CJK_SC_INDEX)
        _FONT_CACHE[key] = f
    return f


def ensure_sibling_path(anchor) -> None:
    """确保 anchor 所在目录在 sys.path（幂等 append）。

    背景：FeatureManager 装载插件时会临时把插件目录加进 sys.path、装载完**移除**；
    而插件在运行时惰性导入兄弟模块（wish→panel、randchar→panel、charcard→randchar）时，
    目录已经不在 path 上 → ModuleNotFoundError 被 except 吞掉 → 功能静默失效
    （2026-10-17 冒烟抓到的真 bug）。惰性导入前先调这个自愈。
    """
    d = str(Path(str(anchor)).resolve().parent)
    if d not in sys.path:
        sys.path.append(d)                       # append：只作兜底，不盖 stdlib


def save_img(im, path, *, level: int = 1) -> None:
    """卡片落盘统一口：compress_level=1（默认 6 的 zlib 是大图渲染头号热点，实测快 ~2 倍）；
    全不透明的 RGBA 先转 RGB（编码更快、体积更小）。失败抛异常，由调用方的兜底接住。"""
    if im.mode == "RGBA":
        try:
            if im.getchannel("A").getextrema() == (255, 255):
                im = im.convert("RGB")
        except (ValueError, OSError):
            pass                                # 读坏就当有透明，原样存 RGBA
    im.save(path, compress_level=level)


def rcache_key(*parts) -> str:
    """渲染缓存 key：内容（命令表 / 展柜数据 / 版本号）任一变化 → 新 key。"""
    h = hashlib.sha1()
    for p in parts:
        h.update(str(p).encode("utf-8", "replace"))
        h.update(b"\x1f")
    return h.hexdigest()[:16]


def rcache_dir(settings, base) -> Path:
    """渲染缓存目录（卡片目录下的 .rcache/，名字不带 pattern 前缀冲突，不受 `*.png` 回收影响）。"""
    d = cards_dir(settings, base) / ".rcache"
    d.mkdir(parents=True, exist_ok=True)
    return d


def rcache_get(settings, base, name: str, key: str):
    """缓存命中 → 返回文件 Path；未命中/任何毛病 → None（渲染照常走）。"""
    try:
        p = rcache_dir(settings, base) / f"{name}-{key}.png"
        if p.is_file() and p.stat().st_size > 512:
            return p
    except OSError:
        pass
    return None


def rcache_put(settings, base, name: str, key: str, src, keep: int = 24) -> None:
    """回填缓存（tmp + 原子替换）；每个名字只留最近 keep 张。"""
    d = rcache_dir(settings, base)
    tmp = d / f".{name}-{key}.png.tmp"
    dst = d / f"{name}-{key}.png"
    shutil.copyfile(src, tmp)
    os.replace(tmp, dst)                        # 原子落位：读方永远看不到半张图
    cleanup_cards(d, f"{name}-*.png", keep)


def rcache_render(settings, base, name: str, key: str, out, render_fn, keep: int = 24) -> bool:
    """渲染结果复用（同步，调用方塞 to_thread）：
    命中 → 拷缓存到 out（省下整个渲染）；未中 → 调 render_fn 渲染并回填。
    缓存层任何毛病都不影响渲染本身（miss 就照常渲染）。render_fn 返回真值 = 渲染成功。"""
    hit = rcache_get(settings, base, name, key)
    if hit is not None:
        try:
            shutil.copyfile(hit, out)
            with contextlib.suppress(OSError):
                os.utime(hit, None)             # 命中续命，别被回收当旧图清掉
            return True
        except OSError as e:
            logger.debug("渲染缓存拷贝失败（%s）→ 现场重渲染", e)
    ok = bool(render_fn())
    if ok:
        try:
            rcache_put(settings, base, name, key, out, keep=keep)
        except OSError as e:
            logger.debug("渲染缓存回填失败（%s）", e)
    return ok


def cards_dir(settings: dict, base, default: str = "cards") -> Path:
    """卡片目录：配置里给了 `cards_dir` 就用它，否则 `base/<default>`（plugins 各自传自己的 BASE）。"""
    d = (settings or {}).get("cards_dir")
    p = Path(str(d)) if d else Path(base) / default
    p.mkdir(parents=True, exist_ok=True)
    return p


def cleanup_cards(directory, pattern: str, keep: int = KEEP_CARDS) -> None:
    """旧图回收：按 mtime 只留最近 keep 张。

    OCR 六轮 #9：原来整批操作套一个 `except OSError: pass` —— 有一个文件并发被删/不可读，
    `sorted()`（对每个文件 `stat()`）就抛异常、**整轮回收放弃**，旧图一直堆着还没人知道。
    现在逐文件兜底：坏的当最旧（排前面先被删），删不掉的只记一笔。
    """
    def _mtime(p: Path) -> float:
        try:
            return p.stat().st_mtime
        except OSError:
            return -1.0                                   # 读不到 mtime 的（含断链）当最旧

    try:
        files = sorted(Path(directory).glob(pattern), key=_mtime)
    except OSError:
        return
    for p in files[:-keep] if keep > 0 else files:
        try:
            p.unlink()
        except OSError as e:
            logger.debug("旧图回收跳过 %s（%s）", p, e)


def fit_box(img, box: int):
    """等比缩放进 box×box（长边贴合）；尺寸非法返回 None。"""
    Image, _, _ = load_pil()
    w, h = img.size
    if w <= 0 or h <= 0 or box <= 0:
        return None
    s = box / max(w, h)
    return img.resize((max(1, int(w * s)), max(1, int(h * s))), Image.LANCZOS)


# ---- 图标内存缓存：同一张图反复贴（练度 9 图标 / 帮助头像）不用每次解码；
#      按 mtime 失效，满了清一半。返回的对象视为只读（下游只有 resize/split/paste）。
_ICON_CACHE: dict = {}
_ICON_CACHE_MAX = 48
_ICON_MAX_DIM = 1024                            # 只缓存常规图标，巨型图直接过不加内存


def _icon_from_path(src):
    global _ICON_CACHE
    Image, _, _ = load_pil()
    key = str(src)
    try:
        mtime = os.stat(src).st_mtime_ns
    except OSError:
        mtime = None
    hit = _ICON_CACHE.get(key)
    if hit is not None and mtime is not None and hit[0] == mtime:
        return hit[1]
    with Image.open(src) as s:
        img = s.convert("RGBA")
    if mtime is not None and max(img.size) <= _ICON_MAX_DIM:
        if len(_ICON_CACHE) >= _ICON_CACHE_MAX:
            for k in list(_ICON_CACHE)[: _ICON_CACHE_MAX // 2]:
                _ICON_CACHE.pop(k, None)
        _ICON_CACHE[key] = (mtime, img)
    return img


def paste_icon(im, src, cx: int, top: int, box: int, radius: int = 14,
               fade: int = 0, fade_color=None, log=None):
    """把一张图（路径 / 已打开的 Image）缩放后按「圆角 + 底部渐隐」贴到卡片上。

    官方头像/图标多是方角、亮底、带白边，直贴到深色卡上像硬补丁，所以默认姿势是
    `圆角 mask ∩ 原图 alpha 通道` 再 paste，并以图底一条渐隐条融进卡片底色。

    - `cx` 水平中心、`top` 顶边；最大边缩到 `box`。
    - `radius=0` 只按原 alpha 贴（不裁圆角）；`fade=0` 不叠渐隐条。
    - `fade_color` 是渐隐条颜色（一般传卡片/底板底色），缺省黑。
    - 任何失败（文件损坏、尺寸非法、PIL 缺失）都返回 None，不抛 —— 卡片是锦上添花。
    - `log` 给了就失败时 `log.debug` 一行，方便定位是哪张图没贴上。

    返回 (贴上后的宽, 高)；失败返回 None。
    """
    try:
        Image, ImageDraw, _ = load_pil()
        if isinstance(src, (str, bytes)) or hasattr(src, "__fspath__"):
            img = _icon_from_path(src)            # 内存缓存（只读；下游只有 resize/paste）
        else:
            img = src.convert("RGBA")
        fitted = fit_box(img, box)
        if fitted is None:
            return None
        fw, fh = fitted.size
        if radius:
            rmask = Image.new("L", (fw, fh), 0)
            ImageDraw.Draw(rmask).rounded_rectangle([0, 0, fw - 1, fh - 1],
                                                    radius=radius, fill=255)
            mask = Image.new("L", (fw, fh), 0)
            mask.paste(rmask, (0, 0), fitted.split()[3])      # 圆角 ∩ 原透明通道
        else:
            mask = fitted.split()[3]
        x = cx - fw // 2
        im.paste(fitted, (x, top), mask)
        if fade and fade > 0 and fh > fade:
            grad = Image.new("L", (fw, fade), 0)
            gd = ImageDraw.Draw(grad)
            for i in range(fade):
                gd.line([(0, i), (fw, i)], fill=int(225 * (i / fade) ** 1.5))
            band = Image.new("RGB", (fw, fade), fade_color or (0, 0, 0))
            im.paste(band, (x, top + fh - fade), grad)
        return (fw, fh)
    except Exception as e:                                    # noqa: BLE001
        if log is not None:
            log.debug("图片粘贴跳过（%s）", e)
        return None


async def send_card(ctx, path: Path, caption: str, *, pattern: str | None = None,
                    keep: int = KEEP_CARDS) -> bool:
    """把卡片发出去；发不出去就删掉图并返回 False（调用方回退文本）。

    「发不出去」有两种：抛异常，或返回 `success=False`（适配器在 WS 断开时就是后者，
    以前只看异常 → 消息静默丢掉，连回复都没有）。两种都算失败。
    """
    p = Path(path)
    try:
        res = await ctx.adapter.send_image_file(ctx.chat_id, str(p), caption=caption)
    except Exception as e:                                   # noqa: BLE001
        logger.warning("卡片发送失败（%s）→ 回退文本", e)
        with contextlib.suppress(OSError):
            p.unlink()                                       # 发不出去就别把图留在盘上
        return False
    # OCR 三轮 #16：裸 False / success=0 这类「falsy 但不是严格 False」也要算失败
    ok_val = res if isinstance(res, bool) else getattr(res, "success", True)
    if res is False or (res is not None and not ok_val):
        logger.warning("卡片未被接受（%s）→ 回退文本",
                       "bare-False" if res is False else (getattr(res, "error", "") or f"success={ok_val!r}"))
        with contextlib.suppress(OSError):
            p.unlink()
        return False
    if pattern:
        await asyncio.to_thread(cleanup_cards, p.parent, pattern, keep)
    return True
