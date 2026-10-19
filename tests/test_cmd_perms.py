#!/usr/bin/env python3
"""命令权限覆盖（`bot/cmdpolicy.py` + 框架分发）。

三条硬要求：
1. **默认行为不变**：没写覆盖表时，每条命令的作用域/权限和插件声明的一模一样；
2. **覆盖真的生效**：改完之后真实分发（`handle_command`）和模拟器走的是同一套判定；
3. **写错不锁死**：非法值不生效、坏文件按空处理，不能因为一个错字把命令全禁掉。
"""
from __future__ import annotations

import asyncio
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


class Adapter:
    owner_id = "1"
    platform = "sim"

    def chat_id_of(self, is_group, group_id, user_id):
        return (f"group:{group_id}" if is_group else f"private:{user_id}")

    def _chat_id(self, *a):
        return self.chat_id_of(*a)

    def supports(self, cap):
        return True


def make_mgr(plugins=("b64", "todo")):
    """用临时插件目录装一份最小框架（不碰生产 features/state）。"""
    from bot.framework import FeatureManager

    tmp = Path(tempfile.mkdtemp(prefix="klee-perm-"))
    cfg = tmp / "features"
    shutil.copytree(ROOT / "bot" / "features", cfg,
                    ignore=shutil.ignore_patterns("state", "__pycache__", "ext", "*.log"))
    (cfg / "config.json").write_text(json.dumps({"enabled": list(plugins)}), encoding="utf-8")
    for junk in ("overrides.json",):
        (cfg / junk).unlink(missing_ok=True)

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


def ctx(text, *, is_group=False, user_id="1", role="member", group_id="999"):
    from bot.framework import FeatureContext
    return FeatureContext(adapter=Adapter(), text=text, is_group=is_group,
                          group_id=group_id, user_id=user_id, user_name="u",
                          message_id="", mentioned=True, role=role)


def main():
    from bot import cmdpolicy as CP

    section("1. 覆盖表的读写与容错")
    tmp = Path(tempfile.mkdtemp(prefix="klee-perm-file-"))
    check("1.1 没有文件 = 空表", CP.load(tmp) == {"commands": {}, "plugins": {}})
    (tmp / "overrides.json").write_text("{坏掉的", encoding="utf-8")
    check("1.2 坏文件当空表（不炸）", CP.load(tmp) == {"commands": {}, "plugins": {}})
    (tmp / "overrides.json").write_text('{"commands": {"x": {"scope": "nope", '
                                        '"permission": "god"}}}', encoding="utf-8")
    check("1.3 非法值被丢掉", CP.load(tmp)["commands"] == {}, str(CP.load(tmp)))
    CP.set_override(tmp, command="b64e", permission="owner")
    check("1.4 写进去能读回来", CP.load(tmp)["commands"]["b64e"] == {"permission": "owner"})
    CP.set_override(tmp, command="b64e", scope="", permission="")
    check("1.5 两项显式清空 = 删掉这条", CP.load(tmp)["commands"] == {})
    for kwargs, why in (({"command": "a", "plugin": "p"}, "命令和插件同时给"),
                        ({}, "都不给"),
                        ({"command": "x", "scope": "sideways"}, "非法作用域"),
                        ({"command": "x", "permission": "root"}, "非法权限")):
        try:
            CP.set_override(tmp, **kwargs)
            check(f"1.6 拒绝{why}", False)
        except CP.PolicyError:
            check(f"1.6 拒绝{why}", True)

    section("1b. 按字段合并（改一项不能把另一项冲掉）")
    CP.set_override(tmp, command="x", permission="owner")
    CP.set_override(tmp, command="x", scope="dm")
    check("1b.1 改作用域保留权限", CP.load(tmp)["commands"]["x"] == {"permission": "owner",
                                                                "scope": "dm"},
          str(CP.load(tmp)["commands"]))
    CP.set_override(tmp, command="x", permission="")
    check("1b.2 只清权限，作用域还在",
          CP.load(tmp)["commands"]["x"] == {"scope": "dm"}, str(CP.load(tmp)["commands"]))
    CP.set_override(tmp, command="x", scope="")
    check("1b.3 两项都清 → 整条消失", CP.load(tmp)["commands"] == {})

    section("2. 默认行为不变（没写覆盖时和插件声明一致）")
    mgr, cfg = make_mgr()
    rows = 0
    for name, cmd in list(mgr.commands.items()):
        eff = CP.effective(cmd, {"commands": {}, "plugins": {}})
        if eff["scope"] != cmd.scope or eff["permission"] != cmd.permission:
            rows += 1
    check("2.1 所有命令的有效值 = 声明值", rows == 0, f"{rows} 条不一致")
    v = mgr.explain_command(ctx(".b64e hi"))
    check("2.2 默认 b64e 是所有人可用", v["action"] == "run" and v["permission"] == "all", str(v.get("permission")))
    check("2.3 覆盖来源标成插件默认", v["rule_source"] == "插件默认", v["rule_source"])

    section("3. 覆盖生效（真实分发与模拟器同源）")
    mgr, cfg = make_mgr()
    CP.set_override(cfg, command="b64e", permission="owner")
    mgr.overrides = CP.load(cfg)
    check("3.1 群友被拦", mgr.explain_command(ctx(".b64e hi", is_group=True, user_id="9"))["action"] == "skip")
    check("3.2 主人放行", mgr.explain_command(ctx(".b64e hi", user_id="1"))["action"] == "run")
    check("3.3 来源标成面板覆盖",
          mgr.explain_command(ctx(".b64e hi"))["rule_source"] == "面板覆盖")
    # 真实分发：拦下时要返回 True（消息被吃掉）且**不执行 handler**
    sent = []

    class A2(Adapter):
        async def send(self, *a, **k):
            sent.append(a)

    c = ctx(".b64e hi", is_group=True, user_id="9")
    c.adapter = A2()
    got = asyncio.run(mgr.handle_command(c))
    check("3.4 被拦时消息被吃掉（True）", got is True)
    check("3.5 被拦时没有执行（没产生回复）", sent == [], str(sent))
    c2 = ctx(".b64e hi", user_id="1")
    c2.adapter = A2()
    check("3.6 主人这条真的执行了", asyncio.run(mgr.handle_command(c2)) is True and sent, str(sent)[:80])

    section("4. 作用域覆盖")
    mgr, cfg = make_mgr()
    CP.set_override(cfg, command="b64e", scope="dm")
    mgr.overrides = CP.load(cfg)
    check("4.1 群里被拦", mgr.explain_command(ctx(".b64e x", is_group=True))["reason"]
          .startswith("这条命令只在私聊"), mgr.explain_command(ctx(".b64e x", is_group=True))["reason"])
    check("4.2 私聊可用", mgr.explain_command(ctx(".b64e x"))["action"] == "run")

    section("5. 插件级覆盖 + 命令级优先")
    mgr, cfg = make_mgr()
    CP.set_override(cfg, plugin="b64", permission="admin")
    mgr.overrides = CP.load(cfg)
    check("5.1 插件级：群友被拦",
          mgr.explain_command(ctx(".b64e x", is_group=True, user_id="9", role="member"))["action"] == "skip")
    check("5.2 插件级：群管理放行",
          mgr.explain_command(ctx(".b64e x", is_group=True, user_id="9", role="admin"))["action"] == "run")
    CP.set_override(cfg, command="b64d", permission="owner")
    mgr.overrides = CP.load(cfg)
    check("5.3 命令级压过插件级",
          mgr.explain_command(ctx(".b64d x", is_group=True, user_id="9", role="admin"))["action"] == "skip")
    check("5.4 同插件另一条仍按插件级",
          mgr.explain_command(ctx(".b64e x", is_group=True, user_id="9", role="admin"))["action"] == "run")

    section("6. 模拟器和真实分发判定一致")
    from bot import simulator
    mgr, cfg = make_mgr()
    CP.set_override(cfg, command="b64e", permission="owner")
    mgr.overrides = CP.load(cfg)
    st = Path(tempfile.mkdtemp(prefix="klee-perm-sim-"))
    cases = [
        {".b64e hi"}, {}]
    for kwargs in ({}, ):
        pass
    combos = [
        (".b64e hi", dict(is_group=True, user_id="9", role="member"), "skip"),
        (".b64e hi", dict(user_id="1"), "run"),
        (".待办清 确认", dict(), "run"),
    ]
    for text, kw, want in combos:
        v = mgr.explain_command(ctx(text, **kw))
        # 模拟器要显式知道「谁是主人」：真实 ctx 从 adapter.owner_id 取，模拟器没有适配器
        sim = simulator.simulate(text=text, mgr=mgr, state_dir=st, owner_id="1", **kw)
        got = "run" if sim["action"] == "command" else "skip"
        check(f"6.x {text} {kw} 两边一致", v["action"] == want == got,
              f"真实={v['action']} 模拟={got} 期望={want}")
    # 没配主人时，仅主人的命令连「主人」试跑都会被拦 —— 必须明说出来，不能让人以为坏了
    sim = simulator.simulate(text=".b64e hi", mgr=mgr, state_dir=st, user_id="1")
    hinted = [s for s in sim["steps"] if s["id"] == "owner"]
    check("6.4 没配主人时有明确提示", bool(hinted) and "没有人是主人" in hinted[0]["detail"],
          str(hinted))
    sim = simulator.simulate(text=".b64e hi", mgr=mgr, state_dir=st, user_id="1", owner_id="1")
    check("6.5 配了主人就没有那条提示",
          not [s for s in sim["steps"] if s["id"] == "owner"], str(sim["steps"][:3]))

    section("6b. 仓库里的覆盖表必须是干净的")
    # 实测面板时会顺手写入覆盖，提交时把测试残留带进仓库过一次（`颜色` 那条）：
    # 语义上是空操作，但会让「被改过」计数永远不是 0，也污染默认行为。
    shipped = ROOT / "bot" / "features" / "overrides.json"
    if shipped.is_file():
        data = json.loads(shipped.read_text(encoding="utf-8") or "{}")
        check("6b.1 提交的 overrides.json 是空表",
              not (data.get("commands") or data.get("plugins")), str(data))
    else:
        check("6b.1 提交的 overrides.json 是空表", True, "（没有这个文件也算干净）")

    section("7. 活服务")
    import urllib.error
    import urllib.request
    HDR = {"X-Klee-Studio": "1"}
    BASE = "http://127.0.0.1:8798"

    def get(url):
        req = urllib.request.Request(url, headers=HDR)
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read())

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
        st_code, d = get(BASE + "/api/control")
        check("7.1 /api/control 200", st_code == 200 and d.get("ok"), str(st_code))
        check("7.2 带命令与有效权限",
              d["commands"] and {"scope_eff", "permission_eff"} <= set(d["commands"][0]),
              str((d.get("commands") or [{}])[0])[:120])
        check("7.3 带三种字典（模式/权限/作用域）",
              {"inherit", "open", "mention", "disabled"} <= set(d["modes"])
              and {"all", "admin", "owner"} <= set(d["perms"]), str(d.get("modes")))
        check("7.4 带主人 QQ（模拟器要用）", d.get("owner_id") == "2100950993",
              str(d.get("owner_id")))
        # 真改一条再改回来，确认落盘 + 立即生效
        code, r1 = post(BASE + "/api/control/cmd", {"command": "b64e", "permission": "owner"})
        check("7.5 写入 200", code == 200 and r1.get("ok"), f"{code} {str(r1)[:80]}")
        saved = json.loads((ROOT / "bot" / "features" / "overrides.json").read_text(encoding="utf-8"))
        check("7.6 真的落盘了", saved["commands"].get("b64e") == {"permission": "owner"}, str(saved))
        check("7.7 响应里带最新快照",
              any(c["name"] == "b64e" and c["permission_eff"] == "owner"
                  for c in r1["after"]["commands"]))
        code, r2 = post(BASE + "/api/control/cmd",
                        {"command": "b64e", "scope": "", "permission": ""})
        check("7.8 能恢复默认", code == 200 and r2["after"]["counts"]["overridden"] == 0,
              str(r2.get("after", {}).get("counts")))
        code, bad = post(BASE + "/api/control/cmd", {"command": "b64e", "permission": "god"})
        check("7.9 非法权限 400", code == 400, str(code))
        # 面板是先设权限、再改作用域的；合并写错就会把「仅主人」悄悄冲掉
        post(BASE + "/api/control/cmd", {"command": "b64e", "permission": "owner"})
        code, r3 = post(BASE + "/api/control/cmd", {"command": "b64e", "scope": "dm"})
        row = next((c for c in r3["after"]["commands"] if c["name"] == "b64e"), {})
        check("7.10 改作用域不会冲掉权限",
              row.get("permission_eff") == "owner" and row.get("scope_eff") == "dm", str(row))
        post(BASE + "/api/control/cmd", {"command": "b64e", "scope": "", "permission": ""})
        check("7.11 收尾清干净",
              not json.loads((ROOT / "bot" / "features" / "overrides.json")
                             .read_text(encoding="utf-8"))["commands"], "")
    except (urllib.error.URLError, OSError) as e:
        print(f"  · 服务没起来，跳过（{type(e).__name__}）")

    print(f"\nPASS {len(PASS)} / {len(PASS) + len(FAIL)}")
    if FAIL:
        print("失败：" + "、".join(FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
