#!/usr/bin/env python3
"""NapCat → 面板的事件鉴权与传输（2026-10-20 那次「QQ 发什么都不回」的回归）。

现场：面板显示 QQ 已登录、NapCat 也在推事件，但每一条都 403，三天一条都没进来。

根因有两条，叠在一起：
1. **NapCat 的 HTTP 上报不发 `Authorization`**。它发的是
   `x-self-id` + `x-signature: sha1=<HMAC_SHA1(body, token)>`
   （napcat.mjs 的 emitEventAsync 里写死的），我们只认 Bearer → 全拒。
2. **它用 chunked 传（没有 Content-Length）**。我们只读 Content-Length →
   body 永远空，而且 chunk 数据留在 socket 里，日志刷 `Bad request syntax ('21e')`。

**这里发的都是 `meta_event`（心跳），不是消息**：鉴权和分块解析都走到了，
但运行时对非消息事件直接 return —— 所以跑这个套件**不会给真实 QQ 发任何消息**，
也不会往 `chat.jsonl` 写东西。以前用私聊消息测，等于每跑一次全量回归
就给主人 QQ 发几条「鉴权自检」。
"""
from __future__ import annotations

import hashlib
import hmac
import http.client
import io
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
BOT_UIN = 3346295670


def check(name, ok, extra=""):
    (PASS if ok else FAIL).append(name)
    print(f"  {'✓' if ok else '✗'} {name}" + (f"   [{extra}]" if extra and not ok else ""))


def section(t):
    print(f"\n== {t}")


def _cfg_token():
    """读面板实际配置的 token；读不到返回 None（仓库被 clone 到别处时不该炸）。"""
    try:
        cfg = json.loads((ROOT / "state" / "bot_config.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    tok = str(cfg.get("napcat_token") or "")
    return tok or None


def token():
    return _cfg_token()


def heartbeat(tag="自检") -> bytes:
    """NapCat 真的会发的心跳事件：走完鉴权+解析，但运行时直接 return（不回消息）。"""
    return json.dumps({
        "post_type": "meta_event", "meta_event_type": "heartbeat",
        "time": int(time.time()), "self_id": BOT_UIN, "interval": 30000,
        "status": {"app_enabled": True, "app_good": True, "online": True, "good": True,
                   "plugins_good": None, "stat": {"packet_received": 1, "note": tag}},
    }, ensure_ascii=False).encode()


def post(body: bytes, headers=None, chunked=True, path="/onebot/event"):
    """完全照 NapCat 的发射方式：chunked + 自定义头。返回 (status, body)。"""
    c = http.client.HTTPConnection("127.0.0.1", 8798, timeout=15)
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


def _fake_handler(raw: bytes):
    """造一个只带 rfile 的 Handler，用来单测 `_read_chunked`（不碰 socket）。"""
    import server

    h = object.__new__(server.Handler)
    h.rfile = io.BytesIO(raw)
    h.close_connection = False
    h._body_too_big = False
    h._body_malformed = False
    return h


def main():
    from bot.adapters.onebot import OneBotAdapter

    section("1. 适配器单元：四种鉴权都认")
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
    check("1.8 access_token（列表形状）",
          ad._authorized({}, body, {"access_token": ["tok123"]}))
    # server 传进来的是 parse_qs(u.query) = {k: str}，不是 {k: [str]}。
    # 以前按列表取 [0]，字符串会被取首字母 → 这条通道永远不成立。
    check("1.9 access_token（字符串形状，server 的实际形状）",
          ad._authorized({}, body, {"access_token": "tok123"}))
    check("1.10 access_token 错 → 拒", not ad._authorized({}, body, {"access_token": "tok12"}))
    check("1.11 什么都没有 → 拒", not ad._authorized({}, body))
    check("1.12 空 token 的适配器一律拒",
          not OneBotAdapter({"napcat_http": "http://x", "napcat_token": ""})._authorized(
              {"X-Signature": f"sha1={good}"}, body))

    section("2. chunked 读取的四个越权边界（未鉴权就能打到）")
    # 正常：两块拼回来
    h = _fake_handler(b"5\r\nhello\r\n6\r\n world\r\n0\r\n\r\n")
    check("2.1 多块拼接", h._read_chunked(1024) == b"hello world")
    # 负数块长：int(b"-1", 16) == -1 → read(-1) 一直读到 EOF，max_bytes 被绕过
    h = _fake_handler(b"-1\r\n" + b"A" * 4096)
    out = h._read_chunked(64)
    check("2.2 负数块长 → 判非法且不吞数据", h._body_malformed and h.close_connection and out == b"")
    # 超限：不排干、直接断开（以前继续读 = 想发多久发多久）
    h = _fake_handler(b"10\r\n" + b"B" * 16 + b"\r\n" + b"10\r\n" + b"C" * 16 + b"\r\n0\r\n\r\n")
    h._read_chunked(8)
    left = h.rfile.read()
    check("2.3 超限 → too_big + 断开 + 剩余不读",
          h._body_too_big and h.close_connection and len(left) > 0 and b"C" * 16 in left)
    # 带长 chunk-extension 的 size 行被截断：不能靠 int() 失败兜底
    h = _fake_handler(b"1;" + b"x" * 5000 + b"\r\nZ\r\n0\r\n\r\n")
    h._read_chunked(1024)
    check("2.4 超长 size 行 → 判非法", h._body_malformed and h.close_connection)
    # 坏 size 行
    h = _fake_handler(b"zz\r\nabc\r\n0\r\n\r\n")
    h._read_chunked(1024)
    check("2.5 非十六进制 size → 判非法", h._body_malformed and h.close_connection)
    # 声明 16 字节只给了 4 字节
    h = _fake_handler(b"10\r\nabcd")
    h._read_chunked(1024)
    check("2.6 半路断开 → 判非法", h._body_malformed and h.close_connection)

    section("3. 真链路：chunked + x-signature（NapCat 的发法）")
    if not token():
        print("  跳过：读不到 state/bot_config.json 里的 napcat_token")
    else:
        body = heartbeat("鉴权自检-a")
        code, text = post(body, {"x-self-id": str(BOT_UIN), "x-signature": sig(body)})
        check("3.1 正确签名 → 200", code == 200 and '"ok":true' in text.replace(" ", ""),
              f"{code} {text}")
        code, text = post(body, {"x-self-id": str(BOT_UIN), "x-signature": "sha1=" + "0" * 40})
        check("3.2 错签名 → 403", code == 403, f"{code} {text}")
        code, text = post(body, {"x-self-id": str(BOT_UIN)})
        check("3.3 不带签名 → 403", code == 403, f"{code} {text}")
        code, text = post(body, {"Authorization": "Bearer " + token()}, chunked=False)
        check("3.4 Bearer + Content-Length 仍然可用", code == 200, f"{code} {text}")

        section("4. 分块体（以前 body 读成空）")
        big = heartbeat("分块自检-" + "字" * 900)      # > 一个 chunk，确保多块
        code, text = post(big, {"x-signature": sig(big)})
        check("4.1 多分块的大 body 也能验签", code == 200, f"{code} {text}")
        c = http.client.HTTPConnection("127.0.0.1", 8798, timeout=15)
        codes = []
        for i in range(2):
            b = heartbeat(f"连发-{i}")
            c.putrequest("POST", "/onebot/event")
            c.putheader("Content-Type", "application/json")
            c.putheader("Transfer-Encoding", "chunked")
            c.putheader("x-signature", sig(b))
            c.endheaders()
            c.send(b"%x\r\n" % len(b) + b + b"\r\n0\r\n\r\n")
            r = c.getresponse()
            codes.append(r.status)
            r.read()
        check("4.2 同一连接连发两条都 200（不再错位）", codes == [200, 200], str(codes))

        section("5. 活服务：事件真的被接收（不是只有 200）")
        hdr = {"X-Klee-Studio": "1"}

        def stats():
            req = urllib.request.Request(BASE + "/api/bot", headers=hdr)
            with urllib.request.urlopen(req, timeout=15) as r:
                return json.loads(r.read())["stats"]

        try:
            before = stats()
        except (urllib.error.URLError, OSError) as e:
            print(f"  跳过：面板 /api/bot 不可达（{e}）")
            before = None
        if before is not None:
            chat_before = _chat_sig()
            b = heartbeat("接收自检")
            code, _ = post(b, {"x-signature": sig(b)})
            got = None
            for _ in range(20):
                now = stats()
                if int(now.get("ingress_ok:onebot11") or 0) > int(before.get("ingress_ok:onebot11") or 0):
                    got = now
                    break
                time.sleep(0.5)
            check("5.1 入站计数涨了（事件真的进了运行时）", got is not None,
                  f"前 {before.get('ingress_ok:onebot11')}")
            after = stats()
            # 3.1/3.2/3.3 在这里之前打过两条错签名 → 连续被拒数涨过；
            # 5.1 那条合法心跳必须把它清回 0（否则面板会永远挂着红字假警报）
            check("5.2 合法事件把「连续被拒」清零",
                  int(after.get("auth_streak:onebot11") or 0) == 0,
                  str(after.get("auth_streak:onebot11")))
            check("5.3 心跳事件没有写进对话历史", _chat_sig() == chat_before)

    section("6. 面板侧不再需要临时诊断代码")
    srv = (ROOT / "server.py").read_text(encoding="utf-8")
    ob = (ROOT / "bot" / "adapters" / "onebot.py").read_text(encoding="utf-8")
    check("6.1 没有「临时诊断」残留", "临时诊断" not in srv and "签名诊断" not in srv)
    check("6.2 有 _authorized", "def _authorized" in ob)
    check("6.3 注释里写清了为什么（X-Signature）",
          "X-Signature" in ob and "emitEventAsync" in ob)
    check("6.4 失败日志只记「带了什么」不记值",
          "authorization=%s x-signature=%s" in ob)
    check("6.5 插件页只念本平台的连续被拒数",
          'auth_streak:onebot11' in ob and "d.stats.auth_reject" not in ob)
    check("6.6 分块读取对负数块长有守卫", "size < 0" in srv)
    check("6.7 _body_malformed 不是死代码", srv.count("_body_malformed") >= 4)

    print(f"\nPASS {len(PASS)} / {len(PASS) + len(FAIL)}")
    if FAIL:
        print("失败：" + "、".join(FAIL))
    return 1 if FAIL else 0


def _chat_sig():
    """chat.jsonl 的「指纹」：心跳事件不该让它变。"""
    p = ROOT / "state" / "chat.jsonl"
    try:
        st = p.stat()
        return (st.st_size, round(st.st_mtime, 3))
    except OSError:
        return (0, 0.0)


if __name__ == "__main__":
    sys.exit(main())
