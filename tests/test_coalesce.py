"""连发合并：一个人连发三句，她只答一次（省钱、也更连贯）。"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
TMP = Path(tempfile.mkdtemp(prefix="coalesce-"))
os.environ["KLEE_STATE_DIR"] = str(TMP / "ps")

from bot import coalesce as C                          # noqa: E402

PASS = FAIL = 0


def check(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}  {detail}")


print("[1] 配置")
c = C.clamp_cfg(None)
check("默认关", c["on"] is False and c["window"] == 1.5 and c["max_msgs"] == 4, str(c))
check("字符串 on 解析", C.clamp_cfg({"on": "true"})["on"] is True
      and C.clamp_cfg({"on": "false"})["on"] is False)
check("窗口夹在 0.3~10", C.clamp_cfg({"window": 0.01})["window"] == 0.3
      and C.clamp_cfg({"window": 99})["window"] == 10.0)
check("条数夹在 1~10", C.clamp_cfg({"max_msgs": 0})["max_msgs"] == 1
      and C.clamp_cfg({"max_msgs": 99})["max_msgs"] == 10)
check("坏数字回落默认", C.clamp_cfg({"window": "很久"})["window"] == 1.5)
check("NaN 也回落", C.clamp_cfg({"window": float("nan")})["window"] == 1.5)

print("[2] 该不该合并")
on = {"on": True, "window": 1.5, "max_msgs": 4}
check("普通短句合并", C.should_merge(on, text="在吗") is True)
check("关掉就不合并", C.should_merge({"on": False}, text="在吗") is False)
check("带图的不合并", C.should_merge(on, text="看图", has_images=True) is False)
check("命令不合并", C.should_merge(on, text=".帮助", command=True) is False)
check("空消息不合并", C.should_merge(on, text="   ") is False)
check("太长的一条不合并", C.should_merge(on, text="啊" * 201) is False)
check("刚好 200 字还能合", C.should_merge(on, text="啊" * 200) is True)

print("[3] 拼起来")
check("多句用换行接", C.merge_texts(["在吗", "那个", "明天几点集合"]) == "在吗\n那个\n明天几点集合")
check("相邻重复只留一句（复读机）", C.merge_texts(["哈哈", "哈哈", "哈"]) == "哈哈\n哈")
check("不挨着的重复保留", C.merge_texts(["a", "b", "a"]) == "a\nb\na")
check("空串被丢掉", C.merge_texts(["", " 在吗 ", ""]) == "在吗")
check("整体有长度上限", len(C.merge_texts(["啊" * 200] * 10)) <= C.MAX_MERGED)

print("[4] 运行时：三条只回一次")
from bot.characters import CharacterStore              # noqa: E402
from bot.framework import FeatureContext               # noqa: E402
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
        self.last_user = ""

    def chat(self, messages, **kw):
        self.calls += 1
        self.last_user = "\n".join(str(m.get("content") or "") for m in messages
                                   if m.get("role") == "user")
        return "明天八点集合哦～"


ms = FakeModels(ROOT / "state" / "models.json")
cfg = dict(DEFAULT_CONFIG)
cfg.update({"owner_id": "1", "self_id": "2", "agent_enabled": False, "kb_enabled": False,
            "split_sentences": False, "send_interval_min": 0, "send_interval_max": 0,
            "coalesce_on": True, "coalesce_window": 0.15, "coalesce_max": 4})
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

    def _chat_id(self, is_group, group_id, user_id):
        return f"group:{group_id}" if is_group else f"private:{user_id}"


rt.ob = OB()


def ev(text, chat="private:9"):
    uid = chat.split(":")[1]
    return {"post_type": "message",
            "message_type": "group" if chat.startswith("group:") else "private",
            "self_id": "2", "user_id": uid, "message_id": 3,
            "message": [{"type": "text", "data": {"text": text}}],
            "sender": {"user_id": uid, "nickname": "t", "role": "member"}}


async def main():
    rt.loop = asyncio.get_running_loop()
    rt.loop_thread = threading.current_thread()
    for t in ("在吗", "那个", "明天几点集合"):
        await rt.handle_event(ev(t))
        await asyncio.sleep(0.01)
    await asyncio.sleep(0.45)                 # 等窗口到点自动发出去


asyncio.run(main())
check("模型只被调了一次", ms.calls == 1, str(ms.calls))
check("发给模型的是三段合并的", all(x in ms.last_user for x in ("在吗", "那个", "明天几点集合")),
      ms.last_user[-120:])
check("只回了一条", len(sent) == 1, str(sent))
check("统计里记了合并次数", int(rt.stats.get("coalesced") or 0) == 1, str(rt.stats.get("coalesced")))
check("窗口已经清空", rt._coal == {})

print("[5] 到上限立刻发（不等窗口）")
ms.calls = 0
sent.clear()
rt.config["coalesce_window"] = 10          # 窗口很长，靠条数触发
rt.config["coalesce_max"] = 2


async def main2():
    rt.loop = asyncio.get_running_loop()
    rt.loop_thread = threading.current_thread()
    await rt.handle_event(ev("第一句"))
    await asyncio.sleep(0.02)
    await rt.handle_event(ev("第二句"))
    await asyncio.sleep(0.25)


asyncio.run(main2())
check("两条就到上限、立刻回", ms.calls == 1 and len(sent) == 1, f"{ms.calls} {sent}")
check("窗口里没有残留", rt._coal == {})

print("[6] 不能合并的消息会先把攒着的发掉（顺序不乱）")
ms.calls = 0
sent.clear()
rt.config["coalesce_window"] = 10
rt.config["coalesce_max"] = 9


async def main3():
    rt.loop = asyncio.get_running_loop()
    rt.loop_thread = threading.current_thread()
    await rt.handle_event(ev("先攒一句"))
    await asyncio.sleep(0.02)
    # 直接调 _coalesce：太长的一条不能合并 → 应该先把上面那句 flush 掉
    ctx = FeatureContext(adapter=rt.ob, text="啊" * 300, is_group=False, group_id="",
                         user_id="9", user_name="t", message_id="4")
    ctx.character = rt.active_character
    merged = await rt._coalesce(ctx, [])
    check("长消息不合并", merged is False)
    await asyncio.sleep(0.05)


asyncio.run(main3())
check("攒着的那句被先发掉了", ms.calls == 1 and len(sent) == 1, f"{ms.calls} {sent}")
check("发出去的是攒着的那句", "先攒一句" in (ms.last_user or ""), ms.last_user[-80:])

print("[7] 关掉服务不留尾巴")
rt.config["coalesce_window"] = 10


async def main4():
    rt.loop = asyncio.get_running_loop()
    rt.loop_thread = threading.current_thread()
    await rt.handle_event(ev("这条还没到点"))
    await asyncio.sleep(0.02)
    await rt.stop()
    await asyncio.sleep(0.25)


ms.calls = 0
sent.clear()
asyncio.run(main4())
check("stop 之后不再补发", ms.calls == 0 and sent == [], f"{ms.calls} {sent}")
check("窗口表清空", rt._coal == {})

print("[8] 面板 / 配置 / 路由")
HTML = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
CFG = (ROOT / "bot" / "service.py").read_text(encoding="utf-8")
for k in ("coalesce_on", "coalesce_window", "coalesce_max"):
    check(f"默认配置有 {k}", f'"{k}"' in CFG)
    check(f"面板常用配置里有 {k}", f'{{k:"{k}"' in HTML)
check("默认关（不改变现有节奏）", '"coalesce_on": False' in CFG)
check("分组在「审与闸」里", 'id: "guard"' in HTML)

print(f"\nPASS {PASS} / {PASS + FAIL}")
sys.exit(0 if FAIL == 0 else 1)
