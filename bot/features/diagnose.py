"""亚托莉 · 高性能自检：.自检

一份看起来很专业、结论永远很可爱的自检报告。
"""
import random

NAME = "高性能自检"
DESCRIPTION = "亚托莉给自己做一次自检：.自检"
VERSION = "1.0"
CHARACTERS = ("atri",)

CHECKS = [
    ("仿生脑", "运算正常", "顺手算了一下你今天的运气，结果是「还不错」"),
    ("情感模块", "读数偏高", "看到你的时候会这样，原因不明"),
    ("防水结构", "30m 正常", "上次背夏生先生掉进水里，证明过了"),
    ("料理单元", "异常", "做出来的东西被称作「工业废料」，正在改进"),
    ("进食逻辑", "无法解释", "明明不能摄取营养，但好吃就是高兴嘛"),
    ("日志本", "有泪痕", "纸页干了，痕迹还在"),
    ("火箭拳", "待命", "请勿在室内演示"),
    ("睡眠模式", "拒绝执行", "还想再和你聊五分钟"),
]


def register(api):
    async def diagnose(ctx):
        picks = random.sample(CHECKS, k=min(4, len(CHECKS)))
        score = random.randint(88, 100)
        lines = [f"⚙️ 亚托莉 · 高性能自检报告　综合评分 {score}/100"]
        for name, state, note in picks:
            lines.append(f"· {name}：{state} —— {note}")
        lines.append("结论：高性能运转中。……大概。")
        await ctx.reply("\n".join(lines))

    api.on_command("自检", diagnose, aliases=("高性能自检",), help=".自检 —— 亚托莉的运行报告", cooldown=5)
