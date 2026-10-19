"""心情查询：``.心情`` 看看她现在什么状态。

状态由 `bot/mood.py` 维护（精神 / 心情 / 亲近），这里只负责把它说成人话。
"""
from __future__ import annotations

import os
from pathlib import Path

NAME = "心情"
NAME_EN = "Mood"
DESCRIPTION = "看看她现在的状态：精神、心情、亲近。"
DESCRIPTION_EN = "Show the character's current mood."
VERSION = "1.0"


def _state_root(ctx) -> Path:
    ad = getattr(ctx, "adapter", None)
    for src in (ad, ctx):
        d = getattr(src, "state_dir", None) if src is not None else None
        if d:
            return Path(str(d))
    base = os.environ.get("KLEE_STATE_DIR")
    return Path(base) if base else Path("state")


def _cid(ctx) -> str:
    ad = getattr(ctx, "adapter", None)
    for src in (ad, ctx):
        v = getattr(src, "active_character", None) if src is not None else None
        if v:
            return str(v).strip().lower()
    return "klee"


def _bar(v: float, width: int = 10) -> str:
    n = max(0, min(width, round(float(v) / 100 * width)))
    return "█" * n + "░" * (width - n)


def register(api):
    async def cmd(ctx):
        from bot import mood as mood_mod
        cid = _cid(ctx)
        s = mood_mod.summary(_state_root(ctx), cid)
        lines = [
            f"{s['emoji']} 心情　{s['mood_text']}　{s['mood']:.0f}/100",
            f"  {_bar(s['mood'], 12)}",
            f"{s['energy_emoji']} 精神　{s['energy_text']}　{s['energy']:.0f}/100",
            f"  {_bar(s['energy'], 12)}",
            f"{s['closeness_emoji']} 亲近　{s['closeness_text']}　{s['closeness']:.0f}/100",
            f"  {_bar(s['closeness'], 12)}",
        ]
        if s["turns"]:
            lines.append(f"（一起聊过 {s['turns']} 轮）")
        await ctx.reply("\n".join(lines))

    api.on_command("心情", cmd, aliases=("mood",), cooldown=3,
                   help=".心情 —— 看看她现在的精神 / 心情 / 亲近")
