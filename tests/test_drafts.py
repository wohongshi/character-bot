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

print("[2.5] 图片：坏结构不能带崩，说明要留住")
img_dir = TMP / "imgs"
img_dir.mkdir()
a1 = D.add(img_dir, chat="g", text="", images="/root/a.webp")
check("单个字符串当一张图（不是逐字符拆开）",
      [e["path"] for e in a1["item"]["images"]] == ["/root/a.webp"],
      str(a1["item"]["images"]))
a2 = D.add(img_dir, chat="g", text="", images=[{"path": "/root/b.webp", "caption": "看图"}])
check("带说明的图连说明一起存", a2["item"]["images"] == [{"path": "/root/b.webp",
                                                       "caption": "看图"}],
      str(a2["item"]["images"]))
check("奇怪类型当没图（不抛）", D.add(img_dir, chat="g", text="有字", images=5)["item"]["images"] == [])
a3 = D.add(img_dir, chat="g", text="", images=[b"/root/c.webp"])
check("bytes 路径被解码（不是 \"b'/root/c.webp'\"）",
      a3["item"]["images"] == [{"path": "/root/c.webp", "caption": ""}],
      str(a3["item"]["images"]))
weird = img_dir / D.FILE
weird.write_text(json.dumps({"items": [{"id": "d1-\u00b2", "chat": "g", "text": "怪 id"}]}),
                 encoding="utf-8")
check("被手改坏的 id 不会让整个队列读不出来", len(D.list_items(img_dir)) == 1,
      str(len(D.list_items(img_dir))))
bad = img_dir / D.FILE
bad.write_text(json.dumps({"items": [
    {"id": "x1", "chat": "g", "text": "t", "images": "/root/x.webp"},
    {"id": "x2", "chat": "g", "text": "t2", "images": 5},
    {"id": "x3", "chat": "g", "text": "t3", "images": [1, 2]}]}), encoding="utf-8")
loaded = D.list_items(img_dir)
check("坏 images 不影响整份队列读出来", len(loaded) == 3, str(len(loaded)))
check("字符串 images 被规范化成 [{path}]",
      loaded[2]["images"] == [{"path": "/root/x.webp", "caption": ""}],
      str(loaded[2]["images"]))

print("[2.8] 队列文件坏了：先留档，写路径拒绝覆盖")
corrupt = TMP / "corrupt"
corrupt.mkdir()
(corrupt / D.FILE).write_text("{ 半截 json", encoding="utf-8")
check("读路径当空队列（不炸）", D.list_items(corrupt) == [])
check("坏文件被改名留档（还能人工恢复）",
      any(p.name.startswith(D.FILE + ".bad") for p in corrupt.iterdir()),
      str([p.name for p in corrupt.iterdir()]))
(corrupt / D.FILE).write_text("{ 又坏了", encoding="utf-8")
check("写路径遇到坏文件直接拒绝（不整份覆盖）",
      _err(lambda: D.add(corrupt, chat="g", text="x")))

# 能解析但结构不对：读路径当空，写路径拒绝（不能整份覆盖）
shape_dir = TMP / "shape"
shape_dir.mkdir()
(shape_dir / D.FILE).write_text('{"drafts": [{"id": "x"}]}', encoding="utf-8")
check("结构不对时读路径当空队列", D.list_items(shape_dir) == [])
check("结构不对时写路径拒绝（不覆盖原文件）",
      _err(lambda: D.add(shape_dir, chat="g", text="x")))
check("原文件没被改写", (shape_dir / D.FILE).read_text(encoding="utf-8").startswith('{"drafts"'))

# ts 是 NaN/Infinity（json 都认）：不能让非法值一路序列化出去
nan_dir = TMP / "nan"
nan_dir.mkdir()
(nan_dir / D.FILE).write_text(
    '{"items": [{"id": "a", "chat": "g", "text": "t", "ts": NaN},'
    ' {"id": "b", "chat": "g", "text": "t2", "ts": Infinity}]}', encoding="utf-8")
nan_items = D.list_items(nan_dir)
check("NaN/Infinity 时间戳被换成正常值",
      all(i["ts"] == i["ts"] and i["ts"] != float("inf") and i["ts"] > 0 for i in nan_items),
      str([i["ts"] for i in nan_items]))
check("清理后的队列能正常序列化成 JSON",
      bool(json.dumps({"items": nan_items}, allow_nan=False)))

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

# 同一毫秒批量入队：显示顺序要按数字后缀（`-10` 应该比 `-2` 新）
batch_dir = TMP / "batch"
batch_dir.mkdir()
for i in range(12):
    D.add(batch_dir, chat="g", text=f"第{i}条", now=500)
ordered = [i["text"] for i in D.list_items(batch_dir, limit=20)]
check("同一毫秒 12 条按入队顺序倒序（不是字符串序）",
      ordered == [f"第{i}条" for i in range(11, -1, -1)], str(ordered[:4]))

# restore 挤掉别人时必须报告（发一条失败的消息不该让另一条待审的静默消失）
small2 = TMP / "restore_full"
small2.mkdir()
victim = D.add(small2, chat="g", text="最旧的一条")
keep = D.add(small2, chat="g", text="留着")
for i in range(D.MAX_ITEMS - 2):
    D.add(small2, chat="g", text=f"填{i}", now=2000 + i)
back = D.restore(small2, {**victim["item"], "id": "d-restored"})
check("restore 报告挤掉几条", (back or {}).get("dropped") == 1, str(back))
check("被挤掉的确实是最旧的那条", D.get(small2, victim["item"]["id"]) is None)
check("放回的那条在队列里", D.get(small2, "d-restored") is not None)
again = D.restore(small2, {**victim["item"], "id": "d-restored"})
check("已经在队列里就不重复放", (again or {}).get("dropped") == 0
      and D.count(small2) == D.MAX_ITEMS, str(D.count(small2)))

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

print("[4.5] 三态不能被压缩")
import json as _json                                    # noqa: E402
tri = TMP / "tri"
tri.mkdir()
(tri / "chat_policy.json").write_text(_json.dumps({"chats": {
    "group:1": {"mode": "inherit", "review": None},     # 手改出 null = 跟全局
    "group:2": {"mode": "inherit", "review": "false"},  # 字符串 false
    "group:3": {"mode": "inherit", "review": "true"}}}), encoding="utf-8")
saved_tri = CP.load(tri)["chats"]
check("null 不会被当成显式 false", "review" not in (saved_tri.get("group:1") or {}),
      str(saved_tri.get("group:1")))
check("字符串 \"false\" 解析成 False", saved_tri.get("group:2", {}).get("review") is False,
      str(saved_tri.get("group:2")))
check("字符串 \"true\" 解析成 True", saved_tri.get("group:3", {}).get("review") is True,
      str(saved_tri.get("group:3")))
# setter 也不能用 bool("false") = True
tri2 = TMP / "tri2"
tri2.mkdir()
CP.set_chat(tri2, "group:1", review="false")
check("setter：字符串 \"false\" 存成 False",
      CP.load(tri2)["chats"].get("group:1", {}).get("review") is False,
      str(CP.load(tri2)["chats"].get("group:1")))

tri3 = TMP / "tri3"
tri3.mkdir()
CP.set_chat(tri3, "group:1", review=0)
check("setter：数字 0 存成 False（JSON 客户端常这么传）",
      CP.load(tri3)["chats"].get("group:1", {}).get("review") is False,
      str(CP.load(tri3)["chats"].get("group:1")))
CP.set_chat(tri3, "group:1", review=1)
check("setter：数字 1 存成 True",
      CP.load(tri3)["chats"].get("group:1", {}).get("review") is True)
check("解析不出来的值直接报错（不静默存成 true）",
      _err(lambda: CP.set_chat(tri3, "group:1", review="maybe")))

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

print("[5.5] 审核模式下模型回了空字符串")
n_before = D.count(ST)                      # 队列里还有 [5] 那条
ms2.reply = ""
asyncio.run(feed("这句她会回空"))
check("空回复不入队（drafts.add 会抛「草稿是空的」）", D.count(ST) == n_before,
      f"{n_before} → {D.count(ST)}")
check("空回复也不假装已回复", rt.trace.rows(limit=1)[0]["stage"] == "empty",
      str(rt.trace.rows(limit=1)[0]))
ms2.reply = "我在的呀～"
asyncio.run(feed("恢复正常的问一句"))
check("恢复正常后又开始攒草稿", D.count(ST) == n_before + 1,
      f"{n_before} → {D.count(ST)}")
# 只清掉 5.5 新加的那条（[5] 的那条留给 [6] 用）
for it in D.list_items(ST, limit=100):
    if it["chat"] == "private:9" and it["user"] == "恢复正常的问一句":
        D.drop(ST, it["id"])

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

# 图 + 说明：发出去的时候说明要跟着（改了审核就掉说明，等于内容不一致）
D.add(ST, chat="private:9", character=rt.active_character, text="看图",
      images=[{"path": "/tmp/x.png", "caption": "这是一张图"}])
q_img = D.list_items(ST)[0]
calls = []
real_sif = rt.send_image_file


async def _spy_sif(chat, path, caption=None, **kw):
    calls.append((path, caption))
    return type("R", (), {"success": True})()


rt.send_image_file = _spy_sif
asyncio.run(rt.send_draft(q_img["id"]))
rt.send_image_file = real_sif
check("发草稿时图带着说明", calls == [("/tmp/x.png", "这是一张图")], str(calls))
check("草稿发完就清掉", D.count(ST) == 0)

print("[6.5] 发送被取消：草稿要放回去")
D.add(ST, chat="private:9", character=rt.active_character, text="要发的话")


async def _cancel_me(*a, **k):
    raise asyncio.CancelledError()


real_send = rt.send
rt.send = _cancel_me
q_c = D.list_items(ST)[0]
try:
    asyncio.run(rt.send_draft(q_c["id"]))
except asyncio.CancelledError:
    pass
rt.send = real_send
check("取消后草稿还在（不是静默消失）", D.get(ST, q_c["id"]) is not None, str(D.count(ST)))
D.drop(ST, q_c["id"])

print("[6.8] 只发出一部分：只补剩下的")
D.add(ST, chat="private:9", character=rt.active_character, text="第一句。第二句。")
q_p = D.list_items(ST)[0]


async def _partial(*a, **k):
    return type("R", (), {"success": False, "sent_parts": 1,
                          "parts": ["第一句。", "第二句。"]})()


real_send2 = rt.send
rt.send = _partial
try:
    asyncio.run(rt.send_draft(q_p["id"]))
except Exception:
    pass
rt.send = real_send2
back_p = D.get(ST, q_p["id"])
check("草稿放回来了", back_p is not None, str(D.count(ST)))
check("只补没发出去的那句（不重复已发的）", back_p and back_p["text"] == "第二句。",
      str(back_p and back_p["text"]))
D.drop(ST, q_p["id"])

print("[6.9] flush 只发自己那个窗口")
from bot.framework import FeatureContext as FC          # noqa: E402

ctx_a = FC(adapter=rt.ob, text="A 的话", is_group=False, group_id="",
           user_id="9", user_name="a", message_id="1")
ctx_a.character = rt.active_character
win_a = {"texts": ["A 的话"], "ctx": ctx_a, "task": None, "user_id": "9"}
rt._coal["private:9"] = win_a
win_b = {"texts": ["B 的话"], "ctx": ctx_a, "task": None, "user_id": "77"}
rt._coal["private:9"] = win_b                      # 并发场景：被换成了别人的窗口
before_n = len(sent)
asyncio.run(rt._flush_coalesce("private:9", expect=win_a))
check("窗口已经换人时不发（避免把别人的消息发出去）", len(sent) == before_n, str(sent[before_n:]))
check("别人的窗口还在", rt._coal.get("private:9") is win_b)
rt._coal.clear()

print("[7] 会话级覆盖")
CP.set_chat(ST, "private:9", review=False)   # 显式不审
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
# 「全部发送」：先快照、按时间正序、超时在 loop 里取消
check("全部发送先快照 id 再发", "DF.list_items(STATE_DIR, chat=chat, limit=DF.MAX_ITEMS)" in SRV)
check("全部发送按时间正序", "batch.sort(key=DF._order)" in SRV)
check("超时在 loop 内部取消（wait_for）", "_send_draft_bounded" in SRV
      and "asyncio.wait_for(rt.send_draft(did)" in SRV)
check("不再边发边重读队列", "while True:" not in SRV.split("send_all")[1][:1200])
# wait_for 超时会让 fut.result() 抛 CancelledError（BaseException）——
# 只接 Exception 的话整轮中断、响应也写不出去
check("全部发送接得住取消",
      "except (Exception, asyncio.CancelledError)" in SRV.split("send_all")[1][:1600])
check("默认关（不打扰现有行为）", '"review_on": False' in CFG)
# 清空输入框后点发送要真的提交（否则发出去的是存着的旧稿）
check("drSend 不再用「非空」当条件",
      "ta && item && ta.value.trim() !==" in HTML and "ta.value.trim() && ta.value" not in HTML)
check("草稿箱空文本由服务端拒（400）", "回复不能是空的" in (ROOT / "bot" / "drafts.py").read_text(encoding="utf-8"))
# 全部发送：坏文件先报错、整轮有时间预算
sd = SRV.split("_drafts_post")[1][:6000]
check("全部发送前先 strict 读（坏文件要报错）", "DF._load(STATE_DIR, strict=True)" in sd)
check("全部发送有整轮时间预算", "deadline = time.time() +" in sd)
check("单条发送也接得住取消", "except (Exception, asyncio.CancelledError)" in sd)
check("外层等待大于内层超时（否则两条同时在飞）",
      "timeout=40" in sd and "fut.result(timeout=50)" in sd, sd[:0])
# 保存自定义正则时做一次编译校验
check("保存配置时校验正则", "GD.compile_rules" in SRV and '"guard_patterns"' in SRV)
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
