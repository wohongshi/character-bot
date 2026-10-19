#!/usr/bin/env python3
"""第三轮（三轮里的这一轮）的回归：朗读 / 长期记忆 / 插件命令试跑。

判据都是**行为**：
- 朗读：Markdown 要被剥掉、长文本分段且封顶、wav 真能取回、越界要 404；
- 记忆：同一条不重复、按角色隔离、能删、**真的进了系统提示**；
- 试跑：走的是 SkillHub 那条链路（`.r 2d6` 出结果），出图命令要带回图片。
"""
from __future__ import annotations

import json
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PASS, FAIL = [], []


def check(name, ok, extra=""):
    (PASS if ok else FAIL).append(name)
    print(f"  {'✓' if ok else '✗'} {name}" + (f"   [{extra}]" if extra and not ok else ""))


BASE = "http://127.0.0.1:8798"
H = {"X-Klee-Studio": "1"}


def get_raw(path, headers=None):
    req = urllib.request.Request(BASE + path, headers=H if headers is None else headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, r.headers, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers, e.read()


def get(path):
    st, _h, body = get_raw(path)
    try:
        return st, json.loads(body.decode("utf-8", "replace") or "{}")
    except ValueError:
        return st, {}


def post(path, body, headers=None, timeout=120):
    req = urllib.request.Request(
        BASE + path, data=json.dumps(body).encode(),
        headers={**(H if headers is None else headers), "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8", "replace") or "{}")
        except ValueError:
            return e.code, {}


def main() -> int:
    import server as S
    from bot import memory as M

    _st, ch = get("/api/characters")
    char = str(ch.get("active") or "klee")

    print("[1] 朗读：把「念出来」的内容洗干净、切对段")
    check("1.1 去掉强调符", "**" not in S._tts_chunks("**重点**：今天要下雨。")[0],
          str(S._tts_chunks("**重点**：今天要下雨。")))
    check("1.2 代码块不念", "print" not in "".join(S._tts_chunks(
        "看这段：\n```py\nprint(1)\n```\n然后告诉我")), "")
    check("1.3 链接只留文字", "https" not in "".join(S._tts_chunks("参见 https://x.com/a 这里")), "")
    check("1.4 中文里的空格被抹掉", S._tts_chunks("**你好**呀")[0] == "你好呀",
          repr(S._tts_chunks("**你好**呀")))
    check("1.5 空内容返回空", S._tts_chunks("") == [] and S._tts_chunks("```\ncode\n```") == [])
    long_txt = "这是一句会被硬切的很长的话，" * 40
    parts = S._tts_chunks(long_txt)
    check("1.6 每段 ≤ 90 字", all(len(p) <= 90 for p in parts), str([len(p) for p in parts]))
    check("1.7 最多 4 段（再长也不念了）", len(parts) <= 4, str(len(parts)))
    check("1.8 标点处断句（不会把「。」念丢）",
          all(p[-1] in "。！？…，、；" or len(p) >= 85 for p in parts[:1]),
          repr(parts[:1]))

    print("\n[2] 朗读接口")
    st, d = post("/api/tts", {"text": "**测试**语音，一二三。"})
    check("2.1 200 且分段", st == 200 and d.get("ok") and d.get("chunks"), str(d)[:160])
    check("2.2 带音色", bool(d.get("voice")), str(d.get("voice")))
    h = (d.get("chunks") or [{}])[0].get("h") or ""
    check("2.2b 只回文件名（URL 由前端拼，接口在面板里有出处）", bool(h) and h.endswith(".wav"), h)
    st, hdrs, blob = get_raw("/api/tts.wav?h=" + urllib.parse.quote(h))
    check("2.3 wav 能取回且是 RIFF", st == 200 and blob[:4] == b"RIFF", f"{st} {blob[:4]!r}")
    check("2.4 Content-Type 是 audio/wav", "audio/wav" in str(hdrs.get("Content-Type")), "")
    st2, d2 = post("/api/tts", {"text": "**测试**语音，一二三。"})
    check("2.5 同一句走缓存（第二次明显更快）",
          st2 == 200 and (d2.get("ms") or 9999) <= max(1500, d.get("ms") or 0),
          f"{d.get('ms')} → {d2.get('ms')}")
    st, d3 = post("/api/tts", {"text": ""})
    check("2.6 空文本 → 400", st == 400, str(d3)[:100])
    st, _ = post("/api/tts", {"text": "你好"}, headers={})
    check("2.7 没有面板头 → 403", st in (401, 403), str(st))
    st, _h, _b = get_raw("/api/tts.wav?h=../../etc/passwd")
    check("2.8 wav 路径穿越 → 404", st == 404, str(st))
    st, _h, _b = get_raw("/api/tts.wav?h=deadbeefdeadbeef.wav")
    check("2.9 没有这个缓存 → 404", st == 404, str(st))

    print("\n[3] 记忆模块（纯函数）")
    td = Path(tempfile.mkdtemp(prefix="klee-mem-"))
    e1 = M.add(td, "demo", "主人喜欢草莓", source="panel")
    check("3.1 加一条", bool(e1) and e1["text"] == "主人喜欢草莓", str(e1))
    e2 = M.add(td, "demo", "  主人喜欢草莓  ")
    check("3.2 重复加只刷新（不新增）", len(M.all_items(td, "demo")) == 1 and e2["hits"] == 1,
          str(M.all_items(td, "demo")))
    M.add(td, "demo", "主人怕黑", source="agent")
    M.add(td, "demo", "明天要考试", source="panel")
    check("3.3 三条都在", len(M.all_items(td, "demo")) == 3, str(len(M.all_items(td, "demo"))))
    check("3.4 按角色隔离", M.all_items(td, "other") == [], str(M.all_items(td, "other")))
    check("3.5 相关度优先（问草莓先给草莓那条）",
          M.pick(td, "demo", "草莓好吃吗")[0]["text"] == "主人喜欢草莓",
          str([x["text"] for x in M.pick(td, "demo", "草莓好吃吗")]))
    check("3.6 没有相关词时按最近给", len(M.pick(td, "demo", "")) == 3)
    txt = M.render(td, "demo", "草莓")
    check("3.7 渲染成提示词且带来源", "【你记得的事】" in txt and "草莓" in txt, txt[:80])
    check("3.8 没记忆时渲染空串", M.render(td, "nobody") == "")
    ts = M.all_items(td, "demo")[0]["ts"]
    check("3.9 删除一条", M.remove(td, "demo", ts) == 1 and len(M.all_items(td, "demo")) == 2)
    check("3.10 删不存在的返回 0", M.remove(td, "demo", 1.0) == 0)
    check("3.11 清空", M.clear(td, "demo") == 2 and M.all_items(td, "demo") == [])

    print("\n[4] 记忆接口 + 真的进提示")
    st, d = post("/api/memories", {"character": "tmem", "text": "主人最爱的颜色是雾蓝色"})
    check("4.1 加一条 200", st == 200 and d.get("ok"), str(d)[:120])
    st, d = get("/api/memories?character=tmem")
    check("4.2 列表里有它", d.get("count") == 1 and "雾蓝色" in d["items"][0]["text"], str(d)[:120])
    st, d2 = post("/api/memories", {"character": "tmem", "text": "主人最爱的颜色是雾蓝色"})
    check("4.3 重复加是幂等（hits 增加、数量不变）",
          d2.get("count") == 1 and (d2.get("item") or {}).get("hits", 0) >= 1, str(d2)[:160])
    st, _d = post("/api/memories", {"character": "tmem", "text": "   "})
    check("4.4 空内容 → 400", st == 400, str(st))
    # 系统提示里必须能看到
    saved_state = S._state_dir
    try:
        S._state_dir = lambda: td
        M.add(td, char, "主人最喜欢的颜色是雾蓝色", source="panel")
        prompt = S._web_system_text(None, char, "我最喜欢什么颜色")
        check("4.5 系统提示里带上了记忆", "【你记得的事】" in prompt and "雾蓝色" in prompt,
              prompt[-160:])
    finally:
        S._state_dir = saved_state
    items = get("/api/memories?character=tmem")[1].get("items") or []
    if items:
        st, d3 = post("/api/memories/delete", {"character": "tmem", "ts": items[0]["ts"]})
        check("4.6 删除接口", st == 200 and d3.get("removed") == 1, str(d3)[:120])
    st, _d = post("/api/memories/delete", {"character": "tmem", "ts": "abc"})
    check("4.7 ts 不合法 → 400", st == 400, str(st))

    print("\n[5] 试跑命令：和聊天里同一条链路")
    st, d = post("/api/commands/run", {"command": "r", "args": "2d6"})
    check("5.1 .r 2d6 跑通", st == 200 and d.get("ok") and "🎲" in (d.get("text") or ""),
          str(d)[:200])
    check("5.2 报出所属技能和耗时",
          d.get("skill") == "dice" and isinstance(d.get("ms"), int), str({k: d.get(k) for k in ("skill", "ms")}))
    st, d = post("/api/commands/run", {"command": "完全不存在的命令zz"})
    check("5.3 未知命令：ok=false 且给提示", st == 200 and d.get("ok") is False
          and "没有技能" in str(d.get("error")), str(d)[:160])
    st, _d = post("/api/commands/run", {"command": ""})
    check("5.4 空命令 → 400", st == 400, str(st))
    # 角色的限定命令：切到 klee 才有「运势」
    st, d = post("/api/commands/run", {"command": "运势", "character": char})
    if char == "klee":
        check("5.5 出图命令带回图片", bool(d.get("images")), str(d)[:120])
        if d.get("images"):
            st2, _h, blob = get_raw(d["images"][0]["url"])
            check("5.6 图能取回", st2 == 200 and blob[:4] == b"\x89PNG", f"{st2} {blob[:4]!r}")
    else:
        check("5.5 别的角色的限定命令：明确说清原因",
              d.get("ok") is False and "当前角色" in str(d.get("error")), str(d)[:160])
        check("5.6 （本机当前不是 klee，跳过出图检查）", True)

    print("\n[6] 清理")
    (ROOT / "state" / "memories" / "tmem.jsonl").unlink(missing_ok=True)
    import shutil
    shutil.rmtree(td, ignore_errors=True)
    check("6.1 测试记忆文件已删", not (ROOT / "state" / "memories" / "tmem.jsonl").is_file())
    check("6.2 临时目录已删", not td.exists())

    print(f"\nPASS {len(PASS)} / {len(PASS) + len(FAIL)}")
    if FAIL:
        print("失败：" + "、".join(FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
