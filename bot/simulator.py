"""消息模拟器：这一句话在群里发出去，会发生什么？

为什么值得做
------------
分发链有五层（群策略 → 命令 → 关键词规则 → @ 门槛 → NLU → 模型），
还有权限、冷却、静默时段这些开关。出了问题只能靠「在群里发一句看看」，
而那句话是**真的**会发出去、真的会执行命令的（`.待办清 确认` 就是不可逆的）。

这个模块把整条链**只读地**跑一遍：不发送、不执行 handler、不写历史、不动冷却。
判定逻辑不复制一份 —— 命令层直接调 `FeatureManager.explain_command`，
关键的那句判断只有一处实现，模拟器和真实分发不可能对不上。

不模拟的部分（诚实标注，不假装）
--------------------------------
- **入站钩子**（屏蔽词/复读检测等）跳过：它们的契约是「可以回话、可以拦截」，
  跑一遍就等于真的回了一句。模拟结果里会单列一行「未模拟」。
- **模型回复内容**：只判断「会交给模型」，不会为了预览去花一次 token。
"""

from __future__ import annotations

from pathlib import Path

MAX_TEXT = 2000


class _FakeAdapter:
    """判定只需要两样：owner_id（谁是主人）和 chat_id_of（会话形态）。"""

    platform = "sim"

    def __init__(self, owner_id: str = ""):
        self.owner_id = str(owner_id or "")

    def chat_id_of(self, is_group, group_id, user_id):
        return (f"group:{group_id}" if is_group else f"private:{user_id}")


def _step(sid: str, label: str, state: str, detail: str) -> dict:
    """state: pass / block / skip / info"""
    return {"id": sid, "label": label, "state": state, "detail": detail}


def simulate(*, text: str, mgr, state_dir: Path | str, rules_items=None,
             is_group: bool = False, mentioned: bool = False,
             user_id: str = "10001", user_name: str = "访客", role: str = "member",
             owner_id: str = "", base_policy: str = "mention",
             chat_id: str = "", group_id: str = "", now: float | None = None,
             model_ready: bool = True) -> dict:
    """跑一遍判定链。返回 `{steps, action, text, command, reason}`。

    `action` 取 `ignore` / `command` / `rule_reply` / `model` 之一，
    表示「真实场景里这条消息最后会走到哪」。
    """
    from . import chatpolicy, rules as rules_mod
    from .framework import FeatureContext

    raw = str(text or "")[:MAX_TEXT]
    steps: list[dict] = []
    chat = chat_id or (f"group:{group_id}" if is_group else f"private:{user_id}")

    def done(action: str, reason: str, extra: dict | None = None) -> dict:
        out = {"text": raw, "chat_id": chat, "is_group": bool(is_group),
               "action": action, "reason": reason, "steps": steps}
        out.update(extra or {})
        return out

    # 0) 入站钩子 —— 只声明，不执行（见模块说明）
    steps.append(_step("hooks", "入站钩子（屏蔽词等）", "skip",
                       "没模拟：钩子可能回话或拦截，跑一遍就等于真发了一条"))
    if not str(owner_id or "").strip():
        # owner_id 空 = 谁都不是主人：`.仅主人` 的命令连你自己试跑都会被拦。
        # 这是个很容易误判的坑，所以明说。
        steps.append(_step("owner", "主人身份", "info",
                           "没配置主人 QQ，本次判定里「没有人是主人」"))

    # 1) 会话策略（含静默时段）
    pol = chatpolicy.resolve(state_dir, chat, base_policy, now=now)
    label = chatpolicy.MODES_CN.get(pol["mode"], pol["mode"])
    if pol["why"]:
        steps.append(_step("policy", "会话策略", "info", f"{label}（{pol['why']}）"))
    else:
        steps.append(_step("policy", "会话策略", "info", f"{label}（跟全局）"))
    if pol["mode"] == "disabled":
        # runtime 现在对私聊也生效（面板允许单独给私聊设「完全不理」）
        return done("ignore", "这个会话被设成「完全不理」，连命令也不接",
                    {"policy": pol})
    if pol["quiet"]:
        steps.append(_step("quiet", "静默时段", "info",
                           f"{'~'.join(pol.get('quiet_win') or [])}：只接命令，不聊天"))
    else:
        steps.append(_step("quiet", "静默时段", "pass", "现在不在静默时段"))

    # 2) 命令层 —— 和真实分发走同一个函数
    ctx = FeatureContext(adapter=_FakeAdapter(owner_id), text=raw,
                         is_group=bool(is_group), group_id=str(group_id or ""),
                         user_id=str(user_id or ""), user_name=str(user_name or ""),
                         message_id="", mentioned=bool(mentioned), role=str(role or ""))
    verdict = mgr.explain_command(ctx)
    if verdict["action"] == "run":
        cmd = verdict["cmd"]
        where = "群里" if is_group else "私聊"
        steps.append(_step("cmd", "命令", "pass",
                           f"命中 .{cmd.name}（插件 {cmd.plugin}，{where}可用，"
                           f"权限 {verdict['permission']}）"))
        return done("command", f"插件 {cmd.plugin}"
                    + (f" · 参数 {' '.join(verdict['args'])}" if verdict["args"] else ""),
                    {"command": cmd.name, "plugin": cmd.plugin, "args": verdict["args"],
                     "policy": pol})
    if verdict["action"] == "skip":
        cmd = verdict.get("cmd")
        steps.append(_step("cmd", "命令", "block",
                           f".{cmd.name if cmd else verdict.get('token', '?')} 会执行但被拦下：{verdict['reason']}"))
        return done("ignore", f"命令被拦下：{verdict['reason']}",
                    {"command": cmd.name if cmd else "", "policy": pol})
    # 不是命令
    steps.append(_step("cmd", "命令", "pass", verdict.get("reason") or "不是命令"))

    # 3) 关键词规则
    hit = None
    try:
        items = rules_items if rules_items is not None else rules_mod.load(state_dir)
        hit = rules_mod.match(items, raw, is_group=bool(is_group), chat_id=chat)
    except Exception as e:                                         # noqa: BLE001
        steps.append(_step("rule", "关键词规则", "skip", f"读规则失败（按没命中）：{e}"))
    if hit:
        act = str(hit.get("action") or "reply")
        if act == "ignore":
            steps.append(_step("rule", "关键词规则", "block",
                               f"命中「{hit.get('name') or hit.get('id')}」→ 不回"))
            return done("ignore", "命中关键词规则，选择不回", {"rule": hit.get("id"), "policy": pol})
        if act == "command":
            full = str(hit.get("command") or "").lstrip("./／").strip()
            # 运行时是把整串（含参数）塞回 handle_command 让它自己解析的。
            # 这里也走同一个 explain_command —— 作用域/权限/冷却同样生效，
            # 否则会报「会执行 .仅主人命令」，而真实场景被拦下。
            token = full.split()[0] if full else ""
            probe = FeatureContext(adapter=_FakeAdapter(owner_id), text="." + full,
                                   is_group=bool(is_group), group_id=str(group_id or ""),
                                   user_id=str(user_id or ""), user_name=str(user_name or ""),
                                   message_id="", mentioned=bool(mentioned), role=str(role or ""))
            v2 = mgr.explain_command(probe) if token else {"action": "none"}
            exists = v2.get("action") in ("run", "skip")
            if v2.get("action") == "skip":
                steps.append(_step("rule", "关键词规则", "block",
                                   f"命中「{hit.get('name') or hit.get('id')}」→ 当命令跑 .{full}"
                                   f"，但被拦下：{v2.get('reason')}"))
                return done("ignore", f"规则命令被拦下：{v2.get('reason')}",
                            {"command": token, "rule": hit.get("id"), "policy": pol})
            steps.append(_step("rule", "关键词规则", "pass" if exists else "block",
                               f"命中「{hit.get('name') or hit.get('id')}」→ 当命令跑 .{full}"
                               + ("" if exists else "（这个命令不存在，会退回正常流程）")))
            if exists:
                return done("command", f"规则把它当命令 .{token} 跑",
                            {"command": token, "rule": hit.get("id"), "policy": pol})
        else:
            reply = rules_mod.render_reply(hit, user=str(user_name or "你"))
            steps.append(_step("rule", "关键词规则", "pass",
                               f"命中「{hit.get('name') or hit.get('id')}」→ 回一句"))
            return done("rule_reply", f"命中规则「{hit.get('name') or hit.get('id')}」",
                        {"reply": reply,
                                                         "rule": hit.get("id"), "policy": pol})
    else:
        steps.append(_step("rule", "关键词规则", "pass", "没有命中"))

    # 4) @ 门槛 / 群策略
    if is_group and pol["mode"] == "mention" and not mentioned:
        # runtime 这一层只看 mentioned（静默例外是**下一层**的事：它决定
        # 「被 @ 的那条要不要回」，不会让没被 @ 的消息通过）。这里必须一致。
        steps.append(_step("mention", "@ 门槛", "block",
                           "群里没被 @，这条不会处理（关键词规则已经在上面查过了）"))
        return done("ignore", "群里没被 @（策略：被叫到才应）", {"policy": pol})
    elif is_group:
        steps.append(_step("mention", "@ 门槛", "pass",
                           "被 @ 了" if mentioned else "这个群设成每条都看"))
    else:
        steps.append(_step("mention", "@ 门槛", "pass", "私聊，不需要 @"))

    # 静默时段：和 runtime 同一个条件 —— `quiet and not (mentioned and quiet_mention)`
    if pol["quiet"] and not (mentioned and pol["quiet_mention"]):
        steps.append(_step("quiet2", "静默时段", "block",
                           "静默时段里不聊天（命令照常；被 @ 是否例外看会话设定）"))
        return done("ignore", "静默时段，不聊天", {"policy": pol})

    # 5) NLU（自然语言触发）
    try:
        nlu = mgr.nlu_match(raw.strip())
    except Exception as e:                                         # noqa: BLE001
        nlu = None
        steps.append(_step("nlu", "自然语言触发", "skip", f"匹配失败：{e}"))
    if nlu:
        # `nlu_match` 返回的常带参数（"生日 可莉" / "天气 北京"），运行时是整串
        # 塞回 handle_command 由它自己切 —— 拿整串查表会永远查不到。
        full = str(nlu).strip()
        token = full.split()[0] if full else ""
        exists = bool(token) and mgr.commands.get(token.lower()) is not None
        steps.append(_step("nlu", "自然语言触发", "pass" if exists else "block",
                           f"整句命中 → .{full}" + ("" if exists else "（对应命令不存在）")))
        if exists:
            return done("command", f"自然语言命中 .{full}", {"command": token, "policy": pol})
    else:
        steps.append(_step("nlu", "自然语言触发", "pass", "没有整句命中"))

    # 6) 模型
    if not model_ready:
        steps.append(_step("model", "交给模型", "block", "模型没配好，真实场景会回一句「脑袋卡住了」"))
        return done("ignore", "模型没配好", {"policy": pol})
    steps.append(_step("model", "交给模型", "pass", "会带着人格/知识库/记忆跑一轮对话"))
    return done("model", "会带着人格、知识库和记忆跑一轮", {"policy": pol})
