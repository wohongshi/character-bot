#!/usr/bin/env python3
"""NapCat → 面板的事件鉴权与传输（2026-10-20 那次「QQ 发什么都不回」的回归）。

现场：面板显示 QQ 已登录、NapCat 也在推事件，但每一条都 403，三天一条都没进来。

根因有两条，叠在一起：
1. **NapCat 的 HTTP 上报不发 `Authorization`**。它发的是
   `x-self-id` + `x-signature: sha1=<HMAC_SHA1(body, token)>`
   （napcat.mjs 的 emitEventAsync 里写死的），我们只认 Bearer → 全拒。
2. **它用 chunked 传（没有 Content-Length）**。我们只读 Content-Length →
   body 永远空，而且 chunk 数据留在 socket 里，日志刷 `Bad request syntax ('21e')`。

所以这里判两件事：签名能过、分块体能读。
"""
from __future__ import annotations

import hashlib
import hmac
import http.client
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PASS, FAIL = [], []
BASE = "http://127.0.0.1:8798"


def check(name, ok, extra=""):
    (PASS if ok else FAIL).append(name)
    print(f"  {'✓' if ok else '✗'} {name}" + (f"   [{extra}]" if extra and not ok else ""))


def section(t):
    print(f"\n== {t}")


def token() -> str:
    return json.loads((ROOT / "state" / "bot_config.json").read_text(encoding="utf-8"))["napcat_token"]


def event(text="鉴权自检", mid=None) -> bytes:
    return json.dumps({
        "post_type": "message", "message_type": "private", "sub_type": "friend",
        "time": int(time.time()), "self_id": 3346295670,
        "message_id": mid or int(time.time() * 1000) % 10 ** 9,
        "user_id": 2100950993, "target_id": 3346295670,
        "raw_message": text, "font": 0,
        "sender": {"user_id": 2100950993, "nickname": "hongshi", "card": "", "role": "owner"},
        "message": [{"type": "text", "data": {"text": text}}],
    }, ensure_ascii=False).encode()


def post(body: bytes, headers=None, chunked=True, path="/onebot/event", host="127.0.0.1", port=8798):
    """完全照 NapCat 的发射方式：chunked + 自定义头。返回 (status, body)。"""
    c = http.client.HTTPConnection(host, port, timeout=15)
    c.putrequest("POST", path)
    c.putheader("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        c.putheader(k, v)
    if chunked:
        c.putheader("Transfer-Encoding", "chunked")
    else:
        c.putheader("Content-Length", str(len(body)))
    c.endheaders()
    if chunked:
        for i in range(0, len(body), 180):          # 故意切碎：验证多分块拼得回来
            part = body[i:i + 180]
            c.send(b"%x\r\n" % len(part) + part + b"\r\n")
        c.send(b"0\r\n\r\n")
    else:
        c.send(body)
    r = c.getresponse()
    return r.status, r.read()[:120].decode("utf-8", "replace")


def sig(body: bytes, algo="sha1", key=None) -> str:
    key = key if key is not None else token()
    return f"{algo}=" + hmac.new(key.encode(), body, getattr(hashlib, algo)).hexdigest()


def main():
    from bot.adapters.onebot import OneBotAdapter

    section("1. 适配器单元：三种鉴权都认")
    ad = OneBotAdapter({"napcat_http": "http://127.0.0.1:3101", "napcat_token": "tok123"})
    body = b'{"post_type":"message"}'
    good = hmac.new(b"tok123", body, hashlib.sha1).hexdigest()
    check("1.1 x-signature sha1 正确", ad._authorized({"X-Signature": f"sha1={good}"}, body))
    check("1.2 小写头名也认", ad._authorized({"x-signature": f"sha1={good}"}, body))
    check("1.3 sha256 也认",
          ad._authorized({"X-Signature": "sha256=" +
                          hmac.new(b"tok123", body, hashlib.sha256).hexdigest()}, body))
    check("1.4 签名错 → 拒", not ad._authorized({"X-Signature": "sha1=" + "0" * 40}, body))
    check("1.5 签名对但 body 被改 → 拒",
          not ad._authorized({"X-Signature": f"sha1={good}"}, body + b" "))
    check("1.6 Bearer 仍然认", ad._authorized({"Authorization": "Bearer tok123"}, body))
    check("1.7 裸 token 仍然认", ad._authorized({"Authorization": "tok123"}, body))
    check("1.8 access_token 查询参数",
          ad._authorized({}, body, {"access_token": ["tok123"]}))
    check("1.9 什么都没有 → 拒", not ad._authorized({}, body))
    check("1.10 空 token 的适配器一律拒",
          not OneBotAdapter({"napcat_http": "http://x", "napcat_token": ""})._authorized(
              {"X-Signature": f"sha1={good}"}, body))

    section("2. 真链路：chunked + x-signature（NapCat 的发法）")
    body = event("鉴权自检-a")
    code, text = post(body, {"x-self-id": "3346295670", "x-signature": sig(body)})
    check("2.1 正确签名 → 200", code == 200 and '"ok":true' in text.replace(" ", ""), f"{code} {text}")
    code, text = post(body, {"x-self-id": "3346295670", "x-signature": "sha1=" + "0" * 40})
    check("2.2 错签名 → 403", code == 403, f"{code} {text}")
    code, text = post(body, {"x-self-id": "3346295670"})
    check("2.3 不带签名 → 403", code == 403, f"{code} {text}")
    code, text = post(body, {"Authorization": "Bearer " + token()}, chunked=False)
    check("2.4 Bearer + Content-Length 仍然可用", code == 200, f"{code} {text}")

    section("3. chunked 解析（以前 body 读成空）")
    big = event("分块自检-" + "字" * 900)      # > 一个 chunk，确保真的走了多块
    code, text = post(big, {"x-signature": sig(big)})
    check("3.1 多分块的大 body 也能验签", code == 200, f"{code} {text}")
    # 用同一个连接连发两条：以前第二条会从半个 chunk 头开始解析
    c = http.client.HTTPConnection("127.0.0.1", 8798, timeout=15)
    ok = []
    for i in range(2):
        b = event(f"连发-{i}")
        c.putrequest("POST", "/onebot/event")
        c.putheader("Content-Type", "application/json")
        c.putheader("Transfer-Encoding", "chunked")
        c.putheader("x-signature", sig(b))
        c.endheaders()
        c.send(b"%x\r\n" % len(b) + b + b"\r\n0\r\n\r\n")
        r = c.getresponse()
        ok.append(r.status)
        r.read()
    check("3.2 同一连接连发两条都 200（不再错位）", ok == [200, 200], str(ok))

    section("4. 活服务：事件真的被处理")
    hdr = {"X-Klee-Studio": "1"}

    def stats():
        req = urllib.request.Request(BASE + "/api/bot", headers=hdr)
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())["stats"]

    before = stats()
    b = event("鉴权自检-e2e")
    code, _ = post(b, {"x-signature": sig(b)})
    handled = False
    for _ in range(40):
        time.sleep(1)
        now = stats()
        if now["events"] > before["events"]:
            handled = True
            break
    check("4.1 事件计数涨了（不是只有 200）", handled,
          f"{before['events']} → {stats()['events']}")

    section("5. 面板侧不再需要临时诊断代码")
    srv = (ROOT / "server.py").read_text(encoding="utf-8")
    ob = (ROOT / "bot" / "adapters" / "onebot.py").read_text(encoding="utf-8")
    check("5.1 没有「临时诊断」残留", "临时诊断" not in srv and "签名诊断" not in srv)
    check("5.2 有 _authorized", "def _authorized" in ob)
    check("5.3 注释里写清了为什么（X-Signature）", "X-Signature" in ob and "emitEventAsync" in ob)
    check("5.4 失败日志只记「带了什么」不记值",
          "authorization=%s x-signature=%s" in ob)

    print(f"\nPASS {len(PASS)} / {len(PASS) + len(FAIL)}")
    if FAIL:
        print("失败：" + "、".join(FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
