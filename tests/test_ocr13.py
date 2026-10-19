#!/usr/bin/env python3
"""ocr 第 13 轮（自 9c972f5 起 4 个提交）的回归。

每一条都对应评审里报的一处「不报错但不对」，判据是行为，不是「代码改成这样了」。
"""
from __future__ import annotations

import base64
import io
import json
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PASS, FAIL = [], []


def check(name, ok, extra=""):
    (PASS if ok else FAIL).append(name)
    print(f"  {'✓' if ok else '✗'} {name}" + (f"   [{extra}]" if extra and not ok else ""))


BASE = "http://127.0.0.1:8798"
H = {"X-Klee-Studio": "1"}


def get_raw(path, headers=None):
    req = urllib.request.Request(BASE + path, headers=H if headers is None else headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, r.headers, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers, e.read()


def post(path, body, timeout=120):
    req = urllib.request.Request(
        BASE + path, data=json.dumps(body).encode(),
        headers={**H, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8", "replace") or "{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8", "replace") or "{}")
        except ValueError:
            return e.code, {}


class _BrokenStream:
    """先吐几行正常 SSE，然后连接断了。"""

    def __init__(self, lines):
        self.lines = list(lines)

    def __iter__(self):
        for ln in self.lines:
            yield ln
        raise OSError("connection reset by peer")


def main() -> int:
    import server as S
    from bot import llm as L
    from bot import memory as M

    print("[1] 流式：断流 / 流内报错都要退回非流式")
    got = L._read_sse(_BrokenStream([
        b'data: {"choices":[{"delta":{"content":"\xe4\xbd\xa0"}}]}\n',
        b'data: {"choices":[{"delta":{"content":"\xe5\xa5\xbd"}}]}\n',
    ]))
    check("1.1 中途断流返回 None（不再把半截当成功）", got is None, repr(got)[:120])
    got = L._read_sse(_BrokenStream([
        b'data: {"choices":[{"delta":{"content":"hi"}}]}\n',
        b'data: {"error":{"message":"rate limited"}}\n',
        b'data: [DONE]\n',
    ]))
    check("1.2 流里的 error 事件返回 None（原因交给非流式路径抛）", got is None, repr(got)[:120])
    got = L._read_sse(_BrokenStream([
        b'data: {"choices":[{"delta":{"content":"hello"}}]}\n',
        b'data: [DONE]\n',
    ]))
    check("1.3 正常流仍然返回正文", isinstance(got, dict) and got["text"] == "hello", repr(got)[:120])

    print("\n[2] 记忆：写失败要说、作用域要隔离")
    td = Path(tempfile.mkdtemp(prefix="klee-ocr13-"))
    M.add(td, "demo", "主人喜欢草莓")
    check("2.1 正常写返回条目", bool(M.add(td, "demo", "主人怕黑")), "")
    # 把文件位置占成目录 → 写必然失败（root 下 chmod 挡不住，这招才有效）
    blocked = M.path_for(td, "blocked")
    blocked.mkdir(parents=True, exist_ok=True)
    check("2.2 写盘失败时 add 返回 None（不再谎报成功）",
          M.add(td, "blocked", "这条写不进去") is None, "")
    M.add(td, "scope", "全局的笔记", scope="")
    M.add(td, "scope", "群里的秘密", scope="group:123")
    M.add(td, "scope", "私聊的话", scope="private:9")
    seen = [e["text"] for e in M.pick(td, "scope", "", scope="group:123")]
    check("2.3 群里只看得到「全局 + 本群」", "群里的秘密" in seen and "私聊的话" not in seen, str(seen))
    seen = [e["text"] for e in M.pick(td, "scope", "", scope="private:9")]
    check("2.4 私聊里看不到群里记的", "群里的秘密" not in seen and "私聊的话" in seen, str(seen))
    check("2.5 面板列表不按作用域过滤（全都看得到）",
          len(M.pick(td, "scope", "")) == 3, str(len(M.pick(td, "scope", ""))))
    txt = M.render(td, "demo", "草莓")
    check("2.6 提示词里来源说人话（主人让你记的）", "主人让你记的" in txt, txt[:120])

    print("\n[3] 备份：不含密钥、收记忆")
    memfile = ROOT / "state" / "memories" / "_selftest.jsonl"
    memfile.parent.mkdir(parents=True, exist_ok=True)
    memfile.write_text(json.dumps({"ts": time.time(), "text": "备份自测", "source": "panel"},
                                  ensure_ascii=False) + "\n", encoding="utf-8")
    try:
        blob = S._build_backup()
        with zipfile.ZipFile(io.BytesIO(blob)) as z:
            names = z.namelist()
            cfg = json.loads(z.read("state/bot_config.json").decode("utf-8"))
        check("3.1 zip 能打开", len(names) > 3, str(len(names)))
        check("3.2 bot_config 里的 napcat_token 被抹掉",
              cfg.get("napcat_token") in ("***", "", None), repr(cfg.get("napcat_token"))[:40])
        check("3.3 max_tokens 这种「名字里带 token」的数值不能被误抹",
              isinstance(cfg.get("max_tokens"), int), repr(cfg.get("max_tokens")))
        check("3.4 长期记忆进了备份",
              "state/memories/_selftest.jsonl" in names, str([n for n in names if "memor" in n]))
        raw = z.raw if hasattr(z, "raw") else None
    finally:
        memfile.unlink(missing_ok=True)
    check("3.5 收尾：自测记忆文件已删", not memfile.exists())

    print("\n[4] 上传：认得出但不支持的格式要说人话")
    bmp = b"BM" + b"\x00" * 200
    data = "data:image/bmp;base64," + base64.b64encode(bmp).decode()
    try:
        S._save_upload(data)
        ok, msg = False, "没有抛异常"
    except ValueError as e:
        ok, msg = "不支持" in str(e), str(e)
    except Exception as e:                                    # noqa: BLE001
        ok, msg = False, f"{type(e).__name__}: {e}"
    check("4.1 BMP → ValueError（不是 KeyError/500）", ok, msg)

    print("\n[5] 面板接口的边界")
    st, _h, _b = get_raw("/api/memories?character=x", headers={})
    check("5.1 记忆列表要面板头（个人数据）", st in (401, 403), str(st))
    st, hdrs, _b = get_raw('/api/chat/export?session=a%22b%0d%0aX&character=klee')
    check("5.2 导出：怪字符的 session 不会撕响应头", st == 200 and "text/markdown" in str(hdrs.get("Content-Type")),
          str(st))

    print("\n[6] 导出：纯图消息也要导")
    cid = "klee"
    sess = "t-ocr13-%d" % int(time.time())
    rows = [
        {"ts": time.time(), "session": sess, "character": cid, "role": "user",
         "text": "", "images": ["uploads/abc123.png"], "id": "x1"},
        {"ts": time.time() + 1, "session": sess, "character": cid, "role": "assistant",
         "text": "看到你的图了"},
    ]
    for r in rows:
        S._append_jsonl(S.CHAT_JSONL, r)
    try:
        st, _h, body = get_raw(f"/api/chat/export?session={sess}&character={cid}")
        txt = body.decode("utf-8", "replace")
        check("6.1 纯图消息出现在导出里", "uploads/abc123.png" in txt, txt[:200])
        check("6.2 文字消息也在", "看到你的图了" in txt, "")
    finally:
        S._chat_rewrite(sess, cid, lambda e: False)
    left = [e for e in S._read_jsonl_all(S.CHAT_JSONL) if e.get("session") == sess]
    check("6.3 收尾：测试会话已清干净", not left, str(len(left)))

    print("\n[7] 试跑：异步插件要等它干完")
    tmp = Path(tempfile.mkdtemp(prefix="klee-drain-"))
    (tmp / "async_demo.py").write_text(
        "import asyncio\n"
        "\n"
        "NAME = \"异步演示\"\n"
        "DESCRIPTION = \"演示后台任务\"\n"
        "\n"
        "\n"
        "def register(api):\n"
        "    async def ad(ctx):\n"
        "        async def worker():\n"
        "            await asyncio.sleep(0.2)\n"
        "            await ctx.adapter.send(\"\", \"后台任务跑完了\")\n"
        "        asyncio.get_running_loop().create_task(worker())\n"
        "        return \"在跑啦\"\n"
        "\n"
        "    api.on_command(\"ad\", ad, help=\"演示后台任务\")\n", encoding="utf-8")
    try:
        from bot.framework import FeatureManager
        from bot.skills import SkillHub

        class A:
            features_dir = str(tmp)
            feature_prefix = "."
            owner_id = "1"
            active_character = "klee"

            def log(self, *a, **k):
                pass

        mgr = FeatureManager(A())
        mgr.load()
        hub = SkillHub(mgr)

        class Ad:
            def __init__(self):
                self.texts = []

            async def send(self, chat_id, text, **kw):
                self.texts.append(text)
                class R:
                    success = True
                return R()

        ad = Ad()
        res = hub.run("ad", "ad", adapter=ad, is_group=False, group_id="",
                      user_id="1", user_name="主人", role="owner", timeout=20,
                      chat={"adapter": ad, "drain": 5, "is_owner": True})
        joined = (str(res.get("text") or "") + "\n" + "\n".join(ad.texts))
        check("7.1 主返回值还在", "在跑啦" in str(res.get("text") or ""), str(res)[:160])
        check("7.2 后台任务真的跑完了（drain 生效）", "后台任务跑完了" in joined, joined[:160])

        ad2 = Ad()
        res2 = hub.run("ad", "ad", adapter=ad2, is_group=False, group_id="",
                       user_id="1", user_name="主人", role="owner", timeout=20,
                       chat={"adapter": ad2, "drain": 0})
        joined2 = (str(res2.get("text") or "") + "\n" + "\n".join(ad2.texts))
        check("7.3 不等（drain=0）时后台任务被丢掉 —— 对照组", "后台任务跑完了" not in joined2,
              joined2[:160])
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n[8] 清理")
    import shutil
    shutil.rmtree(td, ignore_errors=True)
    check("8.1 临时目录已删", not td.exists())

    print(f"\nPASS {len(PASS)} / {len(PASS) + len(FAIL)}")
    if FAIL:
        print("失败：?" + "、".join(FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
