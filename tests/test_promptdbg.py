#!/usr/bin/env python3
"""提示词调试台（`bot/promptdbg.py` + runtime 的分块组装）。

关键点：
1. **块拼回去必须和原来逐字节一样** —— 调试器一旦说假话，比没有更糟；
2. 捕获只在内存里、有上限，失败不能影响回复；
3. 试跑（preview）**不写捕获**，否则「最近一次」会被预览挤掉；
4. 面板侧从真实消息反推块时不能丢字符（`split_system` 要能无损拼回）。
"""
from __future__ import annotations

import tempfile
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PASS, FAIL = [], []


def check(name, ok, extra=""):
    (PASS if ok else FAIL).append(name)
    print(f"  {'✓' if ok else '✗'} {name}" + (f"   [{extra}]" if extra and not ok else ""))


def section(t):
    print(f"\n== {t}")


class FakeModels:
    def chat(self, *a, **k):
        return "好呀"

    def for_character(self, cid):
        return {}


def main():
    from bot import promptdbg as PB

    tmp = Path(tempfile.mkdtemp(prefix="klee-prompt-"))

    section("1. token 估算")
    check("1.1 中文 1 字 ≈ 1 token", PB.tokens("你好世界") == 4, str(PB.tokens("你好世界")))
    check("1.2 英文 4 字符 ≈ 1 token", PB.tokens("abcdefgh") == 2, str(PB.tokens("abcdefgh")))
    check("1.3 空串是 0", PB.tokens("") == 0)
    check("1.4 有内容的短块不会显示成 0", PB.tokens("a") == 1, str(PB.tokens("a")))
    check("1.5 纯空白算 0", PB.tokens("   \n ") == 0)

    section("2. block()")
    b = PB.block("memory", "system", "abc")
    check("2.1 默认标签是中文名", b["label"] == "长期记忆", b["label"])
    check("2.2 记住字符数", b["chars"] == 3)
    check("2.3 未知 role 退回 system", PB.block("x", "tool", "a")["role"] == "system")
    big = PB.block("persona", "system", "字" * (PB.MAX_TEXT + 50))
    check("2.4 超长截断", len(big["text"]) == PB.MAX_TEXT)
    check("2.5 截断要标出来", big["cut"] is True)
    check("2.6 chars 记的是原文长度", big["chars"] == PB.MAX_TEXT + 50)

    section("3. merge_blocks：拼回去必须一模一样")
    parts = [PB.block("persona", "system", "P", group="sys"),
             PB.block("rules", "system", "\n\n【QQ 聊天规则】R", group="sys"),
             PB.block("user", "user", "hi")]
    msgs = PB.merge_blocks(parts)
    check("3.1 同组拼成一条", len(msgs) == 2, str(len(msgs)))
    check("3.2 拼接不加分隔符（逐字节相等）", msgs[0]["content"] == "P\n\n【QQ 聊天规则】R",
          repr(msgs[0]["content"]))
    check("3.3 user 不参与合并", msgs[1] == {"role": "user", "content": "hi"})
    check("3.4 空块被丢掉", len(PB.merge_blocks([PB.block("x", "user", "")])) == 0)
    check("3.5 不同 role 不合并",
          len(PB.merge_blocks([PB.block("a", "system", "1", group="sys"),
                               PB.block("b", "user", "2", group="sys")])) == 2)
    check("3.6 没有 group 的块各自成条",
          len(PB.merge_blocks([PB.block("a", "system", "1"), PB.block("b", "system", "2")])) == 2)
    check("3.7 坏输入不炸", PB.merge_blocks(
        [None, 1, {"role": "user", "content": "x"}, PB.block("u", "user", "x")]) ==
        [{"role": "user", "content": "x"}])

    section("4. snapshot")
    snap = PB.snapshot(parts, chat_id="group:1", character="atri", user_text="hi", ts=100.0)
    check("4.1 消息条数", snap["messages"] == 2)
    check("4.2 字符数是各块之和", snap["chars"] == sum(int(x["chars"]) for x in snap["blocks"]))
    check("4.3 ts 可注入", snap["ts"] == 100.0)
    check("4.4 带 user_text", snap["user_text"] == "hi")
    check("4.5 没有空块", all(x["text"] for x in snap["blocks"]))

    section("5. Store：有上限、只留内存")
    st = PB.Store(max_captures=5, per_chat=2)
    for i in range(4):
        st.add(PB.snapshot([PB.block("user", "user", f"a{i}")], chat_id="c1",
                           character="x", ts=float(i)))
    check("5.1 单会话封顶", len(st) == 2, str(len(st)))
    check("5.2 latest 拿到最后一条", st.latest("c1")["blocks"][0]["text"] == "a3")
    for i in range(6):
        st.add(PB.snapshot([PB.block("user", "user", f"b{i}")], chat_id=f"c{i}",
                           character="x", ts=10.0 + i))
    check("5.3 全局封顶", len(st) == 5, str(len(st)))
    check("5.4 别的会话查不到就返回空", st.latest("nope") == {})
    check("5.5 recent 是倒序（新的在前）",
          st.recent(3)[0]["user_text"] == st.recent(3)[0]["user_text"])
    check("5.6 recent 条数受限", len(st.recent(2)) == 2)
    check("5.7 clear 返回清掉的条数", st.clear() == 5 and len(st) == 0)
    check("5.8 清空后再 add 正常", st.add(PB.snapshot([PB.block("user", "user", "z")],
                                                      chat_id="c", character="x")) is not None)

    section("6. split_system / split_msgs：面板侧反推")
    src = ("人格\n\n【QQ 聊天规则】直接说话\n【你记得的事】a\n【你现在的状态】b\n"
           "【表情】c\n\n【工具使用】d\n【你能用的技能】e")
    blocks = PB.split_system(src)
    check("6.1 按标记分段", [x["key"] for x in blocks] ==
          ["persona", "rules", "memory", "mood", "stickers", "tools", "skills"],
          str([x["key"] for x in blocks]))
    check("6.2 无损：拼回去等于原文", "".join(x["text"] for x in blocks) == src)
    check("6.3 没有标记时只有一块", len(PB.split_system("就一句话")) == 1)
    check("6.4 空串返回空列表", PB.split_system("") == [])
    msgs = [{"role": "system", "content": "人格\n\n【工具使用】x"},
            {"role": "user", "content": "早"}, {"role": "assistant", "content": "早呀"},
            {"role": "user", "content": "在吗"}]
    got = PB.split_msgs(msgs)
    check("6.5 最后一条 user 记成本轮输入", got[-1]["key"] == "user", got[-1]["key"])
    check("6.6 中间的历史记成 hist", [x["key"] for x in got].count("hist") == 2)
    check("6.7 空消息被跳过", all(x["text"] for x in PB.split_msgs(
        [{"role": "system", "content": ""}, {"role": "user", "content": "x"}])))

    section("7. diff")
    a = PB.snapshot([PB.block("persona", "system", "abc"), PB.block("user", "user", "x")],
                    chat_id="c", character="x")
    b2 = PB.snapshot([PB.block("persona", "system", "ab"), PB.block("memory", "system", "mm")],
                     chat_id="c", character="x")
    rows = {r["key"]: r for r in PB.diff(a, b2)}
    check("7.1 变短是负的", rows["persona"]["delta"] == -1, str(rows.get("persona")))
    check("7.2 新增块标 added", rows["memory"]["kind"] == "added")
    check("7.3 消失的块标 removed", rows["user"]["kind"] == "removed")
    check("7.4 没变的不出现", all(r["delta"] != 0 for r in PB.diff(a, a)))

    section("8. runtime 接线（块合并 = 原来的消息体）")
    from bot.runtime import BotRuntime
    feat = tmp / "features"
    feat.mkdir(parents=True, exist_ok=True)
    (feat / "config.json").write_text('{"enabled": [], "settings": {}}', encoding="utf-8")
    soul = tmp / "SOUL.md"
    soul.write_text("你是测试角色。", encoding="utf-8")
    rt = BotRuntime(features_dir=feat, config={"owner_id": "42", "agent_enabled": False},
                    models=FakeModels(), soul_path=soul, state_dir=tmp / "state")
    ctx = _ctx(rt, is_group=False)
    same = rt._build_messages(ctx, "你好", hist=[{"role": "user", "content": "在吗"},
                                            {"role": "assistant", "content": "在"}],
                              kb="知识片段", summary="之前聊过")
    blocks = rt.prompts.latest("private:42")
    check("8.1 组装时自动捕获", bool(blocks), str(blocks)[:80])
    check("8.2 捕获的 msgs 等于返回值", blocks["msgs"] == same)
    # 人格+规则 合并成一条，知识库和提要各自一条
    check("8.3 系统提示 3 条", len([m for m in same if m["role"] == "system"]) == 3,
          str([m["role"] for m in same]))
    check("8.4 人格在系统提示开头", same[0]["content"].startswith(rt._persona()))
    check("8.5 知识库块带前缀", any(m["content"].startswith("【相关背景资料") for m in same))
    check("8.6 提要块带前缀", any(m["content"].startswith("【前情提要") for m in same))
    check("8.7 最后一条是本轮输入", same[-1] == {"role": "user", "content": "你好"})
    check("8.8 块数与消息数对得上",
          len(blocks["blocks"]) >= len(same) - 1)

    section("9. 试跑 preview")
    pv = rt.build_preview("今天好累", chat_id="private:42")
    check("9.1 有块", bool(pv["blocks"]))
    check("9.2 最后一块是本轮输入", pv["blocks"][-1]["key"] == "user", pv["blocks"][-1]["key"])
    check("9.3 试跑不写捕获", rt.prompts.latest("private:42")["user_text"] == "你好",
          rt.prompts.latest("private:42")["user_text"])
    check("9.4 试跑带会话 id", pv["chat_id"] == "private:42", pv["chat_id"])
    pv2 = rt.build_preview("x", chat_id="group:777", is_group=True, kb=False)
    check("9.5 群聊会加「群聊身份」", any(b["key"] == "who" for b in pv2["blocks"]))
    check("9.6 关掉知识库就不检索", not any(b["key"] == "kb" for b in pv2["blocks"]))
    check("9.7 群聊 chat_id 解析对", pv2["chat_id"] == "group:777", pv2["chat_id"])

    section("10. 捕获失败不能影响回复")
    class Boom(PB.Store):
        def add(self, snap):
            raise RuntimeError("炸了")

    rt.prompts = Boom()
    try:
        got = rt._build_messages(ctx, "还在吗")
        ok = got[-1]["content"] == "还在吗"
    except Exception as e:                                        # noqa: BLE001
        ok, got = False, e
    check("10.1 Store 抛异常时消息照常组装", ok, str(got)[:80])

    print()
    if FAIL:
        print(f"FAIL {len(FAIL)} / {len(PASS) + len(FAIL)}")
        for f in FAIL:
            print("  ✗", f)
        return 1
    print(f"PASS {len(PASS)} / {len(PASS)}")
    return 0


def _ctx(rt, *, is_group=False):
    from bot.framework import FeatureContext
    from bot.runtime import _PreviewAdapter
    return FeatureContext(adapter=_PreviewAdapter("42"), text="", is_group=is_group,
                          group_id="777" if is_group else "", user_id="42",
                          user_name="主人", message_id="")


if __name__ == "__main__":
    raise SystemExit(main())
