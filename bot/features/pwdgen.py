"""密码生成：``.密码`` / ``.密码 24`` / ``.密码 24 数字``。

通用插件。用 ``secrets`` 而不是 ``random`` —— 密码不该是可预测的伪随机。

几条实务上的讲究：
- 默认**排除易混字符**（0O1lI|），因为手抄密码时这几个最容易错；
- 每一类（大写/小写/数字/符号）至少出现一个，最后再打乱 ——
  否则「随机生成」有相当概率全是字母；
- 在群里发密码是很糟的主意，所以群聊里会提醒一句撤回。
"""
from __future__ import annotations

import secrets
import string

NAME = "密码生成"
NAME_EN = "Password generator"
DESCRIPTION = "生成随机密码（可选长度和字符类型）。"
DESCRIPTION_EN = "Generate a random password with an optional length and charset."
VERSION = "1.0"

LOWER = "abcdefghijkmnopqrstuvwxyz"          # 去掉 l
UPPER = "ABCDEFGHJKLMNPQRSTUVWXYZ"           # 去掉 I O
DIGIT = "23456789"                           # 去掉 0 1
SYMBOL = "!@#$%^&*()-_=+[]{}:,.?"
AMBIGUOUS = "0O1lI|`'\""

MAX_LEN = 128
MIN_LEN = 6


def raw_args(ctx) -> str:
    t = str(getattr(ctx, "text", "") or "").strip()
    body = t[1:].strip() if t[:1] in "./／" else t
    return body.split(None, 1)[1] if len(body.split(None, 1)) > 1 else ""


def parse_spec(raw: str) -> tuple[int, str]:
    """'24 数字' → (24, 'digit')。类型词里带「号」的算符号，带「数」的算纯数字。"""
    words = str(raw or "").split()
    length = 16
    kind = "mix"
    for w in words:
        if w.isdigit():
            length = max(MIN_LEN, min(MAX_LEN, int(w)))
        elif any(k in w for k in ("数字", "纯数", "digit")):
            kind = "digit"
        elif any(k in w for k in ("符号", "强", "complex", "strong")):
            kind = "strong"
        elif any(k in w for k in ("字母", "letter", "alpha")):
            kind = "alpha"
        elif any(k in w for k in ("易读", "好记", "readable")):
            kind = "readable"
    return length, kind


def _pool(kind: str) -> tuple[list[str], list[str]]:
    """返回 (必选类, 可选扩充)。必选类保证每类至少一个。"""
    if kind == "digit":
        return [DIGIT], []
    if kind == "alpha":
        return [LOWER, UPPER], []
    if kind == "strong":
        return [LOWER, UPPER, DIGIT, SYMBOL], []
    if kind == "readable":
        # 好记：只用大小写 + 数字，且排除易混字符（上面几组已经排过）
        return [LOWER, UPPER, DIGIT], []
    # 默认（mix）= 字母 + 数字；想要符号就明说「强」
    return [LOWER, UPPER, DIGIT], []


def generate(length: int = 16, kind: str = "mix", rng=None) -> str:
    """生成密码。rng 可注入（测试用）；默认 secrets。"""
    rng = rng or secrets
    required, extra = _pool(kind)
    if length < len(required):
        length = len(required)
    chars: list[str] = []
    for group in required:
        chars.append(rng.choice(group))
    universe = "".join(required) + "".join(extra)
    while len(chars) < length:
        chars.append(rng.choice(universe))
    # Fisher–Yates：保证「必选字符」不会总在开头。
    # 注意 secrets 模块**没有** randrange（只有 choice/randbelow），
    # 所以两种都认，测试注入 random 也能跑。
    def _below(n: int) -> int:
        fn = getattr(rng, "randbelow", None)
        return fn(n) if callable(fn) else rng.randrange(n)

    for i in range(len(chars) - 1, 0, -1):
        j = _below(i + 1)
        chars[i], chars[j] = chars[j], chars[i]
    return "".join(chars[:length])


def strength(length: int, kind: str) -> str:
    """粗估一下强度（只按长度和字符集算熵，不装专业）。"""
    import math
    size = {"digit": 8, "alpha": 52, "readable": 57, "mix": 66, "strong": 92}.get(kind, 66)
    bits = length * math.log2(size)
    if bits < 40:
        label = "偏弱"
    elif bits < 60:
        label = "够用"
    elif bits < 80:
        label = "不错"
    else:
        label = "很强"
    return f"{label}（约 {bits:.0f} bit 熵）"


def register(api):
    async def cmd(ctx):
        length, kind = parse_spec(raw_args(ctx))
        pwd = generate(length, kind)
        kind_cn = {"digit": "纯数字", "alpha": "字母", "strong": "含符号",
                   "readable": "好记", "mix": "字母+数字"}[kind]
        msg = f"🔑 {pwd}\n（{length} 位 · {kind_cn} · {strength(length, kind)}）"
        if ctx.is_group:
            msg += "\n⚠️ 这是群聊，密码别留着——用完记得撤回。"
        await ctx.reply(msg)

    api.on_command("密码", cmd, aliases=("pwd", "password"), cooldown=3,
                   help=".密码 [长度] [数字|字母|强|好记] —— 生成随机密码")
