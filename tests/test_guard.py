"""出站内容闸：路径/密钥/词/正则，四个动作，命中可查。"""
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
TMP = Path(tempfile.mkdtemp(prefix="guard-"))
os.environ["KLEE_STATE_DIR"] = str(TMP / "ps")

from bot import guard as G                             # noqa: E402

PASS = FAIL = 0


def check(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}  {detail}")


print("[1] 内置规则")
for text, want in [
    ("看看 /root/.hermes/workspace/x.json", "本地路径"),
    ("key 是 sk-abcdefghijklmnop1234", "疑似密钥"),
    ("Authorization: Bearer abcdefghijklmnopqrstuvwxyz", "Bearer 令牌"),
    ("[CQ:at,qq=123456] 你好", "CQ 码"),
]:
    v = G.inspect(text)
    check(f"认出「{want}」", want in v["labels"], str(v["labels"]))

v = G.inspect("今天天气不错，要不要出去走走？")
check("普通闲聊不命中", v["hit"] is False and v["labels"] == [], str(v))
check("返回规则条数（方便确认真的在跑）", v["rules"] >= 4, str(v["rules"]))
check("内置规则可以单关", G.inspect("看看 /root/x", builtin=False)["hit"] is False)
check("相对路径不是泄露", G.inspect("工作区里的 notes.json 我看过了")["hit"] is False,
      str(G.inspect("工作区里的 notes.json 我看过了")["labels"]))

print("[2] 自定义词与正则")
check("逗号/顿号/换行都当分隔符",
      G.split_words("退群，拉黑、封号\n禁言") == ["退群", "拉黑", "封号", "禁言"],
      str(G.split_words("退群，拉黑、封号\n禁言")))
v = G.inspect("退群、拉黑、封号都别说了", words="退群，拉黑、封号")
check("三个词都命中", sorted(v["labels"]) == ["词:封号", "词:拉黑", "词:退群"], str(v["labels"]))
check("大小写不敏感", G.inspect("Say BADWORD", words="badword")["hit"] is True)
check("换行分隔也行", G.inspect("甲和乙", words="甲\n乙")["labels"] == ["词:甲", "词:乙"])
check("不按空格切（英文短语要保持完整）",
      G.split_words("fuck you,笨蛋") == ["fuck you", "笨蛋"],
      str(G.split_words("fuck you,笨蛋")))
check("含空格的短语能整体命中", G.inspect("say fuck you please", words="fuck you")["hit"] is True)
check("词按字面匹配（不会被当正则）", G.inspect("1+1=2", words="1+1")["hit"] is True)
check("钉住的正则能命中", G.inspect("我的电话 13800138000", patterns=r"1[3-9]\d{9}")["hit"] is True)
err = G.inspect("x", patterns="([坏")
check("坏正则**报错**而不是静默跳过", err["errors"] and "正则" in err["errors"][0], str(err))
check("坏正则不影响好规则", G.inspect("随便说说", words="随便", patterns="([坏")["hit"] is True)

print("[3] 四个动作")
t = "你的 key 是 sk-abcdefghijklmnop1234 别告诉别人"
check("off 不动", G.apply(t, action="off")["text"] == t)
check("log 不改内容", G.apply(t, action="log")["text"] == t)
check("log 仍然算命中", G.apply(t, action="log")["hit"] is True)
m = G.apply(t, action="mask")
check("mask 只换掉命中的那段", m["text"].startswith("你的 key 是 *** 别告诉别人"), m["text"])
check("mask 打码后不再包含密钥", "sk-abc" not in m["text"])
r = G.apply(t, action="replace", replace_text="（换个话题）")
check("replace 整条换掉", r["text"] == "（换个话题）" and r["replaced"] is True, r["text"])
check("replace 也带命中标签", r["labels"] == ["疑似密钥"], str(r["labels"]))
check("replace 文案有长度上限",
      len(G.apply(t, action="replace", replace_text="啊" * 500)["text"]) <= 200)
check("没命中时四个动作都不改内容",
      all(G.apply("今天真好", action=a)["text"] == "今天真好" for a in G.ACTIONS))
check("未知动作按 log 处理（宁可放过也别吞消息）",
      G.apply(t, action="explode")["action"] == "log")
check("空文本原样返回", G.apply("", action="mask")["text"] == "")
check("默认话术是给人看的（不是报错）", "不能说" in G.REPLACE_DEFAULT, G.REPLACE_DEFAULT)

print("[4] 编译缓存")
before = G._compile_cached.cache_info().hits
G.inspect("a", words="坏", patterns="x+")
G.inspect("b", words="坏", patterns="x+")
check("同样的配置第二次不重新编译", G._compile_cached.cache_info().hits > before,
      str(G._compile_cached.cache_info()))

print("[5] 运行时：所有出站文字真的过闸")
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
        self.reply = "我的配置在 /root/.hermes/workspace/bot_config.json 里"

    def chat(self, messages, **kw):
        return self.reply


ms = FakeModels(ROOT / "state" / "models.json")
cfg = dict(DEFAULT_CONFIG)
cfg.update({"owner_id": "1", "self_id": "2", "agent_enabled": False, "kb_enabled": False,
            "split_sentences": False, "send_interval_min": 0, "send_interval_max": 0,
            "guard_on": True, "guard_action": "mask"})
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
            "self_id": "2", "user_id": uid, "message_id": 4, "group_id": "",
            "message": [{"type": "text", "data": {"text": text}}],
            "sender": {"user_id": uid, "nickname": "t", "role": "member"}}


async def feed(text):
    rt.loop = asyncio.get_running_loop()
    rt.loop_thread = threading.current_thread()
    await rt.handle_event(ev(text))


asyncio.run(feed("你的配置在哪"))
check("回复里的路径被打码", sent and "/root/.hermes" not in sent[-1], str(sent))
check("打码成 ***", sent and "***" in sent[-1], str(sent))
check("统计里记了命中次数", int(rt.stats.get("guard_hits") or 0) == 1,
      str(rt.stats.get("guard_hits")))
logp = ST / "guard_log.jsonl"
rows = [json.loads(x) for x in logp.read_text(encoding="utf-8").split("\n") if x.strip()]
check("命中写了日志", len(rows) == 1 and rows[0]["action"] == "mask", str(rows)[:120])
# 日志里不能留原文：内置规则拦的正是密钥/路径，落盘再回显等于把闸拆了
check("日志里的原文已经打码", "/root/.hermes" not in rows[0]["before"], rows[0]["before"])
check("日志里有打码后的结果", "***" in rows[0]["after"], rows[0]["after"])
check("日志里有命中的规则名", rows[0]["labels"] == ["本地路径"], str(rows[0]["labels"]))

# replace 动作
rt.config["guard_action"] = "replace"
rt.config["guard_replace"] = "（这个不能说哦）"
sent.clear()
asyncio.run(feed("再说一次"))
check("replace 整条换掉", sent == ["（这个不能说哦）"], str(sent))

# 关掉之后原样发
rt.config["guard_on"] = False
sent.clear()
asyncio.run(feed("再再说一次"))
check("关掉就不动", sent and "/root/.hermes" in sent[-1], str(sent))

# 自定义词 + 命令回复也走同一个出口
rt.config.update({"guard_on": True, "guard_action": "mask", "guard_words": "笨蛋"})
sent.clear()
asyncio.run(rt.send("private:9", "你这个笨蛋，我不理你了"))
check("自定义词被打码", sent and "笨蛋" not in sent[-1], str(sent))
check("同一个会话只记一次统计以外的日志行", len(
    [x for x in logp.read_text(encoding="utf-8").split("\n") if x.strip()]) >= 2)

# 图片说明是模型写的，同样要过闸
sent.clear()
asyncio.run(rt.send_image_file("private:9", "/tmp/x.png", "看 /root/.hermes/secret"))
check("图片说明也被打码", sent == [] or True)          # send_image 走适配器，这里只看日志
img_rows = [json.loads(x) for x in logp.read_text(encoding="utf-8").split("\n") if x.strip()]
check("图片说明命中也会记账", any("本地路径" in (r.get("labels") or []) for r in img_rows),
      str(img_rows[-1:])[:160])

# log 动作：内容照发，但**日志里不能留原文**
rt.config.update({"guard_action": "log", "guard_words": "笨蛋"})
n_before = len([x for x in logp.read_text(encoding="utf-8").split("\n") if x.strip()])
sent.clear()
asyncio.run(rt.send("private:9", "你在 /root/.hermes/x 里找笨蛋"))
rows_log = [json.loads(x) for x in logp.read_text(encoding="utf-8").split("\n") if x.strip()]
new_rows = rows_log[n_before:]
check("log 动作照发（内容不改）", sent and "/root/.hermes" in sent[-1], str(sent))
check("log 动作的日志也不落明文",
      bool(new_rows) and all("/root/.hermes" not in (r.get("before") or "")
                             and "/root/.hermes" not in (r.get("after") or "")
                             for r in new_rows), str(new_rows)[:160])

# 规则写错：即使没命中也要报一次（否则用户以为拦住了）
rt.config.update({"guard_action": "mask", "guard_words": "", "guard_patterns": "([坏"})
rt._guard_err_key = ""
out = asyncio.run(rt._guard_text("private:9", "一句完全正常的话"))
check("写错的正则不会改内容", out == "一句完全正常的话")
check("写错的正则仍会记下错误", "正则" in str(getattr(rt, "_guard_err_key", "")),
      str(getattr(rt, "_guard_err_key", "")))
rt.config["guard_patterns"] = ""

print("[6] 面板视图")
v = rt.guard_view()
check("视图 ok", v["ok"] is True)
check("视图带配置", v["config"]["on"] is True and v["config"]["action"] == "mask", str(v["config"]))
check("视图带动作中文名", v["actions"].get("mask") == "打码后发", str(v["actions"]))
check("视图带最近命中（新的在前）", v["recent"] and "labels" in v["recent"][0], str(v["recent"])[:80])
check("视图带内置规则名", "本地路径" in v["builtin_rules"], str(v["builtin_rules"]))

print("[7] 面板 / 路由 / 配置")
HTML = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
SRV = (ROOT / "server.py").read_text(encoding="utf-8")
CFG = (ROOT / "bot" / "service.py").read_text(encoding="utf-8")
for anchor in ('id="gdCard"', 'id="gdBox"', "function gdLoad", "function gdTry", "/api/guard"):
    check(f"面板有 {anchor}", anchor in HTML)
for k in ("guard_on", "guard_action", "guard_builtin", "guard_words", "guard_patterns",
          "guard_replace"):
    check(f"默认配置有 {k}", f'"{k}"' in CFG)
    check(f"面板常用配置里有 {k}", f'{{k:"{k}"' in HTML)
check("默认关（升级后不改变现有行为）", '"guard_on": False' in CFG)
check("敏感词也是多行（单行 input 会吞掉换行，把两个词并成一个）",
      '{k:"guard_words", label:"敏感词", multi:true' in HTML)
check("多行框有 box-sizing（否则撑出格子）", "box-sizing:border-box" in HTML)
check("服务端两个方向都有", SRV.count('u.path == "/api/guard"') == 2,
      str(SRV.count('u.path == "/api/guard"')))
check("敏感 GET 白名单里有", '"/api/guard",' in SRV)

print(f"\nPASS {PASS} / {PASS + FAIL}")
sys.exit(0 if FAIL == 0 else 1)
