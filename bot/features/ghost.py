"""胡桃 · 幽灵委托：.幽灵、.收留 [名字]

替檐下不肯走的游魂记一笔：名字、日子，还有那件没办完的小事。
"""
import random
from datetime import datetime

from _plugstore import chat_key, db_get, db_update, safe_text, state_path

NAME = "幽灵委托"
DESCRIPTION = "替没人认领的游魂记一笔：.幽灵、.收留 [名字]"
VERSION = "1.0"
CHARACTERS = ("hutao",)

FILE = state_path("hutao_ghost")
LIMIT = 6

GHOSTS = [
    "檐下打伞的书生",
    "总在找鞋的小孩",
    "记得自己姓但忘了名的老太太",
    "桥头等船的货郎",
    "抱着空坛子的酒鬼",
    "一直数台阶的更夫",
    "袖口没缝完的绣娘",
    "说要去赶考的少年",
    "蹲在墙根的老木匠",
    "提灯找路的小丫鬟",
]
WISHES = [
    "想吃一口桂花糕",
    "想看一次雪",
    "想有人念对名字",
    "想听一句「回来了」",
    "想把袖口缝完",
    "想再晒一次太阳",
    "想有人把灯留着",
    "想摸一摸自家的猫",
    "想把这双鞋还回去",
    "想听巷口的叫卖",
]


def _rows(cur):
    """坏值一律当空册子，别让一条脏数据把整本账卡住。"""
    return [r for r in (cur if isinstance(cur, list) else []) if isinstance(r, dict)]


def _grid(rows):
    """两位一行：六位也能压进三行，不然一条消息要翻半天。"""
    cells = [
        "·{n}｜{d}｜{w}".format(
            n=str(r.get("n") or "无名")[:10],
            d=str(r.get("d") or "--"),
            w=str(r.get("w") or "没说完")[:8],
        )
        for r in rows
    ]
    return ["  ".join(cells[i:i + 2]) for i in range(0, len(cells), 2)]


def register(api):
    async def take_in(ctx):
        arg = safe_text(ctx.arg_text, 10)
        name = arg or random.choice(GHOSTS)
        wish = random.choice(WISHES)
        day = datetime.now().strftime("%m-%d")
        state = {"dup": False, "old_wish": "", "dropped": ""}

        def _add(cur):
            rows = _rows(cur)
            for r in rows:
                if r.get("n") == name:
                    state["dup"] = True
                    state["old_wish"] = str(r.get("w") or wish)
                    return rows
            if len(rows) >= LIMIT:
                state["dropped"] = str(rows.pop(0).get("n") or "那位")
            rows.append({"n": name, "w": wish, "d": day})
            return rows

        db_update(FILE, chat_key(ctx), _add, default=[])

        if state["dup"]:
            await ctx.reply(
                f"🏮 「{name}」已经在册上了。\n"
                f"遗愿：{state['old_wish']}。\n"
                "一件事记两遍，那边会以为咱们催得急。\n"
                "要改遗愿，先说一声，本堂主划了重写。"
            )
            return

        lines = [
            "🏮 收留了。",
            f"「{name}」，遗愿：{wish}。",
            f"记在册子上，{day}。",
        ]
        if state["dropped"]:
            lines.append(f"（册子满了，最早收的「{state['dropped']}」本堂主先送走了——他等得够久了。）")
        else:
            lines.append("客官放心，本堂主记性好，答应过的事，一笔一笔都忘不了。")
            lines.append("等哪天真办成了，本堂主再替他把这一笔划掉。")
        await ctx.reply("\n".join(lines))

    async def roster(ctx):
        rows = _rows(db_get(FILE, chat_key(ctx), []))
        if not rows:
            await ctx.reply(
                "🏮 册子还空着。\n"
                "客官要是路上遇见谁在檐下站着不走，就替本堂主问一句：还有什么没办完的？\n"
                "记下了名字，说「.收留 名字」，本堂主替他记着。"
            )
            return

        head = f"🏮 本堂主册子上这 {len(rows)} 位："
        body = _grid(rows)
        tail = "——小事最难，客官说是不是？"
        extra = "客官要是遇见谁站着不走，替本堂主问一句：还有什么没办完的？"

        lines = [head] + body + [tail, extra]
        if len("\n".join(lines)) > 200:
            lines = [head] + body + [tail]
        if len("\n".join(lines)) > 200:
            lines = [head] + body
        if len("\n".join(lines)) < 60:
            lines.append(extra)
        await ctx.reply("\n".join(lines))

    api.on_command("收留", take_in, help=".收留 [名字] —— 替一位游魂记下遗愿", cooldown=4)
    api.on_command("幽灵", roster, help=".幽灵 —— 翻翻本堂主的寄名册", cooldown=4)
