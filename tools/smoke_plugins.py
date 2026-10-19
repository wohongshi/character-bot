"""插件冒烟：每条参数是一整行「命令 参数…」，跑一遍打印回复。

用法：
  python3 tools/smoke_plugins.py "b64e 你好" "密码 20 强" "变形 反转 你好呀"

状态走临时目录（KLEE_STATE_DIR），不碰生产。
"""
from __future__ import annotations

import asyncio
import inspect
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


async def _await_any(value):
    """把任意 awaitable 包成协程：`asyncio.run()` 只接受 coroutine 对象。"""
    return await value


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
    all_bad = [p for p in mgr.snapshot().get("plugins", []) if p.get("error")]
    # 外部插件（ext/*.ext.json）的失败多半是环境问题：干净 checkout 下
    # `bot/features/ext/tools/*.bin` 没编译（.gitignore），crc/sort/textstat 都会报错。
    # 那不算「代码坏了」，只警告；纯 Python 插件的加载失败才算失败。
    bad = [p for p in all_bad if not p.get("external")]
    if all_bad:
        print("!! 加载失败的插件：", [(p["name"], p["error"]) for p in all_bad])

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
            # 和 FeatureManager 一样用 isawaitable：Future / 自定义 awaitable 也算
            if inspect.isawaitable(res):
                res = asyncio.run(_await_any(res))
            # handle_command 支持「直接 return 一段话」的处理器（lore / 一些老插件
            # 就是 `return "…"`）。只收 ctx.reply 的话，这些命令会被误报成「没有回复」。
            if isinstance(res, str) and res.strip() and not ad.sent:
                ad.sent.append(res.strip())
        except Exception as e:                                   # noqa: BLE001
            print(f"✗ .{line} 抛异常：{type(e).__name__}: {e}")
            rc = 1
            continue
        print(f"── .{line}")
        print("\n".join(ad.sent) if ad.sent else "（没有回复）")
        print()
    shutil.rmtree(TMP, ignore_errors=True)
    # 插件装载失败必须算失败：CI 拿退出码判断，以前只打印一行警告就返回 0，
    # 「160 个插件里有 3 个 import 崩了」会被当成通过 —— 而冒烟最该报红的就是它。
    return 1 if bad else rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
