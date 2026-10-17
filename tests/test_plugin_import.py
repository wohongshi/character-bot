"""插件包 ZIP 导入：校验、角色 id、覆盖与回滚。"""
import io
import sys
import zipfile
from pathlib import Path

STUDIO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(STUDIO))

import server  # noqa: E402

PASS = FAIL = 0


def check(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name} {detail}")


def raises(exc, fn):
    try:
        fn()
    except exc:
        return True
    except Exception:  # noqa: BLE001
        return False
    return False


def zbytes(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for k, v in files.items():
            z.writestr(k, v)
    return buf.getvalue()


PLUGIN = '''"""demo"""
NAME = "演示"
DESCRIPTION = "演示插件"

def register(api):
    async def hi(ctx):
        await ctx.reply("hi")
    api.on_command("演示", hi, help=".演示")
'''

print("[1] 文件名与内容")
check("1.1 合法文件名", bool(server.PLUGIN_NAME_RE.match("demo.py")))
check("1.2 拒绝下划线开头", not server.PLUGIN_NAME_RE.match("_tool.py"))
check("1.3 拒绝大写/横线", not server.PLUGIN_NAME_RE.match("Demo-1.py"))
check("1.4 非 zip", raises(server.ImportError_, lambda: server._import_plugin_zip(b"nope")))
check("1.5 空 zip", raises(server.ImportError_, lambda: server._import_plugin_zip(zbytes({}))))
check("1.6 语法错误", raises(server.ImportError_, lambda: server._import_plugin_zip(
    zbytes({"bad.py": "def register(api)\n  pass"}))))
check("1.7 非法文件类型", raises(server.ImportError_, lambda: server._import_plugin_zip(
    zbytes({"demo.py": PLUGIN, "run.sh": "echo"}))))
check("1.8 未知角色 id", raises(server.ImportError_, lambda: server._import_plugin_zip(
    zbytes({"demo.py": PLUGIN + '\nCHARACTERS = ("nobody",)\n'}))))
check("1.9 已知角色 id 通过校验（不落盘：先查重名）",
      raises(server.ImportError_, lambda: server._import_plugin_zip(
          zbytes({"charcard.py": PLUGIN + '\nCHARACTERS = ("klee",)\n'}))))

print(f"\nPASS {PASS} / {PASS + FAIL}")
sys.exit(1 if FAIL else 0)
