"""低性能设备相关的静态断言（防优化被改回去）。

这批改动的共同点：**改坏了不会报错，只是慢**。所以用静态检查把关键决定钉住：
  - 字体必须是子集 + woff2 优先 + swap + 不 preload
  - 角色图必须有多档，首屏不许直接引用 900w
  - 整屏飘落层不许再动 background-position（那是每帧重绘整屏的元凶）
  - 低性能降级规则必须在（关动画 / 关 backdrop-filter）
  - 对话页不许再「等会话列表才画壳子」
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HTML = (ROOT / "static/index.html").read_text(encoding="utf-8")
FONTS = ROOT / "static/fonts"
CHARS = ROOT / "static/img/chars"

PASS = FAIL = 0


def check(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}  {detail}")


print("· 字体")
woff2, ttf = FONTS / "zcool-ui.woff2", FONTS / "zcool-ui.ttf"
check("子集 woff2 存在", woff2.is_file())
check("子集 ttf 兜底存在", ttf.is_file())
if woff2.is_file():
    kb = woff2.stat().st_size / 1024
    check(f"woff2 ≤ 220KB（现在 {kb:.0f}KB）", kb <= 220, f"{kb:.0f}KB")
if ttf.is_file():
    kb = ttf.stat().st_size / 1024
    check(f"ttf ≤ 400KB（现在 {kb:.0f}KB）", kb <= 400, f"{kb:.0f}KB")
check("面板不再引用原始 1.5MB TTF", "ZCOOLKuaiLe-Regular.ttf" not in HTML)
check("没有字体 preload（会和首屏图抢带宽）",
      not re.search(r'rel="preload"[^>]*as="font"', HTML))
check("font-display:swap", "font-display:swap" in HTML)
check("woff2 排在 truetype 之前",
      HTML.index('format("woff2")') < HTML.index('format("truetype")')
      if 'format("woff2")' in HTML and 'format("truetype")' in HTML else False)

print("\n· 角色图多档")
ids = sorted(p.stem for p in CHARS.glob("*.webp") if not p.stem.endswith(("_s", "_m")))
check("6 个角色都在", len(ids) >= 6, str(ids))
missing, not_smaller = [], []
for cid in ids:
    s, m, big = CHARS / f"{cid}_s.webp", CHARS / f"{cid}_m.webp", CHARS / f"{cid}.webp"
    if not (s.is_file() and m.is_file() and big.is_file()):
        missing.append(cid)
    elif not (s.stat().st_size < m.stat().st_size < big.stat().st_size):
        not_smaller.append(cid)
check("每个角色都有 _s / _m / 原图", not missing, str(missing))
check("尺寸档位大小递增（小的确实更小）", not not_smaller, str(not_smaller))
small_kb = sum((CHARS / f"{c}_s.webp").stat().st_size for c in ids if (CHARS / f"{c}_s.webp").is_file()) / 1024
check(f"6 张小图合计 ≤ 260KB（现在 {small_kb:.0f}KB）", small_kb <= 260, f"{small_kb:.0f}KB")
# 不用 srcset：这套布局里 Chrome 按 100vw 算，一律挑最大那张（实测过）。
# 改成 JS 按 视口×DPR 选档，见 charSrc()。
check("有 charSrc() 按视口×DPR 选档", "function charSrc(" in HTML and "CHAR_STEPS" in HTML)
check("不再用 srcset（会被算成 100vw）", "srcset=" not in HTML)
check("聊天大图走 hero 档", 'charSrc(ACTIVE.id, "hero")' in HTML)
check("角色卡走 card 档", 'charSrc(c.id, "card")' in HTML)
check("头像/侧栏/看板走 thumb 档", 'charSrc(ACTIVE.id, "thumb")' in HTML)
check("可容忍 10% 轻微放大", "need * 0.9" in HTML)

print("\n· 飘落层不许每帧重绘")
# 注意：html.perf-low 那条也会匹配子串，必须锚定行首才是主规则
m = re.search(r'(?m)^body\[data-effects="true"\]::after\{(.*?)^\}', HTML, re.S)
block = re.sub(r"/\*.*?\*/", "", m.group(1), flags=re.S) if m else ""   # 注释里提到不算
check("飘落层存在", bool(block))
check("用 transform 位移（合成层），不再动 background-position",
      "transform" in block and "background-position" not in block)
kf = re.search(r"@keyframes drift\{(.*?)\}\s*\n", HTML, re.S)
check("drift 关键帧只动 transform",
      bool(kf) and "transform" in kf.group(1) and "background-position" not in kf.group(1),
      (kf.group(1)[:80] if kf else "缺 keyframes"))
check("will-change:transform", "will-change:transform" in block)

print("\n· 低性能降级")
check("有 .perf-low 样式", "html.perf-low" in HTML)
check("降级时关掉整屏动画", 'html.perf-low body[data-effects="true"]::after{animation:none}' in HTML)
check("降级时关掉 backdrop-filter", "backdrop-filter:none!important" in HTML)
check("硬件线索触发（mem/cores ≤4）",
      re.search(r"mem\s*<=\s*4", HTML) and re.search(r"cores\s*<=\s*4", HTML))
check("有帧率实测兜底", "requestAnimationFrame(tick)" in HTML and "perfWhy" in HTML)
check("可手动覆盖（?perf=）", "/[?&]perf=(low|high|auto)/" in HTML)
check("尊重 prefers-reduced-motion", "prefers-reduced-motion" in HTML)

print("\n· 对话页首帧不等后端")
i = HTML.index("async function viewChat()")
j = HTML.index("\n}", i)
body = HTML[i:j]
check("viewChat 里不再 await /api/chat/sessions 才画壳子",
      "await api(\"/api/chat/sessions" not in body)
check("会话列表改成异步补下拉", 'api("/api/chat/sessions' in body and "then(" in body)

print(f"\nPASS {PASS} / {PASS + FAIL}")
sys.exit(1 if FAIL else 0)
