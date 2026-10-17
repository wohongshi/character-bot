#!/usr/bin/env python3
"""主题插件：发现 / 校验 / CSS 生成 / 角色映射。"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

STUDIO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(STUDIO))

from bot import themes as T                      # noqa: E402
from bot.characters import CHARACTERS            # noqa: E402

PASS, FAIL = [], []


def check(name, ok, extra=""):
    (PASS if ok else FAIL).append(name)
    print(f"  {'✓' if ok else '✗'} {name}" + (f"  [{extra}]" if extra and not ok else ""))


print("[1] 发现与元信息")
found = T.discover()
ids = set(found)
check("1.1 五个主题都被发现", ids == {"klee", "hutao", "citlali", "cyrene", "atri"}, str(ids))
check("1.2 每个主题都有名字/版本", all(t["name"] and t["version"] for t in found.values()))
check("1.3 每个主题至少 10 个变量", all(len(t["tokens"]) >= 10 for t in found.values()),
      str({k: len(v["tokens"]) for k, v in found.items()}))
check("1.4 theme.css 存在", all(t["extra_css"] for t in found.values()),
      str([k for k, v in found.items() if not v["extra_css"]]))
check("1.5 角色映射到存在的主题",
      all(c.get("theme") in found for c in CHARACTERS),
      str([(c["id"], c.get("theme")) for c in CHARACTERS if c.get("theme") not in found]))

print("[2] CSS 生成")
for cid in ("klee", "hutao", "citlali", "cyrene", "atri"):
    css = T.css_for(cid)
    check(f"2 {cid} 选择器/变量正确",
          f'body[data-theme="{cid}"]' in css and "--bg:" in css and "--font-cute:" in css,
          css[:120])
check("2.6 不存在的主题 → 回退默认", 'data-theme="klee"' in T.css_for("nope"))
check("2.7 CSS 不含 script/url()", all(
    "url(" not in T.css_for(c) and "<script" not in T.css_for(c)
    for c in ("klee", "hutao", "citlali", "cyrene", "atri")))
check("2.8 每套 CSS 互不串味（只含自己的选择器）",
      all(f'data-theme="{other}"' not in T.css_for(cid)
          for cid in ("klee", "hutao", "citlali", "cyrene", "atri")
          for other in ("klee", "hutao", "citlali", "cyrene", "atri") if other != cid))

print("[3] 校验：坏插件不生效")
TMP = Path(tempfile.gettempdir()) / "klee_themes_test"
if TMP.exists():
    shutil.rmtree(TMP)
(TMP / "bad").mkdir(parents=True)
(TMP / "bad" / "theme.json").write_text(json.dumps({
    "id": "../evil", "name": "坏", "tokens": {"--bg": "red; } body{display:none}"}}),
    encoding="utf-8")
(TMP / "evil2").mkdir()
(TMP / "evil2" / "theme.json").write_text(json.dumps({
    "id": "evil2", "name": "坏2",
    "tokens": {"--bg": "url(http://x/y)", "--nope": "#fff", "--ink": "ok"}}),
    encoding="utf-8")
old_dir = T.THEME_DIR
try:
    T.THEME_DIR = TMP
    bad = T.discover()
    check("3.1 目录名与 id 不一致 → 拒绝", "bad" not in bad, str(list(bad)))
    check("3.2 非法值被丢弃（url/分号）", "--bg" not in bad.get("evil2", {}).get("tokens", {}),
          str(bad.get("evil2", {}).get("tokens")))
    check("3.3 白名单外的变量被丢弃", "--nope" not in bad["evil2"]["tokens"])
    check("3.4 合法变量保留", bad["evil2"]["tokens"].get("--ink") == "ok")
    check("3.5 目录里没有默认主题时回退空主题不炸",
          T.get("nothing").get("id") in ("klee", "evil2"), str(T.get("nothing")))
finally:
    T.THEME_DIR = old_dir

print("[4] 列表接口形状")
items = T.list_themes()
check("4.1 五条 + 必要字段",
      len(items) == 5 and all({"id", "name", "tokens", "source", "has_css"} <= set(i) for i in items))
check("4.2 不含 extra_css 全文（省流量）", all("extra_css" not in i for i in items))

print()
print(f"PASS {len(PASS)} / {len(PASS) + len(FAIL)}")
if FAIL:
    print("FAILED:", FAIL)
    sys.exit(1)
