"""事件旁听示例（框架自带的「怎么写插件」参考实现）。

它演示三件框架能力，正是第三方插件最常用的：

1. **订阅非消息事件**：`api.on_notice` / `api.on_request`
   —— 消息事件走 `on_message`，这两类以前插件根本拿不到。
2. **按平台能力降级**：`ctx.supports("group_member_list")`
   —— 换成 Webhook 适配器时自动跳过只有 QQ 才有的功能，而不是抛 AttributeError。
3. **自己记状态**：`api.state`（重载即清）+ 可选落盘到 `state/event_log.jsonl`

装上它以后，`.事件` 能看到最近旁听到的 notice / request。
不需要它就把 `config.json` 的 enabled 里去掉 `eventlog`。
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path

logger = logging.getLogger("klee.feature.eventlog")

NAME = "事件旁听"
DESCRIPTION = "把戳一戳 / 入群 / 好友申请这类非消息事件记下来，.事件 查看"
VERSION = "1.0"
AUTHOR = "framework-demo"
USAGE = "在面板「插件」里启用即可；QQ 里发 .事件 看最近 20 条"

MAX_KEEP = 200


def register(api):
    base = (api.settings or {}).get("state_dir") or (Path(__file__).resolve().parent / "state")
    base = Path(base)
    log_path = Path(base) / "event_log.jsonl"
    recent = api.state.setdefault("recent", [])
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)   # 每个事件都 mkdir 是白费
    except OSError as e:
        logger.warning("事件日志目录建不出来（%s）：%s", log_path.parent, e)

    _warned = {"done": False}

    def _append(row: dict) -> None:
        try:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        except OSError as e:
            # 以前是静默 pass：盘满/没权限时日志悄悄不长了，谁也不知道。
            if not _warned["done"]:
                _warned["done"] = True
                logger.warning("事件日志写入失败（后续同类不再提示）：%s", e)

    async def _push(kind: str, label: str, extra: dict) -> None:
        row = {"ts": time.time(), "kind": kind, "label": label, **extra}
        recent.append(row)
        del recent[:-MAX_KEEP]
        await asyncio.to_thread(_append, row)      # 别在事件循环里做文件 IO

    async def on_notice(ctx):
        """戳一戳 / 入群 / 退群 / 撤回 ……"""
        kind = ctx.notice_type or "notice"
        who = ctx.user_id or "?"
        where = f"群 {ctx.group_id}" if ctx.is_group else "私聊"
        await _push("notice", f"{kind} · {where} · {who}",
                    {"platform": ctx.platform, "chat_id": ctx.chat_id})
        # 注意：内置 runtime 已经会回「戳一戳」（可在配置里关），
        # 这里只记录，不再抢着回 —— 否则一次戳会收到两句。
        # 要接管这个行为，就把配置里的 notify_poke 关掉，然后在这里 api.send()。

    async def on_request(ctx):
        """好友申请 / 入群申请 —— 只记录，绝不自动通过。"""
        kind = ctx.request_type or "request"
        # comment 可能是 None / 非字符串（上游 JSON 什么都有），[:30] 会 TypeError，
        # 异常被钩子链吞掉 → 这条申请既不记录也不报错。
        comment = str(ctx.raw.get("comment") or "")[:30]
        await _push("request", f"{kind} · {ctx.user_id} · {comment}",
                    {"platform": ctx.platform, "flag": str(ctx.raw.get("flag") or "")})

    async def events_cmd(ctx):
        rows = recent[-20:]
        if not rows:
            await ctx.reply("还没旁听到什么事件。")
            return
        lines = []
        for r in reversed(rows):
            t = time.strftime("%H:%M:%S", time.localtime(r["ts"]))
            lines.append(f"{t} [{r['kind']}] {r['label']}")
        await ctx.reply("📋 最近的事件：\n" + "\n".join(lines))

    api.on_notice(on_notice)
    api.on_request(on_request)
    api.on_command("事件", events_cmd, aliases=("事件日志",), cooldown=3,
                   help=".事件 —— 最近旁听到的 notice / request")
