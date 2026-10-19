"""数独：``.数独`` 出题 / ``.数独解 <81 位>` 求解。

通用插件。纯离线。

- 出题：先随机填满（回溯 + 打乱候选），再按难度挖洞，**并检查唯一解** ——
  随手挖洞会出多解题，那种题做起来会让人怀疑自己；
- 求解：接受 81 个字符（``.`` ``0`` 空格都算空），也接受直接贴 9 行网格。
"""
from __future__ import annotations

import asyncio
import random

NAME = "数独"
NAME_EN = "Sudoku"
DESCRIPTION = "出一题数独，或者解一题（贴 81 个数字）。"
DESCRIPTION_EN = "Generate or solve a 9x9 sudoku."
VERSION = "1.0"

BLANK = "."
LEVELS = {"简单": 40, "普通": 32, "困难": 26, "地狱": 22}


def raw_args(ctx) -> str:
    t = str(getattr(ctx, "text", "") or "").strip()
    body = t[1:].strip() if t[:1] in "./／" else t
    parts = body.split(None, 1)
    return parts[1] if len(parts) > 1 else ""


def _ok(board: list[int], pos: int, val: int) -> bool:
    r, c = divmod(pos, 9)
    for i in range(9):
        if board[r * 9 + i] == val or board[i * 9 + c] == val:
            return False
    br, bc = (r // 3) * 3, (c // 3) * 3
    for i in range(3):
        for j in range(3):
            if board[(br + i) * 9 + (bc + j)] == val:
                return False
    return True


def solutions(board: list[int], limit: int = 2, rng=None) -> list[list[int]]:
    """回溯求所有解（最多 limit 个）。返回解列表，不改动入参。"""
    rng = rng or random
    found: list[list[int]] = []

    def bt(b: list[int]) -> None:
        if len(found) >= limit:
            return
        best, cands = -1, None
        for pos in range(81):
            if b[pos]:
                continue
            cs = [v for v in range(1, 10) if _ok(b, pos, v)]
            if not cs:
                return                      # 这个分支死了
            if cands is None or len(cs) < len(cands):
                best, cands = pos, cs
                if len(cs) == 1:
                    break
        if cands is None:
            found.append(list(b))           # 填满了
            return
        order = list(cands)
        rng.shuffle(order)
        for v in order:
            b[best] = v
            bt(b)
            b[best] = 0
            if len(found) >= limit:
                return

    bt(list(board))
    return found


def generate(clues: int = 32, rng=None) -> tuple[list[int], list[int]]:
    """返回 (题目, 答案)。题目里 0 表示空格。"""
    rng = rng or random.Random()
    full = [0] * 81
    # 先填满对角线三个宫，再整体求解 —— 比从空盘硬解快很多
    for b in range(3):
        nums = list(range(1, 10))
        rng.shuffle(nums)
        for i in range(3):
            for j in range(3):
                full[(b * 3 + i) * 9 + (b * 3 + j)] = nums[i * 3 + j]
    solution = solutions(full, 1, rng)[0]
    puzzle = list(solution)
    cells = list(range(81))
    rng.shuffle(cells)
    blanks = 0
    for pos in cells:
        if blanks >= 81 - clues:
            break
        keep = puzzle[pos]
        puzzle[pos] = 0
        if len(solutions(puzzle, 2, rng)) != 1:     # 必须仍然唯一解
            puzzle[pos] = keep
        else:
            blanks += 1
    return puzzle, solution


def parse_grid(raw: str) -> list[int]:
    """'530070000…' 或带换行的网格 → 81 个 int（0 = 空）。"""
    chars = [ch for ch in str(raw or "") if ch.isdigit() or ch in ".·_x"]
    if len(chars) != 81:
        raise ValueError(f"要 81 个格子，你给了 {len(chars)} 个")
    out = []
    for ch in chars:
        # 0 和 . 都当空格（两种写法都常见）
        out.append(0 if ch in ".·_x0" else int(ch))
    return out


def has_conflict(grid: list[int]) -> bool:
    """开局检查：每格先**挖掉自己**再判冲突（否则自己和自己必然冲突）。"""
    for i, v in enumerate(grid):
        if not v:
            continue
        probe = list(grid)
        probe[i] = 0
        if not _ok(probe, i, v):
            return True
    return False


def render(grid: list[int]) -> str:
    rows = []
    for r in range(9):
        if r and r % 3 == 0:
            rows.append("------+-------+------")
        cells = []
        for c in range(9):
            v = grid[r * 9 + c]
            cells.append(str(v) if v else BLANK)
            if c in (2, 5):
                cells.append("|")
        rows.append(" ".join(cells))
    return "\n".join(rows)


def register(api):
    async def make(ctx):
        raw = raw_args(ctx).strip()
        clues = LEVELS.get(raw, 32)
        if raw and raw not in LEVELS and not raw.isdigit():
            await ctx.reply("难度写：简单 / 普通 / 困难 / 地狱，或者给个数字（留几个数）")
            return
        if raw.isdigit():
            clues = max(17, min(60, int(raw)))
        # 生成是 81 格回溯（困难难度要做几十次唯一性校验），同步跑会把整个
        # 事件循环卡住 —— 期间所有会话的回复都停。
        puzzle, _sol = await asyncio.to_thread(generate, clues)
        await ctx.reply("🧩 数独（. 是空格）\n" + render(puzzle) +
                        "\n\n想要答案：.数独解 <贴上面这 81 个格子>")

    async def solve_cmd(ctx):
        raw = raw_args(ctx)
        try:
            grid = parse_grid(raw)
        except ValueError as e:
            await ctx.reply(f"{e}\n例：.数独解 530070000600195000098000060800060003400803001700020006060000280000419005000080079")
            return
        if has_conflict(grid):
            await ctx.reply("这题一开局就有冲突（同一行/列/宫里有重复数字），检查一下？")
            return
        sols = solutions(grid, 2)
        if not sols:
            await ctx.reply("这题无解…是不是哪里抄错了？")
            return
        if len(sols) > 1:
            await ctx.reply("这题有多个解，下面只是其中一个：\n" + render(sols[0]))
            return
        await ctx.reply("✅ 解开了\n" + render(sols[0]))

    api.on_command("数独", make, cooldown=5, help=".数独 [简单|普通|困难|地狱] —— 出一题")
    api.on_command("数独解", solve_cmd, cooldown=5, help=".数独解 <81 个格子> —— 求解")
