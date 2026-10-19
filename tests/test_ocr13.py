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
    check("2.7 不会拼成「记的记的」", "记的记的" not in txt, txt[:120])
    # 同一条在群里记过、又在另一个群记一次 → 两边都得看得到
    M.add(td, "merge", "同一条事", scope="group:A")
    M.add(td, "merge", "同一条事", scope="group:B")
    in_a = [e["text"] for e in M.pick(td, "merge", "", scope="group:A")]
    in_b = [e["text"] for e in M.pick(td, "merge", "", scope="group:B")]
    check("2.8 两个群记同一条：两边都能看到", "同一条事" in in_a and "同一条事" in in_b,
          f"A={in_a} B={in_b}")
    check("2.9 合并后仍然只有一条（不重复）", len(M.all_items(td, "merge")) == 1,
          str(len(M.all_items(td, "merge"))))
    # 面板再全局记一次 → 升级成全局
    M.add(td, "merge", "同一条事", scope="")
    in_c = [e["text"] for e in M.pick(td, "merge", "", scope="group:C")]
    check("2.10 面板再记一次 → 升级成全局（别的群也看得到）", "同一条事" in in_c, str(in_c))
    # 作用域很多时：按整条丢，不能截出半截 scope
    for i in range(6):
        M.add(td, "many", "一件大事", scope="web:会话-%d" % i)
    sc = str(M.all_items(td, "many")[0].get("scope") or "")
    check("2.12 作用域按整条丢，不留半截", all(len(x) > 0 and not x.endswith("会话-")
                                              for x in sc.split(",")), sc)
    check("2.13 最新加进去的作用域一定保得住", "web:会话-5" in sc.split(","), sc)
    # 写失败不能把「没落盘的 ts」留在缓存里
    blocked2 = M.path_for(td, "ghost")
    blocked2.mkdir(parents=True, exist_ok=True)
    check("2.11 写失败返回 None 且读回来还是空的", M.add(td, "ghost", "幽灵") is None
          and M.all_items(td, "ghost") == [])

    print("\n[2b] agent 的 remember：写不进去不能回「记住了」")
    from bot import agent as AG
    td2 = Path(tempfile.mkdtemp(prefix="klee-ocr13-rem-"))
    blocked = M.path_for(td2, "c1")
    blocked.mkdir(parents=True, exist_ok=True)          # 让记忆文件写不进去
    ctx = AG.ToolContext(state_dir=td2, character="c1", scope="group:1")
    msg = AG.tool_remember({"text": "记住这条"}, ctx)
    check("2b.1 写盘失败时不谎报「记住了」", "记住了" not in msg and "没写进磁盘" in msg, msg[:120])
    ctx2 = AG.ToolContext(state_dir=td2, character="", scope="group:1")
    msg2 = AG.tool_remember({"text": "没有角色的情况"}, ctx2)
    check("2b.2 没有角色（记忆无处可写）也不谎报", "记住了" not in msg2, msg2[:120])
    check("2b.3 原因说对（不是「磁盘满了」）",
          "没有关联角色" in msg2 and "磁盘" not in msg2, msg2[:120])
    import shutil
    shutil.rmtree(td2, ignore_errors=True)

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
        check("3.3c webhook_url 整值抹掉（飞书/钉钉/Bark 的 token 在路径里）",
              cfg.get("webhook_url") in ("***", "", None), repr(cfg.get("webhook_url"))[:60])
        check("3.3d client_secret 这类参数名也认",
              "q" not in str(S._scrub_secrets({"u": "https://a.com/x?client_secret=q&b=2"})),
              "")
        check("3.3f userinfo 里的密码也要抹（哪怕没有 query）",
              "pw" not in str(S._scrub_secrets({"u": "https://user:pw@host/x"})),
              str(S._scrub_secrets({"u": "https://user:pw@host/x"})))
        # ntfy 的路径本身就是 topic（https://ntfy.sh/<topic>），没法从形状上认出来；
        # 但配置里这类地址都在 webhook_url 下，那个键是整值抹掉的。
        # 这里只保证 query 形式的 topic 不残留。
        masked = str(S._scrub_secrets({"u": "https://ntfy.sh/x?topic=mytopic"}))
        check("3.3g ntfy 的 ?topic= 也算凭据", "topic=***" in masked and "mytopic" not in masked,
              masked)
        check("3.3e 没有 scheme 的 URL 也洗",
              "zzz" not in str(S._scrub_secrets({"u": "127.0.0.1:9000/hook?token=zzz"})), "")
        check("3.3b webhook_url 里的 access_token 也被抹掉",
              "abc123" not in str(cfg.get("webhook_url") or ""), repr(cfg.get("webhook_url"))[:60])
        check("3.4 长期记忆进了备份",
              "state/memories/_selftest.jsonl" in names, str([n for n in names if "memor" in n]))
        raw = z.raw if hasattr(z, "raw") else None
    finally:
        memfile.unlink(missing_ok=True)
    check("3.5 收尾：自测记忆文件已删", not memfile.exists())

    print("\n[4] 朗读/试跑的小口径")
    check("4.0a [表情:xx] 不念出来", "[表情" not in S._tts_clean("今天的可莉也活力满满！[表情:呲牙]"),
          repr(S._tts_clean("今天的可莉也很棒[表情:呲牙]")))
    check("4.0b 正常文字还在", "活力满满" in S._tts_clean("今天的可莉也活力满满！[表情:呲牙]"), "")
    broken = "坏掉的[表情: 后面还有话[链接]尾巴"
    check("4.0c 坏掉的 [表情: 标记不会吞掉后面的句子",
          S._tts_clean(broken) == broken, repr(S._tts_clean(broken)))

    print("\n[4b] 上传：认得出但不支持的格式要说人话")
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

        # join 的预算必须把 drain 算进去：timeout=1 + drain=5 也要等得到
        ad3 = Ad()
        res3 = hub.run("ad", "ad", adapter=ad3, is_group=False, group_id="",
                       user_id="1", user_name="主人", role="owner", timeout=1,
                       chat={"adapter": ad3, "drain": 5})
        joined3 = (str(res3.get("text") or "") + "\n" + "\n".join(ad3.texts))
        check("7.4 timeout=1 但 drain=5：后台任务仍然等到了",
              "后台任务跑完了" in joined3, joined3[:200])
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
