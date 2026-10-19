"""角色配色：从立绘里取主色，生成一套主题色。

以前换角色只能手填色号，或者沿用上一个角色的红色 —— **换一张立绘，主题还是别人的颜色**。
现在：PIL 解码（Python 侧，成熟）+ C++ 直方图取色（native.palette），
结果落 `state/charpalette.json` 缓存（立绘 mtime 变了才重算）。

输出口径
--------
- `accent`：最「像主色」的那个（饱和度高、亮度适中、占比大）
- `accent2`：与 accent 色相差得远的第二个，用来做渐变
- `ink` / `dark`：从同一批颜色里挑最暗的，给深色主题兜底
- `luma`：整图平均亮度，决定覆盖文字用白还是黑
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path

log = logging.getLogger("klee.palette")

_ROOT = Path(__file__).resolve().parents[1]
_LOCK = threading.Lock()


def _state_dir() -> Path:
    env = os.environ.get("KLEE_STATE_DIR")
    return Path(env) if env else (_ROOT / "state")


def _cache_path() -> Path:
    return _state_dir() / "charpalette.json"


def _load_cache() -> dict:
    try:
        return json.loads(_cache_path().read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}


def _save_cache(data: dict) -> None:
    p = _cache_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(p)
    except OSError as e:
        log.warning("配色缓存写不进去：%s", e)


def _img_path(character: str, root: Path) -> Path | None:
    """角色立绘：优先中号（够算颜色，又不用解码 900px 大图）。"""
    base = root / "static" / "img" / "chars"
    for suffix in ("_m.webp", ".webp", "_m.jpg", ".jpg", ".png"):
        p = base / f"{character}{suffix}"
        if p.is_file():
            return p
    return None


def _hex2rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _dist(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    return sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5


def _hue(rgb: tuple[int, int, int]) -> float:
    r, g, b = (v / 255 for v in rgb)
    mx, mn = max(r, g, b), min(r, g, b)
    if mx == mn:
        return 0.0
    d = mx - mn
    if mx == r:
        h = ((g - b) / d) % 6
    elif mx == g:
        h = (b - r) / d + 2
    else:
        h = (r - g) / d + 4
    return h * 60


def compute(character: str, root: Path | None = None, *, n: int = 6) -> dict:
    """算一个角色的配色（带缓存）。失败时返回 ok=False，调用方回落到默认色。"""
    cid = str(character or "").strip().lower()
    if not cid:
        return {"ok": False, "error": "缺少角色 id"}
    root = root or _ROOT
    path = _img_path(cid, root)
    if path is None:
        return {"ok": False, "error": f"没有 {cid} 的立绘"}
    try:
        stamp = f"{path.stat().st_mtime_ns}:{path.stat().st_size}"
    except OSError:
        stamp = ""
    with _LOCK:
        cache = _load_cache()
        hit = cache.get(cid) or {}
        if hit.get("stamp") == stamp and hit.get("colors"):
            return {**hit, "cached": True}

    t0 = time.perf_counter()
    try:
        from bot.features import _cardimg
        Image, _Draw, _Font = _cardimg.load_pil()
        im = Image.open(path).convert("RGBA")
        # 取色不需要原分辨率：缩到 480 宽以内，采样更快、直方图更稳
        if im.width > 480:
            im = im.resize((480, max(1, round(im.height * 480 / im.width))), Image.BILINEAR)
        w, h = im.size
        # 一次性 tobytes()：以前逐像素 bytes((r,g,b,255)) 循环，38 万像素要 400 ms
        rgba = im.tobytes()
    except Exception as e:                                       # noqa: BLE001
        log.warning("读立绘失败 %s: %s", path, e)
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    from bot import native
    colors = native.palette(rgba, w, h, n, step=2)
    lum = native.luma(rgba, w, h, step=4)
    took = (time.perf_counter() - t0) * 1000
    if not colors:
        return {"ok": False, "error": "取色失败（原生库没编？）", "ms": round(took, 1)}

    rgb = [_hex2rgb(c) for c in colors]

    def _luma_of(c):
        return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]

    # 主色不能是近乎黑/白：那当 accent 会把整套主题拖成灰。
    # 优先挑「亮度适中 + 有饱和度」的，实在没有才用第一个。
    def _sat(c):
        mx, mn = max(c), min(c)
        return 0 if mx == 0 else (mx - mn) / mx

    pick = next((c for c in rgb if 45 <= _luma_of(c) <= 225 and _sat(c) >= 0.18), rgb[0])
    rgb = [pick] + [c for c in rgb if c != pick]
    accent = rgb[0]
    # 第二个色：色相差 ≥ 25°，没有就退而求其次取距离最远的
    accent2 = next((c for c in rgb[1:] if abs(_hue(c) - _hue(accent)) >= 25), None)
    if accent2 is None:
        accent2 = max(rgb[1:] or [accent], key=lambda c: _dist(c, accent))
    darkest = min(rgb, key=lambda c: sum(c))

    out = {
        "ok": True,
        "character": cid,
        "image": str(path.relative_to(root)),
        "stamp": stamp,
        "colors": colors,
        "accent": "#%02x%02x%02x" % accent,
        "accent2": "#%02x%02x%02x" % accent2,
        "ink": "#%02x%02x%02x" % darkest,
        "luma": lum,
        "on_dark": bool(lum >= 0 and lum > 150),   # True = 亮底，用深色文字
        "ms": round(took, 1),
        "by": "C++",
    }
    with _LOCK:
        cache = _load_cache()
        cache[cid] = out
        _save_cache(cache)
    return out


def all_cached() -> dict:
    return _load_cache()
