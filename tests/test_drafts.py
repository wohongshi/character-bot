"""回复审核（先审后发）：草稿队列 + 运行时真的扣住 + 面板发送链路。"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
TMP = Path(tempfile.mkdtemp(prefix="drafts-"))
os.environ["KLEE_STATE_DIR"] = str(TMP / "ps")

from bot import chatpolicy as CP                       # noqa: E402
from bot import drafts as D                            # noqa: E402

PASS = FAIL = 0


def check(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}  {detail}")


def _err(fn) -> bool:
    """跑一下，看它是不是**抛了**（该拒绝的输入必须抛，不能默默吞）。"""
    try:
        fn()
    except Exception:                                          # noqa: BLE001
        return True
    return False


S = TMP / "state"
S.mkdir()

print("[1] 存储：入队 / 读到 / 改 / 取")
D.add(S, chat="group:1", character="klee", user="在吗", text="在的呀")
D.add(S, chat="group:2", character="atri", user="吃了吗", text="刚吃完～")
items = D.list_items(S)
check("两条都在，且新的在前", len(items) == 2 and items[0]["chat"] == "group:2",
      str([i["chat"] for i in items]))
check("按会话筛", len(D.list_items(S, chat="group:1")) == 1)
check("count 全量 / 按会话", D.count(S) == 2 and D.count(S, chat="group:2") == 1)
one = D.get(S, items[0]["id"])
check("get 拿得到", one is not None and one["text"] == "刚吃完～")
check("改一句还留在队列里", (D.set_text(S, items[0]["id"], "刚吃完，你呢？") or {}).get("text")
      == "刚吃完，你呢？")
check("空回复不许改", _err(lambda: D.set_text(S, items[0]["id"], "   ")))
check("取不到返回 None", D.set_text(S, "nope", "x") is None)

print("[2] 发送前先占位（pop 是原子的）")
it = D.pop(S, items[0]["id"])
check("pop 拿得到那条", it is not None and it["text"] == "刚吃完，你呢？")
check("pop 之后就不在了", D.get(S, items[0]["id"]) is None)
check("再 pop 一次是 None（两面同点只发一次）", D.pop(S, items[0]["id"]) is None)
D.restore(S, it)
check("发失败放回去", D.get(S, items[0]["id"]) is not None)
D.restore(S, it)
check("放回两次不会变两条", D.count(S) == 2, str(D.count(S)))

print("[3] 上限与坏数据")
small = TMP / "small"
small.mkdir()
for i in range(D.MAX_ITEMS + 3):
    D.add(small, chat="group:9", text=f"第 {i} 条", now=1000 + i)
check("超上限从最旧的丢", D.count(small) == D.MAX_ITEMS, str(D.count(small)))
last = D.add(small, chat="group:9", text="最新的", now=9999)
check("add 告诉你丢了几条", last["dropped"] == 1, str(last["dropped"]))
check("丢的是最旧的那条", "第 0 条" not in [i["text"] for i in D.list_items(small, limit=100)])
(small / D.FILE).write_text("{ 坏 json", encoding="utf-8")
check("坏文件当空队列（不抛）", D.list_items(small) == [])
(small / D.FILE).write_text(json.dumps({"items": [
    {"id": "", "chat": "group:1", "text": "没 id"},
    {"id": "a", "chat": "", "text": "没会话"},
    {"id": "b", "chat": "group:1", "text": "  "},
    {"id": "c", "chat": "group:1", "text": "好的"},
    {"id": "c", "chat": "group:1", "text": "重复 id"}]}), encoding="utf-8")
clean = D.list_items(small)
check("坏行被丢掉、重复 id 只留一条", len(clean) == 1 and clean[0]["id"] == "c",
      str([(i["id"], i["text"]) for i in clean]))

ms = TMP / "same_ms"
ms.mkdir()
a = D.add(ms, chat="g", text="一", now=100)
b = D.add(ms, chat="g", text="二", now=100)
c = D.add(ms, chat="g", text="三", now=100)
check("同一毫秒入队 id 不撞", len({a["item"]["id"], b["item"]["id"], c["item"]["id"]}) == 3,
      str([a["item"]["id"], b["item"]["id"], c["item"]["id"]]))

print("[4] 会话策略里的三态 review")
P = TMP / "pol"
P.mkdir()
CP.set_chat(P, "group:5", review=True)
check("显式 true 存下来", CP.load(P)["chats"]["group:5"].get("review") is True)
check("resolve 带 review", CP.resolve(P, "group:5")["review"] is True)
check("why 里说了要审", "审" in CP.resolve(P, "group:5")["why"])
CP.set_chat(P, "group:5", review=False)
check("显式 false 也是设定（不是「没设」）",
      CP.load(P)["chats"]["group:5"].get("review") is False)
CP.set_chat(P, "group:5", quiet_mention=True)
check("只改别的字段不会把 review 冲掉",
      CP.load(P)["chats"]["group:5"].get("review") is False)
CP.set_chat(P, "group:5", review="")
check("空串 = 回到跟全局", "review" not in CP.load(P)["chats"].get("group:5", {}))
CP.set_chat(P, "group:6", review=False)
CP.set_chat(P, "group:6", mode="inherit")
check("只有 review=false 的会话不会被当成默认值丢掉",
      CP.load(P)["chats"].get("group:6", {}).get("review") is False)

print("[5] 运行时：回复进草稿，不发")
from bot.characters import CharacterStore              # noqa: E402
from bot.llm import ModelStore                         # noqa: E402
from bot.runtime import BotRuntime                     # noqa: E402
from bot.service import DEFAULT_CONFIG                 # noqa: E402

ST = TMP / "rt"
ST.mkdir()
store = CharacterStore(ST, seeds_dir=ROOT / "bot" / "character_seeds")


class FakeModels(ModelStore):
    def __init__(self, path):
        super().__init__(path)
        self.calls = 0
        self.reply = "我在的呀～"

    def chat(self, messages, **kw):
        self.calls += 1
        return self.reply


ms2 = FakeModels(ROOT / "state" / "models.json")
cfg = dict(DEFAULT_CONFIG)
cfg.update({"owner_id": "1", "self_id": "2", "agent_enabled": False, "kb_enabled": False,
            "split_sentences": False, "send_interval_min": 0, "send_interval_max": 0,
            "review_on": True})
rt = BotRuntime(features_dir=ROOT / "bot" / "features", config=cfg, models=ms2,
                soul_path=store.soul_path("klee"), state_dir=ST, characters=store)
sent = []


class OB:
    def send_text(self, c, t, reply_to=None, at_uid=None):
        sent.append(t)
        return {"message_id": 10 + len(sent)}

    def send_image(self, *a, **k):
        return {"message_id": 99}

    def get_group_member_list(self, g):
        return []

    def get_msg(self, m):
        return {}

    def get_status(self):
        return {"good": True}


rt.ob = OB()


def ev(text, chat="private:9"):
    uid = chat.split(":")[1]
    return {"post_type": "message",
            "message_type": "group" if chat.startswith("group:") else "private",
            "self_id": "2", "user_id": uid, "message_id": 7,
            "message": [{"type": "text", "data": {"text": text}}],
            "sender": {"user_id": uid, "nickname": "t", "role": "member"}}


async def feed(text, chat="private:9"):
    rt.loop = asyncio.get_running_loop()
    rt.loop_thread = threading.current_thread()
    await rt.handle_event(ev(text, chat))


asyncio.run(feed("你好呀"))
check("审核模式下一条都没发", sent == [], str(sent))
check("模型仍然被调了一次（回复是生成好的）", ms2.calls == 1, str(ms2.calls))
queued = D.list_items(ST)
check("草稿落在队列里", len(queued) == 1 and queued[0]["chat"] == "private:9", str(queued))
check("草稿记下了用户原话", queued[0]["user"] == "你好呀", queued[0]["user"])
check("草稿记了角色", queued[0]["character"] == rt.active_character, queued[0]["character"])
check("草稿记住了触发它的那条消息（审批晚了也不会 @ 错人）",
      queued[0].get("reply_to") == "7", str(queued[0].get("reply_to")))
check("统计里有 drafted", int(rt.stats.get("drafted") or 0) == 1, str(rt.stats.get("drafted")))
check("没有写进聊天历史", not (rt.hist.get(f"{rt.active_character}:private:9") or []),
      str(rt.hist)[:80])

print("[6] 面板点「发送」")
res = asyncio.run(rt.send_draft(queued[0]["id"]))
check("发出去了一条", sent == ["我在的呀～"], str(sent))
check("草稿删了", D.count(ST) == 0)
check("返回里带发出的正文", res.get("text") == "我在的呀～", str(res))
hist = rt.hist.get(f"{rt.active_character}:private:9") or []
check("历史补上了这一问一答",
      [h["content"] for h in hist] == ["你好呀", "我在的呀～"], str(hist)[:120])
check("再点一次报「不在了」", _err(lambda: asyncio.run(rt.send_draft(queued[0]["id"]))))

asyncio.run(feed("第二句"))
q2 = D.list_items(ST)[0]
D.set_text(ST, q2["id"], "改过的回复")
asyncio.run(rt.send_draft(q2["id"]))
check("发的是改过的那句", sent[-1] == "改过的回复", str(sent))

asyncio.run(feed("第三句"))
q3 = D.list_items(ST)[0]
D.drop(ST, q3["id"])
check("丢弃后队列空", D.count(ST) == 0)
check("丢弃不会发出去", len(sent) == 2, str(sent))

print("[7] 会话级覆盖")
CP.set_chat(ST, "private:9", review=False)
before = len(sent)
asyncio.run(feed("这次直接回我"))
check("会话设成不用审就直接发", len(sent) == before + 1, str(sent[-1:]))
CP.set_chat(ST, "private:9", review="")
check("清掉覆盖后回到全局（要审）", rt.review_for("private:9") is True)
asyncio.run(feed("又要审了"))
check("回到审核", len(sent) == before + 1 and D.count(ST) == 1, str(D.count(ST)))

print("[8] 面板视图")
v = rt.drafts_view()
check("视图 ok 且带总数", v["ok"] is True and v["total"] == 1, str(v)[:120])
check("视图里带全局开关状态", v["review_on"] is True)
check("视图带「最早一条」和会话数", "oldest" in v and v.get("chats") == 1, str(v)[:120])

print("[9] 面板 / 路由 / 配置真的在")
HTML = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
SRV = (ROOT / "server.py").read_text(encoding="utf-8")
CFG = (ROOT / "bot" / "service.py").read_text(encoding="utf-8")
for anchor in ('id="drCard"', 'id="drBox"', "function drLoad", "function drSend",
               "function drDrop", "function drAll", "/api/drafts"):
    check(f"面板有 {anchor}", anchor in HTML)
check("会话策略行有审核下拉", 'data-k="rv"' in HTML)
check("保存会话策略时带 review", "body.review" in HTML)
check("服务端有两个方向的路由", SRV.count('u.path == "/api/drafts"') == 2,
      str(SRV.count('u.path == "/api/drafts"')))
check("敏感 GET 白名单里有", '"/api/drafts",' in SRV)
check("默认配置有 review_on", '"review_on"' in CFG)
check("默认关（不打扰现有行为）", '"review_on": False' in CFG)
check("「删」会连审核三态一起清", 'review: ""' in HTML)
check("确认框用真实总数（列表只取 60 条）", "drTotal" in HTML and "${drTotal} 条" in HTML)
check("最早一条按分钟/小时/天显示", "小时前" in HTML and "天前" in HTML)

# 引用 id 不能被截短（QQ 官方 / 飞书的 id 都超过 32 字符）
long_id = "ROBOT1.0_" + "x" * 80
D.add(S, chat="group:8", text="长 id", reply_to=long_id)
check("长 message_id 完整保留", D.list_items(S, chat="group:8")[0]["reply_to"] == long_id[:D.MAX_REPLY_TO],
      str(len(D.list_items(S, chat="group:8")[0]["reply_to"])))
check("上限够长（≥64）", D.MAX_REPLY_TO >= 64, str(D.MAX_REPLY_TO))

print(f"\nPASS {PASS} / {PASS + FAIL}")
sys.exit(0 if FAIL == 0 else 1)
