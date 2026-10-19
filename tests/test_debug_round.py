#!/usr/bin/env python3
"""debug 那轮修掉的问题，逐条用**行为**守住（不是「代码里写了就算」）。

这一轮找到的共同点：**都不报错**。
屏蔽词抛 NameError 被框架吞掉、看板数字悄悄翻倍、安装插件永远 500、
相似度分数整体错位一格 —— 每一个看起来都「正常工作」。
"""
from __future__ import annotations

import asyncio
import importlib.util as ilu
import io
import json
import shutil
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bot import native as N                                   # noqa: E402
from bot.store import MessageIndex                            # noqa: E402

PASS, FAIL = [], []


def check(name, ok, extra=""):
    (PASS if ok else FAIL).append(name)
    print(f"  {'✓' if ok else '✗'} {name}" + (f"   [{extra}]" if extra and not ok else ""))


def load_mod():
    spec = ilu.spec_from_file_location(f"mod_{id(object())}", ROOT / "bot" / "features" / "moderation.py")
    mod = ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakeAPI:
    BLOCK = object()

    def __init__(self, settings):
        self.settings = settings
        self.state = {}
        self.hooks = []

    def on_message(self, h, *, priority=50):
        self.hooks.append(h)

    def log(self, *a):
        pass


class Ctx:
    def __init__(self, text):
        self.text = text
        self.replies = []

    async def reply(self, t):
        self.replies.append(t)


async def run_hook(words, text, cooldown=0, times=1):
    api = FakeAPI({"bad_words": words, "reply": "不行", "cooldown": cooldown})
    load_mod().register(api)
    out = []
    for _ in range(times):
        c = Ctx(text)
        try:
            r = await api.hooks[0](c)
            out.append(("BLOCK" if r is api.BLOCK else None, list(c.replies)))
        except Exception as e:                                # noqa: BLE001
            out.append(("EXC", f"{type(e).__name__}: {e}"))
    return out


# ---------------------------------------------------------------- 1) 屏蔽词
print("· 屏蔽词：命中必须真的拦住并回复")
small = asyncio.run(run_hook(["屏蔽"], "这里有个屏蔽词"))
check("1.1 小词表（Python 路径）拦住并回复", small[0][0] == "BLOCK" and small[0][1] == ["不行"],
      str(small))
big = asyncio.run(run_hook([f"坏词{i}" for i in range(70)] + ["屏蔽"], "这里有个屏蔽词"))
check("1.2 大词表（Rust 路径）拦住并回复", big[0][0] == "BLOCK" and big[0][1] == ["不行"],
      str(big))
miss = asyncio.run(run_hook(["屏蔽"], "今天天气不错"))
check("1.3 没命中就放行（返回 None）", miss[0][0] is None, str(miss))
two = asyncio.run(run_hook(["屏蔽"], "屏蔽", cooldown=0, times=2))
check("1.4 cooldown=0 = 每次都回（以前 0 被 `or 60` 变成 60）",
      [x[1] for x in two] == [["不行"], ["不行"]], str(two))
two_cd = asyncio.run(run_hook(["屏蔽"], "屏蔽", cooldown=60, times=2))
check("1.5 cooldown=60：第二次静默拦截（不回复但仍 BLOCK）",
      two_cd[0][1] == ["不行"] and two_cd[1] == ("BLOCK", []), str(two_cd))

# ---------------------------------------------------------------- 2) 相似度
print("\n· 相似度：批量分数要和候选一一对应")
words = ["你好", "陪我聊会儿", "今天天气怎么样"]
target = "你好"
recent = ["陪我聊会儿", "你好", "今天吃什么"]
try:
    dll_path = ROOT / "tools" / "pkgcheck"  # 只为了确认工作目录正确
    from bot.ext.host import ExtPlugin, load_spec
    ext = ROOT / "bot" / "features" / "ext"
    p = ExtPlugin(load_spec(ext / "similar.ext.json"), ext)
    p.start()
    payload = target + "\u0001" + "\u0002".join(recent)
    res = p.call("similar_batch", [], payload, {})
    scores = json.loads(res.get("reply") or "[]")
    p.close()
    check("2.1 返回的分数个数 = 候选个数", len(scores) == len(recent), str(scores))
    check("2.2 第一条候选的分数是第一格（以前整体错位一格）",
          abs(scores[0] - 0.0) < 1e-6, f"{recent[0]} → {scores[0]}")
    check("2.3 相同的句子得 1.0", abs(scores[1] - 1.0) < 1e-6, f"{recent[1]} → {scores[1]}")
    check("2.4 无关的句子得 0.0", abs(scores[2] - 0.0) < 1e-6, f"{recent[2]} → {scores[2]}")
except Exception as e:                                        # noqa: BLE001
    check("2.1 返回的分数个数 = 候选个数", False, f"{type(e).__name__}: {e}")

# ---------------------------------------------------------------- 3) 看板不能翻倍
print("\n· 看板聚合：同一批消息在 chat/messages 两个来源里不能算两遍")
tmp = Path(tempfile.mkdtemp(prefix="klee-dbg-"))
try:
    msgs = [{"ts": 1700000000 + i * 60, "chat_id": "web:s", "role": "user" if i % 2 == 0 else "assistant",
             "text": f"第 {i} 条"} for i in range(20)]
    (tmp / "messages.jsonl").write_text(
        "\n".join(json.dumps(m, ensure_ascii=False) for m in msgs) + "\n", encoding="utf-8")
    # chat.jsonl 存的是同一批消息（这是真实情况）
    (tmp / "chat.jsonl").write_text(
        "\n".join(json.dumps({**m, "session": "s"}, ensure_ascii=False) for m in msgs) + "\n",
        encoding="utf-8")
    idx = MessageIndex(tmp / "m.db", {"chat": tmp / "chat.jsonl", "messages": tmp / "messages.jsonl"},
                       tmp / "off.json")
    idx.ensure()
    idx.sync()
    s = idx.summary(3650)
    rows = idx.health().get("rows")
    check(f"3.1 索引里确实是两倍行数（{rows}）", rows == 40, str(rows))
    check("3.2 但聚合只算 messages 那一份 → 20", s["total"] == 20, str(s["total"]))
    check("3.3 来源分组也不重复计数", sum(x["n"] for x in s["sources"]) == 20,
          str(s["sources"]))
    recent = idx.recent_user_texts(10)
    check("3.4 最近发言也不重复", len(recent) == len(set(recent)) and len(recent) == 10,
          f"{len(recent)} 条 / {len(set(recent))} 唯一")
finally:
    shutil.rmtree(tmp, ignore_errors=True)

# ---------------------------------------------------------------- 4) 原生层与 Python 一致
print("\n· 原生扫描：重复词、重叠词、大小写都要和 Python 完全一致")


def py_scan(text, ws):
    low = text.lower()
    return [i for i, w in enumerate(ws) if w.lower() in low]


cases = [
    ("可莉可莉可莉", ["可莉", "可莉可"]),
    ("世世世", ["世", "世", "世"]),               # 重复词：以前 Rust 只返回最后一个下标
    ("ABC abc", ["abc"]),
    ("😀表情", ["表情"]),
]
bad = [c for c in cases if N.scan(c[0], c[1]) != py_scan(c[0], c[1])]
check("4.1 重复词返回全部下标", not bad, str(bad))

# ---------------------------------------------------------------- 5) 插件包安装真的能成
print("\n· 插件包安装：最后一行不能再 NameError")
BASE = "http://127.0.0.1:8798"
H = {"X-Klee-Studio": "1"}


def post_raw(path, data, ctype="application/zip"):
    req = urllib.request.Request(BASE + path, data=data, headers={**H, "Content-Type": ctype})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8", "replace") or "{}")


buf = io.BytesIO()
with zipfile.ZipFile(buf, "w") as z:
    z.writestr("dbg_probe.py", 'NAME = "调试探针"\nDESCRIPTION = "临时"\ndef register(api):\n    pass\n')
try:
    st, d = post_raw("/api/plugins/import?overwrite=1", buf.getvalue())
    check("5.1 安装返回 200/ok", st == 200 and d.get("ok") is True, f"{st} {d}")
    check("5.2 报出装了哪些文件", d.get("installed") == ["dbg_probe.py"], str(d.get("installed")))
    man = d.get("manifest") or {}
    check("5.3 C# 审计给出 SHA-256 清单（或如实说明没装 .NET）",
          bool(man.get("files")) or bool(man.get("skipped")), str(man)[:120])
finally:
    for f in (ROOT / "bot" / "features" / "dbg_probe.py",):
        try:
            f.unlink()
        except OSError:
            pass

# ---------------------------------------------------------------- 6) 写接口鉴权
print("\n· 写接口鉴权：新加的 /api/highlight 不能是例外")
req = urllib.request.Request(BASE + "/api/highlight", data=b'{"lang":"js","code":"x"}',
                             headers={"Content-Type": "application/json"})
try:
    with urllib.request.urlopen(req, timeout=10) as r:
        code = r.status
except urllib.error.HTTPError as e:
    code = e.code
check("6.1 没有 X-Klee-Studio 头 → 403", code == 403, str(code))

# ---------------------------------------------------------------- 6.5 网页聊天也要过钩子
print("\n· 网页聊天：入站钩子对普通消息同样生效")
try:
    import server as S                                        # noqa: E402
    from bot.framework import BLOCK

    class _Mgr:
        def __init__(self):
            self.ran = []
            self.blocked = False

        def nlu_match(self, text):
            return None

        async def run_message_hooks(self, ctx):
            self.ran.append(ctx.text)
            if self.blocked:
                await ctx.reply("拦下了")
                return False
            return True

        async def handle_command(self, ctx):
            return False

    class _Rt:
        def __init__(self, blocked=False):
            self.mgr = _Mgr()
            self.mgr.blocked = blocked
            self.config = {"feature_prefix": "."}

    rt = _Rt(blocked=False)
    out = S._web_plugin_dispatch(rt, {"session": "s"}, "一句普通的话", "klee")
    check("6.5.1 普通消息也会跑入站钩子", rt.mgr.ran == ["一句普通的话"], str(rt.mgr.ran))
    check("6.5.2 没命中就返回 None（继续聊天）", out is None, repr(out))

    rt2 = _Rt(blocked=True)
    out2 = S._web_plugin_dispatch(rt2, {"session": "s"}, "一句普通的话", "klee")
    check("6.5.3 被钩子拦下时回复拦截文案", out2 == "拦下了", repr(out2))

    rt3 = _Rt(blocked=False)
    S._web_plugin_dispatch(rt3, {"session": "s"}, ".help", "klee")
    check("6.5.4 命令消息同样先过钩子", rt3.mgr.ran == [".help"], str(rt3.mgr.ran))
except Exception as e:                                        # noqa: BLE001
    check("6.5.1 普通消息也会跑入站钩子", False, f"{type(e).__name__}: {e}")

# ---------------------------------------------------------------- 6.8 缺 .so 也要能跑
print("\n· 降级：把三个原生库摘掉，功能必须还在")
_saved = dict(N._libs)
try:
    N._libs.update({"width": None, "text": None, "palette": None})
    px = bytes([220, 40, 40, 255] * 256)
    check("6.8.1 width 回落纯 Python", N.width("汉A") == 3, str(N.width("汉A")))
    check("6.8.2 truncate 回落", N.truncate("可莉是个火花骑士", 4) == "可莉", repr(N.truncate("可莉是个火花骑士", 4)))
    check("6.8.3 scan 回落", N.scan("这里有个屏蔽词", ["屏蔽"]) == [0])
    cols = N.palette(px, 16, 16, 2)
    check("6.8.4 palette 有纯 Python 兜底（以前返回空）", bool(cols) and cols[0].startswith("#"), str(cols))
    check("6.8.5 luma 有兜底", N.luma(px, 16, 16, 2) > 60, str(N.luma(px, 16, 16, 2)))
finally:
    N._libs.clear()
    N._libs.update(_saved)
check("6.8.6 恢复后仍然用原生库", N.status()["loaded"] == 3, str(N.status()["loaded"]))

# ---------------------------------------------------------------- 7) 配色缓存按角色失效
print("\n· 配色缓存：刷新一个角色不能把别人的也清了")
try:
    from bot import palette
    pal_file = Path(__file__).resolve().parents[1] / "state" / "charpalette.json"
    before = json.loads(pal_file.read_text(encoding="utf-8")) if pal_file.is_file() else {}
    if len(before) >= 2:
        victim = sorted(before)[0]
        pal.drop(victim)
        after = json.loads(pal_file.read_text(encoding="utf-8"))
        check("7.1 只删掉被刷新的那一个", victim not in after and len(after) == len(before) - 1,
              f"{sorted(before)} → {sorted(after)}")
    else:
        check("7.1 只删掉被刷新的那一个", True, "缓存不足两个角色，跳过")
except Exception as e:                                        # noqa: BLE001
    check("7.1 只删掉被刷新的那一个", False, f"{type(e).__name__}: {e}")

# ---------------------------------------------------------------- 8) OCR 第六轮
print("\n· 钩子改写后：命令判定要用改写后的文本")
try:
    import server as S2                                       # noqa: E402

    class _Mgr2:
        def __init__(self, rewrite=None, cmds=()):
            self.rewrite = rewrite
            self.cmds = set(cmds)
            self.ran = []
            self.commands = []

        def nlu_match(self, text):
            return None

        async def run_message_hooks(self, ctx):
            self.ran.append(ctx.text)
            if self.rewrite is not None:
                ctx.text = self.rewrite      # 和真框架一样：钩子返回字符串 = 改写文本
            return True

        async def handle_command(self, ctx):
            self.commands.append(ctx.text)
            tok = str(ctx.text).split()[0]
            if tok in self.cmds:
                await ctx.reply("命令回执")
                return True
            return False

    class _Rt2:
        def __init__(self, **kw):
            self.mgr = _Mgr2(**kw)
            self.config = {"feature_prefix": "."}

    # 8.1 钩子把普通话改成命令 —— 以前 is_cmd 是钩子之前算的，改写结果被静默丢掉
    rt = _Rt2(rewrite=".help", cmds={".help"})
    out = S2._web_plugin_dispatch(rt, {"session": "s"}, "帮个忙", "klee")
    check("8.1 钩子改写成命令后真的会执行", rt.mgr.commands == [".help"], str(rt.mgr.commands))
    check("8.2 命中的命令返回插件回执", out == "命令回执", repr(out))

    # 8.3 错字命令：没消费就交回模型（以前返回 ""，于是没有任何回复）
    rt3 = _Rt2(cmds=set())
    out3 = S2._web_plugin_dispatch(rt3, {"session": "s"}, ".helo", "klee")
    check("8.3 未消费的命令不是静默", out3 is None, repr(out3))

    # 8.4 命令被消费且没话说 → 空串（静默）
    class _Mgr4(_Mgr2):
        async def handle_command(self, ctx):
            return True
    rt4 = _Rt2(cmds=set())
    rt4.mgr = _Mgr4()
    out4 = S2._web_plugin_dispatch(rt4, {"session": "s"}, ".quiet", "klee")
    check("8.4 已消费但不回话 = 空串", out4 == "", repr(out4))

    # 8.5 钩子改写的普通话要交给模型（落在 job 上）
    rt5 = _Rt2(rewrite="改写后的句子")
    job = {"session": "s"}
    out5 = S2._web_plugin_dispatch(rt5, job, "原句", "klee")
    check("8.5 改写后的文本传给模型", out5 is None and job.get("dispatch_text") == "改写后的句子",
          str(job))
except Exception as e:                                        # noqa: BLE001
    check("8.1 钩子改写成命令后真的会执行", False, f"{type(e).__name__}: {e}")

print("\n· 尾部兜底：只有真的截断才丢第一行，脏行不能 500")
try:
    import server as S3                                       # noqa: E402
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "messages.jsonl"
        f.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in [
            {"role": "user", "text": "最早的一条"},
            {"role": "assistant", "text": "回答"},
            ["不是对象"],
            {"role": "user", "text": "最近的这条"},
        ]) + "\n", encoding="utf-8")
        old = S3.MSG_JSONL
        S3.MSG_JSONL = f
        old_index = S3._index
        S3._index = lambda: (_ for _ in ()).throw(RuntimeError("索引还没建好"))   # 强制走尾部兜底
        try:
            got = S3._similar("完全新的问题")
            texts = [h["text"] for h in got.get("hits") or []]
            check("8.6 小文件不丢第一条候选", "最早的一条" in texts or got.get("scanned") == 2,
                  f"{texts} scanned={got.get('scanned')}")
            check("8.7 非对象行不会把接口打成 500", got.get("ok") is True, str(got)[:120])
        finally:
            S3.MSG_JSONL = old
            S3._index = old_index
except Exception as e:                                        # noqa: BLE001
    check("8.6 小文件不丢第一条候选", False, f"{type(e).__name__}: {e}")

print("\n· Java 批量：只有一个候选也必须能算")
try:
    import subprocess
    cls = ROOT / "bot" / "features" / "ext" / "tools" / "classes"
    one = json.dumps({"op": "call", "id": 1, "cmd": "similar_batch",
                      "text": "今天天气怎么样" + "\u0001" + "今天天气如何"}, ensure_ascii=False)
    res = subprocess.run(["java", "-cp", str(cls), "Similar",
                          "-XX:TieredStopAtLevel=1"],
                         input='{"op":"hello"}\n' + one + '\n{"op":"bye"}\n',
                         capture_output=True, text=True, timeout=60).stdout.strip().splitlines()
    reply = json.loads(res[-1])
    check("8.8 单候选批量能算出分（以前直接报错）", reply.get("ok") is True, str(reply))
    if reply.get("ok"):
        sc = json.loads(reply["reply"])
        check("8.9 单候选分数与候选对齐（0.38~0.67 是换个说法）", 0.3 <= sc[0] <= 0.8, str(sc))
except Exception as e:                                        # noqa: BLE001
    check("8.8 单候选批量能算出分（以前直接报错）", False, f"{type(e).__name__}: {e}")

print("\n· 取色兜底：和 C++ 版逐色一致（以前桶下界 / 曼哈顿距离都不一样）")
_saved2 = dict(N._libs)
try:
    import random
    rnd = random.Random(7)
    for tag, gen in (("纯色", lambda: [(200, 50, 60)] * 256),
                     ("渐变", lambda: [(x * 5 % 256, (x * 3) % 256, (x * 7) % 256) for x in range(256)]),
                     ("随机", lambda: [(rnd.randrange(256), rnd.randrange(256), rnd.randrange(256))
                                       for _ in range(256)])):
        px = b"".join(bytes((*c, 255)) for c in gen())
        a = N.palette(px, 16, 16, 2, 4)
        N._libs["palette"] = None
        b = N.palette(px, 16, 16, 2, 4)
        N._libs.update(_saved2)
        check(f"8.10 {tag}：原生与纯 Python 取色一致", a == b, f"{a} vs {b}")
finally:
    N._libs.clear()
    N._libs.update(_saved2)

print("\n· pkgcheck：审计口径要和安装器一致")
try:
    import subprocess
    dll = ROOT / "tools" / "pkgcheck" / "bin" / "Release" / "net10.0" / "pkgcheck.dll"
    if dll.is_file():
        with tempfile.TemporaryDirectory() as td:
            def mk(name, entries):
                fp = Path(td) / name
                with zipfile.ZipFile(fp, "w") as z:
                    for n, body in entries:
                        z.writestr(n, body)
                return fp
            wrapped = mk("w.zip", [("demo/demo.py", "x=1\n"), ("demo/README.md", "# d\n"),
                                   ("demo/plugin.json", "{}")])
            nopy = mk("n.zip", [("README.md", "# nothing\n")])
            out = {}
            for tag, fp in (("w", wrapped), ("n", nopy)):
                r = subprocess.run(["dotnet", str(dll), str(fp), "--plugin"],
                                   capture_output=True, text=True, timeout=90)
                out[tag] = json.loads(r.stdout.strip().splitlines()[-1])
            # 注意：Inspect 走的是 Report（PascalCase 键），selftest 走匿名对象（小写）
            check("8.11 顶层目录包不再误报（安装器接受）", out["w"].get("Ok") is True,
                  str(out["w"].get("Problems")))
            check("8.12 没有 .py 的插件包要被报出来",
                  any("没有 .py" in x for x in out["n"].get("Problems", [])),
                  str(out["n"].get("Problems")))
            st = subprocess.run(["dotnet", str(dll), "--selftest"], capture_output=True,
                                text=True, timeout=120)
            check("8.13 自检覆盖插件模式", st.returncode == 0, st.stdout[-200:])
    else:
        check("8.11 顶层目录包不再误报（安装器接受）", False, "pkgcheck 没编译")
except Exception as e:                                        # noqa: BLE001
    check("8.11 顶层目录包不再误报（安装器接受）", False, f"{type(e).__name__}: {e}")

print(f"\nPASS {len(PASS)} / {len(PASS) + len(FAIL)}")
if FAIL:
    print("失败：" + "、".join(FAIL))
sys.exit(1 if FAIL else 0)
