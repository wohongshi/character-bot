"""帮我选：``.帮我选 火锅 烧烤 面条`` —— 选择困难症交给可莉。

用法：
  ``.帮我选 火锅 烧烤 面条``      # 空格分
  ``.帮我选 火锅、烧烤、面条``     # 顿号/逗号/斜杠/换行也行
  ``.帮我选 火锅还是烧烤``         # 「还是」「或者」也当分隔

- 2~8 个选项；选项里的引号会去掉、方括号会洗掉（防 ``[CQ:at,qq=all]`` 那类出站富文本注入）
- 无状态、纯离线（不写任何文件），所以干跑/测试天然干净
- 随机挑选 + 可莉味理由；冷却 3 秒，不满意可以再点
"""

import random
import re

NAME = "帮我选"
DESCRIPTION = "选择困难？.帮我选 火锅 烧烤 面条 —— 可莉帮你挑一个"
VERSION = "1.0"

MAX_OPTIONS = 8
_LABEL_MAX = 24

# 出站会解析 [CQ:...] 富文本标记，选项来自用户输入 —— 必须洗（和 comfort/anniversary 一个规矩）。
_UNSAFE = re.compile(r"[\[\]<>]|&#\d+;")
# 分隔符：空白（含全角空格）、逗号、顿号、分号、斜杠、竖线，以及「还是 / 或者」
_SPLIT = re.compile(r"[\s\u3000,，、;；/|]+|还是|或者")
_QUOTES = "[\"'“”‘’]"
# 只去**成对**的外层包裹（「（打球）」→「打球」，引号同理），否则「看电影(3D)」会被啃掉右括号。
_PAIRS = {"（": "）", "(": ")", "《": "》", "【": "】", "[": "]", "“": "”", "‘": "’"}


def _trim_wrappers(s: str) -> str:
    s = (s or "").strip()
    while len(s) >= 2:
        head, tail = s[0], s[-1]
        if head in _PAIRS and _PAIRS[head] == tail:
            s = s[1:-1].strip()
            continue
        stripped = re.sub(rf"^{_QUOTES}+|{_QUOTES}+$", "", s)
        if stripped != s:
            s = stripped.strip()
            continue
        break
    return s

REASONS = (
    "闭上眼睛随便指一个，就是它了。",
    "可莉把骰子丢进火花里，蹦出来的就是它！",
    "嗯……感觉今天特别适合这个。",
    "蹦蹦炸弹的弹道算过了，就它啦。",
    "可莉数了三下，数到它。",
    "别的都先放放，今天就它吧。",
)


def _clean_full(raw: str) -> str:
    """洗标记 + 去成对包裹，不截断（去重键要用完整串，先截断再去重会误并，OCR #10）。"""
    return _UNSAFE.sub("", _trim_wrappers(raw)).strip()


def _safe(raw: str) -> str:
    """选项里最长的安全片段：干掉方括号与实体转义（顺带去掉成对的外层引号/括号）。"""
    return _clean_full(raw)[:_LABEL_MAX]


def split_options(raw: str) -> list:
    """把用户那句话切成选项列表：去空白、去引号、去重（不区分大小写）、保序。"""
    out, seen = [], set()
    for piece in _SPLIT.split(raw or ""):
        full = _clean_full(piece)
        if not full:
            continue
        key = re.sub(r"\s+", "", full).lower()      # 完整串做键：前 24 字相同也不算重复
        if key in seen:
            continue
        seen.add(key)
        out.append(full[:_LABEL_MAX])
    return out


def pick(options, rng=random):
    """随机挑一个（抽出来给测试用，好注入固定种子）。"""
    return rng.choice(options)


def register(api):
    async def cmd(ctx):
        raw = ctx.arg_text or ""
        opts = split_options(raw)
        if not raw.strip():
            await ctx.reply("想让我选什么呀～用法：.帮我选 火锅 烧烤 面条（2~8 个，"
                            "顿号/逗号/「还是」都行）")
            return
        if not opts:
            await ctx.reply("这些选项好像都是空的诶～写点实在的，比如：.帮我选 火锅 烧烤")
            return
        if len(opts) == 1:
            await ctx.reply(f"只有「{opts[0]}」一个选项呀，那当然是它啦～"
                            f"（多写几个我才好挑：.帮我选 {opts[0]} 别的）")
            return
        if len(opts) > MAX_OPTIONS:
            await ctx.reply(f"选项太多啦（{len(opts)} 个），可莉最多比 {MAX_OPTIONS} 个～"
                            f"先砍掉几个再来？")
            return
        picked = pick(opts)
        await ctx.reply(f"可莉帮你想好啦：**{picked}**\n{random.choice(REASONS)}\n"
                        f"（从 {len(opts)} 个里挑的：{'、'.join(opts)}）")

    api.on_command("帮我选", cmd, aliases=("选择", "帮我决定", "决定", "选", "choose"),
                   cooldown=3, help=".帮我选 a b c —— 选择困难交给可莉（2~8 个选项）")
