"""插件冒烟：每条参数是一整行「命令 参数…」，跑一遍打印回复。

用法：
  python3 tools/smoke_plugins.py "b64e 你好" "密码 20 强" "变形 反转 你好呀"

状态走临时目录（KLEE_STATE_DIR），不碰生产。
"""
from __future__ import annotations

import asyncio
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="klee-smoke-")
os.environ["KLEE_STATE_DIR"] = TMP


class FakeAdapter:
    platform = "web"
    owner_id = "1"
    CAPABILITIES = {"text": True, "image": False}

    def __init__(self):
        self.sent = []

    async def send(self, chat_id, text, **kw):
        self.sent.append(text)

        class R:
            success = True
        return R()

    def chat_id_of(self, is_group, group_id, user_id):
        return "private:" + str(user_id)


def build():
    from bot.framework import FeatureManager

    class A:
        features_dir = str(ROOT / "bot" / "features")
        feature_prefix = "."
        owner_id = "1"
        active_character = "klee"

        def log(self, *a, **k):
            pass

    mgr = FeatureManager(A())
    mgr.load()
    return mgr


def main(argv) -> int:
    from bot.framework import FeatureContext

    mgr = build()
    bad = [p for p in mgr.snapshot().get("plugins", []) if p.get("error")]
    if bad:
        print("!! 加载失败的插件：", [(p["name"], p["error"]) for p in bad])

    rc = 0
    for line in argv:
        line = line.strip()
        if not line:
            continue
        token, _, rest = line.partition(" ")
        name = token.lstrip("./／")
        cmd = mgr.commands.get(name.lower())
        if not cmd:
            print(f"✗ 没有命令 .{name}")
            rc = 1
            continue
        ad = FakeAdapter()
        ctx = FeatureContext(adapter=ad, text="." + line, is_group=False, group_id="",
                             user_id="1", user_name="主人", message_id="",
                             mentioned=True, role="owner")
        ctx.command = cmd.name
        ctx.args = rest.split()
        try:
            res = cmd.handler(ctx)
            if asyncio.iscoroutine(res):
                asyncio.run(res)
        except Exception as e:                                   # noqa: BLE001
            print(f"✗ .{line} 抛异常：{type(e).__name__}: {e}")
            rc = 1
            continue
        print(f"── .{line}")
        print("\n".join(ad.sent) if ad.sent else "（没有回复）")
        print()
    shutil.rmtree(TMP, ignore_errors=True)
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
