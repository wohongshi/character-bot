"""赞我：``.赞我`` —— 让可莉给你的 QQ 名片点 10 个赞（每天一次）。

走 OneBot ``send_like`` 动作（NapCat 支持，适配器 ``_action`` 通道）。
state/likes.json 记录每人每天点过没有，失败不记账（可以重试）。
"""

import datetime
import json
from pathlib import Path

NAME = "点赞"
DESCRIPTION = "让她给你的名片点赞：.赞我（每天一次）"
VERSION = "1.0"

CHARACTERS = ('klee',)

def _state_root() -> "pathlib.Path":
    """状态目录：`KLEE_STATE_DIR` 可覆盖（干跑测试台靠它把状态写到临时目录）。"""
    import os
    base = os.environ.get("KLEE_STATE_DIR")
    from pathlib import Path as _P
    return _P(base) if base else _P(__file__).resolve().parent / "state"


BASE = _state_root()
STATE_FILE = BASE / "likes.json"
TIMES = 10


def _today() -> str:
    return datetime.date.today().isoformat()


def _load() -> dict:
    try:
        d = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(d: dict) -> None:
    BASE.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    tmp.replace(STATE_FILE)


def register(api):
    async def like_cmd(ctx):
        d = _load()
        if d.get(ctx.user_id) == _today():
            await ctx.reply("诶嘿，今天已经给你点过赞啦！名片亮闪闪的✨（明天再来嘛）")
            return
        action = getattr(ctx.adapter, "_action", None)
        if not action:
            await ctx.reply("呜……现在点不了赞，等会儿再让可莉试试嘛")
            return
        try:
            resp = await action("send_like",
                                {"user_id": str(ctx.user_id), "times": TIMES},
                                timeout=15.0)
        except Exception:                                    # noqa: BLE001
            resp = {}
        code = int((resp or {}).get("retcode", -1))
        if code == 0:
            d[ctx.user_id] = _today()
            _save(d)
            await ctx.reply(f"叮！给你点了 {TIMES} 个赞～快去看看吧！[表情:呲牙]")
            return
        wording = str((resp or {}).get("wording") or (resp or {}).get("message") or "")
        if "上限" in wording or "limit" in wording.lower():
            d[ctx.user_id] = _today()                        # 已到 QQ 侧上限，今天不再试
            _save(d)
            await ctx.reply("诶……今天的点赞额度好像到顶了，明天再给你点嘛～")
            return
        await ctx.reply("呜，这次没点上，等一会儿再试试嘛")

    api.on_command("赞我", like_cmd, aliases=("点赞",), cooldown=10,
                   help=".赞我 —— 给你的 QQ 名片点 10 个赞（每天一次）")
