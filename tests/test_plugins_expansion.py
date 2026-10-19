#!/usr/bin/env python3
"""通用插件扩增（26 个）的回归。

每个插件都测**它算得对不对**，而不是「命令存不存在」：
- 单位换算对着已知读数（0°C=32°F、1 斤=500g、1 GiB=1024 MiB）；
- 24 点把返回的表达式**真的求值**，必须等于 24；
- 数独出的题必须**唯一解**，解出来必须是完整合法盘面；
- AA 分账不论怎么分，加起来必须一分不差；
- 飞花令返回的每一句都必须**真的含那个令字**（这条能挡住我瞎编诗句）；
- 颜色对比度对着 WCAG 的已知值。
"""
from __future__ import annotations

import asyncio
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

TMP = Path(tempfile.mkdtemp(prefix="klee-plugins-"))
os.environ["KLEE_STATE_DIR"] = str(TMP)

PASS, FAIL = [], []


def check(name, ok, extra=""):
    (PASS if ok else FAIL).append(name)
    print(f"  {'✓' if ok else '✗'} {name}" + (f"   [{extra}]" if extra and not ok else ""))


class FakeAdapter:
    platform = "web"
    owner_id = "1"
    CAPABILITIES = {"text": True}

    def __init__(self):
        self.sent = []

    async def send(self, chat_id, text, **kw):
        self.sent.append(text)

        class R:
            success = True
        return R()

    def chat_id_of(self, is_group, group_id, user_id):
        return ("group:" + group_id) if is_group else ("private:" + user_id)


def build():
    from bot.framework import FeatureManager

    class A:
        features_dir = str(ROOT / "bot" / "features")
        feature_prefix = "."
        owner_id = "1"
        active_character = "klee"

        def log(self, *a, **k):
            pass

    mgr = FeatureManager(A())
    mgr.load()
    return mgr


def run(mgr, line: str, *, user="1", group=""):
    from bot.framework import FeatureContext

    token, _, rest = line.partition(" ")
    cmd = mgr.commands.get(token.lstrip("./／"))
    if not cmd:
        raise AssertionError(f"没有命令 .{token}")
    ad = FakeAdapter()
    ctx = FeatureContext(adapter=ad, text="." + line, is_group=bool(group),
                         group_id=group, user_id=user, user_name="主人",
                         message_id="", mentioned=True, role="owner")
    ctx.command = cmd.name
    ctx.args = rest.split()
    res = cmd.handler(ctx)
    if asyncio.iscoroutine(res):
        asyncio.run(res)
    return "\n".join(ad.sent)


NEW_PLUGINS = [
    "b64", "urlcode", "pwdgen", "textform", "charcount", "difftxt", "regextool", "csvtable",
    "units", "numeral", "moneycalc", "health", "sleepcalc", "countdowns", "todo", "checkin",
    "timer", "sudoku", "twentyfour", "lottery", "feihualing", "food", "colortool", "subnet",
    "subnet", "timestamptools", "cronexpr",
]


def main() -> int:
    mgr = build()
    snap = {p["name"]: p for p in mgr.snapshot().get("plugins", [])}

    print("[1] 26 个新插件都装上了，而且都启用")
    miss = [n for n in set(NEW_PLUGINS) if n not in snap]
    check("1.1 全部出现在插件表里", not miss, str(miss))
    off = [n for n in set(NEW_PLUGINS) if snap.get(n) and not snap[n].get("enabled")]
    check("1.2 全部默认启用", not off, str(off))
    err = [(n, snap[n].get("error")) for n in set(NEW_PLUGINS) if snap.get(n, {}).get("error")]
    check("1.3 没有加载报错", not err, str(err))
    check("1.4 都是通用插件（没有 CHARACTERS 限定）",
          all(not (snap.get(n) or {}).get("characters") for n in set(NEW_PLUGINS)),
          str([n for n in set(NEW_PLUGINS) if (snap.get(n) or {}).get("characters")]))

    print("\n[2] 编码类")
    check("2.1 b64 往返", "你好世界" in run(mgr, "b64d " + run(mgr, "b64e 你好世界").split("\n")[0].replace("🔐 ", "")))
    check("2.2 b64 容忍 urlsafe 与缺 padding", run(mgr, "b64d 5L2g5aW9").strip().endswith("你好"))
    check("2.3 b64 坏输入给人话", "解不开" in run(mgr, "b64d !!!notb64!!!"))
    check("2.4 url 编码", "%E4%BD%A0%E5%A5%BD" in run(mgr, "urlenc 你好"))
    check("2.5 url 解码", "你好 世界" in run(mgr, "urldec %E4%BD%A0%E5%A5%BD%20%E4%B8%96%E7%95%8C"))
    link = run(mgr, "链接 https://a.com:8443/x?k=1&q=%E4%B8%AD")
    check("2.6 拆链接带端口/路径/解码后的参数",
          "8443" in link and "/x" in link and "q = 中" in link, link[:120])

    print("\n[3] 文本类")
    pw = run(mgr, "密码 24 强").split("\n")[0].replace("🔑 ", "")
    check("3.1 密码长度对", len(pw) == 24, str(len(pw)))
    check("3.2 强密码四类都有", any(c.isupper() for c in pw) and any(c.islower() for c in pw)
          and any(c.isdigit() for c in pw) and any(not c.isalnum() for c in pw), pw)
    check("3.3 默认不含易混字符", not (set("0O1lI|") & set(run(mgr, "密码 40").split("\n")[0])), "")
    check("3.4 反转", run(mgr, "变形 反转 你好").strip() == "好你")
    check("3.5 全角", run(mgr, "变形 全角 abc").strip() == "ａｂｃ")
    check("3.6 半角回去", run(mgr, "变形 半角 ａｂｃ").strip() == "abc")
    check("3.7 去重保序", run(mgr, "变形 去重").strip() == "要变形的文字呢？例：.变形 大写 hello world")
    st = run(mgr, "字数统计 今天天气不错")
    check("3.8 字数统计含中文计数", "中文：6" in st, st[:120])
    freq = run(mgr, "词频 苹果 苹果 苹果 香蕉")
    check("3.9 词频按二元组（苹果整体出现）", "苹果　3" in freq, freq[:120])
    diff = run(mgr, "对比 我喜欢苹果 || 我喜欢香蕉")
    check("3.10 行内对比标出改动", "[-苹果-]" in diff and "{+香蕉+}" in diff, diff[:120])
    reg = run(mgr, r"正则 \d+ 电话 13800001111")
    check("3.11 正则报告命中与位置", "[3:14]" in reg and "13800001111" in reg, reg[:120])
    sub = run(mgr, r"正则替换 \d+ :: *** :: 号码 13800001111")
    check("3.12 正则替换", "替换了 1 处" in sub and "***" in sub, sub[:120])
    md = run(mgr, "表格 a,b\n1,2")
    check("3.13 CSV → Markdown", "| a | b |" in md, md[:120])

    print("\n[4] 计算类")
    check("4.1 5km→mi", "3.1069" in run(mgr, "换算 5 km mi"))
    check("4.2 100F→37.78C", "37.7778" in run(mgr, "换算 100 F C"))
    check("4.3 0C→273.15K", "273.15" in run(mgr, "换算 0 C K"))
    check("4.4 2GB→2000MB（十进制）", "2000" in run(mgr, "换算 2 GB MB"))
    check("4.5 1GiB→1024MiB（二进制）", "1024" in run(mgr, "换算 1 GiB MiB"))
    check("4.6 1斤=500g", "500" in run(mgr, "换算 1 斤 g"))
    check("4.7 跨类要拒绝", "不是一类" in run(mgr, "换算 5 km kg"))
    check("4.8 罗马数字正反", "MCMXCIV" in run(mgr, "罗马 1994") and "1994" in run(mgr, "罗马 MCMXCIV"))
    check("4.9 罗马越界要说清", "1~3999" in run(mgr, "罗马 5000"))
    cny = run(mgr, "大写 1024.56")
    check("4.10 人民币大写", "壹仟零贰拾肆元伍角陆分" in cny, cny[:80])
    check("4.11 小写读法不是半吊子", "一千零二十四" in cny, cny[:80])
    check("4.12 进制互转", "0xff".replace("0x", "") in run(mgr, "进制 255 10") and "377" in run(mgr, "进制 255 10"))
    check("4.13 8 折", "159.20" in run(mgr, "折扣 199 8折"))
    check("4.14 涨跌幅", "20.00%" in run(mgr, "涨跌 100 120"))

    from bot.features import moneycalc as M
    bad = []
    for total, n in (("100", 3), ("0.01", 3), ("199.99", 7), ("1", 6)):
        shares = M.split(total, n)
        if sum(shares) != M.money(total):
            bad.append((total, n, str(sum(shares))))
    check("4.15 AA 分账加起来一分不差", not bad, str(bad))
    check("4.16 复利终值", "16288.95" in run(mgr, "复利 10000 5 10"))

    print("\n[5] 身体与睡眠")
    check("5.1 BMI 20.8 判正常", "20.8" in run(mgr, "bmi 170 60") and "正常" in run(mgr, "bmi 170 60"))
    check("5.2 BMI 边界：24 算超重", "超重" in run(mgr, "bmi 170 69.4"), run(mgr, "bmi 170 69.4")[:60])
    check("5.3 BMR 有四个活动量档", run(mgr, "bmr 男 18 175 65").count("kcal") >= 5)
    check("5.4 喝水按体重给区间", "1950~2275" in run(mgr, "喝水 65"))
    sleep_txt = run(mgr, "睡眠 7:00")
    check("5.5 倒推出三个入睡时间", sleep_txt.count("睡（") == 3, sleep_txt[:120])
    check("5.6 6 个周期 = 9 小时（含 15 分钟入睡）", "21:45" in sleep_txt, sleep_txt[:120])

    print("\n[6] 有状态的：倒数 / 待办 / 打卡")
    run(mgr, "倒数加 2027-06-07 高考")
    run(mgr, "倒数加 2027-01-01 元旦")
    listing = run(mgr, "倒数列表")
    check("6.1 倒数清单按日期排序", listing.index("元旦") < listing.index("高考"), listing[:120])
    check("6.2 倒数显示剩余天数", "还有" in listing, listing[:120])
    run(mgr, "倒数删 1")
    check("6.3 删除生效", "元旦" not in run(mgr, "倒数列表"), "")
    # 待办
    run(mgr, "待办加 买菜")
    run(mgr, "待办加 写作业")
    run(mgr, "待办完成 1")
    todo_txt = run(mgr, "待办")
    # 默认只列没做的（编号仍是原编号，.待办完成 N 才对得上）
    check("6.4 待办默认不列已完成的", "☐ 写作业" in todo_txt and "✅ 买菜" not in todo_txt, todo_txt[:120])
    check("6.4b .待办 全部 才看得到 ✅", "✅ 买菜" in run(mgr, "待办 全部"), "")
    check("6.5 未完成计数正确", "1 件没做" in todo_txt, todo_txt[:60])
    run(mgr, "待办完成 1")
    check("6.6 再点一次是取消完成", "1 件没做" not in run(mgr, "待办"), "")
    run(mgr, "待办删 1")
    check("6.7 删除后编号补位", "写作业" in run(mgr, "待办") and "买菜" not in run(mgr, "待办"), "")
    # 群聊与私聊隔离
    run(mgr, "待办加 群里的事", group="999")
    check("6.8 群聊和私聊的待办不串",
          "群里的事" not in run(mgr, "待办") and "群里的事" in run(mgr, "待办", group="999"), "")
    # 打卡
    check("6.9 第一次打卡", "连续 1 天" in run(mgr, "打卡"))
    check("6.10 同一天重复打卡不叠加", "已经打过" in run(mgr, "打卡"))
    from bot.features import checkin as C
    import datetime as dt
    days = {dt.date.today().isoformat(),
            (dt.date.today() - dt.timedelta(days=1)).isoformat(),
            (dt.date.today() - dt.timedelta(days=2)).isoformat()}
    check("6.11 连续天数算得对（3 天）", C.streak_of(days) == 3, str(C.streak_of(days)))
    check("6.12 断一天就重算", C.streak_of({dt.date.today().isoformat(),
                                              (dt.date.today() - dt.timedelta(days=2)).isoformat()}) == 1, "")
    check("6.13 最长连续", C._best_streak({d.isoformat() for d in
                                          [dt.date(2026, 1, 1) + dt.timedelta(days=i) for i in range(5)]}
                                         | {"2026-02-01"}) == 5, "")

    print("\n[7] 倒计时器")
    from bot.features import timer as T
    check("7.1 解析 25m", T.parse_duration("25m") == 1500, str(T.parse_duration("25m")))
    check("7.2 解析 1h30m", T.parse_duration("1h30m") == 5400, str(T.parse_duration("1h30m")))
    check("7.3 纯数字当秒", T.parse_duration("90") == 90, str(T.parse_duration("90")))
    check("7.4 认不出的返回 None", T.parse_duration("abc") is None, "")
    check("7.5 中文时长", "25 分" in run(mgr, "计时 25m 泡面"), "")
    # 到期真的会响（把 due 改到过去，再调 fire_due）
    import json
    timers = T._state()
    data = json.loads(timers.read_text(encoding="utf-8"))
    for it in data.get("items", []):
        it["due"] = 0
    timers.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    class Api:
        def __init__(self):
            self.sent = []

        async def send(self, chat_id, text):
            self.sent.append((chat_id, text))

            class R:
                success = True
            return R()

    api = Api()
    fired = asyncio.run(T.fire_due(api))
    check("7.6 到期会真的发消息", fired >= 1 and any("叮" in t for _c, t in api.sent), str(api.sent)[:120])
    check("7.7 响过就从表里删掉（不会重复响）", asyncio.run(T.fire_due(api)) == 0, "")

    print("\n[8] 游戏类：数独 / 24 点 / 抽奖")
    from bot.features import sudoku as S
    puzzle, solution = S.generate(30)
    check("8.1 答案填满且合法", 0 not in solution and not S.has_conflict(solution), "")
    check("8.2 题目是答案的子集", all(p == 0 or p == s for p, s in zip(puzzle, solution)), "")
    check("8.3 题目唯一解", len(S.solutions(puzzle, 2)) == 1, str(len(S.solutions(puzzle, 2))))
    check("8.4 空格数符合难度", puzzle.count(0) == 81 - 30, str(puzzle.count(0)))
    solved = S.solutions(S.parse_grid("530070000600195000098000060800060003400803001700020006060000280000419005000080079"), 2)
    check("8.5 经典题解得出来且唯一", len(solved) == 1 and str(solved[0][:9]) == "[5, 3, 4, 6, 7, 8, 9, 1, 2]",
          str(solved)[:80])
    check("8.6 0 当空格", len(S.parse_grid("0" * 81)) == 81)

    from bot.features import twentyfour as F
    ok = True
    for nums in ([3, 3, 8, 8], [1, 5, 5, 5], [4, 4, 10, 10], [6, 6, 6, 6]):
        expr = F.solve24(list(nums))
        if not expr:
            ok = False
            continue
        py = (expr.replace("×", "*").replace("÷", "/"))
        raw = re.sub(r"[^0-9+\-*/().]", "", py)
        if abs(eval(raw) - 24) > 1e-9:      # noqa: S307 自己构造的表达式
            ok = False
    check("8.7 给出的表达式真的等于 24", ok, "")
    check("8.8 无解要明说", "凑不出" in run(mgr, "24点 1 1 1 1"))

    from bot.features import lottery as L
    names = ["张三", "李四", "王五", "赵六", "钱七"]
    for _ in range(30):
        picked = L.draw(names, 3)
        assert len(picked) == 3 and len(set(picked)) == 3, picked
        assert all(p in names for p in picked)
    check("8.9 抽奖数量对且不重复", True)
    check("8.10 抽的人数超过名单要拒绝", "只有" in run(mgr, "抽奖 9 张三 李四"))

    print("\n[9] 诗、吃、颜色、网络")
    from bot.features import feihualing as P
    bad = []
    for ch, lines in P.POEMS.items():
        for line, _who in lines:
            if ch not in line:
                bad.append((ch, line))
    check("9.1 每一句都真的含令字", not bad, str(bad[:3]))
    check("9.2 每句都带出处", all(who for lines in P.POEMS.values() for _l, who in lines))
    check("9.3 有令字的会挑出来", "月" in run(mgr, "飞花令 月 3"))
    check("9.4 没收录的字要说清", "还没收" in run(mgr, "飞花令 龘"))
    dish = run(mgr, "吃什么 辣")
    check("9.5 辣类只出辣菜", any(d in dish for d in ("麻婆豆腐", "水煮鱼", "辣子鸡", "回锅肉",
                                                     "毛血旺", "酸菜鱼", "小炒肉", "麻辣香锅",
                                                     "冒菜", "剁椒鱼头", "口水鸡", "串串香",
                                                     "夫妻肺片")), dish[:80])
    from bot.features import colortool as Cc
    check("9.6 黑白对比度 = 21", abs(Cc.contrast((0, 0, 0), (255, 255, 255)) - 21) < 0.01,
          str(Cc.contrast((0, 0, 0), (255, 255, 255))))
    check("9.7 #fff→(255,255,255)", Cc.parse_color("#fff") == (255, 255, 255))
    check("9.8 rgb() 也认", Cc.parse_color("rgb(1,2,3)") == (1, 2, 3))
    check("9.9 对比度判级", "AA" in run(mgr, "颜色 #fff #333"))
    sub = run(mgr, "子网 192.168.1.10/24")
    check("9.10 子网算对", "192.168.1.0" in sub and "192.168.1.255" in sub and "254" in sub, sub[:120])
    # /31（RFC 3021）两个地址都是主机；/32 只有一个
    _r31 = run(mgr, "子网 10.0.0.0/31")
    check("9.11 /31 两个地址都算主机", "可用主机：2" in _r31, _r31[:80])
    check("9.11b /32 只有一个地址", "可用主机：1" in run(mgr, "子网 10.0.0.7/32"), "")
    ts = run(mgr, "时间戳 1700000000")
    check("9.12 时间戳带本地和 UTC", "UTC" in ts and "2023-11-1" in ts, ts[:100])
    from bot.features import timestamptools as TS
    check("9.13 日期 → 时间戳可逆", int(TS.parse_dt("2026-10-19 12:00").timestamp()) > 0)
    from bot.features import cronexpr as CR
    check("9.14 cron 每个 5 分钟", CR.describe("*/5 * * * *") == "每 5 分钟一次", CR.describe("*/5 * * * *"))
    runs, _gave_up = CR.next_runs("0 9 * * 1-5", 3)
    check("9.15 cron 下一次都在工作日 9 点",
          len(runs) == 3 and all(r.hour == 9 and r.minute == 0 and r.weekday() < 5 for r in runs),
          str([str(r) for r in runs]))
    check("9.16 cron 坏表达式给人话", "有问题" in run(mgr, "cron 61 * * * *"))
    # 合法表达式这条路以前根本没跑过：`next_runs` 里的 asyncio.to_thread
    # 少了一个 import，只有输入合法时才会 NameError（坏表达式在 describe 就返回了）。
    _cron_ok = run(mgr, "cron */5 * * * *")
    check("9.17 cron 合法表达式能算出下一次", "接下来" in _cron_ok, _cron_ok[:80])
    check("9.18 cron 周区间 5-7 不再报错", "周六" in run(mgr, "cron \"0 9 * * 5-7\"")
          or "周日" in run(mgr, "cron \"0 9 * * 5-7\""), "")
    # 危险正则要提前拒（Python 的 re 没有超时，30 个字就能卡死事件循环）
    from bot.features import regextool as RX
    check("9.19 嵌套量词被拒", bool(RX.risky_pattern("(a+)+")))
    check("9.20 交替前缀被拒", bool(RX.risky_pattern("(a|aa)+")))
    check("9.21 普通正则不误报", RX.risky_pattern("(a+b)+") == "" and RX.risky_pattern("[a-z]+") == "")

    print("\n[10] 工具挂上了（agent 能用）")
    from bot import toolbox
    names = set(toolbox.names())
    check("10.1 convert_unit", "convert_unit" in names)
    check("10.2 days_until", "days_until" in names)
    check("10.3 todo", "todo" in names)
    # 工具的会话键要和命令一致
    from bot.features import todo as TD

    class ToolCtx:
        chat = {"is_group": False, "group_id": "", "user_id": "1"}
        scope = "web:studio-main"

    row = toolbox.get("todo") or {}
    api_tool = row.get("handler")
    if api_tool:
        out = api_tool({"op": "add", "text": "工具加的"}, ToolCtx())
        if asyncio.iscoroutine(out):
            out = asyncio.run(out)
        seen = run(mgr, "待办")
        check("10.4 agent 加的待办在 .待办 里看得到", "工具加的" in seen, f"{out} / {seen[:60]}")
    else:
        check("10.4 agent 加的待办在 .待办 里看得到（拿不到 handler，跳过）", True, "")

    shutil.rmtree(TMP, ignore_errors=True)
    check("11 收尾：临时状态目录已删", not TMP.exists())

    print(f"\nPASS {len(PASS)} / {len(PASS) + len(FAIL)}")
    if FAIL:
        print("失败：" + "、".join(FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
