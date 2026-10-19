#!/usr/bin/env python3
"""插件包：导出（单插件 / 全部备份）与卸载。

导出必须满足两件事：
1. **能被同一个导入入口装回去** —— 形状要和 `server._plugin_zip_files` 的约定一致
   （顶层只有 `<名字>.py`，`plugin.json` / `README.md` 会被忽略）；
2. 包里带得出「这个插件是干什么的」——命令清单从源码 AST 里取，**不执行插件**。
"""
from __future__ import annotations

import io
import json
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PASS, FAIL = [], []
BASE = "http://127.0.0.1:8798"
HDR = {"X-Klee-Studio": "1"}
FEATURES = ROOT / "bot" / "features"

SAMPLE = '''"""测试用插件。"""
from __future__ import annotations

NAME = "包测试"
NAME_EN = "Pack probe"
DESCRIPTION = "验证导出格式。"
VERSION = "2.1"

def register(api):
    async def hello(ctx):
        await ctx.reply("hi")

    api.on_command("包测试", hello, aliases=("packtest", "pt"), cooldown=1)
'''


def check(name, ok, extra=""):
    (PASS if ok else FAIL).append(name)
    print(f"  {'✓' if ok else '✗'} {name}" + (f"   [{extra}]" if extra and not ok else ""))


def section(t):
    print(f"\n== {t}")


def main():
    from bot import pluginpack as P

    section("1. 读源码里的元信息（不执行）")
    m = P.module_meta(SAMPLE)
    check("1.1 取到 NAME", m["NAME"] == "包测试", str(m.get("NAME")))
    check("1.2 取到 VERSION", m["VERSION"] == "2.1", str(m.get("VERSION")))
    check("1.3 命令清单", m["commands"] == ["包测试"], str(m["commands"]))
    check("1.4 别名清单", m["aliases"] == ["packtest", "pt"], str(m["aliases"]))
    try:
        P.module_meta("def broken(:\n")
        check("1.5 语法错误报人话", False)
    except P.PackError as e:
        check("1.5 语法错误报人话", "第 1 行" in str(e), str(e))

    section("2. 导出单个插件")
    blob, fname = P.pack(FEATURES, "b64")
    z = zipfile.ZipFile(io.BytesIO(blob))
    names = z.namelist()
    check("2.1 文件名", fname == "b64.zip", fname)
    check("2.2 顶层有 b64.py", "b64.py" in names, str(names))
    check("2.3 带说明文件", {"plugin.json", "README.md"} <= set(names), str(names))
    man = json.loads(z.read("plugin.json").decode())
    check("2.4 清单里有命令", man["commands"] == ["b64e", "b64d"], str(man["commands"]))
    check("2.5 README 写命令", ".b64e" in z.read("README.md").decode())
    check("2.6 源文件内容一致",
          z.read("b64.py") == (FEATURES / "b64.py").read_bytes())

    section("3. 能被导入入口装回去（格式契约）")
    import server
    files = server._plugin_zip_files(blob)
    check("3.1 导入侧解析出一个 .py", list(files) == ["b64.py"], str(list(files)))
    blob2, fname2 = P.pack(FEATURES, ["b64", "todo", "mood"])
    files2 = server._plugin_zip_files(blob2)
    check("3.2 多插件包也能被导入侧吃下",
          set(files2) == {"b64.py", "todo.py", "mood.py"}, str(list(files2)))
    check("3.3 多插件包名", fname2 == "klee-plugins.zip", fname2)

    section("4. 导出全部 = 源码备份")
    back, bname = P.pack_backup(FEATURES)
    z2 = zipfile.ZipFile(io.BytesIO(back))
    n2 = z2.namelist()
    pys = [x for x in n2 if x.endswith(".py")]
    check("4.1 覆盖了所有插件", len(pys) >= 100, str(len(pys)))
    check("4.2 没有下划线开头的内部模块",
          not any(x.startswith("_") for x in pys), str([x for x in pys if x.startswith("_")][:3]))
    check("4.3 备份文件名带日期", bname.startswith("klee-plugins-") and bname.endswith(".zip"), bname)
    idx = json.loads(z2.read("plugin.json").decode())
    check("4.4 清单条数对得上", idx["count"] == len(pys), f"{idx['count']} vs {len(pys)}")
    check("4.5 README 说明这不是一键安装包", "不是" in z2.read("README.md").decode())

    section("4b. 打包上限要和导入入口对齐")
    # 导入入口把 plugin.json / README.md 也算进 20 个条目里，所以自己只能放 18 个 .py
    names = sorted(f.stem for f in FEATURES.glob("*.py") if not f.name.startswith("_"))
    blob18, _ = P.pack(FEATURES, names[:18])
    files18 = server._plugin_zip_files(blob18)
    check("4b.1 18 个插件打得出来且能被导入", len(files18) == 18, str(len(files18)))
    try:
        P.pack(FEATURES, names[:19])
        check("4b.2 19 个插件被自己挡住", False)
    except P.PackError as e:
        check("4b.2 19 个插件被自己挡住", "最多打包 18" in str(e), str(e))

    section("5. 拒绝坏输入")
    for name, label in (("Bad-Name", "大小写/横线"), ("a", "太短"), ("_x", "下划线开头"),
                        ("", "空名字")):
        try:
            P.pack(FEATURES, name)
            check(f"5.x 拒绝{label}", False)
        except P.PackError:
            check(f"5.x 拒绝{label}", True)
    try:
        P.pack(FEATURES, "zzz_not_exist")
        check("5.5 拒绝不存在的插件", False)
    except P.PackError as e:
        check("5.5 拒绝不存在的插件", "没有这个插件" in str(e), str(e))

    section("6. 卸载 = 移到回收站")
    tmp = Path(tempfile.mkdtemp(prefix="klee-pack-"))
    (tmp / "feat").mkdir()
    (tmp / "feat" / "b64.py").write_text(SAMPLE, encoding="utf-8")
    res = P.uninstall(tmp / "feat", "b64", tmp / "trash")
    check("6.1 原文件不见了", not (tmp / "feat" / "b64.py").exists())
    check("6.2 回收站里有", Path(res["moved_to"]).exists(), res["moved_to"])
    check("6.3 内容没丢", SAMPLE in Path(res["moved_to"]).read_text(encoding="utf-8"))
    try:
        P.uninstall(tmp / "feat", "b64", tmp / "trash")
        check("6.4 再卸一次要报错", False)
    except P.PackError:
        check("6.4 再卸一次要报错", True)

    section("7. 活服务：导出接口")
    def raw(url, method="GET", body=None, ctype=None):
        req = urllib.request.Request(url, headers=dict(HDR), method=method)
        if ctype:
            req.add_header("Content-Type", ctype)
        data = json.dumps(body).encode() if body is not None else None
        try:
            with urllib.request.urlopen(req, data=data, timeout=30) as r:
                return r.status, r.read(), dict(r.headers)
        except urllib.error.HTTPError as e:
            return e.code, e.read(), dict(e.headers)

    try:
        code, blob3, heads = raw(BASE + "/api/plugins/export?name=b64")
        check("7.1 200", code == 200, str(code))
        check("7.2 类型是 zip", "zip" in heads.get("Content-Type", ""), heads.get("Content-Type"))
        check("7.3 带下载文件名", "b64.zip" in heads.get("Content-Disposition", ""),
              heads.get("Content-Disposition"))
        check("7.4 下回来能打开", "b64.py" in zipfile.ZipFile(io.BytesIO(blob3)).namelist())
        code, _, _ = raw(BASE + "/api/plugins/export?name=zzz_not_exist")
        check("7.5 不存在的插件 400", code == 400, str(code))
        code, allblob, _ = raw(BASE + "/api/plugins/export")
        check("7.6 全部备份 200", code == 200 and len(allblob) > 10000, str(len(allblob)))

        # 真卸一个探针插件：装进 features → 调卸载 → 文件进回收站 → 清干净
        probe = FEATURES / "zz_pack_probe.py"
        probe.write_text(SAMPLE.replace("包测试", "packprobe")
                         .replace('"packtest", "pt"', '"packprobe2"'), encoding="utf-8")
        try:
            code, out, _ = raw(BASE + "/api/plugins/uninstall", "POST",
                              {"name": "zz_pack_probe"}, "application/json")
            ok = code == 200 and json.loads(out).get("ok")
            check("7.7 卸载接口 200", ok, f"{code} {out[:80]}")
            check("7.8 文件已从插件目录移走", not probe.exists())
            trash = ROOT / "state" / "trash"
            moved = list(trash.glob("zz_pack_probe.*.py")) if trash.is_dir() else []
            check("7.9 回收站里有这份文件", bool(moved), str(trash))
            cfg = json.loads((FEATURES / "config.json").read_text(encoding="utf-8"))
            check("7.10 已从启用名单摘掉", "zz_pack_probe" not in (cfg.get("enabled") or []),
                  str([x for x in (cfg.get("enabled") or []) if "zz_" in x]))
            for f in moved:
                f.unlink()
        finally:
            if probe.exists():
                probe.unlink()
            # 双保险：探针绝不能留在配置里
            try:
                cfg = json.loads((FEATURES / "config.json").read_text(encoding="utf-8"))
                if "zz_pack_probe" in (cfg.get("enabled") or []):
                    cfg["enabled"] = [x for x in cfg["enabled"] if x != "zz_pack_probe"]
                    (FEATURES / "config.json").write_text(
                        json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            except (OSError, ValueError):
                pass
    except (urllib.error.URLError, OSError) as e:
        print(f"  · 服务没起来，跳过（{type(e).__name__}）")

    print(f"\nPASS {len(PASS)} / {len(PASS) + len(FAIL)}")
    if FAIL:
        print("失败：" + "、".join(FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
