#!/usr/bin/env python3
"""模型配置「配了不生效」：agent / vision 被面板回填成具体模型后钉死主模型。

用户报的现象：不管把主模型改成什么，聊天永远走 `cn:deepseek-v4.1-flash`。

根因（两条，都会把「跟聊天同一个」写成具体值）：

1. `/api/agent` 返回的是 `agent()` —— 一个**解析后**的值，没配时会返回当前
   active。面板把返回值填进下拉，用户只要点一次「保存 Agent 设置」（哪怕只改
   步数），就把当时的主模型写成了「工具模型」。而工具循环是常开的，于是之后
   所有聊天都走那个钉死的模型。
2. 「看图模型」下拉在未配置时没有任何 option 带 selected，浏览器默认选第一项
   （= 模型清单第一个）。点一次「保存看图模型」就把默认模型写成了看图模型。

这一组守「表单回填只能用原样配置」+「老配置要能自愈」。
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bot.llm import ModelStore, SCHEMA     # noqa: E402

PASS, FAIL = [], []


def check(name, ok, extra=""):
    (PASS if ok else FAIL).append(name)
    print(f"  {'✓' if ok else '✗'} {name}" + (f"   [{extra}]" if extra and not ok else ""))


def section(t):
    print(f"\n== {t}")


def mkfile(d: dict) -> Path:
    p = Path(tempfile.mkdtemp(prefix="klee-models-")) / "models.json"
    p.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    return p


def base(agent=None, vision=None, active="m1", models=("m1", "m2", "m3")) -> dict:
    d = {"active": {"provider": "p1", "model": active},
         "providers": [{"id": "p1", "name": "P1", "api": "http://127.0.0.1:1/v1",
                        "key_env": "K", "models": list(models)}]}
    if agent is not None:
        d["agent"] = agent
    if vision is not None:
        d["vision"] = vision
    return d


def main():
    section("1. 原样配置 vs 解析结果")
    st = ModelStore(mkfile(base()))
    check("1.1 没配 agent 时 agent_raw 是空的",
          st.agent_raw() == {"provider": "", "model": ""}, str(st.agent_raw()))
    check("1.2 但 agent() 解析成主模型（调用方要能直接用）",
          st.agent()["model"] == "m1", str(st.agent()))
    check("1.3 没配 vision 时 vision_raw 是空的",
          st.vision_raw() == {"provider": "", "model": ""}, str(st.vision_raw()))
    check("1.4 vision() 说未配置", st.vision()["configured"] is False)

    st.set_active("p1", "m3")
    check("1.5 改主模型后 agent() 跟着变（这才是用户要的）",
          st.agent()["model"] == "m3", str(st.agent()))
    check("1.6 agent_raw 仍然是空的（没被顺手写成 m3）",
          st.agent_raw()["model"] == "", str(st.agent_raw()))

    section("2. 显式指定就不该被主模型带走")
    st.set_agent("p1", "m2")
    st.set_active("p1", "m1")
    check("2.1 显式配的 agent 不被主模型覆盖", st.agent()["model"] == "m2", str(st.agent()))
    check("2.2 raw 记的是显式值", st.agent_raw()["model"] == "m2")
    st.set_agent("", "")
    check("2.3 清空后回到跟随", st.agent()["model"] == "m1", str(st.agent()))

    section("3. 老配置自愈（schema < 2）")
    p = mkfile(base(agent={"provider": "p1", "model": "m1"},
                    vision={"provider": "p1", "model": "m1"}))
    st2 = ModelStore(p)
    check("3.1 被回填成清单第一项的 agent 被清掉", st2.agent_raw()["model"] == "",
          str(st2.agent_raw()))
    check("3.2 被回填成清单第一项的 vision 被清掉", st2.vision()["configured"] is False,
          str(st2.vision()))
    disk = json.loads(p.read_text(encoding="utf-8"))
    check("3.3 迁移当场落盘", disk.get("_schema") == SCHEMA, str(disk.get("_schema")))
    check("3.4 磁盘上的 agent 也清了", (disk.get("agent") or {}).get("model") == "",
          str(disk.get("agent")))

    section("4. 迁移不能误删真·用户选择")
    p2 = mkfile(base(agent={"provider": "p1", "model": "m3"},
                     vision={"provider": "p1", "model": "m2"}))
    st3 = ModelStore(p2)
    check("4.1 专门挑的非默认模型保留", st3.agent_raw()["model"] == "m3", str(st3.agent_raw()))
    check("4.2 看图模型也保留", st3.vision()["model"] == "m2", str(st3.vision()))
    check("4.3 仍然打了 schema 标记", json.loads(p2.read_text())["_schema"] == SCHEMA)

    section("4b. 另外两种伪值形状")
    # a) provider 空（「跟聊天同一个」那项）但 model 被回填成主 provider 的第一个
    p4 = mkfile(base(agent={"provider": "", "model": "m1"}))
    check("4b.1 provider 空也认得出来", ModelStore(p4).agent_raw()["model"] == "",
          str(ModelStore(p4).agent_raw()))
    # b) agent 被钉在当时的主模型上（工具卡片回填的特征）
    p5 = mkfile(base(active="m2", agent={"provider": "p1", "model": "m2"}))
    check("4b.2 钉在主模型上的 agent 也清掉", ModelStore(p5).agent_raw()["model"] == "",
          str(ModelStore(p5).agent_raw()))
    check("4b.3 迁移前留了备份", p5.with_suffix(".json.bak").exists(),
          str(sorted(x.name for x in p5.parent.iterdir())))

    section("5. 已经迁移过的文件不再动")
    p3 = mkfile({**base(agent={"provider": "p1", "model": "m1"}), "_schema": SCHEMA})
    st4 = ModelStore(p3)
    check("5.1 schema 已是 2 → 不动（用户后来真选了第一项也不删）",
          st4.agent_raw()["model"] == "m1", str(st4.agent_raw()))

    section("6. 改供应商别把正在用的模型删出清单")
    st5 = ModelStore(mkfile(base(agent={"provider": "p1", "model": "m2"})))
    st5.set_active("p1", "m3")
    st5.upsert_provider({"id": "p1", "name": "P1", "api": "http://127.0.0.1:1/v1",
                         "key_env": "K", "models": ["m1"]})     # 只勾了一个
    models = st5.get_provider("p1")["models"]
    check("6.1 主模型还在清单里（否则 active() 静默回退第一项）", "m3" in models, str(models))
    check("6.2 显式 agent 也在", "m2" in models, str(models))
    check("6.3 active 没被换掉", st5.active()["model"] == "m3", str(st5.active()))

    section("7. 用户看到的症状：改主模型 → 实际调用换过去")
    st6 = ModelStore(mkfile(base(agent={"provider": "p1", "model": "m1"},
                                  vision={"provider": "p1", "model": "m1"})))
    st6.set_active("p1", "m3")
    check("7.1 迁移后 agent 跟随主模型（不再钉死 m1）",
          st6.for_agent("atri")["model"] == "m3", str(st6.for_agent("atri")))
    check("7.2 聊天模型也是 m3", st6.for_character("atri")["model"] == "m3")
    check("7.3 看图模型不再被误设成文本模型", st6.vision()["configured"] is False)

    section("7b. characters 被手改成非 dict 也不能炸")
    bad = mkfile({**base(), "characters": ["oops"]})
    st7 = ModelStore(bad)
    try:
        st7.upsert_provider({"id": "p1", "name": "P1", "api": "http://127.0.0.1:1/v1",
                             "key_env": "K", "models": ["m1"]})
        ok, err = True, ""
    except Exception as e:                                        # noqa: BLE001
        ok, err = False, f"{type(e).__name__}: {e}"
    check("7b.1 坏 characters 不影响改供应商", ok, err)

    section("8. 面板拿到的字段")
    import server as SV

    class _M:
        def __init__(self, real):
            self._r = real

        def __getattr__(self, k):
            return getattr(self._r, k)

    class _Bot:
        models = None
        config: dict = {}

    old = SV.BOT
    try:
        b = _Bot()
        b.models = _M(st6)
        SV.BOT = b
        md = SV._models_get()
        check("8.1 /api/models 带 vision_raw", "vision_raw" in md, str(list(md)))
        check("8.2 vision_raw 是原样（空）", md["vision_raw"]["model"] == "", str(md["vision_raw"]))
        check("8.3 agent_raw 也是原样", md["agent_raw"]["model"] == "", str(md["agent_raw"]))
        check("8.4 解析后的 agent 仍在（别的调用方要用）",
              md["agent"]["model"] == "m3", str(md.get("agent")))
    finally:
        SV.BOT = old

    print()
    if FAIL:
        print(f"FAIL {len(FAIL)} / {len(PASS) + len(FAIL)}")
        for f in FAIL:
            print("  ✗", f)
        return 1
    print(f"PASS {len(PASS)} / {len(PASS)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
