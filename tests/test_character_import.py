"""角色 ZIP 导入：只测校验与路径安全（不落盘、不建索引）。"""
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


def _raises(exc, fn):
    try:
        fn()
    except exc:
        return True
    except Exception:  # noqa: BLE001
        return False
    return False


def zip_bytes(files: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for k, v in files.items():
            z.writestr(k, v)
    return buf.getvalue()


BASE = {
    "character.json": '{"id":"demo","name":"Demo"}',
    "SOUL.md": "你是 Demo。" * 40,
    "theme/theme.json": '{"name":"Demo Theme"}',
    "plugins/a.py": "def register(api):\n    pass\n",
    "kb/a.jsonl": '{"text":"hello"}\n',
}

print("[1] 路径安全")
check("1.1 普通路径", server._zip_rel("a/b.py") == "a/b.py")
check("1.2 拒绝上跳", _raises(server.ImportError_, lambda: server._zip_rel("../x")))
check("1.3 拒绝绝对路径", _raises(server.ImportError_, lambda: server._zip_rel("/etc/passwd")))
check("1.4 去掉 ./", server._zip_rel("./a/./b") == "a/b")

print("[2] 校验")
check("2.1 非 zip", _raises(server.ImportError_, lambda: server._import_character_zip(b"nope")))
check("2.2 缺 SOUL", _raises(server.ImportError_,
                          lambda: server._import_character_zip(zip_bytes({k: v for k, v in BASE.items() if k != "SOUL.md"}))))
check("2.3 缺 theme", _raises(server.ImportError_,
                          lambda: server._import_character_zip(zip_bytes({k: v for k, v in BASE.items() if not k.startswith("theme/")}))))
check("2.4 缺插件", _raises(server.ImportError_,
                          lambda: server._import_character_zip(zip_bytes({k: v for k, v in BASE.items() if not k.startswith("plugins/")}))))
check("2.5 缺语料", _raises(server.ImportError_,
                          lambda: server._import_character_zip(zip_bytes({k: v for k, v in BASE.items() if not k.startswith("kb/")}))))
check("2.6 非法 id", _raises(server.ImportError_,
                           lambda: server._import_character_zip(zip_bytes({**BASE, "character.json": '{"id":"A B","name":"x"}'}))))
check("2.7 不支持的文件类型", _raises(server.ImportError_,
                                  lambda: server._import_character_zip(zip_bytes({**BASE, "evil.sh": "rm -rf /"}))))

print(f"\nPASS {PASS} / {PASS + FAIL}")
sys.exit(1 if FAIL else 0)

