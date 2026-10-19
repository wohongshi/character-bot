"""表情包命令：``.表情`` 列出这个角色有什么，``.表情 开心`` 发一张。

表情存在 ``<state>/stickers/<角色>/<名字>.png``，由面板「人格 → 表情包」管理。
这里只负责「按名字取一张、交给适配器发出去」。

不占 ``.表情包`` 这个别名：命令名和别名是全局唯一的，撞了整条命令会被静默丢掉。
"""
from __future__ import annotations

import os
from pathlib import Path

NAME = "表情包"
NAME_EN = "Stickers"
DESCRIPTION = "把这个角色的表情包发出来：.表情 开心"
DESCRIPTION_EN = "Send one of the character's stickers."
VERSION = "1.0"

MAX_LIST = 24


def _state_root(ctx) -> Path:
    """状态目录。

    `FeatureContext` 本身不带 state_dir；能拿到它的是**适配器**：
    QQ 侧 `_RouteView.__getattr__` 会转发到 runtime，面板侧 `_WebAdapter`
    直接有这一项。最后一条回退看 `KLEE_STATE_DIR`（干跑测试台靠它隔离）。
    """
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


def register(api):
    async def cmd(ctx):
        from bot import stickers as st
        root = _state_root(ctx)
        cid = _cid(ctx)
        want = str(getattr(ctx, "arg_text", "") or "").strip()
        items = st.list_pack(root, cid)
        if not items:
            await ctx.reply("这边还没有表情包～去面板「人格 → 表情包」传几张，文件名写它的意思就行。")
            return
        if not want:
            names = [x["name"] for x in items][:MAX_LIST]
            more = "" if len(items) <= MAX_LIST else f"（还有 {len(items) - MAX_LIST} 张）"
            await ctx.reply("😀 我有这些表情：" + "、".join(names) + more + "\n用 .表情 名字 发一张")
            return
        item = st.pick(root, cid, want)
        if item is None:
            await ctx.reply(f"没有「{want}」这张诶。发 .表情 看看有哪些？")
            return
        path = st.pack_dir(root, cid) / item["file"]
        # 走适配器的标准发图口：QQ 是 send_image，面板是写进 job["images"]
        res = await ctx.adapter.send_image_file(ctx.chat_id, str(path))
        if not getattr(res, "success", True):
            await ctx.reply("图没发出去（这张表情的目录不在白名单里）")

    api.on_command("表情", cmd, cooldown=1, help=".表情 [名字] —— 列出 / 发一张表情包")
