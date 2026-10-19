"""待办清单：``.待办`` / ``.待办加`` / ``.待办完成`` / ``.待办删``。

通用插件。状态写在 ``state/todos.json``（按会话隔离）。

设计上刻意很「土」：
- 编号从 1 开始、**只在本会话内有效**，删掉一条后面的会补位（列表短，好认）；
- 每条最多 100 字，一个会话最多 30 条 —— 待办清单一旦长到需要分页，
  就说明该用正经的待办 App 了；
- 完成的任务默认不显示，`.待办 全部` 才看得到。
"""
from __future__ import annotations

from _plugstore import chat_key, db_update, load, safe_text, state_path

NAME = "待办清单"
NAME_EN = "Todo list"
DESCRIPTION = "记下要做的事，做完勾掉。" 
DESCRIPTION_EN = "A small per-chat todo list."
VERSION = "1.0"

FILE = state_path("todos")
MAX_ITEMS = 30
MAX_LEN = 100


def raw_args(ctx) -> str:
    t = str(getattr(ctx, "text", "") or "").strip()
    body = t[1:].strip() if t[:1] in "./／" else t
    parts = body.split(None, 1)
    return parts[1] if len(parts) > 1 else ""


def items_of(key: str) -> list[dict]:
    d = load(FILE).get(key) or []
    return d if isinstance(d, list) else []


def render(items: list[dict], show_done: bool = False) -> str:
    if not items:
        return "清单是空的～用 .待办加 买菜 这样加一条"
    rows = []
    done_n = 0
    for i, it in enumerate(items, 1):
        if it.get("done"):
            done_n += 1
            rows.append(f"{i}. ✅ {it.get('text')}")
            continue
        rows.append(f"{i}. ☐ {it.get('text')}")
    head = f"📋 待办（{len(items) - done_n} 件没做 / 共 {len(items)}）"
    return head + "\n" + "\n".join(rows)


def register(api):
    async def listing(ctx):
        raw = raw_args(ctx).strip()
        items = items_of(chat_key(ctx))
        await ctx.reply(render(items, show_done=raw in ("全部", "all", "-a")))

    async def add(ctx):
        text = safe_text(raw_args(ctx), MAX_LEN)
        if not text:
            await ctx.reply("用法：.待办加 买菜\n（要带一句说明就写在后面）")
            return
        key = chat_key(ctx)
        box: dict = {}

        def mutate(items):
            items = list(items or [])
            if len(items) >= MAX_ITEMS:
                box["full"] = True
                return items
            items.append({"text": text, "done": False})
            return items

        items = db_update(FILE, key, mutate, [])
        if box.get("full"):
            await ctx.reply(f"最多 {MAX_ITEMS} 条～先 .待办删 几条吧")
            return
        await ctx.reply(f"✅ 加上了（第 {len(items)} 条）：{text}")

    async def done(ctx):
        raw = raw_args(ctx).strip()
        if not raw.isdigit():
            await ctx.reply("用法：.待办完成 2（编号看 .待办）")
            return
        idx = int(raw)
        key = chat_key(ctx)
        got: dict = {}

        def mutate(items):
            items = list(items or [])
            if not (1 <= idx <= len(items)):
                return items
            it = dict(items[idx - 1])
            it["done"] = not it.get("done")     # 再点一次 = 取消完成
            items[idx - 1] = it
            got.update(it)
            return items

        db_update(FILE, key, mutate, [])
        if not got:
            await ctx.reply("没有这个编号～用 .待办 看看")
            return
        state = "做完了" if got.get("done") else "又变回没做了"
        await ctx.reply(f"{'🎉' if got.get('done') else '↩️'} 第 {idx} 条{state}：{got.get('text')}")

    async def drop(ctx):
        raw = raw_args(ctx).strip()
        if not raw.isdigit():
            await ctx.reply("用法：.待办删 2（编号看 .待办）")
            return
        idx = int(raw)
        key = chat_key(ctx)
        gone: dict = {}

        def mutate(items):
            items = list(items or [])
            if not (1 <= idx <= len(items)):
                return items
            gone.update(items.pop(idx - 1))
            return items

        db_update(FILE, key, mutate, [])
        if gone:
            await ctx.reply(f"删掉了：{gone.get('text')}")
        else:
            await ctx.reply("没有这个编号～用 .待办 看看")

    async def clear(ctx):
        raw = raw_args(ctx).strip()
        if raw not in ("确认", "yes", "确定"):
            await ctx.reply("真要清空整份待办？回一句「.待办清 确认」")
            return
        db_update(FILE, chat_key(ctx), lambda _items: [], [])
        await ctx.reply("清空了。")

    api.on_command("待办", listing, cooldown=2, help=".待办 [全部] —— 看清单")
    api.on_command("待办加", add, cooldown=2, help=".待办加 <内容> —— 加一条")
    api.on_command("待办完成", done, cooldown=2, help=".待办完成 <编号> —— 勾掉/取消")
    api.on_command("待办删", drop, cooldown=2, help=".待办删 <编号> —— 删一条")
    api.on_command("待办清", clear, cooldown=3, help=".待办清 确认 —— 全部清空")

    async def tool_todo(args: dict, ctx=None) -> str:
        """给 agent 用：op = list / add / done。"""
        op = str(args.get("op") or "list").lower()
        # 和命令用**同一个**会话键，否则 agent 加的事在 `.待办` 里看不到
        chat = getattr(ctx, "chat", None) or {}
        is_group = bool(chat.get("is_group"))
        gid = str(chat.get("group_id") or "")
        uid = str(chat.get("user_id") or "")
        key = ("g:" + gid) if (is_group and gid) else ("u:" + (uid or "anon"))
        text = safe_text(args.get("text") or "", MAX_LEN)
        if op == "add":
            if not text:
                return "要加什么？text 不能为空"
            def mutate(items):
                items = list(items or [])
                items.append({"text": text, "done": False})
                return items[:MAX_ITEMS]
            items = db_update(FILE, key, mutate, [])
            return f"已加入待办（共 {len(items)} 条）：{text}"
        if op == "done":
            try:
                idx = int(args.get("index") or 0)
            except (TypeError, ValueError):
                return "index 要是数字"
            got: dict = {}
            def mutate(items):
                items = list(items or [])
                if 1 <= idx <= len(items):
                    it = dict(items[idx - 1])
                    it["done"] = True
                    items[idx - 1] = it
                    got.update(it)
                return items
            db_update(FILE, key, mutate, [])
            return f"完成了：{got.get('text')}" if got else "没有这个编号"
        return render(items_of(key), show_done=False)

    api.add_tool(
        "todo",
        description="本会话的待办清单：列出来 / 加一条 / 勾掉一条。",
        parameters={"type": "object",
                    "properties": {
                        "op": {"type": "string", "enum": ["list", "add", "done"],
                               "description": "list 看、add 加、done 完成"},
                        "text": {"type": "string", "description": "add 时必填"},
                        "index": {"type": "integer", "description": "done 时的编号"}},
                    "required": ["op"]},
        handler=tool_todo,
    )
