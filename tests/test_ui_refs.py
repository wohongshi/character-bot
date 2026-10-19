"""前端引用完整性：`onclick="xxx()"` 里的函数必须真的存在。

这个项目是单文件前端，没有构建期检查 —— 删函数时漏改一处模板字符串，
按钮就变成哑的（点下去只抛 ReferenceError，界面毫无反应）。
2026-10-18 就踩过：`switchChar` / `pickChar` 被删，「切换出演」整条失效。

只做静态检查，不需要起服务。
"""
import re
import sys
from pathlib import Path

HTML = Path(__file__).resolve().parent.parent / "static" / "index.html"

PASS = FAIL = 0


def check(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name} {detail}")


html = HTML.read_text(encoding="utf-8")

# 插件自带页面的 HTML 住在 Python 字符串里（bot/adapters/*.py、bot/features/*.py）。
# 它们也算「面板」，一律并进来一起查引用 —— 否则接口一搬进插件页，
# 这里就会误报「面板调不到」。
PLUGIN_SRC = ""
for d in ("bot/adapters", "bot/features"):
    for f in sorted((HTML.parent.parent / d).glob("*.py")):
        PLUGIN_SRC += f.read_text(encoding="utf-8", errors="replace")
PANEL_AND_PLUGINS = html + PLUGIN_SRC

# 1) 已定义的函数 / 变量
defined = set(re.findall(r"function\s+([A-Za-z_$][\w$]*)\s*\(", html))
defined |= set(re.findall(r"(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=", html))
defined |= set(re.findall(r"^\s*([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?(?:function|\()", html, re.M))

# 2) 内联事件里调用的名字
calls = set()
for m in re.finditer(r'on(?:click|change|input|keydown|submit|scroll)\s*=\s*"([^"]*)"', html):
    for name in re.findall(r"(?<![\w$.])([A-Za-z_$][\w$]*)\s*\(", m.group(1)):
        calls.add(name)

# 关键字 / 内建 / 只在模板里做转义的辅助函数
ALLOW = {"if", "for", "while", "switch", "return", "typeof", "event", "this",
         "parseInt", "parseFloat", "Number", "String", "Boolean", "Array", "Object",
         "JSON", "Math", "Date", "encodeURIComponent", "decodeURIComponent",
         "setTimeout", "clearTimeout", "alert", "confirm", "prompt", "fetch",
         "require", "isNaN", "RegExp", "Error"}
missing = sorted(c for c in calls if c not in defined and c not in ALLOW)

check("onclick/onchange 里没有未定义的函数", not missing, str(missing))

# 3) 关键交互函数必须存在（这几个是按钮的唯一入口，缺一个就是死按钮）
REQUIRED = ["go", "flash", "pickChar", "switchChar", "viewChars", "viewPersona",
            "viewPlugins", "viewChat", "viewBoard", "viewOps", "viewModels", "viewCustom",
            "sendMsg", "newConv", "delConv", "cmdkOpen", "cmdkClose",
            "openPluginDrawer", "installPluginPack", "importZip", "setTheme",
            "openPluginPage", "pluginFrameDoc", "navToggle", "navOpen", "navClose"]
miss_req = [n for n in REQUIRED if n not in defined]
check("关键交互函数齐全", not miss_req, str(miss_req))

# 4) 已删掉的东西不许再被引用（防止改回来一半）
GONE = ["restorePersona", "viewDocs", "openFile", "savePluginFile",
        "opsQr", "opsQrOpen",   # 二维码搬进 onebot 插件页，旧的全局函数名不许回来
        "viewOpsBot",           # 运维/机器人双子页已合并成单页
        "moreSheet", "closeSheet", "dock",   # 底栏 + 更多面板 → 可折叠侧栏
        "agentBtn", "agEnabled"]  # agent 常开：开关已拆掉，别再长回来
referenced = [n for n in GONE if re.search(rf"(?<![\w$.]){re.escape(n)}\s*\(", html)]
check("已移除的功能不再被引用", not referenced, str(referenced))

# 5) 导航目标必须真的能落到一个视图上
#    go("kb") 是 persona 的别名（知识库在人格页里），不算错
VIEW_ALIAS = {"kb": "persona"}
views = {m.lower() for m in re.findall(r"async function view(\w+)\s*\(", html)}
targets = set(re.findall(r"\bgo\(['\"]([a-z]+)['\"]\)", html))
bad_go = sorted(t for t in targets
                if t not in views and VIEW_ALIAS.get(t) not in views)
check("go() 的目标都有对应视图", not bad_go, str(bad_go))

# 6) 服务端不许留「面板永远调不到」的写接口（那些是白送的攻击面）
srv = (HTML.parent.parent / "server.py").read_text(encoding="utf-8")
routes = set(re.findall(r'u\.path\s*==\s*"([^"]+)"', srv))
for grp in re.findall(r"u\.path\s+in\s+\(([^)]+)\)", srv):
    routes |= set(re.findall(r'"([^"]+)"', grp))
# 这些是有意保留给脚本 / 测试 / 外部调用方的，不是死接口
KEEP = {"/api/napcat/qr", "/qr.png", "/qr", "/onebot/event", "/index.html", "/",
        "/api/docs", "/api/themes", "/api/characters/theme", "/api/logs/meta",
        # 诊断口：服务卡住时用 curl 看「谁拿着全局锁」，设计上不给面板用
        "/api/lock"}
dead = sorted(r for r in routes
              if r.startswith("/api") and r not in KEEP and r not in PANEL_AND_PLUGINS)
check("没有面板调不到的 /api 写接口", not dead, str(dead))

# 7) 插件页必须能开关（曾经只读，「保存」只能去改仓库文件）
check("插件抽屉里有开关按钮", "function plugToggle(" in html and "pdToggle" in html)
check("插件抽屉里能改设置", "function plugSaveSettings(" in html and "/api/plugins/settings" in html)
check("插件页不再是「只读展示」文案", "只读展示" not in html)

# 8) 模板里 `${}` 之外的裸括号不该出现在属性值里（常见手误）
bad = re.findall(r'onclick="[^"]*\[:\d+\]', html)
check("内联事件里没有 Python 式切片", not bad, str(bad[:3]))

print(f"\nPASS {PASS} / {PASS + FAIL}")
sys.exit(1 if FAIL else 0)
