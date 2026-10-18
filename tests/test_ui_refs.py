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
            "viewPlugins", "viewChat", "viewBoard", "viewOpsBot", "viewCustom",
            "sendMsg", "newConv", "delConv", "cmdkOpen", "cmdkClose",
            "openPluginDrawer", "installPluginPack", "importZip", "setTheme"]
miss_req = [n for n in REQUIRED if n not in defined]
check("关键交互函数齐全", not miss_req, str(miss_req))

# 4) 已删掉的东西不许再被引用（防止改回来一半）
GONE = ["restorePersona", "viewDocs", "openFile", "savePluginFile",
        "opsQr", "opsQrOpen", "qrCard", "qrBox"]   # 面板不再放 QQ 二维码，改去 NapCat WebUI
referenced = [n for n in GONE if re.search(rf"(?<![\w$.]){re.escape(n)}\s*\(", html)]
check("已移除的功能不再被引用", not referenced, str(referenced))

# 5) 模板里 `${}` 之外的裸括号不该出现在属性值里（常见手误）
bad = re.findall(r'onclick="[^"]*\[:\d+\]', html)
check("内联事件里没有 Python 式切片", not bad, str(bad[:3]))

print(f"\nPASS {PASS} / {PASS + FAIL}")
sys.exit(1 if FAIL else 0)
