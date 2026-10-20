#!/usr/bin/env python3
"""面板里那个纯函数 `pickSel()` —— 换供应商时的「该选哪个」。

为什么单独测：这个函数决定「用户选过的模型会不会在换供应商时被悄悄抹掉」，
而它的 bug 表现是**静默的**（下拉回到「跟聊天同一个」，下次保存就把钉住的模型
清掉）。逻辑在 index.html 的内联 JS 里，这里把它抠出来丢给 node 跑。

规则：
  1. 当前下拉的值合法 → 用它（用户刚选的）；
  2. 用户没动过 → 用服务端保存的值；
  3. 用户显式选了空 → 空（他要「跟聊天同一个 / 不启用」）；
  4. 用户选的在新供应商里不存在（中途切过供应商）→ 退回服务端的值，
     而不是清空。
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML = ROOT / "static" / "index.html"

PASS, FAIL = [], []


def check(name, ok, extra=""):
    (PASS if ok else FAIL).append(name)
    print(f"  {'✓' if ok else '✗'} {name}" + (f"   [{extra}]" if extra and not ok else ""))


CASES = [
    # now, sel, saved, models, 期望
    ("当前值合法", "m2", "m2", "m1", ["m1", "m2"], "m2"),
    ("没动过 → 用服务端值", "", None, "m1", ["m1"], "m1"),
    ("没动过且服务端值不在清单 → 空", "", None, "mX", ["m1"], ""),
    ("显式选空 → 空（要跟聊天同一个）", "", "", "m1", ["m1"], ""),
    ("显式选了别家模型 → 退回服务端值", "", "mB", "m1", ["m1"], "m1"),
    ("显式选了这家有的模型 → 用它", "", "m2", "m1", ["m1", "m2"], "m2"),
    ("A→B→A：现在空、选过 m2、服务端 m1、A 有 m2", "", "m2", "m1", ["m1", "m2"], "m2"),
    ("当前值不在清单但显式值在", "m9", "m2", "m1", ["m1", "m2"], "m2"),
    ("全都对不上 → 空", "m9", "m8", "m7", ["m1"], ""),
    ("服务端值为空且没动过 → 空", "", None, "", ["m1"], ""),
]


def main():
    # 和仓库里其它吃 node 的用例保持一致：没装就跳过，别让整轮 rc=1
    if not shutil.which("node"):
        print("SKIP（没有 node，跳过 pickSel 检查）")
        return 0
    if not HTML.exists():
        print("找不到 static/index.html")
        return 1
    src = HTML.read_text(encoding="utf-8")
    m = re.search(r"function pickSel\(now, sel, saved, ms\)\{.*?\n\}", src, re.S)
    if not m:
        print("✗ 在 index.html 里找不到 pickSel()（改名了？同步改这个测试）")
        return 1
    fn = m.group(0)

    script = fn + "\n" + "\n".join(
        f"console.log(JSON.stringify(pickSel({json.dumps(n)}, {json.dumps(s)}, "
        f"{json.dumps(v)}, {json.dumps(ms)})));"
        for _, n, s, v, ms, _ in CASES)
    tmp = Path(tempfile.mkdtemp(prefix="klee-picksel-")) / "t.js"
    tmp.write_text(script, encoding="utf-8")
    r = subprocess.run(["node", str(tmp)], capture_output=True, text=True)
    if r.returncode != 0:
        print("node 跑挂了：", r.stderr[:400])
        return 1
    got = [json.loads(x) for x in r.stdout.splitlines() if x.strip()]
    if len(got) != len(CASES):
        print(f"期望 {len(CASES)} 行输出，实际 {len(got)}")
        return 1

    print("== pickSel()")
    for (name, *_, want), g in zip(CASES, got):
        check(name, g == want, f"想要 {want!r}，实际 {g!r}")

    print()
    if FAIL:
        print(f"FAIL {len(FAIL)} / {len(PASS) + len(FAIL)}")
        return 1
    print(f"PASS {len(PASS)} / {len(PASS)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
