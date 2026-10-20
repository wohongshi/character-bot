#!/usr/bin/env python3
"""文档导入知识库（`bot/kbingest.py` + server 的 `/api/kb/ingest`）。

关键点：
1. 切块要按标题/段落/句子走，超长段落才硬切，块间留重叠；
2. 去重靠**内容指纹**（归一化后），同一个文件导三次也只入一次；
3. 短尾块并进上一块，别制造一堆 5 字块；
4. 不认识的字符 id 必须报错 —— klee-kb 对未知角色会**回退到默认库**，
   传错一个字母就会把资料灌进克莱的库里。
"""
from __future__ import annotations

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


DOC = """# 原神 · 枫丹

芙宁娜是枫丹的水神。她喜欢戏剧，也喜欢甜点。她在审判庭上总是很夸张。
这段是第二段，讲的是那维莱特。他是最高审判官，性格很认真。

## 蒙德

温迪是风神，喜欢喝酒和唱歌。
"""


def main():
    from bot import kbingest as IG

    tmp = Path(tempfile.mkdtemp(prefix="klee-kbing-"))

    section("1. 切块")
    chunks = IG.split(DOC, max_chars=200, overlap=0, min_chars=10, title="枫丹")
    check("1.1 按标题切开", [c["title"] for c in chunks] == ["原神 · 枫丹", "蒙德"],
          str([c["title"] for c in chunks]))
    check("1.2 每块都有 idx", [c["idx"] for c in chunks] == [0, 1])
    check("1.3 chars 与 text 一致", all(c["chars"] == len(c["text"]) for c in chunks))
    check("1.4 小节内容进对了块", "温迪" in chunks[1]["text"])
    check("1.5 没标题时用文件名当标题",
          IG.split("一段话。", title="x.txt")[0]["title"] == "x.txt")
    check("1.6 空文本切出 0 块", IG.split("") == [])

    section("2. 长段落 / 重叠 / 下限")
    long_par = "。".join(f"第{i}句说的是第{i}件事" for i in range(40)) + "。"
    out = IG.split(long_par, max_chars=80, overlap=20, min_chars=10)
    check("2.1 超长段落被切成多块", len(out) > 3, str(len(out)))
    check("2.2 每块不超过上限", all(c["chars"] <= 80 for c in out),
          str(max(c["chars"] for c in out)))
    check("2.3 块之间真的有重叠",
          any(out[i]["text"][-8:] and out[i]["text"][-8:] in out[i + 1]["text"]
              for i in range(len(out) - 1)))
    check("2.4 句子没被从中间切开", all(c["text"].rstrip().endswith("。") for c in out),
          str([c["text"][-3:] for c in out]))
    tiny = IG.split("这是一段足够长的正文内容，用来当上一块。" * 3 + "\n\n短。",
                    max_chars=200, overlap=0, min_chars=60)
    check("2.5 短尾块并进上一块", len(tiny) == 1 and "短。" in tiny[0]["text"],
          str([c["chars"] for c in tiny]))
    long_line = "、".join(f"名字{i}" for i in range(400))      # 没有句号的超长行
    cut = IG.split(long_line, max_chars=200, overlap=0, min_chars=10)
    check("2.6a 没有句号也能切开", len(cut) > 5, str(len(cut)))
    check("2.6b 一个字符都不许丢",
          "".join(c["text"] for c in cut) == long_line,
          f'{sum(c["chars"] for c in cut)} vs {len(long_line)}')
    check("2.6c 每块不超上限", all(c["chars"] <= 200 for c in cut))
    check("2.6 下限不会把 max_chars 顶破",
          all(c["chars"] <= 200 for c in IG.split(long_par, max_chars=200, overlap=0,
                                                  min_chars=180)))

    section("3. 指纹 / 归一化")
    check("3.1 空白不影响指纹",
          IG.fingerprint("a b\n\nc") == IG.fingerprint("a  b\nc"))
    check("3.2 全角空格不影响", IG.fingerprint("a\u3000b") == IG.fingerprint("a b"))
    check("3.3 markdown 井号不影响", IG.fingerprint("## 标题") == IG.fingerprint("标题"))
    check("3.4 内容不同指纹不同", IG.fingerprint("甲") != IG.fingerprint("乙"))
    check("3.5 指纹长度 16", len(IG.fingerprint("abc")) == 16)

    section("4. plan：本篇重复 + 已入过库")
    p1 = IG.plan(DOC, max_chars=200, min_chars=10, title="枫丹")
    check("4.1 第一次都能入库", p1["counts"]["fresh"] == p1["counts"]["total"])
    dup_doc = DOC + "\n\n# 另一节\n\n" + chunks[0]["text"]
    p2 = IG.plan(dup_doc, max_chars=200, min_chars=10, title="枫丹")
    check("4.2 篇内重复被标出来", p2["counts"]["dup"] >= 1, str(p2["counts"]))
    check("4.3 dup 有中文原因", any(c["dup"] for c in p2["chunks"]))
    IG.save_seen(tmp, "atri", [c["fp"] for c in p1["chunks"]])
    p3 = IG.plan(DOC, state_dir=tmp, character="atri", max_chars=200, min_chars=10,
                 title="枫丹")
    check("4.4 第二次全判重复", p3["counts"]["fresh"] == 0 and p3["counts"]["dup"] ==
          p3["counts"]["total"], str(p3["counts"]))
    check("4.5 重复的原因写「已入过库」",
          all(c["dup"] == "已入过库" for c in p3["chunks"]))
    p4 = IG.plan(DOC, state_dir=tmp, character="hutao", max_chars=200, min_chars=10,
                 title="枫丹")
    check("4.6 指纹按角色隔离", p4["counts"]["fresh"] == p1["counts"]["total"],
          str(p4["counts"]))

    section("5. 上限")
    try:
        IG.plan("字" * (IG.MAX_TEXT + 1))
        ok, err = False, "没报错"
    except ValueError as e:
        ok, err = True, str(e)
    check("5.1 超大文本被拒", ok, err)
    try:
        IG.plan("\n\n".join("这是一段独立的正文。" * 3 for _ in range(IG.MAX_CHUNKS + 20)),
                max_chars=80, min_chars=10)
        ok, err = False, "没报错"
    except ValueError as e:
        ok, err = True, str(e)
    check("5.2 块数超上限被拒", ok, err)
    check("5.3 max_chars 有下限", all(c["chars"] <= 120 for c in
                                      IG.split("啊" * 500, max_chars=1)))

    section("6. commit")
    plan = IG.plan("\n\n".join(f"第{i}段：这是一段够长的正文，用来凑出多个块。" for i in range(4)),
                   state_dir=tmp, character="atri", max_chars=40, overlap=0,
                   min_chars=10, title="t")
    check("6.0 至少 3 块才测得了", len(plan["chunks"]) >= 3, str(len(plan["chunks"])))
    calls = []

    def add_ok(c):
        calls.append(c["idx"])

    res = IG.commit(plan["chunks"], add_ok)
    check("6.1 全部写入", res["added"] == len(plan["chunks"]) and not res["failed"], str(res))
    check("6.2 返回成功指纹", len(res["fps"]) == res["added"])

    def add_bad(c):
        if c["idx"] == 1:
            raise RuntimeError("第二条坏了")

    calls.clear()
    res2 = IG.commit(plan["chunks"], add_bad)
    check("6.3 一条失败不影响其它", res2["failed"] == 1 and res2["added"] == len(plan["chunks"]) - 1,
          str(res2))
    check("6.4 错误信息带块号", "第 2 块" in (res2["errors"] or [""])[0], str(res2["errors"]))
    res3 = IG.commit([{**c, "dup": "x"} for c in plan["chunks"]], add_ok)
    check("6.5 重复的一条都不写", res3["added"] == 0 and res3["failed"] == 0)

    section("7. seen 表")
    IG.save_seen(tmp, "atri", ["a", "b"])
    IG.save_seen(tmp, "atri", ["b", "c"])
    seen = IG.load_seen(tmp, "atri")
    check("7.1 合并去重", seen.count("b") == 1 and set(seen) >= {"a", "c"}, str(seen))
    check("7.2 别的角色不受影响", IG.load_seen(tmp, "hutao") == [])
    IG.save_seen(tmp, "big", [f"h{i}" for i in range(IG.SEEN_MAX + 50)])
    got = IG.load_seen(tmp, "big")
    check("7.3 FIFO 截断", len(got) == IG.SEEN_MAX, str(len(got)))
    check("7.4 留的是新的", got[-1] == f"h{IG.SEEN_MAX + 49}")
    (tmp / "kb_ingest.json").write_text("{坏 json", encoding="utf-8")
    check("7.5 坏文件当成空表", IG.load_seen(tmp, "atri") == [])
    IG.save_seen(tmp, "atri", ["z"])
    check("7.6 坏文件能自愈", IG.load_seen(tmp, "atri") == ["z"])

    section("8. server 接线")
    import server as SV
    try:
        SV._kb_ingest("一段文字。" * 20, filename="x.txt", character="没这个角色",
                      dry_run=True)
        ok, err = False, "没报错"
    except ValueError as e:
        ok, err = True, str(e)
    check("8.1 未知角色必须报错（否则写进默认库）", ok, err)
    check("8.2 报错里带角色名", "没这个角色" in str(err), str(err))
    try:
        SV._kb_ingest("一段文字。" * 20, filename="x.txt", character="zzz",
                      dry_run=True)
        ok2, err2 = False, "没报错"
    except ValueError as e:
        ok2, err2 = True, str(e)
    check("8.3 合法但不存在的角色也要报错", ok2 and "zzz" in err2, err2)
    out0 = SV._kb_ingest("正文。" * 60, filename="x.txt", character="klee",
                         overlap=0, dry_run=True)
    check("8.4 overlap=0 不会被当成默认值", out0["params"]["overlap"] == 0,
          str(out0["params"]))
    check("8.5 默认值仍然生效",
          SV._kb_ingest("正文。" * 60, filename="x.txt", character="klee",
                        dry_run=True)["params"]["overlap"] == 80)

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
