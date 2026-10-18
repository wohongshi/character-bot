"""猜谜语：``.谜语`` 出题、``.猜谜 <答案>`` 作答、``.谜底`` 认输揭晓。

- 内置 43 条谜语（每条带「打一…」类别提示），全离线、零依赖
- 状态放 ``api.state``（内存），按聊天独立；残局 1 小时没动静就在下次开局时作废
- 答案归一化：去空白/标点、英文小写；也认「答案是 X」「谜底是 X」这类写法
"""

import random
import re
import time

NAME = "猜谜语"
DESCRIPTION = "猜谜语：.谜语 出题、.猜谜 <答案> 作答、.谜底 揭晓"
VERSION = "1.0"

STALE_AFTER = 3600

# (谜面, 谜底, 类别提示)
RIDDLES = [
    ("千条线，万条线，掉到水里看不见", "雨", "打一自然物"),
    ("白天一起玩，晚上各自散；你走它也走，你停它也停", "影子", "打一现象"),
    ("五个兄弟，住在一起，名字不同，高矮不齐", "手指", "打一身体部位"),
    ("上边毛，下边毛，中间一颗黑葡萄", "眼睛", "打一身体部位"),
    ("有头无颈，有眼无眉，无脚能走，有翅难飞", "鱼", "打一动物"),
    ("小时穿黑衣，长大披绿袍，水里过日子，岸上来睡觉", "青蛙", "打一动物"),
    ("身体细又长，身穿黑衣裳，尾巴像剪刀，飞来找春天", "燕子", "打一动物"),
    ("红公鸡，绿尾巴，身体钻到地底下，又甜又脆营养大", "胡萝卜", "打一食物"),
    ("兄弟七八个，围着柱子坐，大家一分手，衣服就扯破", "大蒜", "打一食物"),
    ("弯弯像小船，黄黄像月牙，剥开黄外衣，白白像雪团", "香蕉", "打一水果"),
    ("身穿绿衣裳，肚里水汪汪，生的子儿多，个个黑脸膛", "西瓜", "打一水果"),
    ("麻屋子，红帐子，里面住着白胖子", "花生", "打一食物"),
    ("红口袋，绿口袋，有人怕，有人爱", "辣椒", "打一蔬菜"),
    ("紫色树，开紫花，紫花开了结紫瓜，紫瓜里面装芝麻", "茄子", "打一蔬菜"),
    ("黄瓷瓶，口儿小，打开瓶儿瞧一瞧，里面装着红珠宝", "石榴", "打一水果"),
    ("一头实，一头空，一头白，一头青", "葱", "打一蔬菜"),
    ("白胖娃娃泥里藏，腰身细细心眼多", "藕", "打一蔬菜"),
    ("一个黑孩，从不开口，要是开口，掉出舌头", "瓜子", "打一食物"),
    ("一个小姑娘，坐在水中央，身穿粉红衫，坐在绿船上", "荷花", "打一植物"),
    ("远看山有色，近听水无声", "画", "打一物"),
    ("头上红冠不用裁，满身雪白走将来。平生不敢轻言语，一叫千门万户开", "公鸡", "打一动物"),
    ("大脑袋，细尾巴，黑黑的身子圆溜溜，长大就变四条腿", "蝌蚪", "打一动物"),
    ("有面没有口，有脚没有手，虽有四只脚，自己不会走", "桌子", "打一家具"),
    ("独木造高楼，没瓦没砖头，人在水下走，水在人上流", "雨伞", "打一日常用品"),
    ("天上棋盘，棋子无数，一夜之间，撒满各处", "星星", "打一自然物"),
    ("青石板，板石青，青石板上钉银钉", "星星", "打一自然物"),
    ("天上一群小白羊，有的站着有的躺", "云", "打一自然物"),
    ("有时落在山腰，有时挂在树梢，有时像面圆镜，有时像把镰刀", "月亮", "打一自然物"),
    ("像云不是云，像烟不是烟，风吹轻轻飘，日出慢慢散", "雾", "打一自然物"),
    ("驼背公公，力大无穷，爱驮什么？车水马龙", "桥", "打一建筑"),
    ("一棵树，高又高，身上挂着千把刀", "皂角树", "打一植物"),
    ("说它是头牛，无法拉车走，说它力气小，却能背屋跑", "蜗牛", "打一动物"),
    ("头上两根毛，身穿彩花袍，飞舞花丛中，快乐又逍遥", "蝴蝶", "打一动物"),
    ("一物生来强，每天织网忙，织完静等待，专等蚊虫撞", "蜘蛛", "打一动物"),
    ("身体半球形，背上七颗星，棉花喜爱它，捕虫最著名", "瓢虫", "打一动物"),
    ("小船儿，白又白，两把桨儿划起来，水上漂呀漂，快乐又自在", "鸭子", "打一动物"),
    ("千只脚，万只脚，站不住，靠墙角", "扫帚", "打一日常用品"),
    ("五颜六色小圆球，一吹就变大，一松就变小", "气球", "打一玩具"),
    ("一样东西亮晶晶，又光又硬又透明，工人叔叔造出来，它的用处说不清", "玻璃", "打一物"),
    ("一座桥，地上架，走着上去坐着下", "滑梯", "打一游乐设施"),
    ("身穿白袍子，头戴红帽子，走路像公子，说话高嗓子", "鹅", "打一动物"),
    ("耳朵长，尾巴短，红眼睛，白毛衫，三瓣嘴儿胆子小，青菜萝卜吃个饱", "兔子", "打一动物"),
    ("嘴边胡子翘，说话喵喵叫，走路静悄悄，捉鼠本领高", "猫", "打一动物"),
]

_PUNCT = re.compile(r"[\s，。、！？：；「」『』【】（）()《》〈〉,.!?:;\"'\u3000…—－\-～~]+")
# 「答案是 X」「谜底：X」「我猜是 X」这类前缀要剥掉（按长词优先，反复剥）
_LEADERS = ("应该是", "我认为是", "我猜是", "是不是", "答案", "谜底", "就是", "猜", "答", "是")


def _strip_leaders(t: str) -> str:
    changed = True
    while changed and t:
        changed = False
        for tok in _LEADERS:
            if t.startswith(tok):
                t = t[len(tok):]
                changed = True
                break
    return t


def _norm(text: str) -> str:
    """归一化：去标点空白、英文小写、剥掉「答案是」这类前缀。"""
    return _strip_leaders(_PUNCT.sub("", (text or "").strip().lower()))


def _games(api) -> dict:
    return api.state.setdefault("riddle.games", {})


def pick(pool=None, rng=None) -> tuple:
    """挑一条谜语（可注入 rng/pool，方便测试）。"""
    rng = rng or random.random
    pool = pool if pool is not None else RIDDLES
    if not pool:
        raise ValueError("谜语池是空的")            # OCR 三轮 #1：空池要报清楚的错，别 IndexError
    return pool[min(int(rng() * len(pool)), len(pool) - 1)]


def check_answer(ans_text: str, item: tuple) -> bool:
    """用户答的是不是这条谜语（归一化后比较）。"""
    return bool(_norm(ans_text)) and _norm(ans_text) == _norm(item[1])


def register(api):
    async def start_cmd(ctx):
        games = _games(api)
        g = games.get(ctx.chat_id)
        now = time.time()
        if g and now - float(g.get("ts") or 0) > STALE_AFTER:
            g = None                                     # 残局作废
        if g:
            await ctx.reply(f"这一条还没猜出来哦～要放弃就 .谜底，不然继续 .猜谜 <答案>")
            return
        item = pick()
        games[ctx.chat_id] = {"item": item, "ts": now}
        await ctx.reply(f"🧩 谜语来啦（{item[2]}）：\n{item[0]}\n\n想好了就 .猜谜 <答案> ～")

    async def guess_cmd(ctx):
        games = _games(api)
        g = games.get(ctx.chat_id)
        if not g:
            await ctx.reply("还没有谜语哦～先 .谜语 来一条嘛")
            return
        raw = (ctx.arg_text or "").strip()
        if not raw:
            await ctx.reply("诶，要把答案写在后面呀，比如 .猜谜 雨")
            return
        g["ts"] = time.time()
        item = g["item"]
        if check_answer(raw, item):
            del games[ctx.chat_id]
            await ctx.reply(f"🎉 {ctx.user_name} 猜对啦！谜底就是「{item[1]}」～她给你鼓掌！")
            return
        await ctx.reply(f"唔……不对哦，再想想～（谜面：{item[0]}）\n"
                        f"实在想不出来就 .谜底")

    async def reveal_cmd(ctx):
        games = _games(api)
        g = games.pop(ctx.chat_id, None)
        if not g:
            await ctx.reply("现在没有在猜的谜语哦～想玩就 .谜语 呀")
            return
        item = g["item"]
        await ctx.reply(f"好嘛，揭晓啦——谜底是「{item[1]}」！\n（{item[2]}：{item[0]}）")

    api.on_command("谜语", start_cmd, aliases=("出谜", "来条谜语"), cooldown=5,
                   help=".谜语 —— 她出一条谜语，你来猜")
    api.on_command("猜谜", guess_cmd, aliases=("猜谜语",), cooldown=1,
                   help=".猜谜 <答案> —— 回答谜语")
    api.on_command("谜底", reveal_cmd, aliases=("揭晓", "猜不出"), cooldown=3,
                   help=".谜底 —— 揭晓答案（认输）")
