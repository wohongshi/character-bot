#!/usr/bin/env python3
"""静态检查面板里所有「可点击元素」：

1. 内联事件（onclick/onchange/oninput/onkeydown/onmouseenter/onblur/onsubmit）
   里调用的函数，JS 里到底有没有定义（含模板字符串里拼出来的调用）；
2. 这些处理器里引用的 DOM id，HTML 里有没有（动态创建的白名单单列）；
3. 看起来能点、但既没有处理器也不是 submit/select/a 的元素。

不做运行时验证 —— 那是 test_clickables 的活。这里只保证「名字是真的」。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "static" / "index.html").read_text(encoding="utf-8")

# 事件属性：面板全用内联，没有 addEventListener 之外的框架
EVENT_ATTR = re.compile(
    r"\b(on(?:click|change|input|keydown|keyup|submit|blur|focus|mouseenter|mouseleave|"
    r"mousedown|scroll|paste|drop|dragover|load|error|toggle))\s*=\s*\"([^\"]*)\"")
EVENT_ATTR_S = re.compile(
    r"\b(on(?:click|change|input|keydown|keyup|submit|blur|focus|mouseenter|mouseleave|"
    r"mousedown|scroll|paste|drop|dragover|load|error|toggle))\s*=\s*'([^']*)'")

# 属性在模板字符串里也是纯文本，所以两遍都要扫
HANDLERS = [(m.group(1), m.group(2), m.start()) for m in EVENT_ATTR.finditer(HTML)]
HANDLERS += [(m.group(1), m.group(2), m.start()) for m in EVENT_ATTR_S.finditer(HTML)]

# JS 里定义的函数名：function foo( / foo = function / foo: function / const foo = / let foo =
DEF_PATTERNS = [
    re.compile(r"\bfunction\s+([A-Za-z_$][\w$]*)\s*\("),
    re.compile(r"\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?(?:function|\()"),
    re.compile(r"\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?[A-Za-z_$][\w$]*\s*=>"),
    re.compile(r"^\s*([A-Za-z_$][\w$]*)\s*\([^)]*\)\s*\{", re.M),          # 对象方法简写
    re.compile(r"\b([A-Za-z_$][\w$]*)\s*:\s*(?:async\s*)?function"),
    re.compile(r"\b([A-Za-z_$][\w$]*)\s*:\s*\([^)]*\)\s*=>"),
]
DEFINED = set()
for p in DEF_PATTERNS:
    DEFINED |= set(p.findall(HTML))

# 浏览器/语言自带，不算缺失
BUILTIN = set("""alert confirm prompt setTimeout setInterval clearTimeout clearInterval
parseInt parseFloat parseFloat isNaN encodeURIComponent decodeURIComponent JSON Object Array
String Number Boolean Math Date Promise fetch console localStorage sessionStorage history
location requestAnimationFrame cancelAnimationFrame structuredClone URL Blob FileReader
Image Audio FormData WebSocket Event CustomEvent navigator screen matchMedia AbortController
this window document event arguments return if else for while switch typeof void new
delete in of try catch finally throw function class extends super import export await
async yield let const var true false null undefined NaN Infinity String.raw
""".split())

# `$` 是面板的 getElementById 简写
IDENT_CALL = re.compile(r"(?<![\w$.])([A-Za-z_$][\w$]*)\s*\(")
KEYWORDS = {"if", "for", "while", "switch", "catch", "return", "typeof", "new", "function",
            "typeof", "void", "delete", "in", "of", "await", "async", "else", "do"}

missing_calls = []
for attr, body, pos in HANDLERS:
    for m in IDENT_CALL.finditer(body):
        name = m.group(1)
        if name in KEYWORDS or name in BUILTIN or name in DEFINED:
            continue
        # 数组/对象字面量的方法调用（.map( 之类）不会走到这里；剩下的就是真缺失
        line = HTML[:pos].count("\n") + 1
        missing_calls.append((line, attr, name, body.strip()[:90]))

# ---- id 引用 ----
IDS_IN_HTML = set(re.findall(r"\bid\s*=\s*[\"'`]([A-Za-z][\w-]*)[\"'`]", HTML))
# 动态创建的 id（JS 里 el.id = "..." / createElement 后赋值）
IDS_DYNAMIC = set(re.findall(r"\.id\s*=\s*[\"'`]([A-Za-z][\w-]*)[\"'`]", HTML))
IDS_DYNAMIC |= set(re.findall(r"\bid\s*=\s*`([A-Za-z][\w-]*)", HTML))     # id="a-${x}"
# 只认「像 id 的」：$()/getElementById() 拿的一定是 id；querySelector 里带 . # [ 空格 的
# 是选择器，不在这里核对（`box.querySelector("label")` 不是 id）。
ID_REF = re.compile(r"(?:\$\(|getElementById\()\s*[\"'`]([A-Za-z][\w-]*)[\"'`]")
# seg() 这类模板：`<input id="${id}">` —— id 名由调用处传入，静态核不了
TPL_ID = re.compile(r"\bid\s*=\s*[\"'`]\$\{")
referenced = {}
for m in ID_REF.finditer(HTML):
    referenced.setdefault(m.group(1), []).append(HTML[:m.start()].count("\n") + 1)

# 前缀型 id（`id="cmm-${cid}"` 这种）没法静态核对，按前缀放行
prefixes = set(re.findall(r"\bid\s*=\s*[\"'`]([A-Za-z][\w-]*?)-\$\{", HTML))
prefixes |= set(re.findall(r"\.id\s*=\s*[\"'`]([A-Za-z][\w-]*?)-\$\{", HTML))

# 字符串拼接出来的前缀：`$("cfg_" + k)`、`$("cmm-"+cid)`
prefixes |= {m.group(1) for m in re.finditer(r'\$\(\s*"([A-Za-z][\w-]*)"\s*\+', HTML)}

# 由辅助函数生成的 id：seg("uiBg", …) → <input id="uiBg">。
# 这些名字以字符串字面量传给 helper，静态 grep 不到 id= 那一侧。
HELPER_IDS = set(re.findall(r'\bseg\(\s*"([A-Za-z][\w-]*)"', HTML))
TPL_IDS = bool(TPL_ID.search(HTML))

missing_ids = []
for i, lines in sorted(referenced.items()):
    if i in IDS_IN_HTML or i in IDS_DYNAMIC or i in HELPER_IDS:
        continue
    if any(i.startswith(p + "-") for p in prefixes):
        continue
    # 前 3 个字符和某个前缀型 id 同名（`cfg_` + k）也算解释得通
    if any(i.startswith(p) for p in prefixes):
        continue
    missing_ids.append((i, lines[:3]))

# ---- 可点击但没有处理器 ----
# 只看 <button ...> 里既没有 on* 也没有 type=submit/reset 也没有 data-v（导航按钮走委托）
BTN = re.compile(r"<button\b([^>]*)>", re.S)
bare = []
for m in BTN.finditer(HTML):
    attrs = m.group(1)
    if re.search(r"\bon[a-z]+\s*=", attrs):
        continue
    if re.search(r'type\s*=\s*"(?:submit|reset)"', attrs):
        continue
    if "data-v=" in attrs or "disabled" in attrs or "hidden" in attrs:
        continue
    line = HTML[:m.start()].count("\n") + 1
    bare.append((line, attrs.strip()[:100]))

out = {
    "handlers": len(HANDLERS),
    "defined_functions": len(DEFINED),
    "ids_in_html": len(IDS_IN_HTML),
    "missing_calls": missing_calls,
    "missing_ids": missing_ids,
    "bare_buttons": bare,
}
print(json.dumps(out, ensure_ascii=False, indent=1))
print(f"\n内联事件 {len(HANDLERS)} 个 · JS 函数 {len(DEFINED)} 个 · 缺失函数 {len(missing_calls)}，"
      f"缺失 id {len(missing_ids)}，无处理器按钮 {len(bare)}")
sys.exit(1 if (missing_calls or missing_ids or bare) else 0)
