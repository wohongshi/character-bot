"""原生扩展层：Rust / C / C++ 编出来的 .so，用 ctypes 直调。

为什么不是「外部插件」那种进程形态
----------------------------------
进程（NDJSON over stdio）适合「偶尔用一次」的能力：Node/Java 启动几十毫秒无所谓。
但屏蔽词扫描是**每条入站消息**都要跑，宽度计算是**每一行卡片文字**都要跑 ——
每秒几十上百次的调用必须同进程，不然光进程间的 pipe 就比计算本身贵。

所以这一层是 `.so + ctypes`：没有额外进程、没有序列化、没有 IPC。

三种语言各接一件事（不是为炫技，是按特性挑的）：

| 语言 | 干什么 | 为什么是它 |
|---|---|---|
| Rust | 多词扫描、文本归一化 | 零 crate 也能写高效匹配器；内存安全，敢在消息路径上跑 |
| C | UTF-8 显示宽度 / 按宽截断 | 一张卡几十行文字，每行都要量；纯字节循环，最省 |
| C++ | 立绘主色提取 | 直方图 + 排序，模板容器写起来最省事；零依赖 |

**降级**：任何一个 .so 缺失或加载失败，对应函数自动回落到纯 Python ——
主机上没装 rustc/gcc 也能跑，只是慢一点。`status()` 把真实状态报给面板。
"""
from __future__ import annotations

import ctypes
import logging
import uuid
from pathlib import Path
from typing import Iterable, Sequence

log = logging.getLogger("klee.native")

_LIB_DIR = Path(__file__).with_name("native") / "lib"

_libs: dict[str, ctypes.CDLL | None] = {}
# 「装了但运行期坏了」以前是**完全静默**的：_lib() 只报加载失败，
# status() 只看 CDLL 能不能 load，于是 ABI 对不上（符号改名/被 strip）时
# 面板照样显示「已编译」，实际每条都走慢速 Python。这里每个库只吼一次。
_warned: set[str] = set()


def _warn_once(lib: str, msg: str, *args: object) -> None:
    if lib in _warned:
        return
    _warned.add(lib)
    log.warning(msg, *args)
_loaded = False


def _lib(name: str, fname: str) -> ctypes.CDLL | None:
    global _loaded
    if name in _libs:
        return _libs[name]
    path = _LIB_DIR / fname
    lib = None
    if path.is_file():
        try:
            lib = ctypes.CDLL(str(path))
        except OSError as e:                                     # noqa: BLE001
            log.warning("原生库 %s 加载失败（回落纯 Python）：%s", fname, e)
            lib = None
    _libs[name] = lib
    _loaded = True
    return lib


# ---------------------------------------------------------------- 宽度（C）
def _bind_width(lib: ctypes.CDLL) -> None:
    lib.klee_width.restype = ctypes.c_int
    lib.klee_width.argtypes = [ctypes.c_char_p, ctypes.c_size_t]
    lib.klee_truncate.restype = ctypes.c_size_t
    lib.klee_truncate.argtypes = [ctypes.c_char_p, ctypes.c_size_t, ctypes.c_int]
    lib.klee_width_version.restype = ctypes.c_char_p


def width(text: str) -> int:
    """显示宽度（列）：CJK / emoji 算 2，组合符算 0。"""
    s = str(text or "")
    if not s:
        return 0
    lib = _lib("width", "libklee_width.so")
    if lib is not None:
        try:
            _bind_width(lib)
            raw = s.encode("utf-8", "replace")
            return int(lib.klee_width(raw, len(raw)))
        except Exception:                                        # noqa: BLE001
            pass
    # 兜底：按 East Asian Width 粗算
    import unicodedata
    w = 0
    for ch in s:
        if unicodedata.combining(ch):
            continue
        w += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return w


def truncate(text: str, max_width: int) -> str:
    """按显示宽度截断（不会切在多字节字符中间）。"""
    s = str(text or "")
    if not s:
        return ""
    if max_width <= 0:
        return ""
    if width(s) <= max_width:
        return s
    lib = _lib("width", "libklee_width.so")
    if lib is not None:
        try:
            _bind_width(lib)
            raw = s.encode("utf-8", "replace")
            n = int(lib.klee_truncate(raw, len(raw), int(max_width)))
            return raw[:n].decode("utf-8", "replace")
        except Exception:                                        # noqa: BLE001
            pass
    out, w = [], 0
    import unicodedata
    for ch in s:
        cw = 0 if unicodedata.combining(ch) else (
            2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1)
        if w + cw > max_width:
            break
        out.append(ch)
        w += cw
    return "".join(out)


# ---------------------------------------------------------------- 扫描（Rust）
def _bind_text(lib: ctypes.CDLL) -> None:
    lib.klee_scan.restype = ctypes.c_int
    lib.klee_scan.argtypes = [ctypes.c_char_p, ctypes.c_size_t,
                              ctypes.c_char_p, ctypes.c_size_t,
                              ctypes.POINTER(ctypes.c_int), ctypes.c_size_t]
    lib.klee_normalize.restype = ctypes.c_int
    lib.klee_normalize.argtypes = [ctypes.c_char_p, ctypes.c_size_t,
                                   ctypes.c_char_p, ctypes.c_size_t]


def _needs_unicode_fold(ws: Sequence[str]) -> bool:
    """词表里有没有「大小写不能用 ASCII 折叠表达」的字符。

    Rust 侧为了快，只折叠 ASCII（`A`→`a`）。中文没有大小写所以无所谓，
    但**西里尔/希腊/土耳其语有**：`ДУРАК` 和 `дурак` 在 Rust 里永远匹配不上。
    这种情况下原生结果不可信，必须让 Python 的 `lower()` 再判一次。
    """
    for w in ws:
        for ch in w:
            if not ch.isascii() and (ch.isupper() or ch.islower()):
                return True
    return False


def scan(text: str | None, words: Sequence[str]) -> list[int]:
    """返回**命中的词下标**（按 words 顺序）。空词表/空文本返回 []。

    原生实现只做 ASCII 大小写折叠；词表里出现西里尔/希腊这类带大小写的
    非 ASCII 字符时，原生返回空**不能**当作「没命中」，要再走一遍 Python。
    """
    s = str(text or "")
    ws = [str(w) for w in (words or []) if str(w)]
    if not s or not ws:
        return []
    lib = _lib("text", "libklee_text.so")
    if lib is not None:
        try:
            _bind_text(lib)
            raw = s.encode("utf-8", "replace")
            packed = b"\x00".join(w.encode("utf-8", "replace") for w in ws)
            out = (ctypes.c_int * len(ws))()
            n = lib.klee_scan(raw, len(raw), packed, len(packed), out, len(ws))
            if n >= 0:
                hits = [int(out[i]) for i in range(n)]
                # 命中了直接返回；没命中且词表含「非 ASCII 的大小写」时才回落
                # （纯中文词表不会触发，快路径不变）
                if hits or not _needs_unicode_fold(ws):
                    return hits
        except Exception:                                        # noqa: BLE001
            pass
    low = s.lower()
    return [i for i, w in enumerate(ws) if w.lower() in low]


def normalize(text: str) -> str:
    """全角转半角、去零宽、空白折叠（比对前的归一化）。"""
    s = str(text or "")
    if not s:
        return ""
    lib = _lib("text", "libklee_text.so")
    if lib is not None:
        try:
            _bind_text(lib)
            raw = s.encode("utf-8", "replace")
            buf = ctypes.create_string_buffer(len(raw) * 3 + 16)
            n = lib.klee_normalize(raw, len(raw), buf, len(buf))
            if n >= 0:
                return buf.raw[:n].decode("utf-8", "replace")
        except Exception:                                        # noqa: BLE001
            pass
    return " ".join(s.split())


# ---------------------------------------------------------------- 取色（C++）
def _bind_palette(lib: ctypes.CDLL) -> None:
    lib.klee_palette.restype = ctypes.c_int
    lib.klee_palette.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                 ctypes.c_int, ctypes.c_char_p]
    lib.klee_luma.restype = ctypes.c_int
    lib.klee_luma.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.c_int, ctypes.c_int]


def palette(rgba: bytes, w: int, h: int, n: int = 5, step: int = 2) -> list[str]:
    """从 RGBA 像素里取主色，返回 ["#rrggbb", ...]（按观感排序）。"""
    if not rgba or w <= 0 or h <= 0 or n <= 0:
        return []
    lib = _lib("palette", "libklee_palette.so")
    if lib is not None:
        try:
            _bind_palette(lib)
            out = ctypes.create_string_buffer(n * 3)
            got = int(lib.klee_palette(rgba, int(w), int(h), int(step), int(n), out))
            raw = out.raw
            return ["#%02x%02x%02x" % (raw[i * 3], raw[i * 3 + 1], raw[i * 3 + 2])
                    for i in range(max(0, got))]
        except Exception as e:                                   # noqa: BLE001
            _warn_once("palette", "klee_palette 调用失败，回落纯 Python：%s", e)
    # 没有 .so 也要能用：粗量化直方图（慢一些，但「缺 .so 就回落 Python」这句话得是真的）
    return _py_palette(rgba, w, h, n, step)


def _py_palette(rgba: bytes, w: int, h: int, n: int, step: int = 8) -> list[str]:
    """纯 Python 取色：口径**对齐** C++ 版（palette.cpp）。

    两个坑（OCR 抓到的，都是「看着一样其实不一样」）：
    1. 桶值必须取**桶内真实颜色的平均值**，不能取 `>>3<<3` 的桶下界；
       同一张纯色图 (220,40,40)，C++ 出 `#dc2828`，取下界出 `#d82828`。
    2. 合并相近色用**平方欧氏距离 < 48²**（和 C++ 一致），
       曼哈顿距离对轴向/对角线差异的判定完全不同，兜底和原生会选出不同的 accent2。
    """
    stride = max(1, int(step))
    buckets: dict[tuple[int, int, int], list[int]] = {}
    for y in range(0, h, stride):
        row = y * w * 4
        for x in range(0, w, stride):
            i = row + x * 4
            if i + 3 >= len(rgba) or rgba[i + 3] < 8:
                continue
            r, g, b = rgba[i], rgba[i + 1], rgba[i + 2]
            key = (r >> 3, g >> 3, b >> 3)
            acc = buckets.get(key)
            if acc is None:
                buckets[key] = [1, r, g, b]
            else:
                acc[0] += 1
                acc[1] += r
                acc[2] += g
                acc[3] += b
    if not buckets:
        return []
    scored = []
    for cnt, sr, sg, sb in buckets.values():
        r, g, b = sr // cnt, sg // cnt, sb // cnt      # 桶内均值，和 C++ 一致
        mx, mn = max(r, g, b), min(r, g, b)
        sat = 0.0 if mx == 0 else (mx - mn) / mx
        lum = (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255
        mid = max(0.15, 1.0 - abs(lum - 0.55) * 1.1)
        scored.append((cnt * (0.25 + sat) * mid, r, g, b))
    scored.sort(reverse=True)
    out: list[tuple[int, int, int]] = []
    for _score, r, g, b in scored:
        if all((r - o[0]) ** 2 + (g - o[1]) ** 2 + (b - o[2]) ** 2 >= 48 * 48 for o in out):
            out.append((r, g, b))
        if len(out) >= n:
            break
    return ["#%02x%02x%02x" % (r, g, b) for r, g, b in out]


def luma(rgba: bytes, w: int, h: int, step: int = 2) -> int:
    """平均亮度 0..255（决定覆盖文字用白还是黑）。"""
    lib = _lib("palette", "libklee_palette.so")
    if lib is not None and rgba:
        try:
            _bind_palette(lib)
            return int(lib.klee_luma(rgba, int(w), int(h), int(step)))
        except Exception as e:                                   # noqa: BLE001
            _warn_once("luma", "klee_luma 调用失败，回落纯 Python：%s", e)
    if not rgba or w <= 0 or h <= 0:
        return -1
    stride = max(1, int(step))
    total, n = 0.0, 0
    for y in range(0, h, stride):
        row = y * w * 4
        for x in range(0, w, stride):
            i = row + x * 4
            if i + 3 >= len(rgba) or rgba[i + 3] < 8:
                continue
            total += 0.2126 * rgba[i] + 0.7152 * rgba[i + 1] + 0.0722 * rgba[i + 2]
            n += 1
    return int(total / n) if n else -1


# ---------------------------------------------------------------- 状态
_WHAT = {
    "width": ("libklee_width.so", "C", "显示宽度 / 按宽度截断"),
    "text": ("libklee_text.so", "Rust", "多词扫描 / 文本归一化"),
    "palette": ("libklee_palette.so", "C++", "立绘主色 / 平均亮度"),
}


def status() -> dict:
    """面板「运行时」用：每个原生库的真实状态（没编的也如实说）。"""
    rows = []
    for key, (fname, lang, what) in _WHAT.items():
        path = _LIB_DIR / fname
        loaded = _lib(key, fname) is not None
        rows.append({
            "key": key,
            "lang": lang,
            "what": what,
            "file": fname,
            "built": path.is_file(),
            "loaded": loaded,
            "bytes": path.stat().st_size if path.is_file() else 0,
            "ok": loaded,
        })
    return {"ok": True, "libs": rows, "dir": str(_LIB_DIR),
            "loaded": sum(1 for r in rows if r["loaded"]), "total": len(rows)}


def warm() -> int:
    """启动时摸一遍，别等到第一条消息才发现库有问题。返回可用的个数。"""
    s = status()
    return int(s["loaded"])
