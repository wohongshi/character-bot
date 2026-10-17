#!/usr/bin/env python3
"""干跑测试台：把一条虚构的消息喂给插件层，报告会发生什么（不碰真 QQ）。

给 klee-studio 的「插件工坊」用。用法：

    <pm-env>/bin/python dryrun.py '{"text":".r 2d6","is_group":true,"mentioned":false,...}'

输出一行 JSON：命中哪个命令 / 被哪个钩子拦截 / 有没有回复 / 最终去向（进 agent 还是止步）。
判定逻辑与 OneBotAdapter._on_message 的顺序一致：命令 → 入站钩子 → 随机搭话闸门。
"""

import asyncio
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

_HOME = Path(__file__).resolve().parent
FEAT = Path(os.environ.get("KLEE_FEATURES_DIR") or (_HOME / "bot" / "features"))
PLUG = Path(os.environ.get("KLEE_PLUGIN_DIR") or (_HOME.parent / "klee-plugin"))

sys.path.insert(0, "/usr/local/lib/hermes-agent")
sys.path.insert(0, str(PLUG))

# 干跑不许写生产状态（曾经踩过：干跑一条 .提醒 就往真 reminders.json 里塞了假提醒）。
# 做法：装载插件后，把这些模块里所有指向 `onebot_features/state/` 的 Path 常量
# （BASE / STATE_FILE / LOCK_FILE / STATS_FILE……）统统改写到本次临时目录。
TMP_STATE = Path(tempfile.mkdtemp(prefix="klee-dryrun-"))
_LIVE_STATE = str(FEAT / "state")
os.environ["KLEE_STATE_DIR"] = str(TMP_STATE / "state")   # 插件在 load/register 时就用它


def _plugin_globals(mgr) -> list:
    """拿到各插件模块的 globals（插件模块不注册进 sys.modules，只能从处理函数反查）。"""
    out = []
    fns = [c.handler for c in mgr.commands.values()] + [h.handler for h in mgr.hooks_in] \
        + [h.handler for h in mgr.hooks_out]
    for fn in fns:
        g = getattr(fn, "__globals__", None)
        if isinstance(g, dict) and not any(g is o for o in out):
            out.append(g)
    return out


def _isolate_state(mgr) -> list:
    """把插件 globals 里指向 `onebot_features/state/` 的 Path 常量改写到临时目录，返回改了哪些。"""
    moved = []
    for g in _plugin_globals(mgr):
        mod_name = g.get("__name__", "?")
        for name, val in list(g.items()):
            if isinstance(val, Path) and str(val).startswith(_LIVE_STATE):
                g[name] = TMP_STATE / val.name
                moved.append(f"{mod_name}.{name}")
    return moved


async def main(spec: dict) -> dict:
    import adapter as A
    from features import FeatureContext

    # 读真实配置（chime 概率/冷却/唤醒词/策略……），干跑才跟生产一致
    real_extra: dict = {}
    config_note = ""
    try:
        import yaml
        cfg = yaml.safe_load((KLEE / "config.yaml").read_text(encoding="utf-8")) or {}
        real_extra = dict(((cfg.get("platforms") or {}).get("onebot") or {}).get("extra") or {})
    except Exception as e:                                     # noqa: BLE001
        config_note = f"读配置失败（干跑将用默认值）：{type(e).__name__}: {e}"
    real_extra.update({"ws_url": "ws://127.0.0.1:1", "self_id": "1",
                       "features_dir": str(FEAT),
                       "state_file": str(TMP_STATE / "onebot_bot.json")})

    class Cfg:
        extra = real_extra

    ad = A.OneBotAdapter(Cfg())
    replies = []

    async def fake_send(chat_id, content, **kw):
        replies.append(content)

        class R:
            success = True
            message_id = "dry"
            retryable = False

        return R()

    ad.send = fake_send
    ad.features.load()
    mgr = ad.features
    isolated = _isolate_state(mgr)                             # 干跑只写临时目录
    before = dict(mgr.hits)

    is_group = bool(spec.get("is_group"))
    ctx = FeatureContext(
        adapter=ad, text=str(spec.get("text") or ""),
        is_group=is_group, group_id=str(spec.get("group_id") or "dryrun-group"),
        user_id=str(spec.get("user_id") or "10001"),
        user_name=str(spec.get("user_name") or "匿名旅人"),
        message_id="dry-1", mentioned=bool(spec.get("mentioned")),
        role=str(spec.get("role") or "member"))
    if spec.get("as_owner"):
        ad.owner_id = ctx.user_id

    result: dict = {"text": ctx.text, "replies": []}
    if config_note:
        result["config_warning"] = config_note
    result["isolated_state"] = isolated
    # 面板上要能看出「这次干跑的状态到底写哪去了」：主机制是环境变量，Path 改写是兜底
    result["state_dir"] = os.environ.get("KLEE_STATE_DIR") or str(TMP_STATE)
    consumed = await mgr.handle_command(ctx)
    result["consumed"] = consumed
    result["text"] = ctx.text
    if consumed:
        result["command"] = ctx.command or ""
        result["route"] = "命令命中 → 已消费（不会进 agent）"
    else:
        blocked = not await mgr.run_message_hooks(ctx)
        result["blocked"] = blocked
        result["text"] = ctx.text
        if blocked:
            result["route"] = "被入站钩子拦截（不会进 agent）"
        elif is_group and not ctx.mentioned:
            p = ad.chime_probability
            result["route"] = (f"群聊没被 @ → 随机搭话闸门（概率 {p:.0%}"
                               + ("，命中才进 agent）" if p > 0 else " = 关闭 → 消息止步于此）"))
        elif is_group:
            result["route"] = "被 @/引用 → 进 agent（可莉会正常看到）"
        else:
            result["route"] = "私聊 → 进 agent（可莉会正常看到）"

    after = dict(mgr.hits)
    result["hits"] = [{"plugin": k, "count": after[k] - before.get(k, 0)}
                      for k in after if after.get(k, 0) > before.get(k, 0)]
    denied = [h for h in result["hits"] if h["plugin"].endswith(":denied")]
    if denied:
        result["route"] = "命令存在但权限不足 → 静默吞掉（不回复、不进 agent）"
        result["silent"] = True
    else:
        result["silent"] = bool(consumed and not result["hits"])
    result["replies"] = replies
    return result


if __name__ == "__main__":
    try:
        spec_in = json.loads(sys.argv[1]) if len(sys.argv) > 1 else json.load(sys.stdin)
        print(json.dumps(asyncio.run(main(spec_in)), ensure_ascii=False))
    finally:
        shutil.rmtree(TMP_STATE, ignore_errors=True)
