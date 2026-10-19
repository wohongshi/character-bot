"""表情包：每个角色自己的一套贴纸，文件名就是它的意思。

为什么不复用 `state/images`（画画/卡片那个目录）：那里的文件名是
`插件名-hash-时间戳`，人生看不懂，也没法「按意思挑一张」。表情包要的是
**名字有意义、能列出来、能删**，所以单独一个目录：`state/stickers/<角色>/开心.png`。

引用形态沿用面板既有的图片白名单（`_img_ref` 会把 `state/` 下的路径写成
`stickers/<角色>/开心.png`），所以 `/api/chat/image?p=` 天然就能服务它，
QQ 那边也直接走 `send_image_file`。
"""

from __future__ import annotations

import base64
import binascii
import os
import random
import re
import threading
import time
from pathlib import Path

MAX_BYTES = 2 * 1024 * 1024        # 表情包不是相册：2MB 已经很奢侈
MAX_PER_CHAR = 40                  # 一个角色最多这么多张（面板一次能看完）
MAX_NAME = 20
EXTS = (".png", ".jpg", ".jpeg", ".gif", ".webp")

_BAD_NAME = re.compile(r"[/\\:*?\"<>|\x00-\x1f]")
_LOCK = threading.Lock()

# 模型偶尔会把标记写成 [[sticker:开心]]、[表情:开心]、[sticker:开心]。
# 三种都认 —— 让它用哪个它未必记得住，认不出来就等于「说了要发表情却没发」。
_MARK = re.compile(r"\[{1,2}\s*(?:表情|sticker)\s*[:：]\s*([^\]\n]{1,24})\s*\]{1,2}", re.I)


def root(state_dir: Path | str) -> Path:
    return Path(state_dir) / "stickers"


def pack_dir(state_dir: Path | str, cid: str) -> Path:
    cid = str(cid or "klee").strip().lower()
    cid = re.sub(r"[^a-z0-9_\-]", "", cid) or "klee"
    return root(state_dir) / cid


def clean_name(raw: str) -> str:
    """名字只留「能当文件名、也能当语义」的部分。

    **带路径的名字直接拒**，不做静默净化：把 `../evil` 悄悄改成 `evil`
    看起来像成功了，实际上用户以为存的是别的名字。
    """
    s = str(raw or "").strip()
    for ext in EXTS:                       # 用户可能顺手写了 .png
        if s.lower().endswith(ext):
            s = s[: -len(ext)]
            break
    if "/" in s or "\\" in s or ".." in s:
        raise ValueError("名字里不能带路径分隔符")
    s = _BAD_NAME.sub("", s).strip(" .")
    s = re.sub(r"\s+", " ", s)
    if not s:
        raise ValueError("名字不能为空")
    if len(s) > MAX_NAME:
        raise ValueError(f"名字最长 {MAX_NAME} 个字")
    return s


def list_pack(state_dir: Path | str, cid: str) -> list:
    """这个角色的表情包，按名字排序。文件坏了/不是图片就跳过。"""
    d = pack_dir(state_dir, cid)
    out = []
    try:
        entries = sorted(d.iterdir(), key=lambda p: p.name)
    except OSError:
        return []
    for p in entries:
        if not p.is_file() or p.suffix.lower() not in EXTS:
            continue
        try:
            st = p.stat()
        except OSError:
            continue
        out.append({"name": p.stem, "file": p.name, "ext": p.suffix.lower(),
                    "bytes": st.st_size, "mtime": round(st.st_mtime, 1),
                    "ref": f"stickers/{d.name}/{p.name}"})
    return out


def _sniff_ext(blob: bytes) -> str:
    if blob.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if blob.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if blob[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif"
    if len(blob) > 12 and blob[:4] == b"RIFF" and blob[8:12] == b"WEBP":
        return ".webp"
    return ""


def save(state_dir: Path | str, cid: str, name: str, data_url: str) -> dict:
    """存一张表情。同名直接覆盖（改图不用先删）。

    只认真图片：按魔数判类型，不信后缀也不信浏览器给的 MIME。
    """
    d = pack_dir(state_dir, cid)
    nm = clean_name(name)
    raw = str(data_url or "")
    if "," in raw and raw.split(",", 1)[0].startswith("data:"):
        raw = raw.split(",", 1)[1]
    if not raw:
        raise ValueError("没有图片数据")
    if len(raw) > MAX_BYTES * 4 // 3 + 16:
        raise ValueError(f"表情太大（上限 {MAX_BYTES // 1024} KB）")
    try:
        blob = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError):
        raise ValueError("图片数据不是合法的 base64") from None
    if not blob:
        raise ValueError("空图片")
    if len(blob) > MAX_BYTES:
        raise ValueError(f"表情太大（上限 {MAX_BYTES // 1024} KB）")
    ext = _sniff_ext(blob)
    if not ext:
        raise ValueError("认不出图片格式（支持 PNG / JPEG / GIF / WebP）")

    with _LOCK:
        d.mkdir(parents=True, exist_ok=True)
        existing = list_pack(state_dir, cid)
        same = [x for x in existing if x["name"] == nm]
        if not same and len(existing) >= MAX_PER_CHAR:
            raise ValueError(f"这个角色最多 {MAX_PER_CHAR} 张表情，先删几张")
        dest = d / f"{nm}{ext}"
        tmp = dest.with_name(f".{dest.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        try:
            tmp.write_bytes(blob)
            os.replace(tmp, dest)
        finally:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
        # 换了个格式（png → gif）：同名旧文件要清掉，否则列表里出现两张同名的
        for x in same:
            if x["file"] != dest.name:
                try:
                    (d / x["file"]).unlink()
                except OSError:
                    pass
    return {"name": nm, "file": dest.name, "ref": f"stickers/{d.name}/{dest.name}",
            "bytes": len(blob), "ext": ext}


def delete(state_dir: Path | str, cid: str, name: str) -> bool:
    nm = clean_name(name)
    removed = False
    for x in list_pack(state_dir, cid):
        if x["name"] != nm:
            continue
        try:
            (pack_dir(state_dir, cid) / x["file"]).unlink()
            removed = True
        except OSError:
            pass
    return removed


def pick(state_dir: Path | str, cid: str, want: str) -> dict | None:
    """按名字挑一张：先精确，再包含（「开心」能命中「超开心」），最后随机。

    `want` 为空 = 随便来一张（用户只发了 `.表情`）。
    """
    items = list_pack(state_dir, cid)
    if not items:
        return None
    w = str(want or "").strip()
    if not w:
        return random.choice(items)
    if w in ("随机", "随便", "random"):
        return random.choice(items)
    low = w.lower()
    for x in items:                                   # 精确
        if x["name"].lower() == low:
            return x
    for x in items:                                   # 包含
        if low in x["name"].lower() or x["name"].lower() in low:
            return x
    return None


def apply_marks(text: str, state_dir: Path | str, cid: str) -> tuple:
    """把回复里的 `[表情:开心]` 换成真的表情。

    返回 `(洗干净的正文, [表情文件绝对路径])`。
    找不到对应表情时**只删标记**，不留一句「没有这个表情」——
    那是给人看的话，混在角色的话里会露馅。
    """
    s = str(text or "")
    if not s:
        return s, []
    found = []

    def _sub(m):
        item = pick(state_dir, cid, m.group(1))
        if item is None:
            return ""
        found.append(pack_dir(state_dir, cid) / item["file"])
        return ""

    out = _MARK.sub(_sub, s)
    if found:
        out = re.sub(r"[ \t]{2,}", " ", out)
        out = re.sub(r"\n{3,}", "\n\n", out).strip()
    return out, found


def has_marks(text: str) -> bool:
    return bool(_MARK.search(str(text or "")))


def names_for_hint(state_dir: Path | str, cid: str, limit: int = 12) -> str:
    """给模型看的「你有哪些表情」提示。空包返回空串（别让提示里出现空目录）。"""
    items = list_pack(state_dir, cid)
    if not items:
        return ""
    nm = [x["name"] for x in items][:limit]
    return "【表情】可用的表情：" + "、".join(nm) + "；想发的时候在回复里写 [表情:名字]，会变成一张图。"
