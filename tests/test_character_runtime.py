"""角色运行时集成：人格/知识库/插件随角色切换（捕获消息 + 真模型抽检）。"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

STUDIO = Path(__file__).resolve().parents[1]
TMP = Path(tempfile.gettempdir()) / "klee_char_integration"
if TMP.exists():
    shutil.rmtree(TMP)
(TMP / "state").mkdir(parents=True)
(TMP / "ps").mkdir()
os.environ["KLEE_STATE_DIR"] = str(TMP / "ps")
sys.path.insert(0, str(STUDIO))
for line in (STUDIO / ".env").read_text().splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"'))

from bot.characters import CharacterStore              # noqa: E402
from bot.llm import ModelStore                          # noqa: E402
from bot.runtime import BotRuntime                      # noqa: E402

PASS, FAIL = [], []


def check(name, ok, extra=""):
    (PASS if ok else FAIL).append(name)
    print(f"  {'✓' if ok else '✗'} {name}" + (f"  [{extra}]" if extra and not ok else ""))


class FakeOB:
    def __init__(self):
        self.calls = []

    def send_text(self, c, t, reply_to=None, at_uid=None):
        self.calls.append(t)
        return {"message_id": 1}

    def send_image(self, *a, **k):
        return {"message_id": 2}

    def get_group_member_list(self, g):
        return []

    def get_msg(self, m):
        return {}

    def get_status(self):
        return {"good": True}


class CaptureModels(ModelStore):
    def __init__(self, path):
        super().__init__(path)
        self.msgs = []

    def chat(self, messages, **kw):
        self.msgs.append([dict(m) for m in messages])
        return f"我是{self.current}。"


def ev(text, uid="1000000001"):
    return {"post_type": "message", "message_type": "private", "self_id": 1000000002,
            "user_id": uid, "message_id": 1,
            "message": [{"type": "text", "data": {"text": text}}],
            "sender": {"user_id": uid, "nickname": "主人", "role": "owner"}}


async def main():
    from bot.service import DEFAULT_CONFIG
    cfg = dict(DEFAULT_CONFIG)          # 不读生产 state：仓库拿下来就能跑
    cfg.update({"owner_id": "1000000001", "self_id": "1000000002", "split_sentences": False,
                "send_interval_min": 0, "send_interval_max": 0,
                "agent_enabled": False, "kb_enabled": True})
    store = CharacterStore(TMP / "state", seeds_dir=STUDIO / "bot" / "character_seeds")
    ms = CaptureModels(STUDIO / "state" / "models.json")
    rt = BotRuntime(features_dir=STUDIO / "bot" / "features", config=cfg, models=ms,
                    soul_path=store.soul_path("klee"), state_dir=TMP / "state",
                    characters=store)
    rt.ob = FakeOB()
    rt.loop = asyncio.get_running_loop()
    rt.loop_thread = threading.current_thread()

    print("[1] 逐个角色：人格 + 知识库 + 插件")
    expect = {"klee": ("可莉", ("蒙德", "可莉")), "hutao": ("胡桃", ("往生堂", "胡桃")),
              "citlali": ("茜特菈莉", ("烟谜主", "夜神")), "cyrene": ("昔莲", ("记忆", "翁法罗斯")),
              "atri": ("亚托莉", ("机器人", "心"))}
    for cid, (name, kb_words) in expect.items():
        rt.set_character(cid)
        ms.current = name
        ms.msgs.clear()
        rt.ob.calls.clear()
        await rt.handle_event(ev("你是谁？用一句话介绍自己。"))
        sysmsg = ms.msgs[-1][0]["content"] if ms.msgs else ""
        allsys = "\n".join(m["content"] for m in (ms.msgs[-1] if ms.msgs else [])
                           if m.get("role") == "system")
        enabled = [p["name"] for p in rt.mgr.snapshot()["plugins"] if p["enabled"]]
        check(f"1 {cid} 人格切换（{name}）",
              name in sysmsg and f"你是{name}" in sysmsg or f"# {name}" in sysmsg, sysmsg[:80])
        check(f"1 {cid} 知识库注入（{kb_words[0]}）",
              any(w in allsys for w in kb_words) and "相关背景资料" in allsys, allsys[-200:])
        check(f"1 {cid} 回复非空", bool(rt.ob.calls), str(rt.ob.calls)[:80])
        check(f"1 {cid} 角色插件与通用插件并存",
              "lore" in enabled and "help" in enabled, str(enabled)[:160])
        check(f"1 {cid} 没有别家角色插件",
              not any(x in enabled for x in ("gallery", "onlyhutao")) or cid == "klee",
              str(enabled)[:160])

    print("[2] 历史按角色隔离")
    rt.set_character("klee")
    ms.current = "可莉"
    rt.ob.calls.clear()
    await rt.handle_event(ev("记住：我最喜欢苹果。"))
    rt.set_character("hutao")
    ms.current = "胡桃"
    ms.msgs.clear()
    await rt.handle_event(ev("我刚才说我喜歡什么？"))
    texts = "\n".join(m["content"] for m in ms.msgs[-1] if m.get("role") == "user")
    check("2.1 换角色后不带上一个角色的历史", "苹果" not in texts, texts[-200:])
    rt.set_character("klee")
    ms.msgs.clear()
    await rt.handle_event(ev("我刚才说我喜歡什么？"))
    texts2 = "\n".join(m["content"] for m in ms.msgs[-1] if m.get("role") == "user")
    check("2.2 切回来仍记得自己的历史", "苹果" in texts2, texts2[-200:])

    print("[3] 真模型抽检（胡桃 / 亚托莉）")
    if os.environ.get("KLEE_LIVE") != "1":
        print("  （跳过：设 KLEE_LIVE=1 才跑真实模型）")
        print()
        print(f"PASS {len(PASS)} / {len(PASS) + len(FAIL)}")
        if FAIL:
            print("FAILED:", FAIL)
        return
    real = ModelStore(STUDIO / "state" / "models.json")
    for cid, mark in (("hutao", ("本堂主", "往生堂", "生死")), ("atri", ("机器人", "计算", "亚托莉"))):
        rt.set_character(cid)
        ms2 = real
        persona = rt._persona()
        t0 = time.time()
        reply = ms2.chat([{"role": "system", "content": persona},
                          {"role": "user", "content": "用一句话跟我打个招呼。"}], timeout=60)
        check(f"3 {cid} 真模型回复像本人", any(m in reply for m in mark), reply[:120])
        check(f"3 {cid} 延迟 <60s", time.time() - t0 < 60, f"{time.time()-t0:.1f}s")

    print()
    print(f"PASS {len(PASS)} / {len(PASS) + len(FAIL)}")
    if FAIL:
        print("FAILED:", FAIL)


asyncio.run(main())
