"""把 1.5MB 的 ZCOOL KuaiLe 子集化：只留面板真正会显示的字。

字体是装饰性手写体，只用在标题/品牌/卡片名上。整份 1.5MB 在 3G 上要下 8 秒，
而且下完还会触发一次全页重排（LCP 直接推到 2.8s）。

字符来源（宁可多收，漏了会回退到系统字体，不会出豆腐块）：
  1. static/index.html 全文
  2. bot/features/*.py 里的 NAME / TITLE / DESCRIPTION / USAGE（插件标题会显示在面板上）
  3. bot/characters.py 的角色名 / 称号 / 标签
  4. 数字、拉丁、常用标点、常见汉字兜底表（约 3500 字，覆盖日常文案）
"""
from __future__ import annotations

import glob
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path("/root/.hermes/workspace/klee-studio")
SRC = ROOT / "static/fonts/ZCOOLKuaiLe-Regular.ttf"
OUT_TTF = ROOT / "static/fonts/zcool-ui.ttf"
OUT_WOFF2 = ROOT / "static/fonts/zcool-ui.woff2"
TMP = Path("/tmp/fontsub")
TMP.mkdir(exist_ok=True)

chars: set[str] = set()


def add(text: str) -> None:
    for ch in text:
        if ch.strip() or ch == " ":
            chars.add(ch)


# 1) 面板源码
add((ROOT / "static/index.html").read_text(encoding="utf-8"))

# 2) 插件元信息（标题/说明会渲染到插件页）
for f in glob.glob(str(ROOT / "bot/features/*.py")):
    src = Path(f).read_text(encoding="utf-8")
    for m in re.finditer(r'^(?:NAME|TITLE|DESCRIPTION|USAGE)\s*=\s*"([^"]*)"', src, re.M):
        add(m.group(1))
    for m in re.finditer(r'^\s*"([^"]{2,60})"\s*,?\s*$', src, re.M):
        add(m.group(1))

# 3) 角色元信息
add((ROOT / "bot/characters.py").read_text(encoding="utf-8"))
for f in glob.glob(str(ROOT / "bot/character_seeds/*.md")):
    add(Path(f).read_text(encoding="utf-8")[:4000])   # 只取开头（名字/称号）

# 4) 兜底：ASCII 可见字符 + 常用中文标点（**不收**整张 GB2312 字表 ——
#    装饰字体缺字时浏览器会回退到系统字体，不如把这 700KB 省掉）
add("".join(chr(c) for c in range(0x20, 0x7F)))
add("·—…「」『』【】（）《》、，。；：？！～×÷±°←→↑↓★☆✦✧")
# 面板上会出现的动态数字/单位再补一遍，防止配置项改了以后缺字
add("0123456789:.,%/+-=()[]{}<>|~`!@#$^&*_ " + "年月日时分秒条个块级")

# 去掉控制字符
chars = {c for c in chars if ord(c) >= 0x20}

text = "".join(sorted(chars))
(TMP / "chars.txt").write_text(text, encoding="utf-8")
print(f"字符集：{len(chars)} 个（其中汉字 {sum(1 for c in chars if '\u4e00' <= c <= '\u9fff')}）")

py = "/usr/local/lib/hermes-agent/venv/bin/python"

# --- TTF 子集 ---
cmd = [py, "-m", "fontTools.subset", str(SRC),
       f"--text-file={TMP / 'chars.txt'}",
       f"--output-file={OUT_TTF}",
       "--layout-features=*", "--no-hinting", "--desubroutinize",
       "--name-IDs=*", "--drop-tables+=DSIG", "--recalc-bounds"]
r = subprocess.run(cmd, capture_output=True, text=True)
print("TTF 子集:", "ok" if r.returncode == 0 else r.stderr[:400])
if OUT_TTF.exists():
    print(f"  {SRC.stat().st_size/1e6:.2f} MB → {OUT_TTF.stat().st_size/1024:.0f} KB")

# --- WOFF2（需要 brotli；失败就只留 TTF） ---
cmd2 = [py, "-m", "fontTools.ttLib.woff2", "compress", str(OUT_TTF),
        f"--output-file={OUT_WOFF2}"]
r2 = subprocess.run(cmd2, capture_output=True, text=True)
if r2.returncode == 0 and OUT_WOFF2.exists():
    print(f"  WOFF2: {OUT_WOFF2.stat().st_size/1024:.0f} KB")
else:
    print("  WOFF2 跳过（缺 brotli）:", (r2.stderr or "").strip()[:160])

print("\n下一步：改 index.html 的 @font-face 指向新文件（保留系统字体回退链）。")
