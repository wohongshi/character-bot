"""会话固定角色：一个群一个角色（存储 / 运行时 / 分发 / 命令 / 面板接口）。

跑法：`python3 tests/test_chat_char.py`（不联网、不碰生产 state）。
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TMP = Path(tempfile.mkdtemp(prefix="chatrole-"))
os.environ["KLEE_STATE_DIR"] = str(TMP / "ps")
sys.path.insert(0, str(ROOT))

from bot import chat_char                                   # noqa: E402
from bot.characters import CharacterStore                   # noqa: E402
from bot.framework import FeatureContext, FeatureManager     # noqa: E402

PASS = FAIL = 0


def check(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}  {detail}")


# ============ 1) 存储 ============
print("[1] 存储")
S = TMP / "s1"
S.mkdir()

check("没有文件时是空的", chat_char.load(S) == {})
check("没绑定的会话 → 空串", chat_char.get(S, "group:1") == "")
check("空/怪会话 id 不 bindable", not chat_char.is_bindable("") and
      not chat_char.is_bindable("nope") and not chat_char.is_bindable("group: "))

chat_char.set_binding(S, "group:1", "hutao", by="42", valid_ids=["klee", "hutao"])
check("写进去读得出来", chat_char.get(S, "group:1") == "hutao")
check("别的会话不受影响", chat_char.get(S, "group:2") == "")
check("落盘文件存在", (S / "chat_chars.json").exists())

chat_char.set_binding(S, "group:1", "klee", by="42", valid_ids=["klee", "hutao"])
check("改绑立刻生效（不靠下次 stat）", chat_char.get(S, "group:1") == "klee")
check("改绑后留了上一版 .bak", (S / "chat_chars.json.bak").exists())
check("记下了改的人", chat_char.load(S)["group:1"].get("by") == "42")

ok = False
try:
    chat_char.set_binding(S, "group:1", "nobody", valid_ids=["klee"])
except chat_char.BindError:
    ok = True
check("角色不存在 → BindError", ok)

for bad in ("", "  ", "group", "group: ", "a:b\nc"):
    ok = False
    try:
        chat_char.set_binding(S, bad, "klee", valid_ids=["klee"])
    except chat_char.BindError:
        ok = True
    check(f"非法会话 id 被拒（{bad!r}）", ok)

ok = False
try:
    chat_char.set_binding(S, "group:7", "Bad Id!", valid_ids=["bad id"])
except chat_char.BindError:
    ok = True
check("非法角色 id 被拒", ok)

check("取消绑定返回 True", chat_char.clear(S, "group:1") is True)
check("取消之后就是空的", chat_char.get(S, "group:1") == "")
check("取消不存在的绑定返回 False", chat_char.clear(S, "group:1") is False)

# 坏文件：按「全部跟随全局」处理，绝不抛
(S / "chat_chars.json").write_text("{not json", encoding="utf-8")
chat_char._CACHE.clear()
check("坏 JSON → 空表（不抛）", chat_char.load(S) == {})
check("坏 JSON 时 get 也是空串", chat_char.get(S, "group:1") == "")

(S / "chat_chars.json").write_text('{"chats": {"group:1": 5, "group:2": {"id": "BAD!"}}}',
                                   encoding="utf-8")
chat_char._CACHE.clear()
check("形状不对的条目被丢掉", chat_char.load(S) == {})

# 上限
S2 = TMP / "s2"
S2.mkdir()
for i in range(chat_char.MAX_CHATS):
    chat_char.set_binding(S2, f"group:{i}", "klee", valid_ids=["klee"])
ok = False
try:
    chat_char.set_binding(S2, "group:999999", "klee", valid_ids=["klee"])
except chat_char.BindError:
    ok = True
check(f"绑定数到上限（{chat_char.MAX_CHATS}）后拒绝新增", ok)
check("已存在的还能改", chat_char.set_binding(S2, "group:0", "hutao",
                                             valid_ids=["klee", "hutao"])["id"] == "hutao")
check("prune 删掉指向已删角色的", chat_char.prune(S2, ["klee"]) and
      chat_char.get(S2, "group:0") == "")

# 面板对话（web:）不参与固定：它有自己的角色选择器
check("web: 会话不可绑定", chat_char.is_bindable("web:studio-main") is False)
check("group:/private: 可绑定", chat_char.is_bindable("group:1") and
      chat_char.is_bindable("private:2"))
ok = False
try:
    chat_char.set_binding(S, "web:studio-main", "klee", valid_ids=["klee"])
except chat_char.BindError:
    ok = True
check("给 web: 写绑定被拒", ok)
(S / "chat_chars.json").write_text(json.dumps(
    {"chats": {"web:studio-main": {"id": "klee"}}}), encoding="utf-8")
chat_char._CACHE.clear()
check("文件里残留的 web: 绑定读的时候也丢掉", chat_char.load(S) == {})

# ContextVar
tok = chat_char.push("hutao")
check("push 之后 current 是它", chat_char.current() == "hutao")
inner = chat_char.push("klee")
check("嵌套 push 覆盖", chat_char.current() == "klee")
chat_char.pop(inner)
check("pop 还原上一层", chat_char.current() == "hutao")
chat_char.pop(tok)
check("pop 到栈底是空（不是全局角色）", chat_char.current() == "")


# ============ 2) 运行时：人格 / 记忆 / 模型跟着会话走 ============
print("[2] 运行时")
from bot.llm import ModelStore                              # noqa: E402
from bot.runtime import BotRuntime                          # noqa: E402
from bot.service import DEFAULT_CONFIG                      # noqa: E402


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
        return "嗯。"


STATE = TMP / "state"
STATE.mkdir()
store = CharacterStore(STATE, seeds_dir=ROOT / "bot" / "character_seeds")
cfg = dict(DEFAULT_CONFIG)
cfg.update({"owner_id": "1000000001", "self_id": "1000000002", "split_sentences": False,
            "send_interval_min": 0, "send_interval_max": 0, "agent_enabled": False,
            "kb_enabled": False})
ms = CaptureModels(ROOT / "state" / "models.json")     # 只读：拿 provider 定义
rt = BotRuntime(features_dir=ROOT / "bot" / "features", config=cfg, models=ms,
                soul_path=store.soul_path("klee"), state_dir=STATE, characters=store)
rt.ob = FakeOB()
rt.loop = asyncio.get_running_loop() if False else None      # 下面单独起 loop

check("没绑定时 char_for 就是全局", rt.char_for("group:9") == rt._character)
chat_char.set_binding(STATE, "group:9", "hutao", valid_ids=[c["id"] for c in store.list()])
check("绑定后 char_for 跟着变", rt.char_for("group:9") == "hutao")
check("没绑的会话仍然是全局", rt.char_for("group:10") == rt._character)

chat_char.set_binding(STATE, "group:9", "ghost", valid_ids=["ghost", "hutao"])
check("绑到已删角色 → 退回全局", rt.char_for("group:9") == rt._character)
chat_char.set_binding(STATE, "group:9", "hutao", valid_ids=["klee", "hutao"])

check("全局角色的 SOUL 用构造时给的那份（自定义部署不被顶掉）",
      rt.soul_path_for(rt._character) == rt.soul_path)
check("绑定角色的 SOUL 走角色库", rt.soul_path_for("hutao") == store.soul_path("hutao"))

tok = chat_char.push("hutao")
check("处理中 active_character 是绑定的角色", rt.active_character == "hutao")
check("active_character_info 也跟着变", rt.active_character_info.get("name") == "胡桃")
soul = rt._persona()
check("人格是胡桃的 SOUL", "胡桃" in soul and "可莉" not in soul[:200], soul[:60])
chat_char.pop(tok)
check("退出后回到全局角色", rt.active_character == rt._character)
# 缓存 key 必须带路径：两个角色的 SOUL 如果 mtime 一样（复制出来的文件），
# 只比 mtime 会把上一个人的 SOUL 喂给这个人
_cache_key = (rt._persona_cache.get("key") or ("", 0))[0]
check("人格缓存 key 带路径（mtime 撞了也不会串）",
      _cache_key.endswith("SOUL.md") and "/hutao/" in _cache_key, _cache_key)

# 真走一遍 handle_event：系统提示词里的人得是胡桃
chat_char.set_binding(STATE, "private:1000000001", "hutao", valid_ids=["klee", "hutao"])


def ev(text, uid="1000000001"):
    return {"post_type": "message", "message_type": "private", "self_id": "1000000002",
            "user_id": uid, "message_id": 1,
            "message": [{"type": "text", "data": {"text": text}}],
            "sender": {"user_id": uid, "nickname": "主人", "role": "owner"}}


async def run_msg():
    rt.loop = asyncio.get_running_loop()
    rt.loop_thread = threading.current_thread()
    rt.ob = FakeOB()
    ms.msgs.clear()
    await rt.handle_event(ev("在吗"))
    return ms.msgs[-1] if ms.msgs else []


try:
    msgs = asyncio.run(run_msg())
except Exception as e:                                       # noqa: BLE001
    msgs = []
    print("      （模型层没跑通，只验结构）:", type(e).__name__, e)
sys_txt = "\n".join(str(m.get("content") or "") for m in msgs if m.get("role") == "system")
check("私聊绑定胡桃 → 系统提示词里是胡桃", "胡桃" in sys_txt, sys_txt[:80])
check("历史按「角色:会话」隔离", any(str(k).startswith("hutao:") for k in rt.hist),
      str(list(rt.hist)[:3]))
check("同一会话再进来还是胡桃", rt.char_for("private:1000000001") == "hutao")


# 整条链路：群里发 .角色 胡桃 → 真的绑上，而且回一句话
GRP = "group:777"


def gev(text, uid="1000000001", role="admin"):
    return {"post_type": "message", "message_type": "group", "self_id": "1000000002",
            "group_id": "777", "user_id": uid, "message_id": 2,
            "message": [{"type": "text", "data": {"text": text}}],
            "sender": {"user_id": uid, "nickname": "主人", "role": role}}


async def run_group(text, uid="1000000001", role="admin"):
    rt.ob = FakeOB()
    ms.msgs.clear()
    rt.mgr.cooldowns.clear()          # 命令有 3 秒冷却：连发两条会被静默跳过
    await rt.handle_event(gev(text, uid=uid, role=role))
    return list(rt.ob.calls)


calls = asyncio.run(run_group(".角色 胡桃"))
check("群里发 .角色 胡桃 → 绑上了", rt.char_for(GRP) == "hutao",
      str(chat_char.load(STATE)))
check("并且回了一句话", bool(calls) and "胡桃" in "".join(calls), str(calls)[:120])

calls = asyncio.run(run_group(".角色"))
check(".角色 查状态会说固定为胡桃", any("固定" in c and "胡桃" in c for c in calls), str(calls)[:120])

ms.msgs.clear()
rt.ob = FakeOB()
rt.group_policy = "open"          # 群默认是「被 @ 才回」，这里要看模型实际收到什么
asyncio.run(rt.handle_event(gev("在吗", uid="1000000009", role="member")))
rt.group_policy = "mention"
sys_txt2 = "\n".join(str(m.get("content") or "") for m in (ms.msgs[-1] if ms.msgs else [])
                     if m.get("role") == "system")
check("同一个群下一条消息直接是胡桃的人格", "胡桃" in sys_txt2, sys_txt2[:80])

calls = asyncio.run(run_group(".角色 默认"))
check("群里发 .角色 默认 → 解绑", rt.char_for(GRP) == rt._character, str(calls)[:80])

calls = asyncio.run(run_group(".角色 胡桃", uid="1000000009", role="member"))
check("普通成员改不了（连命令都不执行）",
      not calls and rt.char_for(GRP) == rt._character, str(calls)[:80])


# ============ 3) 分发：同名命令按角色挑 ============
print("[3] 分发")
FEAT = TMP / "feat"
FEAT.mkdir()

KLEE_PLUGIN = '''
NAME = "同名可莉"
CHARACTERS = ("klee",)
def register(api):
    async def h(ctx):
        return "klee 版"
    async def hook(ctx):
        return "HOOK-K"
    api.on_command("同名", h, help=".同名 —— 可莉版")
    api.on_message(hook)
'''
HUTAO_PLUGIN = '''
NAME = "同名胡桃"
CHARACTERS = ("hutao",)
def register(api):
    async def h(ctx):
        # 把 ctx.character 带回去：技能通路必须把「此刻的角色」放进上下文
        return "hutao 版|" + str(getattr(ctx, "character", ""))
    async def hook(ctx):
        return "HOOK-H"
    api.on_command("同名", h, aliases=("拥抱",), help=".同名 —— 胡桃版")
    api.on_message(hook)
'''
GENERAL_PLUGIN = '''
NAME = "通用"
def register(api):
    async def h(ctx):
        return "通用版"
    async def alias(ctx):
        return "抱抱别名"
    async def who(ctx):
        # 通用插件问「我是谁」= manager 此刻的角色（走 ContextVar）
        return "who:" + api.character_id
    api.on_command("通用命令", h, help=".通用命令 —— 谁都能用")
    api.on_command("谁的", who, help=".谁的 —— 看此刻是哪个角色")
    api.on_command("摸摸头", alias, aliases=("抱抱",), help=".摸摸头 —— 通用")
'''
(FEAT / "k.py").write_text(KLEE_PLUGIN, encoding="utf-8")
(FEAT / "h.py").write_text(HUTAO_PLUGIN, encoding="utf-8")
(FEAT / "g.py").write_text(GENERAL_PLUGIN, encoding="utf-8")


class FakeAdapter:
    def __init__(self, cid):
        self.features_dir = str(FEAT)
        self.feature_prefix = "."
        self.active_character = cid
        self.active_character_info = {}
        self.owner_id = "42"
        self.state_dir = str(STATE)


fm = FeatureManager(FakeAdapter("klee"))
fm.active_character = "klee"
fm.load()
check("三个插件都装上了", len(fm.plugins) == 3 and not any(
    p.get("error") for p in fm.plugins.values()), str(fm.plugins.keys()))
check("非当前角色的限定插件也注册了命令",
      "同名" in fm.commands and fm.commands["同名"].plugin in ("h", "k"),
      str(getattr(fm.commands.get("同名"), "plugin", None)))
check("两个插件都没有报错", all(not p.get("error") for p in fm.plugins.values()))
check("同名两条都在表里", len(fm._cmd_all.get("同名") or []) == 2, str(fm._cmd_all.get("同名")))


def ctx_for(text, char, role="member"):
    return FeatureContext(adapter=FakeAdapter("klee"), text=text, is_group=True,
                          group_id="1", user_id="7", user_name="t", message_id="",
                          role=role, character=char)


v1 = fm.explain_command(ctx_for(".同名", "klee"))
v2 = fm.explain_command(ctx_for(".同名", "hutao"))
v3 = fm.explain_command(ctx_for(".同名", "atri"))
check("klee 会话挑可莉那条", v1["action"] == "run" and v1["cmd"].plugin == "k")
check("hutao 会话挑胡桃那条", v2["action"] == "run" and v2["cmd"].plugin == "h")
check("别的角色 → 当作没有这条命令（继续聊天）",
      v3["action"] == "none" and v3["kind"] == "character", str(v3))

check("通用命令谁都能用", fm.explain_command(ctx_for(".通用命令", "hutao"))["action"] == "run")
check("撞名命令的**其它别名**照常可用（.拥抱 只有一条）",
      fm.explain_command(ctx_for(".拥抱", "hutao"))["action"] == "run")
check("撞名命令的其它别名不会被别的角色抢走",
      fm.explain_command(ctx_for(".拥抱", "klee"))["action"] == "none")
check("通用别名不被角色挡住", fm.explain_command(ctx_for(".抱抱", "atri"))["action"] == "run")

from bot.framework import FeatureAPI                            # noqa: E402

api = FeatureAPI(fm, "g", {})
h1 = api.commands_help("klee")
h2 = api.commands_help("hutao")
check("help 里只有本角色那条（可莉）", any("可莉版" in x for x in h1) and
      not any("胡桃版" in x for x in h1), str(h1))
check("help 里只有本角色那条（胡桃）", any("胡桃版" in x for x in h2) and
      not any("可莉版" in x for x in h2), str(h2))
check("help 不会把同名命令列两遍", sum(1 for x in h1 if "同名" in x) == 1)


async def hook_test():
    c1 = ctx_for("hi", "klee")
    await fm.run_message_hooks(c1)
    c2 = ctx_for("hi", "hutao")
    await fm.run_message_hooks(c2)
    return c1.text, c2.text


t1, t2 = asyncio.run(hook_test())
check("入站钩子按角色跑（可莉）", t1 == "HOOK-K", t1)
check("入站钩子按角色跑（胡桃）", t2 == "HOOK-H", t2)


# ============ 3b) 技能通路也要按角色门控 ============
print("[3b] 技能通路")
from bot.skills import SkillHub                             # noqa: E402

hub = SkillHub(fm)
seen_roles = []


def skills_for(char):
    tok = chat_char.push(char)
    try:
        return [x["name"] for x in hub.skills()]
    finally:
        chat_char.pop(tok)


k_skills, h_skills, a_skills = skills_for("klee"), skills_for("hutao"), skills_for("atri")
check("可莉看到自己的限定插件", "k" in k_skills and "h" not in k_skills, str(k_skills))
check("胡桃看到自己的限定插件", "h" in h_skills and "k" not in h_skills, str(h_skills))
check("两个角色都能看到通用插件", "g" in k_skills and "g" in h_skills, str((k_skills, h_skills)))
check("没有对应角色的会话看不到任何一个", "k" not in a_skills and "h" not in a_skills,
      str(a_skills))


def run_skill(char, name, text):
    tok = chat_char.push(char)
    try:
        return hub.run(name, text, adapter=FakeAdapter(char), is_group=True, group_id="1",
                       user_id="42", user_name="t", role="admin")
    finally:
        chat_char.pop(tok)


r1 = run_skill("klee", "同名", "x")
r2 = run_skill("hutao", "同名", "x")
check("可莉的会话跑到可莉那条", r1.get("ok") and "klee" in str(r1.get("text") or ""), str(r1)[:120])
check("胡桃的会话跑到胡桃那条", r2.get("ok") and "hutao" in str(r2.get("text") or ""), str(r2)[:120])
check("技能上下文的 ctx.character 就是本会话的角色",
      "|hutao" in str(r2.get("text") or ""), str(r2.get("text"))[:80])

# 技能是在**新线程**里跑的：插件侧读「我是谁」也必须还是这个会话的角色
w1 = run_skill("hutao", "谁的", "谁的")
w2 = run_skill("klee", "谁的", "谁的")
check("技能线程里 api.character_id 跟着会话走（胡桃）",
      "who:hutao" in str(w1.get("text") or ""), str(w1.get("text"))[:80])
check("技能线程里 api.character_id 跟着会话走（可莉）",
      "who:klee" in str(w2.get("text") or ""), str(w2.get("text"))[:80])

# 面板试跑：HTTP 线程里没有 ContextVar，靠 run(character=…) 显式传
noctx = hub.run("谁的", "谁的", adapter=FakeAdapter("klee"), is_group=False, group_id="",
                user_id="panel", user_name="主人", role="owner", character="hutao")
check("显式 character 覆盖空上下文（面板试跑）",
      "who:hutao" in str(noctx.get("text") or ""), str(noctx.get("text"))[:80])
noctx2 = hub.run("同名", "同名", adapter=FakeAdapter("klee"), is_group=False, group_id="",
                 user_id="panel", user_name="主人", role="owner", character="klee")
check("显式 character 也决定同名命令挑哪条",
      "klee" in str(noctx2.get("text") or ""), str(noctx2.get("text"))[:80])

# 索引缓存必须按角色分开：先问一个角色，再问另一个，不能拿到上一个的表
sr1 = hub.search("同名")
sr2_tok = chat_char.push("hutao")
try:
    sr2 = hub.search("同名")
finally:
    chat_char.pop(sr2_tok)
check("技能检索不串角色",
      any(x[1]["name"] == "k" for x in sr1) and any(x[1]["name"] == "h" for x in sr2),
      str([(round(x[0], 2), x[1]["name"]) for x in sr1])[:100])
r4 = run_skill("atri", "hutao_only_nonexistent", "x")
check("不存在的技能仍是普通错误", r4.get("ok") is False and "没有技能" in str(r4.get("error")), str(r4)[:100])

# ============ 4) 命令插件 chatrole ============
print("[4] .角色 命令")
ONLY = TMP / "feat2"
ONLY.mkdir()
shutil.copy(ROOT / "bot" / "features" / "chatrole.py", ONLY / "chatrole.py")
store3 = CharacterStore(TMP / "state3", seeds_dir=ROOT / "bot" / "character_seeds")


class Adapter3:
    def __init__(self):
        self.features_dir = str(ONLY)
        self.feature_prefix = "."
        self.active_character = "klee"
        self.active_character_info = {}
        self.owner_id = "42"
        self.state_dir = str(TMP / "state3")
        self.characters = store3


fm3 = FeatureManager(Adapter3())
fm3.load()
check("chatrole 装上了", "chatrole" in fm3.plugins and not fm3.plugins["chatrole"]["error"],
      str(fm3.plugins.get("chatrole")))
check("注册了 .角色", "角色" in fm3.commands)
check("别名也在（本群角色/char）", "本群角色" in fm3.commands and "char" in fm3.commands)


class C3:
    def __init__(self, args, role="admin", chat="group:555"):
        self.args = list(args)
        self.is_group = True
        self.group_id = "555"
        self.user_id = "42"
        self.user_name = "主人"
        self.message_id = ""
        self.role = role
        self.character = "klee"
        self.chat_id = chat
        self.arg_text = " ".join(args)
        self.out = []

    async def reply(self, text, **kw):
        self.out.append(str(text))


cmd = fm3.commands["角色"]


def say(args, **kw):
    """跑一次命令，拿它给人的那句话（handler 返回字符串，分发层负责回复）。"""
    c = C3(args, **kw)
    out = asyncio.run(cmd.handler(c))
    return str(out or (c.out[-1] if c.out else ""))


check("空参数 → 说还没固定", "还没固定" in say([]))
check("「.角色 胡桃」写得进去",
      "胡桃" in say(["胡桃"]) and chat_char.get(TMP / "state3", "group:555") == "hutao")
check("再看一眼显示「固定为胡桃」", "固定为「胡桃」" in say([]), say([]))
check("英文名也能用（Hu Tao）",
      "胡桃" in say(["Hu", "Tao"]) and chat_char.get(TMP / "state3", "group:555") == "hutao",
      say([]))
check("前缀匹配（亚 → 亚托莉）", "亚托莉" in say(["亚"]) and
      chat_char.get(TMP / "state3", "group:555") == "atri", say([]))
check("没这个角色 → 列出现有的", "没有叫" in say(["不存在"]))
check("「.角色 默认」取消绑定",
      "取消" in say(["默认"]) and chat_char.get(TMP / "state3", "group:555") == "")
check("本来没绑 → 说明跟着全局", "本来就没固定" in say(["默认"]))

vm = fm3.explain_command(FeatureContext(adapter=Adapter3(), text=".角色 胡桃", is_group=True,
                                        group_id="1", user_id="9", user_name="m",
                                        message_id="", role="member"))
check("普通群成员用不了（只有主人/群管能改整个群的角色）",
      vm["action"] == "skip" and vm["kind"] == "permission", str(vm))
vo = fm3.explain_command(FeatureContext(adapter=Adapter3(), text=".角色 胡桃", is_group=False,
                                        group_id="", user_id="42", user_name="o",
                                        message_id=""))
check("主人（私聊）能用", vo["action"] == "run", str(vo))


# ============ 5) 面板接口 ============
print("[5] 面板接口")
import server as SV                                          # noqa: E402

SRV = TMP / "srv"
SRV.mkdir()
SV.BOT = None
SV.STATE_DIR = SRV
SV.MSG_JSONL = SRV / "messages.jsonl"
SV._MSG_ROWS_CACHE.update(sig=None, ts=0.0, rows=[])
SV.MSG_JSONL.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in [
    {"ts": 3.0, "chat_id": "group:555", "role": "user", "user_id": "9", "name": "小明",
     "text": "在吗"},
    {"ts": 4.0, "chat_id": "group:555", "role": "assistant", "text": "在的"},
    {"ts": 5.0, "chat_id": "private:9", "role": "user", "user_id": "9", "name": "小明",
     "text": "悄悄话"},
]) + "\n", encoding="utf-8")
chat_char.set_binding(SRV, "group:555", "hutao", by="面板", valid_ids=["klee", "hutao"])
chat_char.set_binding(SRV, "group:777", "ghost", by="面板", valid_ids=["ghost"])

v = SV._char_bind_view()
check("接口 ok", v.get("ok") is True, str(v)[:120])
check("全局角色带回来了", bool(v.get("global")))
check("角色清单有 6 个", len(v.get("characters") or []) == 6, str(len(v.get("characters") or [])))
bound = {b["chat"]: b for b in v.get("bindings") or []}
check("绑定列出来了", bound.get("group:555", {}).get("id") == "hutao", str(bound.keys()))
check("名字不是 id（hutao → 胡桃）", bound.get("group:555", {}).get("name") == "胡桃")
check("带上了最近说话的人", bound.get("group:555", {}).get("who") == "小明")
check("指向已删角色的标成 stale", "group:777" in (v.get("stale") or []), str(v.get("stale")))
check("已绑定的会话不进下拉候选",
      all(c["chat"] != "group:555" for c in (v.get("chats") or [])), str(v.get("chats")))

labels = {x["chat"]: x for x in SV._chat_labels()}
check("会话条数含她的回复（2 行）", labels.get("group:555", {}).get("n") == 2,
      str(labels.get("group:555")))
check("最近的说话人对得上", labels.get("private:9", {}).get("who") == "小明")

# 面板 HTML 的锚点（改坏了前端就静默失效，这里钉住）
HTML = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
for anchor in ('id="ccCard"', 'id="ccList"', 'id="ccChat"', 'id="ccChar"',
               "function ccRender", "async function ccSet", "async function ccAdd",
               'onclick="ccAdd()"', "/api/characters/bindings", "/api/characters/bind"):
    check(f"面板有 {anchor}", anchor in HTML)

print(f"\nPASS {PASS} / {PASS + FAIL}")
sys.exit(0 if FAIL == 0 else 1)
