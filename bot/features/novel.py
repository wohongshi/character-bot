"""茜特菈莉 · 追更书架：.追更 [书名]、.书架、.追更 删 [书名]

大萨满的轻小说书架，按会话保存。
"""
from _plugstore import chat_key, clip, db_get, db_set, state_path

NAME = "追更书架"
DESCRIPTION = "把想看的轻小说塞进大萨满的书架：.追更 书名、.书架"
VERSION = "1.0"
CHARACTERS = ("citlali",)

FILE = state_path("citlali_novels")
MAX = 30


def _key(ctx) -> str:
    return chat_key(ctx)


def register(api):
    async def follow(ctx):
        arg = clip(ctx.arg_text, 60)
        if arg.startswith("删"):
            name = arg[1:].strip()
            if not name:
                await ctx.reply("要删哪本？写成「.追更 删 书名」。")
                return
            books = db_get(FILE, _key(ctx), [])
            left = [b for b in books if b != name]
            if len(left) == len(books):
                await ctx.reply(f"书架上没有《{name}》哦。")
                return
            db_set(FILE, _key(ctx), left)
            await ctx.reply(f"🗑 把《{name}》从书架上拿下来了。……其实我还没看完。")
            return
        if not arg:
            await ctx.reply("要追哪本？写成「.追更 书名」。我最近在看一本讲末日图书馆的，还不错。")
            return
        books = [str(b) for b in (db_get(FILE, _key(ctx), []) or []) if isinstance(b, str)]
        if arg in books:
            await ctx.reply(f"《{arg}》已经在书架上了，排在还书日期后面。")
            return
        if len(books) >= MAX:
            await ctx.reply(f"书架满了（{MAX} 本）。先删一本吧——「.追更 删 书名」。")
            return
        books.append(arg)
        db_set(FILE, _key(ctx), books)
        await ctx.reply(f"📚 记下了：《{arg}》。\n看完记得告诉我结局，我懒得自己翻。")

    async def shelf(ctx):
        books = [str(b) for b in (db_get(FILE, _key(ctx), []) or []) if isinstance(b, str)]
        if not books:
            await ctx.reply("书架是空的。……正好，省得我又熬夜。")
            return
        lines = [f"{i + 1}. 《{b}》" for i, b in enumerate(books)]
        await ctx.reply("📚 大萨满的书架：\n" + "\n".join(lines) +
                        f"\n共 {len(books)} 本。别催，看小说也是正经事。")

    api.on_command("追更", follow, aliases=("加书",), help=".追更 书名 / .追更 删 书名 —— 管理书架")
    api.on_command("书架", shelf, aliases=("我的书架",), help=".书架 —— 看看大萨满在追什么")
