"""决策追踪：每条消息走到哪一步、为什么没回。"""
from __future__ import annotations

import ast
import asyncio
import os
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
TMP = Path(tempfile.mkdtemp(prefix="trace-"))
os.environ["KLEE_STATE_DIR"] = str(TMP / "ps")

from bot import trace as TR                            # noqa: E402

PASS = FAIL = 0


def check(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}  {detail}")


print("[1] ring 本身")
t = TR.Trace(maxlen=5)
for i in range(8):
    t.note("group:1", f"第{i}句", "replied", now=1000 + i)
rows = t.rows(limit=10)
check("定长：只留最近 5 条", len(rows) == 5 and len(t) == 5, str(len(rows)))
check("时间倒序（新的在前）", rows[0]["text"] == "第7句", rows[0]["text"])
check("带中文标签", rows[0]["label"] == "已回复" and rows[0]["tone"] == "ok", str(rows[0]))
check("按会话过滤", t.rows(chat="group:2") == [])
check("按 stage 过滤", len(t.rows(stage="replied")) == 5)
t.note("group:2", "别的群", "quiet", "静默时段 23:30~07:00", now=2000)
check("未知 stage 原样返回（不崩）", t.note("g", "x", "??", now=2000)["stage"] == "??")
check("未知 stage 也有标签", t.rows(limit=1)[0]["label"] in ("??", "静默时段"),
      str(t.rows(limit=1)[0]))
c = t.counts()
# maxlen=5：8 条 replied 只剩 3 条，再加 quiet 与 ?? 各一条
check("counts 按 stage 计数", c.get("replied") == 3 and c.get("quiet") == 1, str(c))
check("counts 能按会话过滤", t.counts(chat="group:2") == {"quiet": 1}, str(t.counts(chat="group:2")))
check("clear 返回清掉的条数", t.clear() == 5 and len(t) == 0, str(len(t)))

r = TR.Trace()
row = r.note("group:9", "一\n二", "error", "RuntimeError: x", user="小明", character="atri")
check("换行压成一行（面板一行一条）", "\n" not in row["text"])
check("detail 有长度上限", len(r.note("g", "x", "y", "d" * 500)["detail"]) <= 160)
check("user/character 带上了", row["user"] == "小明" and row["character"] == "atri")
check("返回的是拷贝（改不动内部）", r.rows()[0] is not r._q[-1])

print("[2] 运行时里的每个 stage 都有中文")
RT_SRC = (ROOT / "bot" / "runtime.py").read_text(encoding="utf-8")
# 用 AST 找 self.note(...) 的第三个参数：正则会被「参数里带括号/逗号」骗过
# （`self.note(self._chat_id(...), "", "self")` 就抽不出来）
used = set()
for node in ast.walk(ast.parse(RT_SRC)):
    if not isinstance(node, ast.Call):
        continue
    fn = node.func
    if not (isinstance(fn, ast.Attribute) and fn.attr == "note"
            and isinstance(fn.value, ast.Name) and fn.value.id == "self"):
        continue
    if len(node.args) >= 3 and isinstance(node.args[2], ast.Constant) \
            and isinstance(node.args[2].value, str):
        used.add(node.args[2].value)
check("至少用到 8 种 stage", len(used) >= 8, str(sorted(used)))
missing = sorted(used - set(TR.STAGES))
check("全都登记在 STAGES 里（否则面板显示裸英文）", not missing, str(missing))
unused = sorted(set(TR.STAGES) - used)
check("STAGES 没有僵尸项", not unused, str(unused))
check("每种 stage 都有语气分类", all(v[1] in ("ok", "held", "miss", "err")
      for v in TR.STAGES.values()), str(TR.STAGES))

print("[3] 运行时真的会记")
from bot.characters import CharacterStore              # noqa: E402
from bot.llm import ModelStore                         # noqa: E402
from bot.runtime import BotRuntime                     # noqa: E402
from bot.service import DEFAULT_CONFIG                 # noqa: E402

ST = TMP / "rt"
ST.mkdir()
store = CharacterStore(ST, seeds_dir=ROOT / "bot" / "character_seeds")


class FakeModels(ModelStore):
    def chat(self, messages, **kw):
        return "在的呀"


ms = FakeModels(ROOT / "state" / "models.json")
cfg = dict(DEFAULT_CONFIG)
cfg.update({"owner_id": "1", "self_id": "2", "agent_enabled": False, "kb_enabled": False,
            "split_sentences": False, "send_interval_min": 0, "send_interval_max": 0,
            "group_policy": "mention"})
rt = BotRuntime(features_dir=ROOT / "bot" / "features", config=cfg, models=ms,
                soul_path=store.soul_path("klee"), state_dir=ST, characters=store)


class OB:
    def send_text(self, c, t, reply_to=None, at_uid=None):
        return {"message_id": 1}

    def send_image(self, *a, **k):
        return {"message_id": 2}

    def get_group_member_list(self, g):
        return []

    def get_msg(self, m):
        return {}

    def get_status(self):
        return {"good": True}

    def _chat_id(self, is_group, group_id, user_id):
        return f"group:{group_id}" if is_group else f"private:{user_id}"


rt.ob = OB()


def ev(text, *, chat="private:9", mentioned=False, uid=None):
    is_group = chat.startswith("group:")
    u = uid or chat.split(":")[1]
    msg = [{"type": "text", "data": {"text": text}}]
    if mentioned:
        msg.insert(0, {"type": "at", "data": {"qq": "2"}})
    return {"post_type": "message", "message_type": "group" if is_group else "private",
            "self_id": "2", "user_id": u, "message_id": 5, "message": msg,
            # group_id 必须给：不给的话会话是 "group:"，策略/静默全都查不到
            "group_id": chat.split(":")[1] if is_group else "",
            "sender": {"user_id": u, "nickname": "t", "role": "member"}}


async def feed(e):
    rt.loop = asyncio.get_running_loop()
    rt.loop_thread = threading.current_thread()
    await rt.handle_event(e)


asyncio.run(feed(ev("你好呀")))
last = rt.trace.rows(limit=1)[0]
check("私聊正常回复 → replied", last["stage"] == "replied", str(last))
check("replied 里带了回复的开头", "在的呀" in last["detail"], last["detail"])

asyncio.run(feed(ev("群里随口一句", chat="group:100")))
last = rt.trace.rows(limit=1)[0]
check("群里没 @ → not_mentioned", last["stage"] == "not_mentioned", str(last))
check("说了原因（群策略）", "没 @" in last["detail"], last["detail"])

asyncio.run(feed(ev("又被叫到", chat="group:100", mentioned=True)))
last = rt.trace.rows(limit=1)[0]
check("@ 了就回 → replied", last["stage"] == "replied", str(last))

asyncio.run(feed(ev(".help", chat="private:9")))
last = rt.trace.rows(limit=1)[0]
check("命令 → command", last["stage"] == "command", str(last))
check("command 记了是前缀命令", "前缀" in last["detail"], last["detail"])

asyncio.run(feed({**ev("", chat="private:9"), "message": []}))
last = rt.trace.rows(limit=1)[0]
check("空消息 → empty", last["stage"] == "empty", str(last))

asyncio.run(feed({**ev("自己的回显", chat="private:2"), "user_id": "2"}))
last = rt.trace.rows(limit=1)[0]
check("自己发的 → self", last["stage"] == "self", str(last))

# 会话被关掉
from bot import chatpolicy                             # noqa: E402
chatpolicy.set_chat(ST, "group:200", mode="disabled")
asyncio.run(feed(ev("这个群不理我", chat="group:200")))
last = rt.trace.rows(limit=1)[0]
check("会话 disabled → disabled", last["stage"] == "disabled", str(last))

# 静默时段
chatpolicy.set_chat(ST, "group:300", quiet=["00:00", "23:59"])
asyncio.run(feed(ev("半夜说话", chat="group:300", mentioned=True)))
last = rt.trace.rows(limit=1)[0]
check("静默时段 → quiet", last["stage"] == "quiet", str(last))

print("[4] 面板视图")
v = rt.trace_view()
check("视图 ok", v["ok"] is True)
check("带 counts", bool(v["counts"]), str(v["counts"]))
check("带中文标签表", v["labels"].get("replied") == "已回复", str(v["labels"])[:60])
check("带总数", v["total"] == len(rt.trace), f'{v["total"]} {len(rt.trace)}')
v2 = rt.trace_view(chat="group:100")
check("按会话过滤", all(i["chat"] == "group:100" for i in v2["items"]), str(v2["items"])[:80])
check("每条都有 label 和 tone", all(i.get("label") and i.get("tone") for i in v["items"]))

print("[5] 面板 / 路由")
HTML = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
SRV = (ROOT / "server.py").read_text(encoding="utf-8")
for anchor in ('id="trCard"', 'id="trBox"', "function trLoad", "/api/trace"):
    check(f"面板有 {anchor}", anchor in HTML)
check("切到管控页会加载", "drLoad(); trLoad(); gdLoad();" in HTML)
check("服务端有路由", 'u.path == "/api/trace"' in SRV)
check("敏感 GET 白名单里有", '"/api/trace",' in SRV)

print(f"\nPASS {PASS} / {PASS + FAIL}")
sys.exit(0 if FAIL == 0 else 1)
