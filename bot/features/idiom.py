"""成语接龙：``.接龙`` 开局 → 她出题，你发 ``.接 <成语>`` 接下去；接不上 ``.认输``。

规则（同字接龙）：
* 你发的成语必须四个汉字、且**首字等于她上一个成语的末字**；
* 她再从自己小本本里挑一个以你末字开头的成语接回去；挑不到就认输（你赢）；
* 用过的成语不再重复用；群里大家都能接（同一局共享）。

状态放 api.state（内存，按聊天独立）；超过 2 小时没动静的残局下次开局自动作废。
（注：``.放弃`` 已经被「猜数字」占用了，所以这里用 ``.认输`` / ``.不接了``。）
"""

import random
import time

NAME = "成语接龙"
DESCRIPTION = "小游戏：.接龙 开局，再用 .接 <成语>"
VERSION = "1.0"

STALE_AFTER = 2 * 3600
TAIL_LINES = [
    "接不上就 .认输 嘛～",
    "（不会的话 .认输 也可以的哦）",
    "嘿嘿，她可是成语小达人！",
]

# 常用四字成语（同字接龙的词库；要保证全四个字，测试里有断言）
IDIOMS = [
    "一心一意", "意气风发", "发扬光大", "大器晚成", "成千上万", "万众一心",
    "心花怒放", "放虎归山", "山清水秀", "秀色可餐", "餐风露宿", "宿雨餐风",
    "风调雨顺", "顺水推舟", "舟车劳顿", "顿开茅塞", "塞翁失马", "马到成功",
    "功成名就", "就地取材", "材大难用", "用心良苦", "苦尽甘来", "来日方长",
    "长驱直入", "入木三分", "分道扬镳", "及时行乐", "乐不思蜀", "蜀犬吠日",
    "日新月异", "异想天开", "开门见山", "山穷水尽", "尽善尽美", "美不胜收",
    "收放自如", "如鱼得水", "水到渠成", "成竹在胸", "胸有成竹", "竹报平安",
    "安居乐业", "业精于勤", "勤能补拙", "拙口笨腮", "赤胆忠心", "心旷神怡",
    "怡然自得", "得心应手", "手忙脚乱", "乱七八糟", "糟糠之妻", "妻离子散",
    "散兵游勇", "勇往直前", "前车之鉴", "鉴往知来", "来龙去脉", "脉络分明",
    "明察秋毫", "毫不犹豫", "足智多谋", "谋事在人", "人山人海", "海阔天空",
    "空前绝后", "后生可畏", "畏首畏尾", "尾大不掉", "掉以轻心", "心灵手巧",
    "巧夺天工", "工于心计", "事半功倍", "倍道兼行", "行云流水", "水滴石穿",
    "穿针引线", "声色俱厉", "厉兵秣马", "马不停蹄", "蹄疾步稳", "稳操胜券",
    "手到擒来", "来去匆匆", "匆匆忙忙", "忙里偷闲", "闲情逸致", "致远任重",
    "重见天日", "日积月累", "累见不鲜", "鲜为人知", "知己知彼", "彼竭我盈",
    "盈千累万", "万无一失", "失之交臂", "果不其然", "然荻读书", "书香门第",
    "足不出户", "户枢不蠹", "蠹众木折", "折戟沉沙", "沙里淘金", "金石为开",
    "开卷有益", "益寿延年", "年富力强", "强人所难", "难能可贵", "贵人多忘",
    "忘乎所以", "以身作则", "天马行空", "空谷足音", "音容笑貌", "貌合神离",
    "离奇古怪", "怪诞不经", "经天纬地", "地大物博", "博学多才", "才华横溢",
    "溢于言表", "表里如一", "一拍即合", "合情合理", "理所当然", "天真烂漫",
    "漫不经心", "心心相印", "印象深刻", "刻舟求剑", "剑拔弩张", "张灯结彩",
    "月明风清", "清心寡欲", "欲速不达", "达官贵人", "人杰地灵", "人定胜天",
]

_BY_FIRST: dict = {}


def _build_index() -> None:
    _BY_FIRST.clear()
    for w in IDIOMS:
        _BY_FIRST.setdefault(w[0], []).append(w)


_build_index()


def is_idiom_like(s: str) -> bool:
    """四个汉字（不校验是否是「真成语」——小本本里没有的也算你接对了）。"""
    return len(s) == 4 and all("\u4e00" <= ch <= "\u9fff" for ch in s)


def options_for(ch: str, used) -> list:
    """小本本里以 ch 开头、还没用过的成语。"""
    return [w for w in _BY_FIRST.get(ch, []) if w not in used]


def _games(api) -> dict:
    return api.state.setdefault("idiom.games", {})


def _seed(rng=None) -> str:
    return (rng or random).choice(IDIOMS)


def register(api):
    async def start_cmd(ctx):
        games = _games(api)
        g = games.get(ctx.chat_id)
        now = time.time()
        if g and now - float(g.get("ts") or 0) > STALE_AFTER:
            g = None                                     # 残局作废
        if g:
            await ctx.reply(f"这一局还没结束哦～现在轮到接「{g['last'][-1]}」开头的成语，"
                            f"用「.接 <成语>」发给我。")
            return
        first = _seed()
        games[ctx.chat_id] = {"last": first, "used": [first], "ts": now,
                              "starter": ctx.user_id, "rounds": 0}
        await ctx.reply(f"来玩成语接龙啦！她先来一个：\n  ✨ {first}\n"
                        f"你要接「{first[-1]}」开头的那四个字哦～发「.接 <成语>」给她吧！\n"
                        f"{random.choice(TAIL_LINES)}")

    async def chain_cmd(ctx):
        games = _games(api)
        g = games.get(ctx.chat_id)
        if not g:
            await ctx.reply("还没开始接龙哦，先发一个 .接龙 嘛～")
            return
        g["ts"] = time.time()
        word = (ctx.arg_text or "").strip().replace(" ", "")
        want = g["last"][-1]
        if not word:
            await ctx.reply(f"用「.接 <成语>」告诉我呀，现在要接「{want}」开头的。")
            return
        if not is_idiom_like(word):
            await ctx.reply(f"诶诶，要四个汉字的成语啦～现在接「{want}」开头的。")
            return
        if word[0] != want:
            await ctx.reply(f"嗯……现在要接的是「{want}」开头的成语哦，再想想？")
            return
        if word in g["used"]:
            await ctx.reply(f"「{word}」这一局已经用过啦，换一个～")
            return
        g["used"].append(word)
        g["rounds"] = int(g.get("rounds") or 0) + 1
        nxt = options_for(word[-1], set(g["used"]))
        if not nxt:
            del games[ctx.chat_id]
            await ctx.reply(f"「{word}」……她接不上「{word[-1]}」开头的了，这局你赢啦！🎉\n"
                            f"（一共接了 {g['rounds']} 轮，好厉害～再来一局就说 .接龙）")
            return
        pick = random.choice(nxt)
        g["used"].append(pick)
        g["last"] = pick
        await ctx.reply(f"她接：✨ {pick}\n轮到你啦——接「{pick[-1]}」开头的！")

    async def quit_cmd(ctx):
        games = _games(api)
        g = games.pop(ctx.chat_id, None)
        if not g:
            await ctx.reply("现在没有进行中的接龙哦～")
            return
        await ctx.reply(f"好，这局算她赢！刚才接到「{g['last']}」，一共 {g.get('rounds') or 0} 轮～\n"
                        f"再来就说 .接龙")

    api.on_command("接龙", start_cmd, aliases=("成语接龙", "idiom"), cooldown=3,
                   help=".接龙 —— 和她玩成语接龙（.接 <成语> / .认输）")
    api.on_command("接", chain_cmd, cooldown=0,
                   help=".接 <成语> —— 接上一个成语（首字要匹配）")
    api.on_command("认输", quit_cmd, aliases=("不接了",), cooldown=3,
                   help=".认输 —— 认输结束当前这局接龙")
