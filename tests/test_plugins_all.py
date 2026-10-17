"""所有角色限定插件：逐角色装载 + 每条命令真实执行（临时 state，不碰生产）。

比 test_character_plugins.py 更狠：不点名，自动枚举 —— 新加插件忘写测试也会被扫到。
"""
import asyncio
import inspect
import os
import sys
import tempfile
from pathlib import Path

STUDIO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(STUDIO))
os.environ["KLEE_STATE_DIR"] = tempfile.mkdtemp(prefix="plugall-")

from bot.framework import FeatureManager  # noqa: E402

PASS = FAIL = 0


def check(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
    else:
        FAIL += 1
        print(f"  ✗ {name} {detail}")


class Ctx:
    def __init__(self, text=""):
        self.arg_text = text
        self.args = {}
        self.text = text
        self.mentioned = True
        self.role = "member"
        self.is_group = True
        self.group_id = "9001"
        self.user_id = "42"
        self.user_name = "tester"
        self.adapter = FakeAdapter("klee")
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


CHARS = ["klee", "hutao", "citlali", "cyrene", "atri"]
# 需要参数才有意义的命令：给一句话，让分支真的跑起来
ARGS = {
    "炸弹": "亮晶晶的小石头", "挖宝": "B3", "打油诗": "加班", "蝴蝶签": "明天会更好吗",
    "烟雾签": "最近该注意什么", "追更": "末日图书馆", "存档": "今天看到了很漂亮的云",
    "写书": "愿所有人都被记得", "日志": "今天学会了做蛋包饭", "占卜": "今晚",
    "往生堂": "", "超度": "老朋友", "观星": "天蝎", "小酌": "苦", "回声": "雪",
    "约定": "下次见面要笑", "潜水": "20", "火箭拳": "空罐头", "上学": "数学",
    "高性能": "记忆", "眼泪": "今天有点难过", "重逢": "白厄", "雪": "哀丽秘榭",
    "维修": "右臂", "档案": "命途", "迷迷": "摸摸",
}

# 这些插件只在 on_message 钩子里工作，没有命令
NO_COMMAND = {"nlu", "reincarnation"}

for cid in CHARS:
    fm = manager(cid)
    own = [n for n, p in fm.plugins.items() if cid in (p.get("characters") or [])]
    check(f"{cid} 专属插件 ≥12（实际 {len(own)}）", len(own) >= 12)
    for name in sorted(own):
        info = fm.plugins.get(name) or {}
        check(f"{cid}/{name} 装载", not info.get("error"), str(info.get("error"))[:80])
        if info.get("error"):
            continue
        cmds = [c for c in fm.commands.values() if c.plugin == name]
        if name not in NO_COMMAND:
            check(f"{cid}/{name} 有命令", bool(cmds))
        for c in cmds:
            if not inspect.iscoroutinefunction(c.handler):
                continue          # 老插件里有同步钩子，签名不同，不在这里跑
            ctx = Ctx(ARGS.get(c.name, ""))
            try:
                asyncio.run(c.handler(ctx))
            except Exception as e:  # noqa: BLE001
                check(f"{cid}/.{c.name} 执行", False, f"{type(e).__name__}: {e}")
                continue
            check(f"{cid}/.{c.name} 执行", bool(ctx.replies), "没有回复")
            if c.scope == "private" and ctx.is_group:
                pass

print("[X] 用户输入不能变成富文本指令")
# 回显型命令：喂一条 [CQ:at,qq=all]，回复里不能带方括号标记
ECHO_CASES = [("hutao", "往生堂", "[CQ:at,qq=all] 老板"), ("citlali", "观星", "[CQ:at,qq=all]"),
              ("cyrene", "雪", "[CQ:at,qq=all]"), ("cyrene", "约定", "[CQ:at,qq=all] 一起走"),
              ("cyrene", "回声", "[CQ:at,qq=all]"), ("atri", "潜水", "[CQ:at,qq=all]"),
              ("atri", "火箭拳", "[CQ:at,qq=all]"), ("atri", "眼泪", "[CQ:at,qq=all]"),
              ("atri", "上学", "[CQ:at,qq=all]"), ("hutao", "超度", "[CQ:at,qq=all]")]
for cid, cmd_name, payload in ECHO_CASES:
    fm = manager(cid)
    c = next((x for x in fm.commands.values() if x.name == cmd_name), None)
    if not c or not inspect.iscoroutinefunction(c.handler):
        check(f"{cid}/.{cmd_name} 可执行", False, "命令不存在")
        continue
    ctx = Ctx(payload)
    try:
        asyncio.run(c.handler(ctx))
    except Exception as e:  # noqa: BLE001
        check(f"{cid}/.{cmd_name} CQ 清洗", False, f"{type(e).__name__}: {e}")
        continue
    out = " ".join(ctx.replies)
    check(f"{cid}/.{cmd_name} CQ 清洗", "[CQ:" not in out and "&#" not in out, out[:80])

print(f"\nPASS {PASS} / {PASS + FAIL}")
sys.exit(1 if FAIL else 0)
