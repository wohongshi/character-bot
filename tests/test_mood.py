#!/usr/bin/env python3
"""角色心情（第一轮）：她有「今天状态」，而且这个状态能解释。

判据是**数值行为**，不是「有这个文件」：
- 好话往上、难听往下、敷衍小跌；
- 熬夜掉精神、白天回一点；
- 时间会把三个维度拉回基线（半衰期）；
- 提示只在明显偏离时出现（正常状态不该给模型加噪声）；
- 面板/QQ 两侧都要记：一轮对话之后数字真的变了。
"""
from __future__ import annotations

import json
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PASS, FAIL = [], []


def check(name, ok, extra=""):
    (PASS if ok else FAIL).append(name)
    print(f"  {'✓' if ok else '✗'} {name}" + (f"   [{extra}]" if extra and not ok else ""))


def section(t):
    print(f"\n== {t}")


DAY = 1792425600.0          # 2026-10-20 00:00 附近，测夜间逻辑时再用


def main():
    from bot import mood

    section("1. 单轮影响")
    pos = mood.analyse("谢谢你，好喜欢你！", "嗯！")
    neg = mood.analyse("讨厌你，闭嘴", "……")
    cold = mood.analyse("嗯", "哦")
    check("1.1 好话抬心情", pos["mood"] > 0, str(pos))
    check("1.2 难听压心情", neg["mood"] < 0, str(neg))
    check("1.3 敷衍小幅下沉", cold["mood"] < 0 and cold["mood"] > neg["mood"], str(cold))
    check("1.4 每轮都掉一点精神", all(d["energy"] < 0 for d in (pos, neg, cold)))
    check("1.5 单轮有上限（不会一句话顶满）", all(abs(v) <= mood.STEP_CAP for v in pos.values()),
          str(pos))
    check("1.6 分享自己涨亲近",
          mood.analyse("我今天遇到一件很好笑的事")["closeness"] >
          mood.analyse("今天天气")["closeness"])

    night = 1792432800.0        # 02:00
    day = 1792456800.0          # 14:00
    check("1.7 半夜掉精神更快",
          mood.analyse("在吗", "", night)["energy"] < mood.analyse("在吗", "", day)["energy"])

    section("2. 累积")
    tmp = Path(tempfile.mkdtemp())
    now = DAY
    for t in ["谢谢你！好喜欢你"] * 10:
        st = mood.apply_turn(tmp, "klee", t, "嗯！", now)
        now += 45
    check("2.1 十句好话能明显抬起来", st["mood"] > 80, f"{st['mood']:.1f}")
    check("2.2 亲近跟着涨", st["closeness"] > 45, f"{st['closeness']:.1f}")
    check("2.3 轮次记得住", st["n"] == 10, str(st["n"]))

    tmp2 = Path(tempfile.mkdtemp())
    now = DAY
    for t in ["讨厌你"] * 10:
        st2 = mood.apply_turn(tmp2, "klee", t, "嗯。", now)
        now += 45
    check("2.4 十句难听能压下去", st2["mood"] < 40, f"{st2['mood']:.1f}")
    check("2.5 不会跌破 0", st2["mood"] >= 0)

    section("3. 时间会拉回基线")
    r = mood.get(tmp, "klee", now)
    later = mood.get(tmp, "klee", now + 6 * 3600)
    check("3.1 六小时后往基线回一半", later["mood"] < r["mood"] and later["mood"] > mood.BASE["mood"],
          f"{r['mood']:.1f} → {later['mood']:.1f}")
    far = mood.get(tmp, "klee", now + 4 * 86400)
    check("3.2 四天后基本回到基线",
          abs(far["mood"] - mood.BASE["mood"]) < 0.5, f"{far['mood']:.1f}")
    check("3.3 衰减不改轮次", far["n"] == 10, str(far["n"]))

    section("4. 提示只在偏离时出现")
    tmp3 = Path(tempfile.mkdtemp())
    check("4.1 初始不给提示", mood.hint(tmp3, "klee", DAY) == "")
    now = DAY
    for t in ["讨厌你，滚"] * 8:
        st3 = mood.apply_turn(tmp3, "klee", t, "嗯。", now)
        now += 30
    h = mood.hint(tmp3, "klee", now)
    check("4.2 低落后给提示", "心情不太好" in h, h)
    check("4.3 提示不是设定台词（明确说了别念）", "不是让你念" in h, h)

    tmp4 = Path(tempfile.mkdtemp())
    now = DAY
    for t in ["好喜欢你，谢谢你"] * 10:
        mood.apply_turn(tmp4, "klee", t, "嘿嘿！", now)
        now += 30
    check("4.4 心情好也给提示", "心情很好" in mood.hint(tmp4, "klee", now))

    section("5. 文案与摘要")
    check("5.1 高/低两端的词不一样",
          mood.label(90)[1] != mood.label(10)[1], f"{mood.label(90)} {mood.label(10)}")
    check("5.2 精力也有词", mood.energy_label(90)[1] != mood.energy_label(5)[1])
    check("5.3 亲近也有词", mood.closeness_label(90)[1] != mood.closeness_label(5)[1])
    s = mood.summary(tmp3, "klee", now)
    check("5.4 摘要字段齐", all(k in s for k in ("mood", "energy", "closeness", "emoji",
                                                "mood_text", "turns")), str(s)[:200])

    section("6. 落盘与隔离")
    raw = json.loads((tmp / "mood.json").read_text(encoding="utf-8"))
    check("6.1 按角色存", "klee" in raw and "atri" not in raw)
    mood.apply_turn(tmp, "atri", "你好", "你好呀", now)
    check("6.2 角色互不影响",
          mood.get(tmp, "klee", now)["mood"] != mood.get(tmp, "atri", now)["mood"])
    mood.reset(tmp, "klee")
    check("6.3 重置回基线", abs(mood.get(tmp, "klee", now)["mood"] - mood.BASE["mood"]) < 0.01)

    section("7. 接线（面板 / QQ / 面板 UI）")
    srv = (ROOT / "server.py").read_text(encoding="utf-8")
    rt = (ROOT / "bot" / "runtime.py").read_text(encoding="utf-8")
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    check("7.1 面板注入提示", "mood_mod.hint(_state_dir(), cid)" in srv)
    check("7.2 面板记一轮", "mood_mod.apply_turn(_state_dir(), cid" in srv)
    check("7.3 QQ 侧注入提示", "mood_mod.hint(self.state_dir" in rt)
    check("7.4 QQ 侧记一轮", "mood_mod.apply_turn(self.state_dir" in rt)
    check("7.5 有 /api/mood", 'u.path == "/api/mood"' in srv)
    check("7.6 有重置接口", '"/api/mood/reset"' in srv)
    check("7.7 面板有心情 chip", 'id="moodChip"' in html and "paintMood()" in html)
    check("7.8 详情里有三条进度", "moodBar(" in html and "一起聊过" in html)
    check("7.9 .心情 命令存在",
          (ROOT / "bot" / "features" / "mood.py").exists()
          and "on_command(\"心情\"" in (ROOT / "bot" / "features" / "mood.py").read_text(encoding="utf-8"))

    section("8. 活服务")
    base = "http://127.0.0.1:8798"
    hdr = {"X-Klee-Studio": "1", "Content-Type": "application/json"}

    def post(path, body):
        req = urllib.request.Request(base + path, data=json.dumps(body).encode(), headers=hdr)
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def get(path):
        req = urllib.request.Request(base + path, headers=hdr)
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read())

    try:
        d = get("/api/mood?character=cyrene")
        check("8.1 接口可用", d.get("ok") is True)
        check("8.2 三个维度都在 0–100",
              all(0 <= d[k] <= 100 for k in ("mood", "energy", "closeness")), str(d))
        code, d = post("/api/mood/reset", {"character": "cyrene"})
        check("8.3 重置接口", code == 200 and d.get("ok") and abs(d["mood"] - 62) < 0.01, str(d)[:160])

        # 真聊一轮：心情轮次要涨
        before = get("/api/mood?character=cyrene")["turns"]
        sess = f"t-mood-{int(time.time())}"
        post("/api/chat", {"session": sess, "character": "cyrene", "text": "今天谢谢你陪我"})
        after = before
        for _ in range(60):
            time.sleep(1)
            cur = get(f"/api/chat?session={sess}&character=cyrene")
            if not cur["running"] and len(cur["messages"]) >= 2:
                after = get("/api/mood?character=cyrene")["turns"]
                break
        check("8.4 聊一轮会记进心情", after > before, f"{before} → {after}")
        post("/api/chat/delete", {"session": sess, "character": "cyrene"})

        code, d = post("/api/commands/run", {"command": "心情", "args": "", "character": "cyrene"})
        check("8.5 .心情 能跑", code == 200 and d.get("ok") and "心情" in str(d.get("text")), str(d)[:160])
        post("/api/mood/reset", {"character": "cyrene"})
    except (urllib.error.URLError, OSError) as e:
        print(f"  · 服务没起来，跳过（{type(e).__name__}）")

    print(f"\nPASS {len(PASS)} / {len(PASS) + len(FAIL)}")
    if FAIL:
        print("失败：" + "、".join(FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
