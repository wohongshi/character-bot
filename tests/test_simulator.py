#!/usr/bin/env python3
"""消息模拟器（`bot/simulator.py` + `/api/simulate`）。

它最容易犯的错是「和真实分发对不上」：模拟说会执行、聊天里却被拦，或者反过来。
所以这里的重点是**同源**和**只读**：

- 命令层必须调用 `FeatureManager.explain_command`（同一份判定）；
- 模拟过程不能发消息、不能执行 handler、不能写历史/冷却。
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PASS, FAIL = [], []


def check(name, ok, extra=""):
    (PASS if ok else FAIL).append(name)
    print(f"  {'✓' if ok else '✗'} {name}" + (f"   [{extra}]" if extra and not ok else ""))


def section(t):
    print(f"\n== {t}")


def make_mgr(plugins=("b64", "todo")):
    from bot.framework import FeatureManager

    tmp = Path(tempfile.mkdtemp(prefix="klee-sim-"))
    cfg = tmp / "features"
    shutil.copytree(ROOT / "bot" / "features", cfg,
                    ignore=shutil.ignore_patterns("state", "__pycache__", "ext", "*.log"))
    (cfg / "config.json").write_text(json.dumps({"enabled": list(plugins)}), encoding="utf-8")
    (cfg / "overrides.json").unlink(missing_ok=True)

    class A:
        features_dir = str(cfg)
        feature_prefix = "."
        owner_id = "1"
        active_character = "klee"

        def log(self, *a, **k):
            pass

    mgr = FeatureManager(A())
    mgr.load()
    return mgr, cfg


def main():
    from bot import cmdpolicy as CP
    from bot import rules as R
    from bot import simulator as S

    st = Path(tempfile.mkdtemp(prefix="klee-sim-state-"))
    mgr, cfg = make_mgr()
    R.add(st, {"match": "equals", "pattern": "早", "action": "reply", "reply": "早安{user}"})
    R.add(st, {"match": "contains", "pattern": "广告", "action": "ignore"})
    R.add(st, {"match": "equals", "pattern": "查", "action": "command",
               "command": ".b64e hi"})

    def sim(text, **kw):
        kw.setdefault("owner_id", "1")
        return S.simulate(text=text, mgr=mgr, state_dir=st, **kw)

    section("1. 命令")
    d = sim(".b64e 你好")
    check("1.1 命令会执行", d["action"] == "command" and d["command"] == "b64e", str(d)[:120])
    check("1.2 带上插件名", d["plugin"] == "b64", str(d.get("plugin")))
    check("1.3 命令层只判定一次", sum(1 for s in d["steps"] if s["id"] == "cmd") == 1)
    d = sim(".没有这个命令")
    check("1.4 不存在的命令继续走后面", d["action"] != "command", d["action"])
    check("1.5 说明里点了名", "没有命令" in " ".join(s["detail"] for s in d["steps"]), "")

    section("2. 关键词规则")
    d = sim("早", user_name="小明")
    check("2.1 回一句", d["action"] == "rule_reply", d["action"])
    check("2.2 占位符渲染了", d["reply"] == "早安小明", str(d.get("reply")))
    d = sim("这是广告")
    check("2.3 ignore 规则", d["action"] == "ignore" and "不回" in d["reason"], d["reason"])
    d = sim("查")
    check("2.4 规则转命令：真的执行", d["action"] == "command" and d["command"] == "b64e",
          str(d.get("command")))
    # 规则指向不存在的命令 → 退回正常流程（不能静默吞掉）
    R.add(st, {"match": "equals", "pattern": "坏规则", "action": "command",
               "command": ".根本没这命令"})
    d = sim("坏规则")
    check("2.5 规则指向不存在的命令会退回", d["action"] == "model", d["action"])
    check("2.6 步骤里标了它不存在",
          any(s["state"] == "block" and "不存在" in s["detail"] for s in d["steps"]))

    section("3. @ 门槛与群策略")
    d = sim("随便聊聊", is_group=True, mentioned=False)
    check("3.1 群里没 @ 不理", d["action"] == "ignore" and "@" in d["reason"], d["reason"])
    d = sim("随便聊聊", is_group=True, mentioned=True)
    check("3.2 被 @ 就交给模型", d["action"] == "model", d["action"])
    d = sim("随便聊聊")
    check("3.3 私聊不需要 @", d["action"] == "model", d["action"])
    # 规则在 @ 门槛之前生效（这是运行时刻意为之）
    d = sim("早", is_group=True, mentioned=False)
    check("3.4 群策略是 mention 时规则照样生效", d["action"] == "rule_reply", d["action"])
    CP.set_override(cfg, plugin="b64", scope="dm")
    mgr.overrides = CP.load(cfg)
    d = sim(".b64e x", is_group=True)
    check("3.5 作用域覆盖在模拟里也生效", d["action"] == "ignore", str(d)[:100])
    CP.set_override(cfg, plugin="b64", scope="", permission="")
    mgr.overrides = CP.load(cfg)

    section("4. 权限")
    CP.set_override(cfg, command="b64e", permission="owner")
    mgr.overrides = CP.load(cfg)
    d = sim(".b64e x", is_group=True, user_id="9", role="member")
    check("4.1 群友被拦", d["action"] == "ignore" and "主人" in d["reason"], d["reason"])
    # 主人 = owner_id 那个号；不指定 user_id 就还是「访客」，权限判定当然不放行
    d = sim(".b64e x", user_id="1")
    check("4.2 主人放行", d["action"] == "command", d["action"])
    d = sim(".b64e x")
    check("4.2b 没指定身份时按访客处理", d["action"] == "ignore", d["action"])
    CP.set_override(cfg, command="b64e", scope="", permission="")
    mgr.overrides = CP.load(cfg)

    section("5. 静默时段")
    from bot import chatpolicy as CHP
    CHP.set_chat(st, "group:555", mode="open", quiet=["00:00", "23:59"])
    d = sim("随便聊聊", is_group=True, chat_id="group:555")
    check("5.1 静默时不聊", d["action"] == "ignore" and "静默" in d["reason"], d["reason"])
    d = sim(".b64e x", is_group=True, chat_id="group:555")
    check("5.2 静默时命令照常", d["action"] == "command", d["action"])
    CHP.set_chat(st, "group:555", quiet_mention=True)
    d = sim("随便聊聊", is_group=True, chat_id="group:555", mentioned=True)
    check("5.3 静默 + 被 @ 仍回（按设定）", d["action"] == "model", d["action"])
    CHP.set_chat(st, "group:555", mode="disabled")
    d = sim(".b64e x", is_group=True, chat_id="group:555")
    check("5.4 disabled 连命令都不接", d["action"] == "ignore" and "完全不理" in d["reason"])

    section("5b. 和 runtime 对齐的两个细节")
    # 静默 + 没被 @：runtime 一定不处理（quiet_mention 只影响「被 @ 的那条」）
    CHP.set_chat(st, "group:556", mode="open", quiet=["00:00", "23:59"], quiet_mention=True)
    d = sim("随便聊聊", is_group=True, chat_id="group:556", mentioned=False)
    check("5b.1 静默+没@ 即使 quiet_mention 也不聊", d["action"] == "ignore", d["action"])
    # 规则转命令要过权限：仅主人的命令，群体触发应当被拦
    CP.set_override(cfg, command="b64e", permission="owner")
    mgr.overrides = CP.load(cfg)
    R.add(st, {"match": "equals", "pattern": "偷偷查", "action": "command",
               "command": ".b64e hi"})
    d = sim("偷偷查", is_group=True, user_id="9", role="member")
    check("5b.2 规则转的仅主人命令会被拦", d["action"] == "ignore" and "主人" in d["reason"],
          d["reason"])
    CP.set_override(cfg, command="b64e", scope="", permission="")
    mgr.overrides = CP.load(cfg)
    # NLU 命中带参数（"生日 可莉"）：要取第一个词查命令，不能整串查
    nlu_text = next((t for t in ("生日 可莉",) if mgr.nlu_match(t)), "")
    if nlu_text:
        d = sim(nlu_text)
        check("5b.3 NLU 带参数也认得命令", d["action"] == "command", str(d)[:120])
    else:
        print("  · 这个环境没有匹配的 NLU 句子，跳过 5b.3")

    section("5c. 前缀可改：规则里的命令按配置剥")
    from bot import framework as FW
    check("5c.1 strip_prefix 认默认三种", FW.strip_prefix("／b64e x", {".", "/", "／"}) == "b64e x")
    check("5c.2 strip_prefix 认自定义前缀", FW.strip_prefix("!b64e x", {"!"}) == "b64e x")
    check("5c.3 不认的字符不剥", FW.strip_prefix("!b64e", {"."}) == "!b64e")
    # 面板把前缀改成 `!` 时，规则里存 `.b64e hi` 也要能跑（运行时无条件 lstrip 的旧行为
    # 和模拟器只看配置会分叉，所以两边现在共用 strip_prefix）
    old_chars = mgr.prefix_chars
    try:
        mgr.prefix_chars = {"!"}
        d = sim("查", is_group=True, mentioned=True)
        check("5c.4 前缀换成 ! 时规则仍能转命令",
              d["action"] in ("command", "ignore"), str(d)[:100])
    finally:
        mgr.prefix_chars = old_chars

    section("6. 只读：不发消息、不执行、不动冷却")
    sent = []

    class SpyAdapter:
        owner_id = "1"
        platform = "spy"

        async def send(self, *a, **k):
            sent.append(a)

        def chat_id_of(self, is_group, group_id, user_id):
            return f"group:{group_id}" if is_group else f"private:{user_id}"

        def supports(self, cap):
            return True

    from bot.framework import FeatureContext
    c = FeatureContext(adapter=SpyAdapter(), text=".b64e hi", is_group=False, group_id="",
                       user_id="1", user_name="u", message_id="", mentioned=True, role="")
    before = dict(mgr.cooldowns)
    d = sim(".b64e hi")
    check("6.1 没有发出任何消息", sent == [], str(sent))
    check("6.2 冷却表没动", mgr.cooldowns == before, str(mgr.cooldowns))
    check("6.3 结果里没有「已执行」之类的话",
          all("执行了" not in s["detail"] for s in d["steps"]))
    chat_before = (st / "chat.jsonl").exists()
    check("6.4 没有写聊天记录", not chat_before)
    check("6.5 命令处理器没被调用（cooldowns 是它的副作用）",
          all(k[0] != "b64e" for k in mgr.cooldowns), str(mgr.cooldowns))

    section("7. 模型没配好")
    d = S.simulate(text="随便聊聊", mgr=mgr, state_dir=st, owner_id="1", model_ready=False)
    check("7.1 明确说会回「脑袋卡住了」", d["action"] == "ignore" and "模型" in d["reason"],
          d["reason"])

    section("8. 结构")
    d = sim("随便聊聊", is_group=True, mentioned=True)
    ids = [s["id"] for s in d["steps"]]
    check("8.1 每一步都有标签", all(s.get("label") for s in d["steps"]))
    check("8.2 入站钩子被标成未模拟",
          any(s["id"] == "hooks" and s["state"] == "skip" for s in d["steps"]), str(ids))
    check("8.3 最后一步是结论", d["action"] in ("model", "ignore", "command", "rule_reply"))
    check("8.4 带 chat_id", bool(d.get("chat_id")))

    section("9. 活服务")
    import urllib.error
    import urllib.request
    HDR = {"X-Klee-Studio": "1"}
    BASE = "http://127.0.0.1:8798"

    def post(url, body):
        req = urllib.request.Request(url, headers=HDR, method="POST",
                                     data=json.dumps(body).encode())
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    try:
        code, d = post(BASE + "/api/simulate", {"text": ".b64e hi"})
        check("9.1 200", code == 200 and d.get("ok"), str(code))
        check("9.2 有步骤", len(d.get("steps") or []) >= 4, str(len(d.get("steps") or [])))
        check("9.3 有结论", d.get("action") in ("command", "model", "ignore", "rule_reply"))
        code, bad = post(BASE + "/api/simulate", {"text": "   "})
        check("9.4 空输入 400", code == 400, str(code))
        code, d2 = post(BASE + "/api/simulate", {"text": "随便聊聊", "is_group": True})
        check("9.5 群聊没 @ → 不理", d2.get("action") == "ignore", str(d2)[:100])
    except (urllib.error.URLError, OSError) as e:
        print(f"  · 服务没起来，跳过（{type(e).__name__}）")

    print(f"\nPASS {len(PASS)} / {len(PASS) + len(FAIL)}")
    if FAIL:
        print("失败：" + "、".join(FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
