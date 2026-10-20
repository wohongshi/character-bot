"""说话样例：``.样例`` —— 看 / 加 / 删这个角色的例句（只有主人能用）。

只给主人：这是改人格内容，不是聊天功能；群里任何人都不该能往她嘴里塞话。
面板「人格 · 样例」页写的是同一个文件，两边等价。
"""
from __future__ import annotations

NAME = "说话样例"
DESCRIPTION = "看/加/删说话样例：.样例 / .样例加 用户说|她答 / .样例删 2"
DESCRIPTION_EN = "Tone samples: .样例 / .样例 加 <user>|<reply> / .样例 删 2"
VERSION = "1.0"
USAGE = (".样例 —— 看这个角色的说话样例\n"
         ".样例加 你好呀|哟，来啦～ —— 加一条（竖线左边用户说、右边她答）\n"
         ".样例删 2 —— 删第 2 条")

_SEP = ("|", "｜", "=>", "→")


def register(api):
    def _cid() -> str:
        return str(api.character_id or "")

    def _split(arg_text: str) -> tuple[str, str]:
        for sep in _SEP:
            if sep in arg_text:
                a, b = arg_text.split(sep, 1)
                return a.strip(), b.strip()
        return "", ""

    async def samples_cmd(ctx):
        from bot import examples as ex
        root = _root(ctx)
        cid = _cid()
        args = list(getattr(ctx, "args", None) or [])
        sub = args[0] if args else ""
        rest = " ".join(args[1:]).strip() if len(args) > 1 else ""

        if sub in ("加", "add", "＋", "+"):
            user, bot = _split(rest)
            if not user or not bot:
                return "写法：.样例加 用户说 | 她答（竖线分开两边）"
            try:
                ex.add(root, cid, user, bot)
            except ValueError as e:
                return f"没加上：{e}"
            return f"✅ 加好了（现在 {len(ex.load(root, cid))} 条）"

        if sub in ("删", "del", "remove", "-", "－"):
            raw = rest.split()[0] if rest.split() else ""
            if not raw:
                return "写法：.样例删 2（先 .样例 看序号）"
            try:
                idx = int(raw)
            except ValueError:
                return f"「{raw}」不是序号呀"
            try:
                ex.remove(root, cid, idx)
            except ValueError as e:
                return f"没删成：{e}"
            return f"✅ 删掉了第 {idx} 条（剩 {len(ex.load(root, cid))} 条）"

        items = ex.load(root, cid)
        if not items:
            return (f"「{_name()}」还没有说话样例。\n"
                    f"加一条：.样例加 你好呀 | 哟，来啦～（最多 {ex.MAX_ITEMS} 条）")
        lines = [f"🗣 {_name()} 的说话样例（{len(items)}/{ex.MAX_ITEMS} 条）："]
        for i, it in enumerate(items, 1):
            lines.append(f"{i}. 用户：{it['user']}\n   她：{it['bot']}")
        lines.append("删：.样例删 <序号> · 加：.样例加 用户说 | 她答")
        return "\n".join(lines)

    def _name() -> str:
        try:
            return str((api.character or {}).get("name") or "她")
        except Exception:                                          # noqa: BLE001
            return "她"

    def _root(ctx):
        ad = getattr(ctx, "adapter", None)
        for src in (ad, ctx):
            d = getattr(src, "state_dir", None) if src is not None else None
            if d:
                from pathlib import Path
                return Path(str(d))
        import os
        from pathlib import Path
        base = os.environ.get("KLEE_STATE_DIR")
        return Path(base) if base else Path("state")

    api.on_command("样例", samples_cmd, aliases=("说话样例", "例句"),
                   permission="owner", cooldown=2.0,
                   help=".样例 —— 看/加/删这个角色的说话样例（照这个语气回你）")
