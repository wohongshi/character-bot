#!/usr/bin/env python3
"""本轮（「插件就是 skill，但它不会去调用」）的回归测试。

真机上看到的现象，按顺序：
1. 问「哥伦比娅好看吗」→ 模型答「我没见过，也没法评价」（一次插件都没调）
2. 追问「你去搜索？」→ 气泡里出现 **`6000` + 一个 fandom 链接**
3. 打开日志才看到：模型把工具调用当**正文**写出来了（DSML 标记），
   我们的清理只删标签，参数就留下了 —— 于是「6000」和 URL 成了回答。

四个根因：
- 标记式工具调用只被删除，没被解析 → 参数漏进用户界面
- 最后一轮 `tool_choice=none` 时模型照样写标记 → 没有兜底收尾
- 面板的系统提示里**没有技能目录** → 模型不知道有 40 个插件可用
- `list_skills("今天运势")` 用子串匹配 → 撞不上「今日运势」→ 回「没有匹配的技能」
  → 模型据此断定「做不到」，再不复试

再加上一个：`_web_agent_call` 把 `adapter` 传成 None，
所有要 adapter 的插件技能都会 `AttributeError: 'NoneType' object has no attribute '_chat_id'`。
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bot import agent as A                                    # noqa: E402
from bot.framework import FeatureManager                      # noqa: E402
from bot.skills import SkillHub                               # noqa: E402

PASS, FAIL = [], []
# 插件出图（.运势/.塔罗/.角色卡）默认写到 bot/features/state/cards ——
# 那是**运行时目录**。测试不能写生产状态，更不能触发 cleanup_cards 按 mtime
# 删掉用户已有的卡片。这里把目录钉到临时目录。
_CARD_TMP = Path(tempfile.mkdtemp(prefix="klee-cards-"))


def _pin_cards_dir():
    mod = sys.modules.get("_cardimg")
    if mod is None or not hasattr(mod, "cards_dir"):
        return False
    mod.cards_dir = lambda settings, base, default="cards": _CARD_TMP
    return True


def _has_pil() -> bool:
    mod = sys.modules.get("_cardimg")
    try:
        return bool(mod and mod.load_pil() is not None)
    except Exception:                                            # noqa: BLE001
        return False


def check(name, ok, extra=""):
    (PASS if ok else FAIL).append(name)
    print(f"  {'✓' if ok else '✗'} {name}" + (f"   [{extra}]" if extra and not ok else ""))


def script(*rounds):
    rounds = list(rounds)
    calls = []

    def complete(messages, **kw):
        calls.append({"messages": [dict(m) for m in messages], **kw})
        if not rounds:
            raise AssertionError("script 用完了")
        r = rounds.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    complete.calls = calls
    return complete


def tc(name, args="{}", cid="c1"):
    return [{"id": cid, "name": name, "arguments": args}]


class Adapter:
    features_dir = str(ROOT / "bot" / "features")
    feature_prefix = "."
    owner_id = "1"

    def log(self, *a, **k):
        pass

    def _chat_id(self, is_group, group_id, user_id):     # 插件几乎都要这个
        return f"test:{user_id}"


DSML = '<｜｜DSML｜｜ calls>\n<｜｜DSML｜｜ invoke name="%s">%s</｜｜DSML｜｜ invoke>\n</｜｜DSML｜｜ calls>'
PARAM = '<｜｜DSML｜｜ parameter name="%s" string="%s">%s</｜｜DSML｜｜ parameter>'


def main():
    print("[1] 标记式工具调用：解析，而不是丢掉")
    txt = DSML % ("web_search",
                  PARAM % ("count", "false", "6") + PARAM % ("query", "true", "哥伦比娅 技能"))
    calls = A._parse_tool_markup(txt)
    check("1.1 解析出一个调用", len(calls) == 1 and calls[0]["name"] == "web_search", str(calls))
    args = json.loads(calls[0]["arguments"]) if calls else {}
    check("1.2 string=false 的数字变成 int", args.get("count") == 6, str(args))
    check("1.3 string=true 保持字符串", args.get("query") == "哥伦比娅 技能", str(args))

    two = DSML % ("now", "") + "\n" + DSML % ("calc", PARAM % ("expr", "true", "1+1"))
    check("1.4 多个调用都解析出来", [c["name"] for c in A._parse_tool_markup(two)] == ["now", "calc"],
          str(A._parse_tool_markup(two)))

    xml = '<tool_call>{"name":"calc","arguments":{"expr":"2*3"}}</tool_call>'
    got = A._parse_tool_markup(xml)
    check("1.5 <tool_call> JSON 形态也认", got and got[0]["name"] == "calc"
          and json.loads(got[0]["arguments"]) == {"expr": "2*3"}, str(got))
    check("1.6 普通正文不会被误解析", A._parse_tool_markup("你好呀，今天想聊什么？") == [])

    print("\n[2] 参数碎片不能当回答（真机上就是「6000 + 链接」）")
    check("2.1 数字 + URL 判定为碎片", A._looks_like_debris("6000\nhttps://genshin-impact.fandom.com/wiki/Columbina"))
    check("2.2 纯标点也是碎片", A._looks_like_debris("…… ！"))
    check("2.3 正常回答不是碎片", not A._looks_like_debris("她叫哥伦比娅，是愚人众第三席。"))
    check("2.4 中英混排不是碎片", not A._looks_like_debris("ok 好的"))

    print("\n[3] 循环：正文里的调用真的会被执行")
    c = script({"text": DSML % ("now", ""), "tool_calls": []},
               {"text": "现在是十点半～", "tool_calls": []})
    res = A.run(None, [{"role": "user", "content": "几点了"}], max_steps=3, complete=c)
    check("3.1 正文里的调用被解析并执行", res["tool_calls"] == ["now"], str(res["tool_calls"]))
    check("3.2 最终回答是模型的人话，不是标记", res["text"] == "现在是十点半～", res["text"])
    roles = [m["role"] for m in c.calls[1]["messages"]]
    check("3.3 第二轮把工具结果带回去了", "tool" in roles, str(roles))

    print("\n[4] 最后一轮：不许再要工具，但要给出人话")
    c = script({"text": "", "tool_calls": tc("now")},
               {"text": DSML % ("now", ""), "tool_calls": []},
               {"text": "都查完了，现在下午三点。", "tool_calls": []})
    res = A.run(None, [{"role": "user", "content": "一直查"}], max_steps=1, complete=c)
    check("4.1 最后一轮的标记调用也被执行", "now" in res["tool_calls"], str(res["tool_calls"]))
    check("4.2 仍然拿到了人话收尾", res["text"] == "都查完了，现在下午三点。",
          f"{res['text']!r} err={res['error']}")
    last = c.calls[-1]
    check("4.3 收尾调用不带 tools", not last.get("tools"), str(last.get("tools")))
    check("4.4 收尾调用 tool_choice=none", last.get("tool_choice") == "none", str(last.get("tool_choice")))

    print("\n[5] 工具跑过、正文是碎片 → 重写一次")
    c = script({"text": "", "tool_calls": tc("now")},
               {"text": "6000\nhttps://example.com/x", "tool_calls": []},
               {"text": "嗯，我查了一下，现在还早呢。", "tool_calls": []})
    res = A.run(None, [{"role": "user", "content": "现在几点"}], max_steps=3, complete=c)
    check("5.1 碎片不会回给用户", res["text"] == "嗯，我查了一下，现在还早呢。", res["text"])
    check("5.2 真的多问了一次", len(c.calls) == 3, str(len(c.calls)))

    c = script({"text": "1+1", "tool_calls": []})
    res = A.run(None, [{"role": "user", "content": "1+1"}], max_steps=2, complete=c)
    check("5.3 没跑过工具时，短回答不触发重写", len(c.calls) == 1 and res["text"] == "1+1",
          f"{len(c.calls)} 次调用")

    print("\n[6] 技能目录：提示里要说清怎么调")
    mgr = FeatureManager(Adapter())
    mgr.load()
    _pin_cards_dir()
    hub = SkillHub(mgr)
    hint = hub.hint("今天运势怎么样", is_owner=True)
    check("6.1 提示里有 use_skill 的调用写法", "use_skill" in hint, hint[:120])
    check("6.2 相关技能排在前面（运势 → fortune）", "fortune" in hint.split("\n")[1] or
          "fortune" in hint[:400], hint[:400])
    check("6.3 提示有长度上限（每条消息都要付这个成本）", len(hint) <= 900, str(len(hint)))
    check("6.4 没有技能时返回空串", SkillHub(FeatureManager(Adapter()))._hint_empty()
          if hasattr(SkillHub, "_hint_empty") else hub.hint("x", is_owner=True) != "", "只做存在性检查")

    print("\n[7] list_skills：子串匹配的死法")
    hits = hub.search("今天运势", is_owner=True)
    names = [s["name"] for _sc, s, _c in hits]
    check("7.1 「今天运势」能命中「今日运势」（子串匹配会漏）", "fortune" in names, str(names))
    hits = hub.search("原神", is_owner=True)
    check("7.2 「原神」命中祈愿类插件", "wish" in [s["name"] for _s, s, _c in hits],
          str([s["name"] for _s, s, _c in hits]))

    ctx = A.ToolContext(skills=hub, chat={"is_owner": True, "is_group": False, "role": "owner"})
    # 用纯 ASCII 乱码：中文「无所谓」的短语会真的命中描述里的字（比如「关键词」），
    # 那是 2-gram 匹配的正常行为，不是误报。
    check("7.3a 纯噪声查询 search 返回空", hub.search("zzqxvbnm", is_owner=True) == [],
          str(hub.search("zzqxvbnm", is_owner=True))[:120])
    out = A.tool_list_skills({"query": "zzqxvbnm"}, ctx)
    check("7.3 搜不到时列出已有技能，而不是只说「没有」",
          "没有精确匹配" in out and "fortune" in out, out[:160])
    out2 = A.tool_list_skills({}, ctx)
    check("7.4 不带 query 时列出技能名且不超长", "fortune" in out2 and len(out2) <= 1900,
          f"{len(out2)} {out2[:80]}")

    print("\n[8] 真插件走一遍（不是桩）")
    ad = Adapter()
    ad.sent = []
    r = hub.run("dice", "r 1d6", adapter=ad, is_group=False, group_id="", user_id="1",
                user_name="主人", role="owner", timeout=10)
    check("8.1 dice 技能真的跑起来了", r.get("ok") and "🎲" in r.get("text", ""), str(r)[:200])
    r2 = hub.run("fortune", "运势", adapter=ad, is_group=False, group_id="", user_id="1",
                 user_name="主人", role="owner", timeout=10)
    check("8.2 fortune 技能真的跑起来了", r2.get("ok") and r2.get("text"), str(r2)[:200])
    r3 = hub.run("dice", "r 1d6", adapter=ad, is_group=False, group_id="", user_id="999",
                 user_name="路人", role="member", timeout=10)
    check("8.3 陌生人照样能用公开命令（dice 是 all）", r3.get("ok"), str(r3)[:160])

    print("\n[9] 面板链路：adapter 不能是 None")
    import server as S                                        # noqa: E402

    seen = {}

    def fake_run(models, msgs, **kw):
        seen.update(kw)
        return {"text": "ok", "steps": 0, "tool_calls": [], "trace": [], "error": "", "images": []}

    real_run = A.run
    A.run = fake_run
    try:
        class Rt:
            config = {"agent_tools": A.DEFAULT_TOOLS, "agent_skills": True,
                      "max_tokens": 800, "agent_max_steps": 3}
            models = None
            skills = hub
            state_dir = ROOT / "state"

        S._web_agent_call(Rt(), [{"role": "user", "content": "hi"}], "klee")
    finally:
        A.run = real_run
    adp = (seen.get("chat") or {}).get("adapter")
    check("9.1 面板 agent 拿到的是真 adapter（以前是 None → 所有插件技能都 AttributeError）",
          adp is not None and hasattr(adp, "_chat_id"), repr(adp))
    check("9.2 面板 agent 带上了技能目录", seen.get("skills") is not None, repr(seen.get("skills")))

    class Rt2:
        config = {"agent_skills": True}
        skills = hub

    sys_text = S._web_system_text(Rt2(), "klee", "今天运势怎么样")
    check("9.3 系统提示里有技能目录", "【你能用的技能】" in sys_text and "use_skill" in sys_text,
          sys_text[-300:])
    check("9.4 系统提示里点到了运势这个技能", "fortune" in sys_text, sys_text[-300:])

    print("\n[10] 模型写中文名，也要找得到技能")
    check("10.1 「运势」→ fortune", (hub.get("运势") or {}).get("name") == "fortune",
          str(hub.get("运势")))
    check("10.2 「今天运势怎么样」→ fortune", (hub.get("今天运势怎么样") or {}).get("name") == "fortune",
          str(hub.get("今天运势怎么样")))
    check("10.3 「帮我丢个骰子」→ dice（不是「帮我选」）",
          (hub.get("帮我丢个骰子") or {}).get("name") == "dice", str(hub.get("帮我丢个骰子")))
    check("10.4 英文名不做模糊匹配（nope 不能匹到 loopinfo）", hub.get("nope") is None)
    check("10.5 找不到时给出「你是指」", "tarot" in hub.detail("占卜"), hub.detail("占卜"))
    # 「占卜」其实是 tarot 的命令别名 —— 精确命中命令名就走别名，这是对的
    alias = hub.run("占卜", "", adapter=ad, is_group=False, group_id="", user_id="1",
                    user_name="主人", role="owner", timeout=12)
    check("10.6 命令别名也能直接调（占卜 → 塔罗）",
          alias.get("ok") and alias.get("command") == "塔罗", str(alias)[:160])
    bad = hub.run("zzz真的不存在", "", adapter=ad, is_group=False, group_id="", user_id="1",
                  user_name="主人", role="owner", timeout=10)
    check("10.7 真找不到时给出 list_skills 的指引",
          not bad.get("ok") and "list_skills" in str(bad.get("error")), str(bad)[:160])

    print("\n[11] 插件出的图：面板也得看得见")
    import tempfile
    import server as S2                                       # noqa: E402

    _pix = bytes.fromhex(
        "89504e470d0a1a0a0000000d494844520000000100000001080200000090"
        "7753de0000000c4944415478da63f8ffff3f0005fe02fea735d6b40000"
        "000049454e44ae426082")
    _img_root = Path(tempfile.mkdtemp(prefix="klee-img-"))
    (_img_root / "cards").mkdir()
    f = _img_root / "cards" / "t.png"
    f.write_bytes(_pix)
    _saved_roots = S2.IMG_ROOTS
    S2.IMG_ROOTS = (_img_root,)
    check("11.1 造一个图片（前置）", f.is_file(), str(f))
    if f is not None:
        ref = S2._img_ref(f)
        check("11.2 引用是相对路径", ref.startswith("cards/") and not Path(ref).is_absolute(), ref)
        check("11.3 引用能还原成同一个文件", S2._img_file(ref) == f.resolve(), ref)
    check("11.4 白名单外的路径不给引用", S2._img_ref("/etc/passwd") == "")
    check("11.5 路径穿越被拒", S2._img_file("cards/../../../../etc/passwd") is None)
    check("11.6 非图片后缀被拒", S2._img_file("cards/x.svg") is None and S2._img_file("a.py") is None)
    check("11.7 不存在的文件被拒", S2._img_file("cards/nope-zzz.png") is None)

    # 适配器真的收图（以前这里 AttributeError，插件只好降级成文字）
    class _Rt:
        config = {"feature_prefix": "."}

    wa = S2._WebAdapter(_Rt(), "klee")
    import asyncio as _a
    if f is not None:
        _a.run(wa.send_image_file("web:panel", str(f), caption="测试卡片"))
        check("11.8 适配器收到了图片", len(wa.images) == 1 and wa.images[0]["ref"].startswith("cards/"),
              str(wa.images))
        check("11.9 图注也留下来了", wa.images[0]["caption"] == "测试卡片", str(wa.images))
        # 白名单外的路径：要如实返回 success=False（否则插件以为发成功、不降级成文字）
        bad = _a.run(wa.send_image_file("web:panel", "/etc/passwd"))
        check("11.9b 白名单外 → success=False，不再装作成功", bad.success is False, str(bad))
    S2.IMG_ROOTS = _saved_roots

    # 只出图不出字：不能判成「静默」
    check("11.10 有图没字 → 不算静默（这正是以前丢掉图片的那条判定）",
          not S2._plugin_silent("", {"images": [{"ref": "cards/a.png"}]}))
    check("11.11 没图没字 → 才算静默", S2._plugin_silent("", {}))
    check("11.12 有字就不算静默", not S2._plugin_silent("说话", {}))

    # 落盘：一行 kind=image，带角色，能被 /api/chat 的过滤带上
    with tempfile.TemporaryDirectory() as td:
        old_chat = S2.CHAT_JSONL
        S2.CHAT_JSONL = Path(td) / "chat.jsonl"
        try:
            S2._append_images({"session": "s1", "images": [{"ref": "cards/a.png", "caption": "图注"}]},
                              "klee")
            rows = [json.loads(x) for x in S2.CHAT_JSONL.read_text(encoding="utf-8").splitlines() if x.strip()]
            check("11.13 写出了一行 kind=image", len(rows) == 1 and rows[0]["kind"] == "image", str(rows))
            check("11.14 行里带角色（否则会被 /api/chat 的按角色过滤丢掉）",
                  rows[0].get("character") == "klee", str(rows[0]))
            check("11.15 图注最多 80 字",
                  len(str(rows[0].get("caption"))) <= 80)
        finally:
            S2.CHAT_JSONL = old_chat

    # 真插件：.运势 只出卡片不出字
    m2 = FeatureManager(type("A2", (Adapter,), {"active_character": "klee"})())
    m2.load()
    _pin_cards_dir()
    hub2 = SkillHub(m2)
    r = hub2.run("fortune", ".运势", adapter=wa, is_group=False, group_id="", user_id="1",
                 user_name="主人", role="owner", timeout=15)
    if _has_pil():
        check("11.16 运势技能产出了图片", r.get("ok") and r.get("images"), str(r)[:200])
        check("11.17 纯出图的技能文字只是图注", bool(r.get("text")), str(r.get("text"))[:80])
        check("11.18 卡片落在临时目录（没写生产 state/cards）",
              all(str(_CARD_TMP) in str(i.get("path")) for i in (r.get("images") or [])),
              str([i.get("path") for i in (r.get("images") or [])])[:200])
    else:
        print("  - 跳过 11.16~11.18：本机没有 Pillow（出图走的是可选依赖）")

    print("\n[12] 插件丢后台任务 / 直接 adapter.send：面板也要收得到")
    import asyncio as _asyncio

    class _MgrBg:
        def __init__(self):
            self.seen = []

        def nlu_match(self, text):
            return None

        async def run_message_hooks(self, ctx):
            return True

        async def handle_command(self, ctx):
            self.seen.append(ctx.text)
            async def worker():
                await _asyncio.sleep(0.25)          # 模拟 .画画 那样「先返回，后台再出图」
                await ctx.adapter.send_image_file("web:panel", str(f))
            _asyncio.get_running_loop().create_task(worker())
            await ctx.adapter.send("web:panel", "直接发的提示")   # 不走 ctx.reply
            return True

    class _RtBg:
        def __init__(self):
            self.mgr = _MgrBg()
            self.config = {"feature_prefix": "."}

    _saved2 = S2.IMG_ROOTS
    S2.IMG_ROOTS = (_img_root,)
    try:
        rt_bg = _RtBg()
        job_bg = {"session": "s"}
        out_bg = S2._web_plugin_dispatch(rt_bg, job_bg, ".delay", "klee")
        check("12.1 插件直接 adapter.send 的文字没被吞", "直接发的提示" in str(out_bg), repr(out_bg))
        check("12.2 关循环前把后台任务跑到出图",
              len(job_bg.get("images") or []) == 1, str(job_bg.get("images")))
    finally:
        S2.IMG_ROOTS = _saved2

    print("\n[13] 这一轮 OCR 的回归点")
    # 13.1 正文里夹着标记、但还有解释文字 → 不能当调用执行
    c = script({"text": "写法是这样的：<tool_call>{\"name\":\"now\",\"arguments\":{}}</tool_call>"
                        " 这就是一条工具调用。", "tool_calls": []})
    res = A.run(None, [{"role": "user", "content": "tool_call 怎么写"}], max_steps=2, complete=c)
    check("13.1 解释文字里的标记不会被当成真调用", res["tool_calls"] == [], str(res["tool_calls"]))
    check("13.2 解释文字本身保留", "写法是这样的" in res["text"], res["text"][:80])

    # 13.3 只有标记、没有正文时才当调用
    c = script({"text": '<tool_call>{"name":"now","arguments":{}}</tool_call>', "tool_calls": []},
               {"text": "现在三点", "tool_calls": []})
    res = A.run(None, [{"role": "user", "content": "几点了"}], max_steps=3, complete=c)
    check("13.3 纯标记才当调用", res["tool_calls"] == ["now"], str(res["tool_calls"]))

    # 13.4 非 ASCII 大小写：原生扫描只折叠 ASCII，不能拿它的空结果当「没命中」
    from bot import native as N2
    check("13.4 西里尔大写能命中小写词", N2.scan("ДУРАК", ["дурак"]) == [0],
          str(N2.scan("ДУРАК", ["дурак"])))
    check("13.5 中文不受影响（快路径还在）", N2.scan("这里有屏蔽词", ["屏蔽"]) == [0])

    # 13.6 Java 必须钉 UTF-8：LANG=C 的机器上 stdio 编码跟 locale 走（实测过）
    from bot.ext.host import ExtPlugin, JAVA_UTF8, LANG_RUN
    from pathlib import Path as _P

    _ext_base = _P(ROOT) / "bot" / "features" / "ext"

    def _cmd(spec):
        return " ".join(ExtPlugin(spec, _ext_base).cmd)

    check("13.6 默认启动命令带 stdin/stdout 编码",
          all(k in " ".join(LANG_RUN["java"]) for k in ("-Dstdin.encoding", "-Dstdout.encoding")),
          str(LANG_RUN["java"]))
    # 13.7 必须测**真的注入到 cmd**，不是只看常量
    got = _cmd({"id": "x", "language": "java", "run": ["java", "-cp", "c"], "args": ["X"]})
    check("13.7 spec 自己的 run 也会被补上四个属性", got.count("-D") == 4, got)
    # 13.8 启动器是 env 包装时，旗标要插到真正的 java 后面，不能插给 env
    got2 = _cmd({"id": "y", "language": "java",
                 "run": ["env", "JAVA_HOME=/opt/jdk", "java", "-cp", "c"], "args": ["Y"]})
    check("13.8 env 包装时插在 java 后面",
          got2.startswith("env JAVA_HOME=/opt/jdk java -D") and "env -D" not in got2, got2)
    # 13.9 spec 只钉了一条：剩下三条照样要补
    got3 = _cmd({"id": "z", "language": "java",
                 "run": ["java", "-Dstdout.encoding=UTF-8", "-cp", "c"], "args": ["Z"]})
    check("13.9 逐条补：只写了一条也不会吞掉其余三条",
          got3.count("-D") == 4 and got3.count("-Dstdout.encoding") == 1, got3)
    # 13.10 认不出 java（sh 包装脚本）：不要动它，别把插件弄坏
    got4 = _cmd({"id": "w", "language": "java", "run": ["sh", "run.sh"], "args": []})
    check("13.10 认不出 java 时原样返回", got4 == "sh run.sh", got4)

    # 13.11 混合词表：原生只折叠 ASCII，结果必须并上 Python 的
    check("13.11 混合词表命中完整（hello + ДУРАК）",
          N2.scan("hello дурак", ["hello", "ДУРАК"]) == [0, 1],
          str(N2.scan("hello дурак", ["hello", "ДУРАК"])))

    # 13.12 讲写法时，块是答案的一部分，不能被删掉
    ex = "写法是这样的：<tool_call>{\"name\":\"now\",\"arguments\":{}}</tool_call> 这就是一条工具调用。"
    c = script({"text": ex, "tool_calls": []})
    res = A.run(None, [{"role": "user", "content": "tool_call 怎么写"}], max_steps=2, complete=c)
    check("13.12 例子里的块保留在回答里", "<tool_call>" in res["text"] and "写法是这样的" in res["text"],
          res["text"][:120])
    check("13.13 例子不被执行", res["tool_calls"] == [], str(res["tool_calls"]))
    # 13.14b 引导词白名单：讲语法要认，说意图不能认
    for h, want in (("例如下面这样：", True), ("格式是：", True),
                    ("我换个写法试试：", False), ("按这个格式来：", False)):
        check(f"13.14b 引导词 {h!r} → 例子={want}",
              A._markup_is_example(h + '<tool_call>{"name":"now"}</tool_call>') is want, h)
    # 13.14 「好的，我去查」+ 纯块 → 仍然要执行（别把正常调用当解释）
    c = script({"text": "好的，我这就去查一下最新的资料。<tool_call>{\"name\":\"now\","
                        "\"arguments\":{}}</tool_call>", "tool_calls": []},
               {"text": "查到了", "tool_calls": []})
    res = A.run(None, [{"role": "user", "content": "查一下"}], max_steps=3, complete=c)
    check("13.14 开场白 + 块 → 照样执行", res["tool_calls"] == ["now"], str(res["tool_calls"]))

    # 13.8 只出图不出字：不能写空文本行
    with tempfile.TemporaryDirectory() as td:
        old_chat2 = S2.CHAT_JSONL
        S2.CHAT_JSONL = Path(td) / "chat.jsonl"
        try:
            S2._append_images({"session": "s", "images": [{"ref": "cards/a.png"}]}, "klee")
            rows = [json.loads(x) for x in S2.CHAT_JSONL.read_text(encoding="utf-8").splitlines() if x.strip()]
            check("13.8 图片行没有多余的空文本", all(r.get("text") for r in rows) is False
                  and all(r.get("kind") == "image" for r in rows), str(rows))
        finally:
            S2.CHAT_JSONL = old_chat2

    import shutil as _sh
    _sh.rmtree(_CARD_TMP, ignore_errors=True)
    print(f"\nPASS {len(PASS)} / {len(PASS) + len(FAIL)}")
    if FAIL:
        print("失败：" + "、".join(FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
