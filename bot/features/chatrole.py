"""本会话固定角色：``.角色 [名字|默认]`` —— 一个群一个角色，各聊各的。

为什么需要它
------------
面板 🎭 页切的是**全局**角色：一换，所有群一起换。可实际上常常是
「A 群跟着可莉，B 群想跟着胡桃」。这个命令把「某个群/私聊用哪个角色」
钉下来，钉住的会话就再跟着全局走了。

- 只认群里能管事的人（主人 / 群主 / 群管理）——别人不该能换掉整个群的角色；
- `.角色 默认` 取消固定，回到「跟着全局」；
- 面板「角色」页有一张同样的卡片，两边写的是同一个文件（state/chat_chars.json）。
"""
from __future__ import annotations

NAME = "本会话角色"
DESCRIPTION = "给这个群/私聊固定一个角色：.角色 <名字>"
DESCRIPTION_EN = "Pin a character to this chat: .角色 <name>"
VERSION = "1.0"
USAGE = ".角色 —— 看本会话的角色\n.角色 <名字> —— 固定成某个角色\n.角色 默认 —— 取消固定，跟着全局"

_OFF = {"默认", "取消", "取消固定", "跟随", "全局", "跟全局", "clear", "off", "reset", "auto"}


def _norm(s: str) -> str:
    return "".join(str(s or "").split()).lower()


def _pick(chars: list, want: str) -> dict | None:
    """按 id / 中文名 / 英文名找角色；先精确，再前缀（`.角色 亚` → 亚托莉）。"""
    w = _norm(want)
    if not w:
        return None
    for key in ("id", "name", "en"):
        for c in chars:
            if _norm(c.get(key)) == w:
                return c
    for key in ("name", "en", "id"):
        for c in chars:
            if _norm(c.get(key)).startswith(w):
                return c
    return None


def register(api):
    async def role_cmd(ctx):
        chat = str(getattr(ctx, "chat_id", "") or "")
        if not chat:
            return "这个会话还没有 id，先跟她说一句话再设～"
        chars = api.list_characters()
        if not chars:
            return "现在读不到角色库，等面板「角色」页正常了再来～"
        args = [a for a in (getattr(ctx, "args", None) or []) if str(a).strip()]
        bound = api.chat_character(chat)

        if not args:
            now = "、".join(c["name"] for c in chars)
            head = (f"📍 本会话固定为「{_name_of(chars, bound)}」" if bound
                    else "📍 本会话还没固定，跟着全局角色走")
            return (f"{head}\n"
                    f"固定成谁：.角色 <名字>（{now}）\n"
                    f"取消固定：.角色 默认")

        want = " ".join(args).strip()
        if _norm(want) in _OFF:
            if not bound:
                return "本来就没固定呀，一直跟着全局角色～"
            api.bind_chat_character(chat, "", by=ctx.user_id)
            return "✅ 已取消固定，这个会话又跟着全局角色走了"

        hit = _pick(chars, want)
        if not hit:
            now = "、".join(c["name"] for c in chars)
            return f"没有叫「{want}」的角色呀…现在是：{now}"
        if bound == hit["id"]:
            return f"这个会话本来就固定成「{hit['name']}」啦～"
        try:
            api.bind_chat_character(chat, hit["id"], by=ctx.user_id)
        except Exception as e:                                     # noqa: BLE001
            return f"没设上：{e}"
        return (f"✅ 这个会话之后由「{hit['name']}」来答\n"
                f"（想换回来：.角色 默认）")

    api.on_command("角色", role_cmd, aliases=("本群角色", "绑角色", "char"),
                   scope="both", permission="admin", cooldown=3.0,
                   help=".角色 <名字> —— 这个群固定跟着某个角色（.角色 默认 取消）")


def _name_of(chars: list, cid: str) -> str:
    for c in chars:
        if c.get("id") == cid:
            return str(c.get("name") or cid)
    return cid or "（未知）"
