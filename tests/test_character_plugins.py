"""新增的 13 个角色限定插件：逐角色装载 + 命令真实执行（临时 state，不碰生产）。"""
import asyncio
import os
import sys
import tempfile
from pathlib import Path

STUDIO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(STUDIO))
os.environ["KLEE_STATE_DIR"] = tempfile.mkdtemp(prefix="plugtest-")

from bot.framework import FeatureManager  # noqa: E402

PASS = FAIL = 0


def check(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name} {detail}")


class Ctx:
    def __init__(self, text=""):
        self.arg_text = text
        self.is_group = True
        self.group_id = "9001"
        self.user_id = "42"
        self.user_name = "tester"
        self.replies = []

    async def reply(self, text, **kw):
        self.replies.append(str(text))


class FakeAdapter:
    def __init__(self, cid):
        self.features_dir = str(STUDIO / "bot" / "features")
        self.feature_prefix = "."
        self.active_character = cid
        self.active_character_info = {}
        self.owner_id = "42"


def manager(cid):
    fm = FeatureManager(FakeAdapter(cid))
    fm.active_character = cid
    fm.load()
    return fm


def run(cid, names, args=None):
    fm = manager(cid)
    for name in names:
        info = fm.plugins.get(name)
        check(f"{cid}/{name} 装载", bool(info) and not info.get("error"), str((info or {}).get("error"))[:80])
        if not info or info.get("error"):
            continue
        cmds = [c for c in fm.commands.values() if c.plugin == name]
        check(f"{cid}/{name} 有命令", bool(cmds))
        for c in cmds:
            ctx = Ctx((args or {}).get(c.name, ""))
            try:
                asyncio.run(c.handler(ctx))
            except Exception as e:  # noqa: BLE001
                check(f"{cid}/.{c.name} 执行", False, f"{type(e).__name__}: {e}")
                continue
            check(f"{cid}/.{c.name} 执行", bool(ctx.replies), "没有回复")
            if ctx.replies:
                print("      →", ctx.replies[0].replace("\n", " ")[:80])


print("[1] 可莉")
run("klee", ["bomb", "treasure"], {"炸弹": "亮晶晶的小石头", "挖宝": "B3"})
print("[2] 胡桃")
run("hutao", ["coupon", "poem", "butterfly"], {"打油诗": "加班", "蝴蝶签": "明天会更好吗"})
print("[3] 茜特菈莉")
run("citlali", ["smoke", "novel"], {"烟雾签": "最近该注意什么", "追更": "末日图书馆"})
print("[4] 昔莲")
run("cyrene", ["memory", "book", "reincarnation"],
    {"存档": "今天看到了很漂亮的云", "写书": "愿所有人都被记得", "轮回": ""})
print("[5] 亚托莉")
run("atri", ["diagnose", "logbook", "countdown"],
    {"日志": "今天学会了做蛋包饭", "倒计时": ""})

print("[6] 状态持久化")
fm = manager("cyrene")
store = fm.commands.get("存档") or next(c for c in fm.commands.values() if c.name == "存档")
recall = fm.commands.get("记忆") or next(c for c in fm.commands.values() if c.name == "记忆")
ctx = Ctx("第二条记忆")
asyncio.run(store.handler(ctx))
ctx2 = Ctx("")
asyncio.run(recall.handler(ctx2))
check("存档后能读回", "第二条记忆" in (ctx2.replies[0] if ctx2.replies else ""), str(ctx2.replies)[:100])

print(f"\nPASS {PASS} / {PASS + FAIL}")
sys.exit(1 if FAIL else 0)
