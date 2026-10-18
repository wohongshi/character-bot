"""可莉 · 炸弹工坊：.炸弹 [材料]、.炸鱼

角色限定插件。纯娱乐，不涉及任何真实危险物品的制作方法——
配方全是「亮晶晶」「哒哒哒」式的童话材料。
"""
import random

from _plugstore import clip

NAME = "炸弹工坊"
DESCRIPTION = "炸弹配方与炸鱼战报：.炸弹 [材料]、.炸鱼"
VERSION = "1.0"
CHARACTERS = ("klee",)

MATERIALS = ["亮晶晶的小石头", "嘟嘟可的绒毛", "星落湖的鱼鳞", "风车菊的花瓣",
             "蒲公英籽", "甜甜花的蜜", "琴团长没收的图纸", "雷泽捡的松果"]
EFFECTS = ["会冒出彩虹色的烟", "炸开是一朵大花花", "声音像打嗝", "会下三秒的小鱼雨",
           "把周围的草吹成心形", "留下一股烤鱼味", "亮得像小太阳", "只会「啵」一声"]
NAMES = ["嘟嘟可轰轰号", "闪闪火花一号", "可莉的秘密惊喜", "哒哒哒小礼花",
         "蹦蹦炸弹·改良版", "亮晶晶大礼包"]


def register(api):
    async def bomb(ctx):
        mat = clip(ctx.arg_text, 24) or random.choice(MATERIALS)
        name = random.choice(NAMES)
        power = random.randint(1, 99)
        safety = random.choice(["很安全！", "大概安全！", "琴团长说不行", "只炸鱼，不炸人", "诶嘿，应该没事"])
        await ctx.reply(
            f"💥 配方名：{name}\n"
            f"🧪 主材料：{mat}\n"
            f"✨ 效果：{random.choice(EFFECTS)}\n"
            f"📈 威力：{power}/99　🔒 安全评级：{safety}\n"
            f"——可莉提醒：只能炸鱼和放烟花哦！"
        )

    async def fish(ctx):
        n = random.randint(0, 9)
        if n == 0:
            await ctx.reply("🌊 今天星落湖的鱼都学聪明了……一条都没炸到。可莉不服，明天再来！")
            return
        pool = ["花鳉", "蓝染花鳉", "甜甜花鳉", "水晶宴", "维护机关鱼"]
        picked = random.sample(pool, k=min(n, len(pool)))
        await ctx.reply(
            f"🐟 炸鱼战报：一共 {n} 条！\n"
            f"战绩：{'、'.join(picked)}……\n"
            f"{'琴团长还有 3 秒到达现场！' if n >= 7 else '可莉跑得很快，不会被抓到的！'}"
        )

    api.on_command("炸弹", bomb, aliases=("炸弹配方",), help=".炸弹 [材料] —— 可莉给你配一枚（童话版）", cooldown=5)
    api.on_command("炸鱼", fish, aliases=(), help=".炸鱼 —— 今日星落湖战报", cooldown=5)
