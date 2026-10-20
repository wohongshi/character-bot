"""每会话回复节流：纯函数 + 滑动窗口 + 运行时真的拦得住。"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
TMP = Path(tempfile.mkdtemp(prefix="throttle-"))
os.environ["KLEE_STATE_DIR"] = str(TMP / "ps")

from bot import throttle as T                              # noqa: E402

PASS = FAIL = 0


def check(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}  {detail}")


print("[1] 配置收敛")
c = T.clamp_cfg(None)
check("默认全关", c["on"] is False and c["per_min"] == 6 and c["per_hour"] == 60,
      str(c))
check("坏类型回落默认", T.clamp_cfg({"per_min": "很多"})["per_min"] == 6)
check("负数/超上限被夹住", T.clamp_cfg({"per_min": -3})["per_min"] == 0
      and T.clamp_cfg({"per_min": 9999})["per_min"] == 120,
      str(T.clamp_cfg({"per_min": 9999})))
check("未知处置回落 drop", T.clamp_cfg({"action": "queue"})["action"] == "drop")
check("非法动作表只有两个", set(T.ACTIONS) == {"drop", "notice"})

print("[2] decide")
on = {"on": True, "per_min": 3, "per_hour": 10, "action": "drop"}
check("没到上限放行", T.decide(on, used_min=2, used_hour=9)["allow"] is True)
d = T.decide(on, used_min=3, used_hour=3)
check("到分钟上限拦下", d["allow"] is False and d["level"] == "min", str(d))
check("拦下的话里带数字", "3" in d["reason"] and "上限" in d["reason"], d["reason"])
d2 = T.decide(on, used_min=0, used_hour=10)
check("到小时上限也拦", d2["allow"] is False and d2["level"] == "hour", str(d2))
check("关掉后永远放行", T.decide({**on, "on": False}, used_min=99, used_hour=99)["allow"])
check("上限 0 = 不限制",
      T.decide({**on, "per_min": 0}, used_min=999, used_hour=0)["allow"] is True)

print("[3] 滑动窗口")
w = T.Window()
base = 1_700_000_000.0
check("空窗口是 0/0", w.counts("g1", now=base) == (0, 0))
for i in range(5):
    w.hit("g1", now=base + i)
check("5 条都在", w.counts("g1", now=base + 4) == (5, 5), str(w.counts("g1", now=base + 4)))
check("会话之间独立", w.counts("g2", now=base + 4) == (0, 0))
# 打点在 base+0..4；「这一分钟」= [now-60, now)：base+61 时 base+0 掉出，剩 4 条
check("过了 60 秒，最早那条掉出分钟窗口，小时数还在",
      w.counts("g1", now=base + 61) == (4, 5), str(w.counts("g1", now=base + 61)))
# 窗口是 [now-3600, now)：base+3601 时 base+1..4 还在（4 条），base+0 已过期
check("过了 1 小时，最早的那条掉出窗口",
      w.counts("g1", now=base + 3601) == (0, 4), str(w.counts("g1", now=base + 3601)))
check("再久一点全归零", w.counts("g1", now=base + 3606) == (0, 0))
w.hit("g1", now=base + 3700)
check("再打一条只算新窗口", w.counts("g1", now=base + 3700) == (1, 1))
w.reset("g1")
check("reset 清掉", w.counts("g1", now=base + 3700) == (0, 0))

# 长跑防膨胀
w2 = T.Window()
for i in range(T.MAX_CHATS + 50):
    w2.hit(f"c{i}", now=base + i * 0.01)
w2.hit("new", now=base + 10000)
check("会话数有上限", len(w2._hits) <= T.MAX_CHATS + 60, str(len(w2._hits)))

print("[4] 运行时真的拦得住")
from bot.llm import ModelStore                             # noqa: E402
from bot.runtime import BotRuntime                         # noqa: E402
from bot.characters import CharacterStore                  # noqa: E402
from bot.service import DEFAULT_CONFIG                     # noqa: E402

ST = TMP / "state"
ST.mkdir()
store = CharacterStore(ST, seeds_dir=ROOT / "bot" / "character_seeds")


class FakeModels(ModelStore):
    def __init__(self, path):
        super().__init__(path)
        self.calls = 0

    def chat(self, messages, **kw):
        self.calls += 1
        return "嗯。"


ms = FakeModels(ROOT / "state" / "models.json")
cfg = dict(DEFAULT_CONFIG)
cfg.update({"owner_id": "1", "self_id": "2", "agent_enabled": False, "kb_enabled": False,
            "split_sentences": False, "send_interval_min": 0, "send_interval_max": 0,
            "throttle_on": True, "throttle_per_min": 2, "throttle_per_hour": 100,
            "throttle_action": "drop"})
rt = BotRuntime(features_dir=ROOT / "bot" / "features", config=cfg, models=ms,
                soul_path=store.soul_path("klee"), state_dir=ST, characters=store)
sent = []


class OB:
    def send_text(self, c, t, reply_to=None, at_uid=None):
        sent.append(t)
        return {"message_id": 1}

    def send_image(self, *a, **k):
        return {"message_id": 2}

    def get_group_member_list(self, g):
        return []

    def get_msg(self, m):
        return {}

    def get_status(self):
        return {"good": True}


rt.ob = OB()


def ev(text, chat="private:9"):
    uid = chat.split(":")[1]
    return {"post_type": "message", "message_type": "private", "self_id": "2",
            "user_id": uid, "message_id": 1,
            "message": [{"type": "text", "data": {"text": text}}],
            "sender": {"user_id": uid, "nickname": "t", "role": "member"}}


async def run(n, chat="private:9"):
    rt.loop = asyncio.get_running_loop()
    rt.loop_thread = __import__("threading").current_thread()
    for _ in range(n):
        await rt.handle_event(ev("在吗", chat))


asyncio.run(run(5))
check("只放行 2 条（模型只被调 2 次）", ms.calls == 2, str(ms.calls))
check("统计里记了拦下的条数", int(rt.stats.get("throttled") or 0) == 3,
      str(rt.stats.get("throttled")))
check("别的会话不受影响", (asyncio.run(run(1, "private:77")), ms.calls)[1] == 3, str(ms.calls))

# 关掉节流立刻放行
rt.config["throttle_on"] = False
asyncio.run(run(3))
check("关掉后不再拦", ms.calls == 6, str(ms.calls))

# 面板视图
rt.config["throttle_on"] = True
v = rt.throttle_view()
check("视图 ok", v["ok"] is True and v["config"]["on"] is True)
check("视图带当前计数", any(x["chat"] == "private:9" for x in v["chats"]), str(v["chats"])[:120])
check("视图带默认值与动作名", "drop" in v["actions"] and "per_min" in v["defaults"])

print("[5] 提示模式：同一个人 10 分钟最多听一次「慢点」")
rt2 = BotRuntime(features_dir=ROOT / "bot" / "features",
                 config={**cfg, "throttle_action": "notice", "throttle_per_min": 1,
                         "throttle_per_hour": 100},
                 models=ms, soul_path=store.soul_path("klee"), state_dir=ST, characters=store)
sent2 = []


class OB2(OB):
    def send_text(self, c, t, reply_to=None, at_uid=None):
        sent2.append(t)
        return {"message_id": 1}


rt2.ob = OB2()
before = ms.calls


async def run2(n):
    rt2.loop = asyncio.get_running_loop()
    rt2.loop_thread = __import__("threading").current_thread()
    for _ in range(n):
        await rt2.handle_event(ev("在吗", "private:55"))


asyncio.run(run2(6))
check("拦下的不发模型", ms.calls - before <= 1, str(ms.calls - before))
check("只提示一次「慢点」", sum(1 for t in sent2 if "跟不上" in t) == 1, str(sent2)[:160])

print("[6] 面板")
HTML = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
for anchor in ('id="thCard"', 'id="thBox"', "function thLoad", "function thSave",
               'id="thOn"', 'id="thMin"', 'id="thAct"', "/api/throttle"):
    check(f"面板有 {anchor}", anchor in HTML)
SRV = (ROOT / "server.py").read_text(encoding="utf-8")
check("服务端路由在", '"/api/throttle"' in SRV)
check("敏感 GET 名单里有", '"/api/throttle",' in SRV)
CFG = (ROOT / "bot" / "service.py").read_text(encoding="utf-8")
check("默认配置有 4 个键", all(k in CFG for k in
      ("throttle_on", "throttle_per_min", "throttle_per_hour", "throttle_action")))

print(f"\nPASS {PASS} / {PASS + FAIL}")
sys.exit(0 if FAIL == 0 else 1)
