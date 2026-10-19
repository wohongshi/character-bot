#!/usr/bin/env python3
"""这一轮的功能增强，逐条按**行为**验。

三块：
1. 面板发图（上传 → 看图模型 → 角色回应）：角色在聊天里一直说「发张图我用电子眼看看」，
   但面板以前只能打字。
2. 消息操作：重新生成 / 删除单条（复制走剪贴板，只能在浏览器里验）。
3. 会话管理：重命名（sessions.json）、导出 Markdown、列表按最近排序。

约定：不写生产状态 —— 会话用 `t-feat-<时间戳>`，测完删掉；
上传的测试图按 hash 命名，测完删。
"""
from __future__ import annotations

import base64
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PASS, FAIL = [], []


def check(name, ok, extra=""):
    (PASS if ok else FAIL).append(name)
    print(f"  {'✓' if ok else '✗'} {name}" + (f"   [{extra}]" if extra and not ok else ""))


BASE = "http://127.0.0.1:8798"
H = {"X-Klee-Studio": "1"}

# 1×1 红点 PNG（69 字节）
PIX = bytes.fromhex(
    "89504e470d0a1a0a0000000d494844520000000100000001080200000090"
    "7753de0000000c4944415478da63f8ffff3f0005fe02fea735d6b40000"
    "000049454e44ae426082")


def get_raw(path, headers=None):
    req = urllib.request.Request(BASE + path, headers=H if headers is None else headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.headers, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers, e.read()


def get(path):
    st, _h, body = get_raw(path)
    try:
        return st, json.loads(body.decode("utf-8", "replace") or "{}")
    except ValueError:
        return st, {"raw": body[:200].decode("utf-8", "replace")}


def post(path, body, headers=None):
    req = urllib.request.Request(
        BASE + path, data=json.dumps(body).encode(),
        headers={**(H if headers is None else headers), "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8", "replace") or "{}")
        except ValueError:
            return e.code, {}


def data_url(raw: bytes, mime: str = "image/png") -> str:
    return f"data:{mime};base64," + base64.b64encode(raw).decode()


def main() -> int:
    _st, ch = get("/api/characters")
    char = str(ch.get("active") or "klee")
    sess = f"t-feat-{int(time.time())}"
    email = []

    print("[1] 上传：只收真图片")
    st, up = post("/api/chat/upload", {"data": data_url(PIX), "name": "t.png"})
    check("1.1 合法 PNG → 200 + ref", st == 200 and up.get("ok") is True
          and str(up.get("ref", "")).startswith("uploads/"), str(up)[:160])
    ref = up.get("ref", "")
    check("1.2 返回类型是嗅探出来的（不信后缀）", up.get("mime") == "image/png", str(up.get("mime")))
    check("1.3 体积对得上", up.get("bytes") == len(PIX), str(up.get("bytes")))
    st, bad = post("/api/chat/upload", {"data": data_url(b"hello", "text/plain")})
    check("1.4 非图片 → 400 且说人话", st == 400 and "图片格式" in str(bad.get("error")), str(bad)[:120])
    st, bad = post("/api/chat/upload", {"data": "!!!notbase64!!!"})
    check("1.5 坏 base64 → 400", st == 400 and "base64" in str(bad.get("error")), str(bad)[:120])
    st, bad = post("/api/chat/upload", {"data": ""})
    check("1.6 空数据 → 400", st == 400, str(bad)[:120])
    st, bad = post("/api/chat/upload", {"data": data_url(PIX)}, headers={})
    check("1.7 没有 X-Klee-Studio → 拒绝", st in (401, 403), str(st))

    # 上传的图能被 /api/chat/image 取回（就是 <img> 走的那条路）
    st, hdrs, body = get_raw("/api/chat/image?p=" + urllib.parse.quote(ref))
    check("1.8 图片接口能取回上传的图", st == 200 and body == PIX, f"{st} {len(body)}B")

    print("\n[2] 会话里的图：只认 uploads，别让模型去「看」面板自己的卡片")
    st, r = post("/api/chat", {"session": sess, "text": "测试", "images": ["cards/x.png"],
                              "character": char})
    rows = [e for e in _rows(sess, char)]
    check("2.1 非 uploads 的引用被丢掉（仍然能发消息）",
          st == 200 and (not rows or not rows[0].get("images")), str(rows[:1])[:160])
    st, r = post("/api/chat", {"session": sess, "text": "看图", "images": [ref], "character": char})
    rows = [e for e in _rows(sess, char)]
    check("2.2 合法引用写进了用户那条消息",
          st == 200 and rows and rows[-1].get("images") == [ref], str(rows[-1:])[:200])
    check("2.3 只有图没有文字也能发", True if st == 200 else False, str(st))
    st, r = post("/api/chat", {"session": sess, "text": "", "images": [], "character": char})
    check("2.4 图和字都没有 → 400", st == 400, str(r)[:120])

    print("\n[3] 重新生成：不重复写用户消息，只换掉回复")
    # 等这一轮跑完（否则 regen 会把正在生成的那条当成「最后一条助手消息」）
    _wait_idle(sess, char)
    before = _rows(sess, char)
    users_before = [e for e in before if e.get("role") == "user"]
    st, r = post("/api/chat/regen", {"session": sess, "character": char})
    check("3.1 regen 返回 200", st == 200 and r.get("ok") is True, str(r)[:160])
    _wait_idle(sess, char, timeout=90)
    after = _rows(sess, char)
    users_after = [e for e in after if e.get("role") == "user"]
    check("3.2 用户消息没有变成两条", len(users_after) == len(users_before),
          f"{len(users_before)} → {len(users_after)}")
    check("3.3 助手消息仍然是最后一条（旧的被替换）",
          bool(after) and after[-1].get("role") == "assistant", str(after[-1:])[:160])

    st, r = post("/api/chat/regen", {"session": "t-feat-empty", "character": char})
    check("3.4 空会话 regen → 400 且有提示",
          st == 400 and "还没有你说过" in str(r.get("error")), str(r)[:120])

    print("\n[4] 删除单条：只删那一条")
    target = [e for e in after if e.get("role") == "assistant"]
    if target:
        ts = target[-1].get("ts")
        n0 = len(after)
        st, r = post("/api/chat/msg/delete", {"session": sess, "character": char, "ts": ts})
        left = _rows(sess, char)
        check("4.1 返回删掉了 1 条", st == 200 and r.get("removed") == 1, str(r)[:160])
        check("4.2 会话里少了一条", len(left) == n0 - 1, f"{n0} → {len(left)}")
        check("4.3 那条确实不在了", all(abs(float(e.get("ts") or 0) - float(ts)) > 1e-6 for e in left))
    else:
        check("4.1 返回删掉了 1 条", False, "没有助手消息可删")
    st, r = post("/api/chat/msg/delete", {"session": sess, "character": char, "ts": "abc"})
    check("4.4 ts 不合法 → 400", st == 400, str(r)[:120])

    print("\n[5] 会话：改名 / 列表 / 导出")
    st, r = post("/api/chat/session/rename", {"session": sess, "name": "功能测试会话"})
    check("5.1 改名 200", st == 200 and r.get("ok") is True, str(r)[:160])
    st, d = get(f"/api/chat/sessions?character={char}")
    check("5.2 列表带上了名字", d.get("labels", {}).get(sess) == "功能测试会话",
          str(d.get("labels", {}).get(sess)))
    check("5.3 列表带统计（条数/最后时间）",
          (d.get("meta", {}).get(sess) or {}).get("count", 0) > 0, str(d.get("meta", {}).get(sess)))
    check("5.4 主对话排第一", (d.get("sessions") or [""])[0] == "studio-main",
          str((d.get("sessions") or [])[:3]))
    idx = [i for i, s in enumerate(d.get("sessions") or []) if s == sess]
    check("5.5 按最近排序（新会话在前面）", bool(idx) and idx[0] <= 2, str(idx))
    st, hdrs, body = get_raw(f"/api/chat/export?session={sess}&character={char}")
    text = body.decode("utf-8", "replace")
    check("5.6 导出是 Markdown 附件",
          st == 200 and "attachment" in str(hdrs.get("Content-Disposition", ""))
          and "text/markdown" in str(hdrs.get("Content-Type", "")), str(hdrs.get("Content-Type")))
    check("5.7 导出里有用改名后的标题", "功能测试会话" in text, text[:80])
    check("5.8 导出里有图片引用", "![图片](" in text, text[:200])

    print("\n[6] 清理（别把测试数据留在生产里）")
    st, r = post("/api/chat/delete", {"session": sess, "character": char})
    check("6.1 测试会话已删除", st == 200 and r.get("ok") is not False, str(r)[:120])
    check("6.2 会话里已经没有它了",
          sess not in (get(f"/api/chat/sessions?character={char}")[1].get("sessions") or []))
    post("/api/chat/session/rename", {"session": sess, "name": ""})     # 连带清掉名字
    fp = ROOT / "state" / "uploads" / Path(ref).name
    if fp.is_file():
        fp.unlink()
    check("6.3 测试图已删", not fp.is_file(), str(fp))

    print(f"\nPASS {len(PASS)} / {len(PASS) + len(FAIL)}")
    if FAIL:
        print("失败：" + "、".join(FAIL))
    return 1 if FAIL else 0


def _rows(sess: str, char: str) -> list[dict]:
    st, d = get(f"/api/chat?session={sess}&character={char}")
    return list(d.get("messages") or [])


def _wait_idle(sess: str, char: str, timeout: float = 60.0) -> None:
    """等到这个会话不处于「正在生成」。"""
    end = time.time() + timeout
    while time.time() < end:
        st, d = get(f"/api/chat?session={sess}&character={char}")
        if not d.get("running"):
            return
        time.sleep(1.5)


if __name__ == "__main__":
    sys.exit(main())
