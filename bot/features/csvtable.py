"""表格转换：``.表格 <csv/tab>`` ⇄ Markdown。

通用插件。纯离线。

两个方向都做，因为两个方向都常用：
- 从 Excel / 数据库复制出来的 tab 分隔文本 → Markdown 表格（贴到文档里）；
- 已有的 Markdown 表格 → CSV（丢回 Excel）。

分隔符自动认：有 tab 用 tab（从 Excel 复制过来一定是 tab），否则逗号，
再否则分号。中文全角逗号也认。
"""
from __future__ import annotations

import csv
import io
import re

NAME = "表格转换"
NAME_EN = "Table converter"
DESCRIPTION = "把 CSV / 从 Excel 复制的表格转成 Markdown，也能转回去。"
DESCRIPTION_EN = "Convert CSV/TSV to a Markdown table and back."
VERSION = "1.0"

MAX_ROWS = 30
MAX_COLS = 10
CELL_MAX = 40


def raw_args(ctx) -> str:
    t = str(getattr(ctx, "text", "") or "").strip()
    body = t[1:].strip() if t[:1] in "./／" else t
    parts = body.split(None, 1)
    return parts[1] if len(parts) > 1 else ""


def _delim(text: str) -> str:
    if "\t" in text:
        return "\t"
    if "," in text:
        return ","
    if "，" in text:
        return "，"
    if ";" in text:
        return ";"
    return "\t"


def parse_rows(text: str) -> list[list[str]]:
    """按分隔符切成二维表。会丢掉全空的行/列尾巴。"""
    text = str(text or "").replace("\r\n", "\n").replace("\r", "\n").strip("\n")
    reader = csv.reader(io.StringIO(text), delimiter=_delim(text))
    rows = [[c.strip() for c in row] for row in reader]
    rows = [r for r in rows if any(c for c in r)]
    if not rows:
        return []
    width = max(len(r) for r in rows)
    return [r + [""] * (width - len(r)) for r in rows]


def looks_like_md(text: str) -> bool:
    first = str(text or "").strip().splitlines()[:1]
    return bool(first and first[0].lstrip().startswith("|"))


def md_table(rows: list[list[str]], limit_rows: int = MAX_ROWS,
             limit_cols: int = MAX_COLS) -> list[str]:
    # 先清洗/转义再算列宽：`|` → `\|`、换行 → ` ⏎ ` 都会改变显示宽度，
    # 先算宽度再转义会让含转义的行比别的行宽 1~2 列（表格就歪了）。
    rows = [[str(c).replace("\r", " ").replace("\n", " ⏎ ").replace("|", "\\|")
             for c in r[:limit_cols]] for r in rows[:limit_rows]]
    if not rows:
        return []
    width = [0] * max(len(r) for r in rows)
    for r in rows:
        for i, c in enumerate(r):
            width[i] = max(width[i], min(CELL_MAX, _w(c)))
    out = []
    for i, r in enumerate(rows):
        cells = []
        for j, w in enumerate(width):
            c = r[j] if j < len(r) else ""
            cells.append(c + " " * max(0, w - _w(c)))
        out.append("| " + " | ".join(cells) + " |")
        if i == 0:
            out.append("|" + "|".join(" " + "-" * max(3, w) + " " for w in width) + "|")
    return out


def _w(s: str) -> int:
    """粗略显示宽度：CJK 算 2。只用于对齐，不追求完美。"""
    return sum(2 if ord(ch) > 0x2E7F else 1 for ch in str(s))


def md_to_rows(text: str) -> list[list[str]]:
    rows = []
    for ln in str(text or "").splitlines():
        s = ln.strip()
        if not s.startswith("|"):
            continue
        s = s.strip("|")
        if re.fullmatch(r"[\s|:\-]+", ln.strip()):
            continue                                  # 分隔行
        rows.append([c.strip() for c in s.split("|")])
    return rows


def to_csv(rows: list[list[str]]) -> str:
    buf = io.StringIO()
    csv.writer(buf, lineterminator="\n").writerows(rows)
    return buf.getvalue().strip("\n")


def register(api):
    async def cmd(ctx):
        raw = raw_args(ctx)
        if not raw.strip():
            await ctx.reply("用法：.表格 <从 Excel 复制的表格 / CSV>，也可以贴 Markdown 表格转回 CSV")
            return
        if looks_like_md(raw):
            rows = md_to_rows(raw)
            if not rows:
                await ctx.reply("这看着像 Markdown 表格，但没解析出内容…")
                return
            await ctx.reply("📄 CSV（直接粘回 Excel）：\n" + to_csv(rows)[:1600])
            return
        rows = parse_rows(raw)
        if not rows:
            await ctx.reply("没解析出表格内容～把 CSV 或者从 Excel 复制的整块贴过来就行")
            return
        lines = md_table(rows)
        note = ""
        if len(rows) > MAX_ROWS:
            note = f"\n（只列前 {MAX_ROWS} 行，共 {len(rows)} 行）"
        await ctx.reply("📊 Markdown 表格：\n" + "\n".join(lines) + note)

    api.on_command("表格", cmd, aliases=("csv", "table"), cooldown=2,
                   help=".表格 <csv/tab> —— 转成 Markdown（反向也认）")
