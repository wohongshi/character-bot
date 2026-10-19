#!/usr/bin/env python3
"""对话导出成网页（第二轮）：一个文件、双击就能看、图片跟着进去。

判据：
- 没有 JS、没有外链（断网也能看，转发也不会掉图）；
- 图片内联成 data URI，超出预算的留占位而不是把文件撑爆；
- 消息里的 `<script>` 必须被转义（聊天记录是不可信输入）；
- 日期分隔按天切，气泡左右分得对。
"""
from __future__ import annotations

import json
import struct
import sys
import tempfile
import urllib.error
import urllib.request
import zlib
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PASS, FAIL = [], []


def check(name, ok, extra=""):
    (PASS if ok else FAIL).append(name)
    print(f"  {'✓' if ok else '✗'} {name}" + (f"   [{extra}]" if extra and not ok else ""))


def section(t):
    print(f"\n== {t}")


def png(rgb=(200, 30, 30), n=8) -> bytes:
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + bytes(rgb) * n for _ in range(n))
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", n, n, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def main():
    import server

    tmp = Path(tempfile.mkdtemp())
    img_dir = tmp / "stickers" / "klee"
    img_dir.mkdir(parents=True)
    (img_dir / "开心.png").write_bytes(png())

    rows = [
        {"ts": 1792400000, "session": "s1", "character": "klee", "role": "user", "text": "第一天的问候"},
        {"ts": 1792400060, "session": "s1", "character": "klee", "role": "assistant", "text": "收到啦"},
        {"ts": 1792400120 + 86400, "session": "s1", "character": "klee", "role": "user",
         "text": "<script>alert(1)</script> 第二天"},
        {"ts": 1792400200 + 86400, "session": "s1", "character": "klee", "role": "assistant",
         "kind": "image", "img": "stickers/klee/开心.png", "caption": "给你"},
        {"ts": 1792400300 + 86400, "session": "s1", "character": "klee", "role": "assistant",
         "text": "出错的那条", "error": "RuntimeError: boom"},
        {"ts": 1792400400, "session": "s2", "character": "klee", "role": "user", "text": "别的会话"},
    ]
    chat = tmp / "chat.jsonl"
    chat.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")

    real = (server.CHAT_JSONL, server.IMG_ROOTS, server._char_store)
    try:
        server.CHAT_JSONL = chat
        server.IMG_ROOTS = (tmp,) + tuple(server.IMG_ROOTS)
        server._char_store = lambda: SimpleNamespace(get=lambda cid: {
            "name": "可莉", "emoji": "💥", "color": "#e4573d"}, defs={"klee": {}})
        doc = server._export_html("s1", "klee").decode("utf-8")

        section("1. 自包含")
        check("1.1 是完整 HTML", doc.startswith("<!doctype html>") and "</html>" in doc)
        check("1.2 没有 JS（静态页最不容易坏）", "<script" not in doc.lower())
        check("1.3 没有外链（断网也能看）",
              "http://" not in doc and "https://" not in doc.replace("http://www.w3.org", ""))
        check("1.4 图片内联成 data URI", 'src="data:image/png;base64,' in doc)

        section("2. 内容")
        check("2.1 角色名在标题里", "可莉" in doc)
        check("2.2 只有这一段会话的消息", "第一天的问候" in doc and "别的会话" not in doc)
        check("2.3 按天分隔（两个日期头）", doc.count('class="day"') == 2, str(doc.count('class="day"')))
        check("2.4 我问/她答分左右", 'class="m me"' in doc and 'class="m her"' in doc)
        check("2.5 图片说明", "给你" in doc)
        check("2.6 错误那行标出来了", "这条出错了" in doc and "boom" in doc)
        check("2.7 统计里有条数", "6 条" in doc or "5 条" in doc)

        section("3. 转义（聊天记录是不可信输入）")
        check("3.1 <script> 被转义", "&lt;script&gt;alert(1)&lt;/script&gt;" in doc)
        check("3.2 没有留下活的标签", "<script>alert(1)" not in doc)

        section("4. 预算")
        real_max = server.EXPORT_IMG_TOTAL
        try:
            server.EXPORT_IMG_TOTAL = 0
            doc2 = server._export_html("s1", "klee").decode("utf-8")
            check("4.1 预算用完就不再内联", "data:image/png" not in doc2)
            check("4.2 并且说明了几张没内联", "未内联" in doc2)
        finally:
            server.EXPORT_IMG_TOTAL = real_max

        section("5. 空会话")
        doc3 = server._export_html("nope", "klee").decode("utf-8")
        check("5.1 空会话给出人话", "还没有消息" in doc3 and 'class="m ' not in doc3)

        section("6. 打印样式")
        check("6.1 有 print 规则", "@media print" in doc)
        check("6.2 跟随系统深浅色", "prefers-color-scheme:dark" in doc)
    finally:
        server.CHAT_JSONL, server.IMG_ROOTS, server._char_store = real

    section("7. 接口（活服务）")
    base = "http://127.0.0.1:8798"
    hdr = {"X-Klee-Studio": "1"}
    try:
        req = urllib.request.Request(
            base + "/api/chat/export?session=studio-main&character=atri&fmt=html", headers=hdr)
        with urllib.request.urlopen(req, timeout=30) as r:
            body = r.read().decode("utf-8", "replace")
            check("7.1 200 + text/html", r.status == 200 and "text/html" in (r.headers.get("Content-Type") or ""))
            cd = r.headers.get("Content-Disposition") or ""
            check("7.2 文件名是 .html", '.html"' in cd, cd)
        check("7.3 有消息", 'class="m ' in body)
        req2 = urllib.request.Request(
            base + "/api/chat/export?session=studio-main&character=atri", headers=hdr)
        with urllib.request.urlopen(req2, timeout=30) as r2:
            md = r2.read().decode("utf-8", "replace")
        check("7.4 不传 fmt 还是 markdown", md.startswith("# ") and "<!doctype" not in md.lower())
    except (urllib.error.URLError, OSError) as e:
        print(f"  · 服务没起来，跳过（{type(e).__name__}）")

    print(f"\nPASS {len(PASS)} / {len(PASS) + len(FAIL)}")
    if FAIL:
        print("失败：" + "、".join(FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
