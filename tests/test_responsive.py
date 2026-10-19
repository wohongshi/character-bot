"""分辨率自适应：把「屏幕多大 / 多细 / 是不是触屏」的判定钉死。

这套机制出问题时不会报错，只是「某类设备上字太小 / 按钮按不准 / 布局溢出」，
很难在日常自测（都用同一台电脑）里发现。这里用静态检查固定住四件事：

  1. 小字号必须有硬下限（11px），并且接上 --fx 系数
  2. 断点变量与分层（--app-max / --side-w）必须存在
  3. 矮窗口（手机横屏）必须收侧栏 + 压角色卡
  4. 触屏判定必须同时有 CSS 媒体查询和 JS 兜底
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HTML = (ROOT / "static" / "index.html").read_text(encoding="utf-8")

PASS = FAIL = 0


def check(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}  {detail}")


print("· 字号下限")
# 所有 < 11px 的字号声明都必须被 max(11px, …) 兜住
bare = re.findall(r"font-size:\s*(9|9\.5|10|10\.5)px", HTML)
check("没有裸的 <11px 字号", not bare, f"{len(bare)} 处：{bare[:5]}")
floored = len(re.findall(r"font-size:max\(11px,\s*calc\(", HTML))
check(f"已加下限的字号 {floored} 处（≥20）", floored >= 20, str(floored))
check("小字号都接上 --fx", HTML.count("var(--fx)") >= floored, str(HTML.count("var(--fx)")))

print("\n· 尺寸 token")
for tok, hint in (("--fx", "小字号系数"), ("--app-max", "内容最大宽"),
                  ("--side-w", "侧栏宽度"), ("--vh", "视觉视口高度")):
    check(f"定义了 {tok}（{hint}）", f"{tok}:" in HTML or f'"{tok}"' in HTML, tok)
check("--fx 有安全默认值 1", re.search(r"--fx:\s*1\s*[;}]", HTML) is not None)
check("#app 用 min(var(--app-max),100vw) 兜窄屏",
      "max-width:min(var(--app-max),100vw)" in HTML)
check("#app 高度同时给 vh 和 dvh", "height:100vh;height:100dvh" in HTML)

print("\n· 宽屏分层")
tiers = re.findall(r"@media\(min-width:(\d+)px\)\{[^}]*--app-max:(\d+)px", HTML)
check("≥3 个宽屏档位", len(tiers) >= 3, str(tiers))
if len(tiers) >= 3:
    widths = [int(v) for _, v in tiers]
    check("档位单调递增", widths == sorted(widths), str(widths))
    check("最宽档 ≥1800（2560 屏别只占一半）", max(widths) >= 1800, str(max(widths)))

print("\n· 矮窗口（分屏小窗 / 手机横屏）")
short = re.search(r"@media\(max-height:520px\)\{(.*?)\n\}", HTML, re.S)
check("有 max-height:520px 分支", bool(short))
if short:
    blk = short.group(1)
    # 角色大卡删掉之后，横屏不用再压缩它 —— 高度直接归消息区。
    check("横屏没有角色大卡（高度全给消息区）", "chathero" not in HTML)
    check("横屏缩小正文内边距", ".content{padding:10px 14px 14px}" in blk)

# 侧栏「收成图标条」只对桌面矮窗口成立：手机横屏走的是浮层侧栏，
# 收条会把 .t/.sidefoot 全藏掉，用户只剩一排没文字的图标。
fine = re.search(r"@media \(max-height:520px\) and \(pointer:fine\)\{(.*?)\n\}", HTML, re.S)
check("桌面矮窗口才收条（pointer:fine）", bool(fine))
if fine:
    fblk = fine.group(1)
    check("收条时侧栏 64px", "--side-w:64px" in fblk, fblk[:60])
    check("收条时隐藏侧栏文字", "display:none" in fblk)
check("手机横屏不收条：浮层断点含 landscape+coarse",
      "and (orientation:landscape) and (pointer:coarse)" in HTML)
check("浮层断点带 safe-area（刘海 / home 指示条）",
      "env(safe-area-inset-top)" in HTML and "env(safe-area-inset-bottom)" in HTML)
check("侧栏关闭时 inert（否则隐藏的按钮还在 tab 序里）",
      "a.inert = hidden" in HTML and 'setAttribute("aria-hidden"' in HTML)
check("☰ 带 aria-expanded / aria-controls",
      'aria-controls="navAside"' in HTML and 'aria-expanded' in HTML)
check("侧栏滚动不传导（overscroll-behavior:contain）",
      "overscroll-behavior:contain" in HTML)
check("横屏给导航让位：隐藏装饰性 sidefoot",
      "aside .sidefoot{display:none}" in HTML)

# 层叠：遮罩必须和 aside 在同一个层叠上下文里。
# #app 有 position:relative + z-index:1，它自己是层叠上下文；
# 遮罩挂在 body 下时 z-index:65 > 1，会连侧栏一起盖住 ——
# 表现为「侧栏半透明发灰，点什么都没反应（其实是点到了遮罩）」。
from html.parser import HTMLParser


class _Tree(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack = []
        self.parent = {}

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if a.get("id"):
            self.parent[a["id"]] = self.stack[-1]["id"] if self.stack else None
        if tag not in ("meta", "link", "br", "img", "input", "hr"):
            self.stack.append({"tag": tag, "id": a.get("id") or ""})

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i]["tag"] == tag:
                del self.stack[i:]
                break


_t = _Tree()
_t.feed(HTML)
check("遮罩在 #app 里（否则 z-index:65 会盖住整个侧栏）", _t.parent.get("navScrim") == "app",
      str(_t.parent.get("navScrim")))
check("侧栏也在 #app 里（同一个层叠上下文才能比大小）", _t.parent.get("navAside") == "app",
      str(_t.parent.get("navAside")))
check("打开侧栏焦点落在容器（不画按钮描边）",
      "#navAside:focus{outline:none}" in HTML and 'a.focus({preventScroll:true})' in HTML)

print("\n· 触屏可点性")
check("有 @media(pointer:coarse) 分支", "@media(pointer:coarse)" in HTML)
check("有 html[data-touch] 兜底（WebView 里 pointer 可能失灵）",
      'html[data-touch="1"]' in HTML)
check("JS 用 maxTouchPoints 判定", "navigator.maxTouchPoints" in HTML)
check("按钮最小 32px", "min-height:32px" in HTML)
check("文字链在触屏上抬到 30px", "min-height:30px" in HTML)

print("\n· 断点状态位")
check("JS 写 data-bp", 'H.dataset.bp = bp' in HTML or "dataset.bp = bp" in HTML)
check("断点表含 xs/sm/md/lg/xl",
      all(k in HTML for k in ('"xs"', '"sm"', '"md"', '"lg"', '"xl"')))
check("监听 resize / orientationchange",
      "orientationchange" in HTML and '"resize"' in HTML)
check("监听 visualViewport（软键盘弹出）", "visualViewport" in HTML)
check("换显示器（DPR 变化）会重算", "(resolution: " in HTML)

print("\n· 窄屏排版")
narrow = re.search(r"@media\(max-width:420px\)\{(.*?)\n\}", HTML, re.S)
check("有 ≤420px 分支", bool(narrow))
if narrow:
    blk = narrow.group(1)
    check("窄屏没有角色大卡（不在小屏上重复占位）", "chathero" not in HTML)

# 顶栏：三个状态胶囊（面板/QQ/NapCat）说的是同一条链路，
# 分开显示等于把「没连上」说三遍，手机上占掉顶栏 60% 的宽度。
print("\n· 顶栏状态")
check("状态合成一个胶囊", len(re.findall(r'class="pill" id="p', HTML)) == 1)
check("正常时整块隐藏（不占地方）", "#pConn.ok{display:none}" in HTML)
check("异常时可点去运维", 'id="pConn"' in HTML and 'onclick="go(\'ops\')"' in HTML)

print(f"\nPASS {PASS} / {PASS + FAIL}")
sys.exit(1 if FAIL else 0)
