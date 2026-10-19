"""字数统计 + 词频：``.字数 <文字>`` / ``.词频 <文字>``。

通用插件。纯离线。写文案、发长消息、算演讲稿时长都用得上。

词频的口径（中文没有空格，别假装能分词）：
- 英文按单词（小写、长度 ≥ 2、去掉 the/and 这类高频虚词）；
- 中文按**单字**统计（真正的分词要词典，硬凑反而错），并排除标点与虚字。
"""
from __future__ import annotations

import re
import unicodedata

NAME = "字数统计"
NAME_EN = "Text statistics"
DESCRIPTION = "统计字数、行数、中文/英文数量、阅读时间，还能算词频。"
DESCRIPTION_EN = "Count characters, lines, CJK/English amounts, reading time and word frequency."
VERSION = "1.0"

MAX_IN = 20000
CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
WORD = re.compile(r"[A-Za-z][A-Za-z'’\-]*")
DIGIT = re.compile(r"\d")

STOP_EN = {
    "the", "and", "for", "are", "but", "not", "you", "all", "any", "can", "had", "her",
    "was", "one", "our", "out", "day", "get", "has", "him", "his", "how", "its", "may",
    "new", "now", "old", "see", "two", "way", "who", "boy", "did", "she", "use", "with",
    "this", "that", "from", "they", "have", "will", "your", "what", "when", "them", "then",
    "there", "their", "would", "could", "should", "about", "into", "than", "been", "were",
}
STOP_CN = set("的了是在我你他她它们这那有和就不人都一二三四五六七八九十个中上下大小多少"
              "吗呢吧啊呀哦嗯啦着过要会能可以也还很更最又再被把对给让从到与而或但如果因为所以")


def raw_args(ctx) -> str:
    t = str(getattr(ctx, "text", "") or "").strip()
    body = t[1:].strip() if t[:1] in "./／" else t
    parts = body.split(None, 1)
    return parts[1] if len(parts) > 1 else ""


def count(text: str) -> dict:
    """统计一份文本。返回的字段直接拿去拼消息。"""
    s = str(text or "")
    n_all = len(s)
    n_space = sum(1 for c in s if c.isspace())
    cjk = len(CJK.findall(s))
    words = WORD.findall(s)
    digits = len(DIGIT.findall(s))
    lines = s.count("\n") + 1 if s else 0
    punct = sum(1 for c in s if unicodedata.category(c).startswith("P"))
    # 中文按 300 字/分钟、英文按 200 词/分钟粗算（朗读再慢一点）
    read_min = (cjk + len(words) * 1.6) / 300.0
    speak_min = (cjk + len(words) * 1.6) / 240.0
    return {"all": n_all, "no_space": n_all - n_space, "cjk": cjk, "words": len(words),
            "digits": digits, "lines": lines, "punct": punct,
            "read_min": read_min, "speak_min": speak_min}


def _dur(minutes: float) -> str:
    sec = int(round(minutes * 60))
    if sec < 60:
        return f"{sec} 秒"
    m, s = divmod(sec, 60)
    return f"{m} 分 {s} 秒" if s else f"{m} 分钟"


def _cjk_runs(s: str) -> list[str]:
    """把连续的汉字切成一段一段（标点/英文/空白都是边界）。"""
    runs, cur = [], []
    for ch in s:
        if CJK.match(ch):
            cur.append(ch)
        elif cur:
            runs.append("".join(cur))
            cur = []
    if cur:
        runs.append("".join(cur))
    return runs


def word_freq(text: str, top: int = 10) -> list[tuple[str, int]]:
    """英文按词、中文按 **二元组**；去停用词，返回 [(词, 次数)]。

    中文为什么用二元组而不是单字：单字统计会把「苹果」拆成「苹 3 次、果 1 次」，
    对读者毫无意义。二元组是公认的廉价替代（不需要词典），「苹果」就能作为一个
    整体冒出来；只在长度 1 的段里才回退成单字。
    """
    s = str(text or "")
    counter: dict[str, int] = {}
    for w in WORD.findall(s.lower()):
        if len(w) < 2 or w in STOP_EN:
            continue
        counter[w] = counter.get(w, 0) + 1
    for run in _cjk_runs(s):
        if len(run) == 1:
            if run not in STOP_CN:
                counter[run] = counter.get(run, 0) + 1
            continue
        for i in range(len(run) - 1):
            gram = run[i:i + 2]
            if gram[0] in STOP_CN and gram[1] in STOP_CN:
                continue                        # 「的时」这种纯虚词对没意义
            counter[gram] = counter.get(gram, 0) + 1
    items = sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))
    # 只出现一次的二元组大半是切出来的噪音：有别的候选时就把它们压掉
    strong = [kv for kv in items if kv[1] > 1]
    if len(strong) >= 3:
        items = strong
    return items[:max(1, min(20, top))]


def register(api):
    async def stats(ctx):
        text = raw_args(ctx)
        if not text.strip():
            await ctx.reply("用法：.字数统计 <一段文字>（也支持多行）")
            return
        if len(text) > MAX_IN:
            await ctx.reply(f"太长了（{len(text)} 字，上限 {MAX_IN}）")
            return
        c = count(text)
        await ctx.reply(
            "📝 统计\n"
            f"总字符：{c['all']}（不含空白 {c['no_space']}）\n"
            f"中文：{c['cjk']} 字 · 英文：{c['words']} 词 · 数字：{c['digits']} 个\n"
            f"行数：{c['lines']} · 标点：{c['punct']}\n"
            f"读一遍约 {_dur(c['read_min'])}，念出来约 {_dur(c['speak_min'])}"
        )

    async def freq(ctx):
        text = raw_args(ctx)
        if not text.strip():
            await ctx.reply("用法：.词频 <一段文字> [数量]")
            return
        if len(text) > MAX_IN:
            await ctx.reply(f"太长了（{len(text)} 字，上限 {MAX_IN}）")
            return
        top = 10
        m = re.search(r"\s(\d{1,2})\s*$", text)
        if m:
            top = int(m.group(1))
            text = text[:m.start()]
        items = word_freq(text, top)
        if not items:
            await ctx.reply("没统计出什么（都是虚词或者太短了）")
            return
        peak = items[0][1] or 1
        lines = ["📊 词频"]
        for w, n in items:
            bar = "▇" * max(1, round(n / peak * 8))
            lines.append(f"{w}　{n}　{bar}")
        await ctx.reply("\n".join(lines))

    # 「字数」这个键已经被外部插件 textstat 占成别名了 —— 命令名/别名冲突时
    # 框架会把**整条命令**静默丢掉，所以这里必须用没被占的名字。
    api.on_command("字数统计", stats, aliases=("wc",), cooldown=2,
                   help=".字数统计 <文字> —— 字数/行数/阅读时间")
    api.on_command("词频", freq, aliases=("wordfreq",), cooldown=3,
                   help=".词频 <文字> [n] —— 出现最多的词")
