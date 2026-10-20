"""新词候选：频次 + 凝聚度 + 左右自由度的筛选，以及忽略名单的增删。"""
from __future__ import annotations

import json
import os
import random
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
TMP = Path(tempfile.mkdtemp(prefix="newwords-"))
os.environ["KLEE_STATE_DIR"] = str(TMP / "ps")

from bot import newwords as N                              # noqa: E402

PASS = FAIL = 0


def check(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}  {detail}")


def corpus(new_words=("绝绝子", "摆烂", "搭子"), n=400, seed=7):
    """日常词随机拼接 + 3 个新词，每个新词周围是随机上下文（这样才有邻接熵）。"""
    common = ["今天", "明天", "晚上", "吃饭", "回家", "上班", "下雨", "天气",
              "真的", "感觉", "可以", "什么", "我们", "一起"]
    rng = random.Random(seed)
    rows = []
    for i in range(n):
        if rng.random() < 0.55:
            w = rng.choice(new_words)
            ctx = "".join(rng.sample(common, 2))
            tail = rng.choice(common) + rng.choice(["啊", "了", "吧", ""])
            text = f"{ctx}{w}{tail}"
        else:
            text = "".join(rng.sample(common, rng.randint(2, 4)))
        rows.append({"role": "user", "user_id": str(i % 6), "text": text})
    return rows


print("[1] 挑得出新词")
rows = corpus()
got = {x["word"] for x in N.scan(rows)}
check("三个新词都在候选里", {"绝绝子", "摆烂", "搭子"} <= got, str(sorted(got))[:160])
check("不是纯频次：碎片「了哈哈」这类不出现", not any(w.endswith("了哈哈") for w in got),
      str(sorted(got))[:160])
check("候选带四项指标", all({"word", "count", "users", "pmi", "left", "right"} <= set(x)
                        for x in N.scan(rows)))

print("[2] 门槛")
rows2 = corpus(new_words=("绝绝子",), n=400)
one = N.scan(rows2, min_count=999)
check("次数门槛能卡死", one == [], str(one[:3]))
# 同一个人、不同上下文地反复说同一个词：次数够、边界也够，只是说话人只有 1 个
ctxs = ["今天", "晚上", "回家", "上班", "下雨", "天气"]
# 前后都要换词：右边固定成「吃饭」时右邻接熵是 0，按设计就该被挡掉
few = [{"role": "user", "user_id": "1",
        "text": f"{ctxs[i % 6]}{ctxs[(i + 1) % 6]}绝绝子{ctxs[(i + 2) % 6]}"}
       for i in range(20)]
check("一个人刷一百遍不算梗（说话人门槛）", N.scan(few) == [], str(N.scan(few))[:80])
check("放宽说话人门槛就出来了", bool(N.scan(few, min_users=1)),
      str(N.scan(few, min_users=1))[:120])

print("[3] 停用词与碎片")
stop_rows = [{"role": "user", "user_id": str(i % 4), "text": "我们今天可以一起吃饭啊"}
             for i in range(30)]
check("日常词被停用表挡掉", N.scan(stop_rows) == [], str(N.scan(stop_rows))[:100])
check("停用表本身对得上（今天/可以/什么 都在）",
      {"今天", "可以", "什么"} <= N.STOP)

print("[4] 忽略名单")
p = N.path_for(TMP)
check("没有文件 → 空名单", N.load_ignored(TMP) == [])
lst = N.ignore_word(TMP, "摆烂")
check("忽略一个词", "摆烂" in lst, str(lst))
check("落盘了", p.is_file() and "摆烂" in p.read_text(encoding="utf-8"))
check("scan 会跳过忽略的词", "摆烂" not in {x["word"] for x in N.scan(rows, ignore=N.load_ignored(TMP))},
      str(sorted({x["word"] for x in N.scan(rows, ignore=N.load_ignored(TMP))}))[:120])
N.ignore_word(TMP, "绝绝子")
check("能记多个", len(N.load_ignored(TMP)) == 2, str(N.load_ignored(TMP)))
N.unignore_word(TMP, "绝绝子")
check("取消忽略", N.load_ignored(TMP) == ["摆烂"], str(N.load_ignored(TMP)))
p.write_text("{坏 json", encoding="utf-8")
check("坏文件 → 空（不抛）", N.load_ignored(TMP) == [])
check("能重新写", N.set_ignored(TMP, ["a", "a", "b"]) == ["a", "b"])
check("整份替换是覆盖不是追加",
      N.set_ignored(TMP, ["c"]) == ["c"] and N.load_ignored(TMP) == ["c"])

print("[5] known 标记")
ks = N.scan(rows, known={"绝绝子"})
check("命中的词标 known", any(x["word"] == "绝绝子" and x["known"] for x in ks),
      str([x for x in ks if x["word"] == "绝绝子"])[:120])
check("没命中的不标", all(not x["known"] for x in ks if x["word"] != "绝绝子"))

print("[6] 边界")
check("空消息 → 空候选", N.scan([]) == [])
check("非 user 行不算", N.scan([{"role": "assistant", "text": "绝绝子绝绝子绝绝子"}]) == [])
check("英数不参与", N.scan([{"role": "user", "user_id": "1", "text": "hello world 12345"}]) == [])
check("形状怪的行走掉不炸",
      N.scan([None, 5, {"role": "user"}, {"role": "user", "text": None}]) == [])
check("top 能限条数", len(N.scan(rows, top=2)) <= 2, str(len(N.scan(rows, top=2))))

print("[7] 面板")
HTML = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
for anchor in ('id="nwCard"', 'id="nwBox"', "function nwLoad", "function nwAdd",
               "function nwIgnore", "/api/newwords", "隐藏已知"):
    check(f"面板有 {anchor}", anchor in HTML)
SRV = (ROOT / "server.py").read_text(encoding="utf-8")
check("GET 与 POST 都在", SRV.count('u.path == "/api/newwords"') == 2,
      str(SRV.count('u.path == "/api/newwords"')))
KBS = (Path("/root/.hermes/workspace/klee-kb") / "server.py").read_text(encoding="utf-8")
check("klee-kb 有 /present（标已知用）", '"/present"' in KBS)

print(f"\nPASS {PASS} / {PASS + FAIL}")
sys.exit(0 if FAIL == 0 else 1)
