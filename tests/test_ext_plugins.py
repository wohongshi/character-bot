"""外部插件协议：任何语言写的插件都走同一条 NDJSON 通道。

用 python3 当「被测插件」是实现细节 —— 协议本身与语言无关，
另外几条用例会真的去跑 Node / Rust / C / C++ / Java 的编译产物（缺工具链就跳过）。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bot.ext import ExtError, ExtPlugin, load_spec       # noqa: E402

PASS = FAIL = 0


def check(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}  {detail}")


TMP = Path(tempfile.mkdtemp())

# 一个最小的「任何语言都能照抄」的插件：读行、回 JSON
PLUGIN = textwrap.dedent('''
    import json, sys, time
    for line in sys.stdin:
        try:
            m = json.loads(line)
        except ValueError:
            continue
        op = m.get("op")
        if op == "hello":
            print(json.dumps({"ok": True, "name": "probe", "title": "探针",
                              "commands": [{"name": "echo", "aliases": ["回声"],
                                            "help": "原样回话"},
                                           {"name": "sleep", "help": "故意睡"}],
                              "meta_seen": True}, ensure_ascii=False), flush=True)
        elif op == "bye":
            break
        elif op == "call":
            cmd, text = m.get("cmd"), m.get("text") or ""
            if cmd == "sleep":
                time.sleep(5)
                print(json.dumps({"id": m.get("id"), "ok": True, "reply": "慢死了"}), flush=True)
            elif cmd == "echo":
                who = (m.get("ctx") or {}).get("user_name") or "?"
                print(json.dumps({"id": m.get("id"), "ok": True,
                                  "reply": f"{who} 说：{text}"}, ensure_ascii=False), flush=True)
            else:
                print(json.dumps({"id": m.get("id"), "ok": False,
                                  "error": "没有这个命令"}), flush=True)
''')
(TMP / "probe.py").write_text(PLUGIN, encoding="utf-8")

print("· 握手与调用")
spec = {"id": "probe", "title": "探针", "language": "python", "entry": "probe.py",
        "timeout": 2}
plug = ExtPlugin(spec, TMP)
meta = plug.start()
check("hello 拿到命令表", [c["name"] for c in meta["commands"]] == ["echo", "sleep"],
      str(meta))
res = plug.call("echo", ["甲", "乙"], "你好", {"user_name": "小明"})
check("call 正常返回", res.get("reply") == "小明 说：你好", str(res))
check("调用计数", plug.calls == 1, str(plug.calls))
check("describe 带语言与入口", plug.describe()["language"] == "python")

print("\n· 错误路径")
try:
    plug.call("nope", [], "", {})
    check("未知命令抛错", False)
except ExtError as e:
    check("未知命令抛错", "没有这个命令" in str(e), str(e))
try:
    slow = ExtPlugin({**spec, "timeout": 0.4}, TMP)
    slow.start()
    slow.call("sleep", [], "", {})
    check("超时被掐断", False)
except ExtError as e:
    check("超时被掐断", "超时" in str(e), str(e))
plug.close()
check("close 之后进程没了", plug.describe()["alive"] is False)

print("\n· 规格校验（安全）")
try:
    ExtPlugin({"id": "x", "language": "python", "entry": "../../etc/passwd"}, TMP)
    check("entry 越界被拒", False, "居然接受了")
except ExtError as e:
    check("entry 越界被拒", "越界" in str(e), str(e))
try:
    ExtPlugin({"id": "x", "language": "rust", "entry": "/bin/sh"}, TMP)
    check("绝对路径被拒", False)
except ExtError as e:
    check("绝对路径被拒", "相对路径" in str(e), str(e))
try:
    ExtPlugin({"id": "x", "language": "rust", "entry": "nope.bin"}, TMP)
    check("二进制不存在时报错", False)
except ExtError as e:
    check("二进制不存在时报错", "找不到" in str(e), str(e))

print("\n· run + args（Java 这类要类名而不是路径）")
jspec = {"id": "j", "run": ["python3", "-c",
                            "import sys,json;print(json.dumps({'ok':True,'commands':[]}))"],
         "args": [], "timeout": 3}
jp = ExtPlugin(jspec, TMP)
check("run 原样使用", jp.cmd[0] == "python3", str(jp.cmd))
check("args 追加在 run 后面", jp.cmd[1] == "-c", str(jp.cmd))
jp.close()

print("\n· 真语言（编译产物）")
EXT = ROOT / "bot" / "features" / "ext"
cases = [
    ("node", "uuid.ext.json", "uuid", "uuid", "x", ["node"]),
    ("rust", "textstat.ext.json", "textstat", "统计", "hello", []),
    ("c", "crc.ext.json", "crc", "crc", "hello", []),
    ("cpp", "sort.ext.json", "sort", "排序", "3,1,2", []),
    ("java", "sha.ext.json", "sha", "sha", "hi", ["java"]),
]
for label, spec_file, pid, cmd, text, needs in cases:
    if needs and not shutil.which(needs[0]):
        print(f"  ~ {label}: 没有 {needs[0]}，跳过")
        continue
    sp = EXT / spec_file
    if not sp.exists():
        print(f"  ~ {label}: 缺 {spec_file}，跳过")
        continue
    spec = load_spec(sp)
    try:
        p = ExtPlugin(spec, EXT)
        p.start()
        r = p.call(cmd, [], text, {})
        ok = bool(r.get("reply"))
        check(f"{label} 插件跑通（{pid}）", ok, str(r)[:120])
        p.close()
    except ExtError as e:
        check(f"{label} 插件跑通（{pid}）", False, str(e)[:160])

print("\n· 面板能看到")
html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
check("卡片有语言图标表", "LANG_ICON" in html)
check("抽屉区分外部插件", "外部插件 ·" in html)

print(f"\nPASS {PASS} / {PASS + FAIL}")
sys.exit(1 if FAIL else 0)
