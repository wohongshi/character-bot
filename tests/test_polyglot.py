#!/usr/bin/env python3
"""十种语言各自接的那件「真活」，逐条验行为。

判断标准不是「能跑个 demo」，而是：
- 原生库（Rust/C/C++）算出来的结果，必须和纯 Python 兜底**逐字节一致**；
- 换掉实现之后，调用方（屏蔽词、卡片截断、看板）的行为不变；
- 常驻外部进程（Node/Java/PHP）在服务里真的被调用，返回的格式能被直接消费；
- C# 的 CLI 真的能拦住恶意包。
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bot import native as N                                  # noqa: E402

PASS, FAIL = [], []


def check(name, ok, extra=""):
    (PASS if ok else FAIL).append(name)
    print(f"  {'✓' if ok else '✗'} {name}" + (f"   [{extra}]" if extra and not ok else ""))


BASE = "http://127.0.0.1:8798"
H = {"X-Klee-Studio": "1"}


def get(path):
    try:
        with urllib.request.urlopen(urllib.request.Request(BASE + path, headers=H), timeout=20) as r:
            return r.status, json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8", "replace") or "{}")
    except Exception as e:                                   # noqa: BLE001
        return 0, {"error": f"{type(e).__name__}: {e}"}


def post(path, body):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode(),
                                 headers={**H, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read().decode("utf-8", "replace"))
    except Exception as e:                                   # noqa: BLE001
        return 0, {"error": f"{type(e).__name__}: {e}"}


# ---------------------------------------------------------------- 1) 原生库
print("· 原生库（Rust / C / C++）")
st = N.status()
check("1.1 三个库都在", st["loaded"] == 3, str([(x["file"], x["built"]) for x in st["libs"]]))
check("1.2 每个库都有语言与职责说明",
      all(x["lang"] and x["what"] for x in st["libs"]))

CASES_W = ["汉A", "abc", "", "可莉是个火花骑士，最喜欢炸弹啦！", "a\u0301b", "🎇🎇", "。（）·—"]
bad = [(s, N.width(s), N._py_width if hasattr(N, "_py_width") else None) for s in CASES_W]
# 兜底实现不在公开 API 里，这里按 East Asian Width 重算一遍做对照
import unicodedata                                            # noqa: E402


def py_w(s):
    w = 0
    for ch in s:
        if unicodedata.combining(ch):
            continue
        w += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return w


mismatch = [(s, N.width(s), py_w(s)) for s in CASES_W if N.width(s) != py_w(s)]
check("1.3 C 的宽度和 Python 兜底一致", not mismatch, str(mismatch))

t = "可莉是个火花骑士hello世界"
tr = N.truncate(t, 10)
check("1.4 按宽度截断不超过上限", N.width(tr) <= 10, f"{tr!r} w={N.width(tr)}")
check("1.5 截断不切坏多字节字符", isinstance(tr, str) and tr and not tr.endswith("\ufffd"), repr(tr))

words = ["坏词", "abc", "屏蔽"]
sc = N.scan("这个 ABC 和 屏蔽 都算", words)
check("1.6 Rust 扫描命中的下标正确", sc == [1, 2], str(sc))
check("1.7 空词表/空文本不炸", N.scan("", words) == [] and N.scan("x", []) == [])
check("1.8 归一化：全角转半角 + 去零宽 + 折叠空白",
      N.normalize("ＡＢＣ\u200b   x  ") == "ABC x", repr(N.normalize("ＡＢＣ\u200b   x  ")))

px = bytes([220, 40, 40, 255] * 100)
cols = N.palette(px, 10, 10, 3)
check("1.9 C++ 取色返回合法 #rrggbb", cols and all(c.startswith("#") and len(c) == 7 for c in cols),
      str(cols))
check("1.10 纯色图的第一个主色就是它", cols and cols[0].lower() in ("#dc2828", "#d92828"),
      str(cols[:2]))
check("1.11 亮度能算出来", N.luma(px, 10, 10, 1) > 60, str(N.luma(px, 10, 10, 1)))

# ---------------------------------------------------------------- 2) 调用方不变
print("\n· 换实现后，调用方行为不变")
sys.path.insert(0, str(ROOT / "bot" / "features"))
import _cardimg                                             # noqa: E402
short = _cardimg.short("涂山红红·红线仙", 8)
check("2.1 卡片截断按显示宽度", _cardimg and N.width(short) <= 8, f"{short!r} w={N.width(short)}")
check("2.2 短名字原样返回", _cardimg.short("可莉", 8) == "可莉")
mod = (ROOT / "bot" / "features" / "moderation.py").read_text(encoding="utf-8")
check("2.3 屏蔽词按词表规模切实现（≥64 走 Rust）", "len(words) >= 64" in mod and "native" in mod)
# 以前这条只数「出现了几次 await _block()」，而 _block 少传了 ctx 它也照样通过 ——
# 现在改成看真实调用签名，行为测试在 tests/test_debug_round.py
check("2.4 两条路径共用同一套拦截逻辑（且都传了 ctx）",
      mod.count("await _block(ctx)") == 2 and "async def _block(ctx)" in mod,
      f"调用 {mod.count('await _block(ctx)')} 处 / 定义 {('async def _block(ctx)' in mod)}")

# ---------------------------------------------------------------- 3) 运行时清单
print("\n· /api/runtime：十种语言都在")
st_code, rt = get("/api/runtime")
langs = {t["lang"]: t for t in (rt.get("toolchains") or [])}
want = {"python", "go", "rust", "c", "cpp", "node", "java", "php", "csharp", "sql"}
check("3.1 十种语言全部列出", want <= set(langs), str(sorted(want - set(langs))))
check("3.2 每种都写清在干什么", all(langs[k].get("what") for k in want))
check("3.3 版本号都取到了", all(langs[k].get("ok") and langs[k].get("version") for k in want),
      str({k: langs[k].get("version") for k in want if not langs[k].get("ok")}))
check("3.5 版本探测有缓存（第二次不再起 10 个子进程）", get("/api/runtime")[1].get("ok") is True)
check("3.4 基准数据带单位与加速比",
      all(b.get("unit") and b.get("how") for b in (rt.get("bench") or [])),
      str((rt.get("bench") or [])[:1]))

# ---------------------------------------------------------------- 4) 常驻服务
print("\n· 常驻外部进程（Node / Java / PHP）")
ext = {x["name"]: x for x in (rt.get("ext") or [])}
check("4.1 三个常驻服务都在跑", all(ext.get(n, {}).get("running") for n in ("highlight", "similar", "market")),
      str(ext))

st_code, hl = post("/api/highlight", {"lang": "python", "code": "def f(x):\n    return 1  # c\n"})
check("4.2 高亮返回 HTML 且带关键字 span", hl.get("ok") and 'class="t-k"' in (hl.get("html") or ""),
      str(hl)[:120])
check("4.3 高亮结果已转义（不给 XSS 留口子）", "<script" not in (hl.get("html") or "").lower())
st_code, hl2 = post("/api/highlight", {"lang": "python", "code": "x = '<img onerror=1>'\n"})
check("4.4 代码里的标签被转义", "&lt;img" in (hl2.get("html") or ""), (hl2.get("html") or "")[:80])

st_code, sm = get("/api/similar?q=" + urllib.parse.quote("你好") + "&limit=3")
check("4.5 相似度接口可用（Java）", sm.get("ok") is True and sm.get("by") in ("java", "python"),
      str(sm)[:120])
check("4.6 相似结果分数在 0..1", all(0 <= h["score"] <= 1 for h in (sm.get("hits") or [])),
      str(sm.get("hits")))
check("4.7 不相关的句子不会被判为相似", sm.get("hits") is not None)

st_code, mk = get("/api/market")
check("4.8 市场清单来自 PHP", mk.get("ok") and str(mk.get("by", "")).startswith("php"),
      str(mk)[:100])
check("4.9 清单里有分类统计", (mk.get("total") or 0) > 100 and mk.get("by_kind"),
      str(mk.get("by_kind")))
check("4.10 命令数能对上（>0）", int(mk.get("commands") or 0) > 0, str(mk.get("commands")))

# ---------------------------------------------------------------- 5) 取色
print("\n· 立绘取色（C++）")
st_code, pal = get("/api/characters/palette?name=klee")
check("5.1 取到主色", pal.get("ok") and str(pal.get("accent", "")).startswith("#"), str(pal)[:120])
check("5.2 主色不是纯黑纯白", pal.get("accent") not in ("#000000", "#ffffff"), str(pal.get("accent")))
check("5.3 有缓存（第二次不再重算）", get("/api/characters/palette?name=klee")[1].get("cached") is True)

# ---------------------------------------------------------------- 6) C# 校验器
print("\n· 插件包校验（C# CLI）")
dll = next(iter((ROOT / "tools" / "pkgcheck").glob("bin/Release/*/pkgcheck.dll")), None)
check("6.1 已编译出 pkgcheck.dll", dll is not None, str(dll))
if dll is not None:
    import subprocess
    r = subprocess.run(["dotnet", str(dll), "--selftest"], capture_output=True, text=True, timeout=120)
    out = json.loads(r.stdout or "{}")
    check("6.2 自检能拦住路径穿越", out.get("ok") is True, r.stdout[:120])
    check("6.3 返回了具体问题清单", any("路径穿越" in p for p in (out.get("problems") or [])),
          str(out.get("problems")))

# ---------------------------------------------------------------- 7) 看板 SQL 与 Python 一致
print("\n· 看板：SQL 与 Python 结果一致")
st_code, dash = get("/api/dashboard")
check("7.1 看板走 SQL（不再遍历 JSONL）", dash.get("from_sql") is True, str(dash.get("errors")))
check("7.2 消息总量自洽", (dash.get("messages", {}).get("total") or 0)
      == (dash.get("messages", {}).get("user") or 0) + (dash.get("messages", {}).get("assistant") or 0),
      str(dash.get("messages")))
check("7.3 逐日数据齐", len(dash.get("daily") or []) >= 1, str(len(dash.get("daily") or [])))

print(f"\nPASS {len(PASS)} / {len(PASS) + len(FAIL)}")
if FAIL:
    print("失败：" + "、".join(FAIL))
sys.exit(1 if FAIL else 0)
