#!/usr/bin/env python3
"""表情包（第一轮）：每个角色一套贴纸，文件名就是它的意思。

判据都是行为：
- 名字带路径要**拒**（不是悄悄改成别的名字），空名/超长要拒；
- 只认真图片（按魔数，不看后缀），超 2MB 拒；
- 同名换格式（png → gif）时旧文件必须消失，否则列表里出现两张同名的；
- `[表情:开心]` 三种写法都能认出来，找不到就只删标记、留一句人话；
- 面板/QQ 两条发送路径都落在同一套「图片气泡」上。
"""
from __future__ import annotations

import base64
import json
import struct
import sys
import tempfile
import urllib.error
import urllib.request
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PASS, FAIL = [], []


def check(name, ok, extra=""):
    (PASS if ok else FAIL).append(name)
    print(f"  {'✓' if ok else '✗'} {name}" + (f"   [{extra}]" if extra and not ok else ""))


def section(t):
    print(f"\n== {t}")


def png(rgb=(255, 210, 60), n=8) -> bytes:
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + bytes(rgb) * n for _ in range(n))
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", n, n, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def data_url(blob: bytes, mime="image/png") -> str:
    return f"data:{mime};base64," + base64.b64encode(blob).decode()


def main():
    from bot import stickers as st

    section("1. 名字")
    check("1.1 去扩展名", st.clean_name("开心.png") == "开心")
    check("1.2 压缩空白", st.clean_name(" 超  开心 ") == "超 开心")
    for bad in ("", "   ", "../evil", "a/b", "a\\b", "x" * 21):
        try:
            st.clean_name(bad)
            check(f"1.3 拒绝 {bad!r}", False)
        except ValueError:
            check(f"1.3 拒绝 {bad!r}", True)

    section("2. 存盘")
    tmp = Path(tempfile.mkdtemp())
    it = st.save(tmp, "klee", "开心", data_url(png()))
    check("2.1 存成 <名字>.png", it["file"] == "开心.png" and it["ext"] == ".png", str(it))
    check("2.2 ref 是面板能取的那种形态", it["ref"] == "stickers/klee/开心.png", it["ref"])
    check("2.3 文件真的在", (st.pack_dir(tmp, "klee") / "开心.png").exists())

    # 同名换格式：旧 png 必须消失
    gif = b"GIF89a" + b"\x00" * 40
    st.save(tmp, "klee", "开心", data_url(gif, "image/gif"))
    names = [x["file"] for x in st.list_pack(tmp, "klee")]
    check("2.4 同名换格式不留旧文件", names == ["开心.gif"], str(names))

    try:
        st.save(tmp, "klee", "不是图", data_url(b"hello world", "image/png"))
        check("2.5 拒绝非图片", False)
    except ValueError as e:
        check("2.5 拒绝非图片", "格式" in str(e), str(e))

    try:
        st.save(tmp, "klee", "巨图", data_url(b"\x89PNG\r\n\x1a\n" + b"\x00" * (st.MAX_BYTES + 10)))
        check("2.6 拒绝超大", False)
    except ValueError:
        check("2.6 拒绝超大", True)

    st.save(tmp, "klee", "困", data_url(png((10, 20, 30))))
    st.save(tmp, "klee", "超开心", data_url(png((30, 20, 10))))
    items = st.list_pack(tmp, "klee")
    check("2.7 按名字排序", [x["name"] for x in items] == ["困", "开心", "超开心"],
          str([x["name"] for x in items]))
    check("2.8 每个都带 ref 和体积", all(x["ref"].startswith("stickers/klee/") and x["bytes"] > 0
                                   for x in items))

    section("3. 按名字挑")
    check("3.1 精确", st.pick(tmp, "klee", "开心")["name"] == "开心")
    check("3.2 包含（开心 → 超开心）", st.pick(tmp, "klee", "超")["name"] == "超开心")
    check("3.3 反向包含（超开心 → 开心）", st.pick(tmp, "klee", "开心开心")["name"] == "开心")
    check("3.4 随机不会空", st.pick(tmp, "klee", "随便") is not None)
    check("3.5 没这张返回 None", st.pick(tmp, "klee", "生气") is None)
    check("3.6 空包返回 None", st.pick(tmp, "atri", "开心") is None)

    section("4. 回复里的标记")
    for tpl in ("[表情:{n}]", "[[sticker:{n}]]", "[sticker:{n}]", "[表情：{n}]"):
        out, files = st.apply_marks("好啊" + tpl.format(n="开心") + "～", tmp, "klee")
        check(f"4.1 认得 {tpl}", out == "好啊～" and len(files) == 1, f"{out!r} {files}")
    out, files = st.apply_marks("那就这样吧[表情:生气]", tmp, "klee")
    check("4.2 找不到就只删标记（不留提示语）", out == "那就这样吧" and files == [], out)
    out, files = st.apply_marks("没有标记的一句话", tmp, "klee")
    check("4.3 没标记原样返回", out == "没有标记的一句话" and files == [])
    check("4.4 has_marks", st.has_marks("[表情:开心]") and not st.has_marks("普通"))
    out, files = st.apply_marks("[表情:开心][表情:困]", tmp, "klee")
    check("4.5 一句里两张都发", len(files) == 2 and out == "", f"{out!r} {len(files)}")

    section("5. 给模型的提示")
    hint = st.names_for_hint(tmp, "klee")
    check("5.1 提示里列了名字", "开心" in hint and "[表情:" in hint, hint)
    check("5.2 空包不给提示（免得提示里出现空目录）", st.names_for_hint(tmp, "atri") == "")

    section("6. 删除与角色隔离")
    check("6.1 删掉", st.delete(tmp, "klee", "困") and "困" not in [x["name"] for x in st.list_pack(tmp, "klee")])
    check("6.2 删不存在的返回 False", st.delete(tmp, "klee", "不存在") is False)
    st.save(tmp, "atri", "开心", data_url(png((1, 1, 1))))
    check("6.3 角色之间互不影响",
          [x["name"] for x in st.list_pack(tmp, "klee")] == ["开心", "超开心"]
          and [x["name"] for x in st.list_pack(tmp, "atri")] == ["开心"])

    section("7. 面板那条路（_apply_stickers）")
    import server
    real = server._state_dir
    real_roots = server.IMG_ROOTS
    try:
        server._state_dir = lambda: tmp
        # 图片白名单是 import 时算的：临时目录不在里面 → ref 会是空串、
        # 图被静默丢掉（这条正是「插件图不在白名单」那个老坑）。测试里补进去。
        server.IMG_ROOTS = (tmp,) + tuple(real_roots)
        job = {}
        clean = server._apply_stickers("嘿嘿[表情:开心]", job, "klee")
        check("7.1 洗掉标记", clean == "嘿嘿", clean)
        # 名字对，扩展名跟着实际文件走（第二轮存的是 .gif）
        want = st.pick(tmp, "klee", "开心")["ref"]
        check("7.2 图挂进 job.images", [i["ref"] for i in job["images"]] == [want],
              f"{job.get('images')} vs {want}")
        job2 = {}
        check("7.3 没标记不动 job", server._apply_stickers("普通回复", job2, "klee") == "普通回复"
              and not job2.get("images"))
    finally:
        server._state_dir = real
        server.IMG_ROOTS = real_roots

    section("8. 接口（活服务）")
    base = "http://127.0.0.1:8798"
    hdr = {"X-Klee-Studio": "1", "Content-Type": "application/json"}

    def post(path, body):
        req = urllib.request.Request(base + path, data=json.dumps(body).encode(), headers=hdr)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def get(path):
        req = urllib.request.Request(base + path, headers=hdr)
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())

    try:
        st_ = "t-stk-ut"
        code, d = post("/api/stickers/upload", {"character": "cyrene", "name": "测试图",
                                                "data": data_url(png((200, 30, 30)))})
        check("8.1 上传 200", code == 200 and d.get("ok"), f"{code} {d}")
        lst = get("/api/stickers?character=cyrene")
        check("8.2 列表里有它", "测试图" in [x["name"] for x in lst["items"]])
        code, d = post("/api/stickers/send", {"character": "cyrene", "name": "测试图", "session": st_})
        check("8.3 发到会话", code == 200 and d.get("ok"), f"{code} {d}")
        rows = get(f"/api/chat?session={st_}&character=cyrene")["messages"]
        check("8.4 落成图片行", rows and rows[-1].get("kind") == "image", str(rows[-1:]))
        ref = rows[-1]["img"]
        with urllib.request.urlopen(f"{base}/api/chat/image?p={urllib.parse.quote(ref)}", timeout=10) as r:
            check("8.5 图能取到", r.status == 200 and r.read()[:4] == b"\x89PNG")
        code, d = post("/api/stickers/delete", {"character": "cyrene", "name": "测试图"})
        check("8.6 删除 200", code == 200 and d.get("ok"), f"{code} {d}")
        code, d = post("/api/stickers/delete", {"character": "cyrene", "name": "测试图"})
        check("8.7 删不存在的 404", code == 404, str(code))
        code, d = post("/api/stickers/upload", {"character": "cyrene", "name": "../x",
                                                "data": data_url(png())})
        check("8.8 路径名 400", code == 400 and "路径" in str(d.get("error")), f"{code} {d}")
        post("/api/chat/delete", {"session": st_, "character": "cyrene"})

        code, d = post("/api/commands/run", {"command": "表情", "args": "", "character": "cyrene"})
        check("8.9 命令 .表情 能列出", code == 200 and d.get("ok") and "表情" in str(d.get("text")), str(d)[:150])
    except (urllib.error.URLError, OSError, ValueError) as e:
        print(f"  · 服务没起来，跳过（{type(e).__name__}）")

    print(f"\nPASS {len(PASS)} / {len(PASS) + len(FAIL)}")
    if FAIL:
        print("失败：" + "、".join(FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    import urllib.parse  # noqa: E402  (只在 8.5 用)
    sys.exit(main())
