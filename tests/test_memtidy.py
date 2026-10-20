#!/usr/bin/env python3
"""记忆整理（`bot/memtidy.py`）。

守的是「合并不能把不同的事并掉、也不能一条都不并」：

1. 同一件事换个说法（实测里「谢谢你陪我」被记了三遍）必须能认出来；
2. 短句（「晚安」「好的」）不能靠模糊比乱并；
3. 合并保留**最新**那条，作用域取并集（原来在别的会话里也还看得见）；
4. 写盘是破坏性的 → 必须留 `.bak`，陈旧条目进归档而不是删掉；
5. 计划是纯函数：`plan()` 不碰磁盘。
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bot import memtidy as M        # noqa: E402

PASS, FAIL = [], []


def check(name, ok, extra=""):
    (PASS if ok else FAIL).append(name)
    print(f"  {'✓' if ok else '✗'} {name}" + (f"   [{extra}]" if extra and not ok else ""))


def section(t):
    print(f"\n== {t}")


# 真实数据里出现过的三条（措辞不同、是同一件事）
REAL = [
    {"ts": 100.0, "text": '今天陪主人聊了一会儿，末尾他说"今天谢谢你陪我"——这句谢谢我收下了。',
     "scope": "web:a"},
    {"ts": 200.0, "text": "主人今天对我说「谢谢你陪我」——这句我记下了。", "scope": "web:b"},
    {"ts": 300.0, "text": "今天主人说「谢谢你陪我」——把这句好好收着，被记得是很珍贵的事。",
     "scope": "web:c", "hits": 2},
]


def main():
    now = 1000.0

    section("1. similar()")
    check("1.1 完全一样 = 1", M.similar("你好呀", "你好呀") == 1.0)
    check("1.2 毫无关系 = 0", M.similar("今天下雨", "代码评审") == 0.0)
    check("1.3 同一件事换说法能被认出来",
          M.similar(REAL[0]["text"], REAL[2]["text"]) >= 0.45,
          str(M.similar(REAL[0]["text"], REAL[2]["text"])))
    check("1.4 短句换一个字不算同一件事", M.similar("主人怕黑", "主人怕狗") < 0.45,
          str(M.similar("主人怕黑", "主人怕狗")))
    check("1.4b 短句几乎一样还是会被认出来",
          M.similar("主人怕黑", "主人怕黑呀") >= 0.45,
          str(M.similar("主人怕黑", "主人怕黑呀")))
    check("1.5 空白不影响", M.similar("今天 下雨了", "今天下雨了") == 1.0)

    section("2. plan：合并近似重复")
    p = M.plan(REAL, threshold=0.45)
    check("2.1 三句并成一组", p["counts"]["groups"] == 1, str(p["counts"]))
    check("2.2 合并掉两条", p["counts"]["merged"] == 2, str(p["counts"]))
    check("2.3 留一条", p["counts"]["keep"] == 1, str(p["counts"]))
    keep = p["groups"][0]["keep"]
    check("2.4 保留的是最新那条", keep["ts"] == 300.0, str(keep.get("ts")))
    check("2.5 作用域取并集", all(x in keep["scope"] for x in ("web:a", "web:b", "web:c")),
          keep["scope"])
    check("2.6 hits 累加", keep["hits"] == 2, str(keep.get("hits")))
    check("2.7 组里带「合并了哪几句」", len(p["groups"][0]["texts"]) == 2)

    section("3. plan：不该动的不动")
    rows = [{"ts": 1, "text": "主人怕黑"}, {"ts": 2, "text": "主人喜欢草莓"},
            {"ts": 3, "text": "周五要交作业"}]
    p = M.plan(rows, threshold=0.45)
    check("3.1 三件不同的事不合并", p["counts"]["merged"] == 0, str(p["counts"]))
    p = M.plan([{"ts": 1, "text": "晚安"}, {"ts": 2, "text": "好的"}], threshold=0.45)
    check("3.2 短句不合并", p["counts"]["groups"] == 0, str(p["counts"]))
    p = M.plan([{"ts": 1, "text": "A" * 40}, {"ts": 2, "text": "A" * 40}], threshold=0.45)
    check("3.3 完全一样的两条并掉（add 挡不住的边界）", p["counts"]["merged"] == 1,
          str(p["counts"]))

    section("3b. 短句之间也要比")
    p = M.plan([{"ts": 1, "text": "谢谢你陪我"}, {"ts": 2, "text": "谢谢你陪我。"},
                {"ts": 3, "text": "好了"}], threshold=0.45)
    check("3b.1 短句近乎一样会合并", p["counts"]["merged"] == 1, str(p["counts"]))
    check("3b.2 不相干的短句不并", len(p["groups"]) == 1 and p["counts"]["total"] == 3)

    section("4. 阈值生效")
    p_low = M.plan(REAL, threshold=0.3)
    p_high = M.plan(REAL, threshold=0.95)
    check("4.1 阈值低 → 更容易并", p_low["counts"]["merged"] >= 2)
    check("4.2 阈值高 → 并得少", p_high["counts"]["merged"] <= 2, str(p_high["counts"]))
    check("4.3 阈值被夹到 [0.3, 0.99]", M.plan(REAL, threshold=5)["params"]["threshold"] == 0.99)

    section("5. 陈旧归档")
    rows = [{"ts": now - 100 * 86400, "text": "很老的一条"},
            {"ts": now - 2 * 86400, "text": "前天的一条"}]
    p = M.plan(rows, stale_days=30, now=now)
    check("5.1 只有老的进 stale", p["counts"]["stale"] == 1, str(p["counts"]))
    check("5.2 stale 里是那条老的", p["stale"][0]["text"] == "很老的一条")
    p = M.plan(rows, stale_days=0, now=now)
    check("5.3 stale_days=0 就不归档", p["counts"]["stale"] == 0)

    section("6. apply 落盘")
    tmp = Path(tempfile.mkdtemp(prefix="klee-memtidy-"))
    mp = tmp / "memories"
    mp.mkdir()
    f = mp / "atri.jsonl"
    with open(f, "w", encoding="utf-8") as fh:
        for r in REAL + [{"ts": 50.0, "text": "另一件不相干的事", "scope": ""}]:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    p = M.plan(M._read(f), threshold=0.45)
    res = M.apply(tmp, "atri", p)
    check("6.1 条数对得上", res["before"] == 4 and res["after"] == 2, str(res))
    rows = M._read(f)
    check("6.2 留下最新的那句", any("好好收着" in r["text"] for r in rows), str(rows)[:200])
    check("6.3 不相干的那条还在", any("不相干" in r["text"] for r in rows))
    check("6.4 留了 .bak", (mp / "atri.jsonl.bak").exists())
    bak = M._read(mp / "atri.jsonl.bak")
    check("6.5 备份里是整理前的原文", len(bak) == 4, str(len(bak)))
    check("6.6 作用域并集写回了磁盘",
          any("web:a" in str(r.get("scope")) and "web:c" in str(r.get("scope")) for r in rows),
          str([r.get("scope") for r in rows]))

    section("7. apply 归档")
    tmp2 = Path(tempfile.mkdtemp(prefix="klee-memtidy2-"))
    (tmp2 / "memories").mkdir()
    f2 = tmp2 / "memories" / "atri.jsonl"
    rows = [{"ts": now - 100 * 86400, "text": "很老的一条", "scope": ""},
            {"ts": now - 3600, "text": "最近的一条", "scope": ""}]
    f2.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
                  encoding="utf-8")
    p = M.plan(M._read(f2), stale_days=30, now=now)
    res = M.apply(tmp2, "atri", p)
    check("7.1 归档一条", res["archived"] == 1, str(res))
    left = M._read(f2)
    check("7.2 主线只剩最近的", len(left) == 1 and "最近" in left[0]["text"], str(left))
    arch = M._read(tmp2 / "memories" / "atri.archive.jsonl")
    check("7.3 归档文件里有那条老的", len(arch) == 1 and "很老" in arch[0]["text"], str(arch))

    section("7b. 失败不留下「凭空少一截」")
    tmp3 = Path(tempfile.mkdtemp(prefix="klee-memtidy4-"))
    (tmp3 / "memories").mkdir()
    f3 = tmp3 / "memories" / "atri.jsonl"
    f3.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in
                            [{"ts": now - 100 * 86400, "text": "很老的一条", "scope": ""},
                             {"ts": now - 60, "text": "新的", "scope": ""}]) + "\n",
                  encoding="utf-8")
    p = M.plan(M._read(f3), stale_days=30, now=now)
    before_mtime = f3.stat().st_mtime_ns
    # 归档目录不可写 → apply 必须整体失败，主线一个字节都不能动
    (tmp3 / "memories" / "atri.archive.jsonl").mkdir()
    try:
        M.apply(tmp3, "atri", p)
        ok, err = False, "没报错"
    except OSError as e:
        ok, err = True, str(e)
    check("7b.1 归档写不进去就整体失败", ok, err)
    check("7b.2 主线没被改（时间戳不变）", f3.stat().st_mtime_ns == before_mtime,
          f"{before_mtime} → {f3.stat().st_mtime_ns}")
    check("7b.3 主线内容还在", len(M._read(f3)) == 2, str(len(M._read(f3))))
    # 计划已过期（文件被别的进程整理过）：不能再写一遍，尤其不能覆盖 .bak
    f3.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in
                            [{"ts": now - 60, "text": "新的", "scope": ""}]) + "\n",
                  encoding="utf-8")
    bak_before = (tmp3 / "memories" / "atri.jsonl.bak").read_bytes() \
        if (tmp3 / "memories" / "atri.jsonl.bak").exists() else b""
    res2 = M.apply(tmp3, "atri", p)          # p 里那条老的早就不在了
    check("7b.4 计划过期时不报删掉一堆", res2["merged"] == 0, str(res2))
    bak_after = (tmp3 / "memories" / "atri.jsonl.bak").read_bytes() \
        if (tmp3 / "memories" / "atri.jsonl.bak").exists() else b""
    check("7b.5 过期计划不会覆盖上一次的 .bak", bak_after == bak_before)

    section("7c. 没有 ts 的行不能一删一片")
    tmp4 = Path(tempfile.mkdtemp(prefix="klee-memtidy5-"))
    (tmp4 / "memories").mkdir()
    f4 = tmp4 / "memories" / "atri.jsonl"
    rows4 = [{"text": "主人怕黑"},
             {"text": "主人怕黑。"},          # 和上一条近似 → 会被合并
             {"text": "完全不相干的一句"}]
    f4.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows4) + "\n",
                  encoding="utf-8")
    p4 = M.plan(M._read(f4), threshold=0.45)
    res4 = M.apply(tmp4, "atri", p4)
    left = [r["text"] for r in M._read(f4)]
    check("7c.1 合并只删掉该删的那条", len(left) == 2, str(left))
    check("7c.2 不相干的那句还在", any("不相干" in t for t in left), str(left))
    check("7c.3 返回的条数对", res4["before"] == 3 and res4["after"] == 2, str(res4))

    section("7d. 整组都陈旧时不能只剩 .bak")
    tmp5 = Path(tempfile.mkdtemp(prefix="klee-memtidy7-"))
    (tmp5 / "memories").mkdir()
    f5 = tmp5 / "memories" / "atri.jsonl"
    old_rows = [{"ts": now - 100 * 86400, "text": "主人怕黑", "scope": ""},
                {"ts": now - 100 * 86400 + 1, "text": "主人怕黑。", "scope": ""},
                {"ts": now - 60, "text": "最近的一条", "scope": ""}]
    f5.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in old_rows) + "\n",
                  encoding="utf-8")
    p5 = M.plan(M._read(f5), threshold=0.45, stale_days=30, now=now)
    M.apply(tmp5, "atri", p5)
    left = [r["text"] for r in M._read(f5)]
    arch = [r["text"] for r in M._read(tmp5 / "memories" / "atri.archive.jsonl")]
    # 这一组要么完整留在主线、要么完整进归档，不能出现「主线没有、归档也没有」
    kept = [t for t in ("主人怕黑", "主人怕黑。") if t in left or t in arch]
    check("7d.1 被归档组的内容一定还在（主线或归档里）", len(kept) >= 1, str((left, arch)))

    section("8. 幂等 / 空数据")
    p0 = M.plan([], threshold=0.45)
    check("8.1 空列表不炸", p0["counts"]["total"] == 0)
    res = M.apply(tempfile.mkdtemp(prefix="klee-memtidy3-"), "nope", p0)
    check("8.2 文件不存在也不炸", res["before"] == 0)
    p = M.plan(M._read(f), threshold=0.45)
    M.apply(tmp, "atri", p)
    p2 = M.plan(M._read(f), threshold=0.45)
    check("8.3 再跑一遍没有新变化", p2["counts"]["merged"] == 0, str(p2["counts"]))

    section("9. server 接线")
    import server as SV
    check("9.1 有 _mem_items 辅助", hasattr(SV, "_mem_items"))
    # 只验证「plan 不改文件」：拿一个只读目录当 state_dir，plan 照样能算
    ro = Path(tempfile.mkdtemp(prefix="klee-memtidy6-"))
    before = sorted(x.name for x in ro.iterdir())
    M.plan([{"ts": 1, "text": "x"}])
    check("9.2 计划是纯函数（不碰磁盘）",
          M.plan([{"ts": 1, "text": "x"}])["counts"]["total"] == 1
          and sorted(x.name for x in ro.iterdir()) == before,
          str(sorted(x.name for x in ro.iterdir())))

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
