"""猜拳：``.猜拳 石头`` —— 和她来一局；战绩记在 state/rps.json。

- 出法：石头/剪刀/布，也认 ✊✌️🖐️ 和 rock/scissors/paper
- 支持「.猜拳 石头」直接出；不写就出用法
- 每人战绩累计（胜/负/平），回复里带上
"""

import json
import logging
import os
import random
import time
from pathlib import Path

logger = logging.getLogger(__name__)

NAME = "猜拳"
DESCRIPTION = "和她猜拳：.猜拳 石头 / 剪刀 / 布（还记战绩）"
VERSION = "1.0"

def _state_root() -> "Path":
    """状态目录：`KLEE_STATE_DIR` 可覆盖（干跑测试台/冒烟靠它把状态写到临时目录，
    别让「跑一遍测试」改到真实战绩和抽卡记录）。"""
    base = os.environ.get("KLEE_STATE_DIR")
    return Path(base) if base else Path(__file__).resolve().parent / "state"


BASE = _state_root()
STATE_FILE = BASE / "rps.json"

CHOICES = ("石头", "剪刀", "布")
EMOJI = {"石头": "✊", "剪刀": "✌️", "布": "🖐️"}
BEATS = {"石头": "剪刀", "剪刀": "布", "布": "石头"}   # 键赢值

NORM = {
    "石头": "石头", "剪刀": "剪刀", "布": "布",
    "✊": "石头", "✌️": "剪刀", "✌": "剪刀", "🖐️": "布", "🖐": "布",
    "rock": "石头", "scissors": "剪刀", "paper": "布",
    "拳": "石头", "剪": "剪刀", "布匹": "布",
}

WIN_LINES = [
    "诶——她的{klee}被你的{user}包住啦，这局你赢！",
    "哒哒哒！你赢了，她的{klee}打不过你的{user}，呜。",
    "你赢啦！她不服，再来一局嘛——（记在小本本上了）",
]
LOSE_LINES = [
    "嘻嘻，她的{klee}赢过你的{user}啦！再来再来！",
    "这局她赢！今天手气很棒哦～",
    "耶！她赢啦～你要不要试试点心贿赂她？",
]
DRAW_LINES = [
    "唔，都是{user}，平局！再来一次嘛～",
    "平局呢……她和你想一块儿去了，嘿嘿。",
]


def _load() -> dict:
    """读战绩。文件不存在 = 从零开始；文件损坏 = 先备份再重建（别静默覆盖全部人的战绩）。"""
    try:
        d = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as e:
        logger.warning("猜拳：读 %s 失败（%s）——备份损坏文件后从空战绩开始", STATE_FILE, e)
        try:
            STATE_FILE.replace(STATE_FILE.with_name(f"{STATE_FILE.name}.corrupt-{int(time.time())}"))
        except OSError:
            pass
        return {}
    return d if isinstance(d, dict) else {}


def _save(d: dict) -> bool:
    """原子写（唯一临时名，防并发写互踩）。写失败只记日志 → 调用方照样回话，不吞回复。"""
    try:
        BASE.mkdir(parents=True, exist_ok=True)
        tmp = BASE / f".rps-{os.getpid()}-{time.time_ns()}.tmp"
        tmp.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        tmp.replace(STATE_FILE)
        return True
    except OSError as e:
        logger.warning("猜拳：写 %s 失败 — %s", STATE_FILE, e)
        return False


def play(user_choice: str, klee_choice: str):
    """→ 'win' | 'lose' | 'draw'（从用户视角）。"""
    if user_choice == klee_choice:
        return "draw"
    return "win" if BEATS[user_choice] == klee_choice else "lose"


def register(api):
    async def rps_cmd(ctx):
        raw = (ctx.arg_text or "").strip().split()[0] if (ctx.arg_text or "").strip() else ""
        choice = NORM.get(raw.lower()) or NORM.get(raw)
        if not choice:
            await ctx.reply("跟她猜拳呀！出法：.猜拳 石头 / .猜拳 剪刀 / .猜拳 布\n"
                            "（✊✌️🖐️ 也认哦）")
            return
        klee = random.choice(CHOICES)
        result = play(choice, klee)
        d = _load()
        rec = d.setdefault(ctx.user_id, {"w": 0, "l": 0, "d": 0})
        rec[{"win": "w", "lose": "l", "draw": "d"}[result]] += 1
        saved = _save(d)
        ku, ke = EMOJI[choice], EMOJI[klee]
        if result == "win":
            line = random.choice(WIN_LINES).format(klee=ke, user=ku)
        elif result == "lose":
            line = random.choice(LOSE_LINES).format(klee=ke, user=ku)
        else:
            line = random.choice(DRAW_LINES).format(klee=ke, user=ku)
        await ctx.reply(f"你出 {ku}{choice}，她出 {ke}{klee}——{line}\n"
                        f"📒 {ctx.user_name} 的战绩：{rec['w']} 胜 {rec['l']} 负 {rec['d']} 平"
                        + ("" if saved else "\n（呜，战绩本本暂时写不进去……她下次再记）"))

    api.on_command("猜拳", rps_cmd, aliases=("rps", "划拳"), cooldown=3,
                   help=".猜拳 石头 —— 和她猜拳（石头/剪刀/布，战绩累计）")
