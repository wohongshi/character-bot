#!/usr/bin/env python3
"""界面语言：中英词条对得上、t() 用到的 key 都存在、没有半截翻译。

只做静态检查（不起浏览器）：
- 两种语言的 key 集合必须完全一致（少一条就会在英文下露出中文或 key 本身）；
- HTML/JS 里所有 `t("xxx")` 的 key 必须在词条里（否则运行时直接显示 "xxx"）；
- 导航八项、筛选七项、页面标题都要有；
- 不允许“英文词条里混中文”（那是最难看的半截翻译）。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

HTML = (Path(__file__).resolve().parent.parent / "static" / "index.html").read_text(encoding="utf-8")
PASS, FAIL = [], []


def check(name, ok, extra=""):
    (PASS if ok else FAIL).append(name)
    print(f"  {'✓' if ok else '✗'} {name}" + (f"   [{extra}]" if extra and not ok else ""))


block = re.search(r"const I18N = \{(.*?)\n\};", HTML, re.S)
check("1.1 找到 I18N 词条表", bool(block))
src = block.group(1) if block else ""

zh_part = re.search(r"zh:\s*\{(.*?)\n  \},", src, re.S)
en_part = re.search(r"en:\s*\{(.*?)\n  \},", src, re.S)
check("1.2 有 zh / en 两套", bool(zh_part) and bool(en_part))

pair = re.compile(r'"([\w.]+)"\s*:\s*"((?:[^"\\]|\\.)*)"')


def entries(m):
    return dict(pair.findall(m.group(1))) if m else {}


zh, en = entries(zh_part), entries(en_part)
check("1.3 中文词条 ≥ 30 条", len(zh) >= 30, str(len(zh)))
check("1.4 英文词条数一致", len(zh) == len(en), f"zh={len(zh)} en={len(en)}")
only_zh = sorted(set(zh) - set(en))
only_en = sorted(set(en) - set(zh))
check("1.5 没有只在中文里出现的 key", not only_zh, str(only_zh[:5]))
check("1.6 没有只在英文里出现的 key", not only_en, str(only_en[:5]))

# t("key") 的调用必须都有词条
# `t("filter." + x)` 是拼出来的 key，不是字面量，别当成缺失
used = {k for k in re.findall(r'\bt\("([\w.]+)"(?![\s]*\+)', HTML) if not k.endswith(".")}
missing = sorted(k for k in used if k not in zh)
check(f"1.7 {len(used)} 处 t() 调用的 key 都在词条里", not missing, str(missing[:8]))

# 导航 / 筛选 / 页面标题必须齐
need = [f"nav.{v}" for v in ("chat", "chars", "board", "persona", "plugins", "reminders", "ops", "custom")]
need += [f"filter.{v}" for v in ("all", "limited", "general", "system", "on", "off", "err")]
need += [f"page.{v}" for v in ("chat", "chars", "board", "persona", "plugins", "reminders", "ops", "custom")]
lack = [k for k in need if k not in zh or k not in en]
check("1.8 导航 / 筛选 / 页面标题都有中英", not lack, str(lack[:6]))

# 英文里出现中文 = 半截翻译
cjk = re.compile(r"[\u4e00-\u9fff]")
dirty = [f"{k}={v}" for k, v in en.items() if cjk.search(v)]
check("1.9 英文词条里没有中文", not dirty, "；".join(dirty[:4]))
# 反过来：中文词条不该整条是英文（专有名词除外，长度 > 2 才算）
latin = re.compile(r"^[A-Za-z ]+$")
odd = [f"{k}={v}" for k, v in zh.items() if latin.match(v) and len(v) > 2]
check("1.10 中文词条没有整条英文", not odd, "；".join(odd[:4]))

# 机制本身
check("2.1 跟随系统语言（navigator.language）", "navigator.language" in HTML)
check("2.2 可手动覆盖（localStorage.ks_lang）", "ks_lang" in HTML)
check("2.3 有 setLang()", "function setLang(" in HTML)
check("2.4 渲染后统一刷外壳（applyChrome）", "function applyChrome(" in HTML and "applyChrome();" in HTML)
check("2.5 切换会更新 <html lang>", 'document.documentElement.lang' in HTML)
check("2.6 自定义页有语言三选一",
      all(x in HTML for x in ('onclick="setLang(\'auto\')"', 'onclick="setLang(\'zh\')"',
                              'onclick="setLang(\'en\')"')))
check("2.7 插件名有英文就优先用（bi()）", "function bi(" in HTML and "m.title_en" in HTML)

# 服务端：内置插件都带英文名
import json                                            # noqa: E402
import urllib.request                                  # noqa: E402
try:
    req = urllib.request.Request("http://127.0.0.1:8798/api/plugins?full=1",
                                 headers={"X-Klee-Studio": "1"})
    data = json.loads(urllib.request.urlopen(req, timeout=10).read())
    meta = data.get("meta") or {}
    builtin = ("web_tools", "utility", "notes", "knowledge", "skillbox",
               "onebot11", "web", "feishu", "wechat", "qqofficial", "webhook")
    lack_en = [n for n in builtin if not (meta.get(n) or {}).get("title_en")]
    check("3.1 11 个内置插件都有英文名", not lack_en, str(lack_en))
    bad = [n for n in builtin if cjk.search((meta.get(n) or {}).get("description_en") or "")]
    check("3.2 内置插件的英文说明里没有中文", not bad, str(bad))
except Exception as e:                                 # noqa: BLE001
    check("3.1 11 个内置插件都有英文名", False, f"{type(e).__name__}: {e}")

print(f"\nPASS {len(PASS)} / {len(PASS) + len(FAIL)}")
if FAIL:
    print("失败：" + "、".join(FAIL))
sys.exit(1 if FAIL else 0)
