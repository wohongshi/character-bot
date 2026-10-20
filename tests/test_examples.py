"""说话样例：存储边界 + 注入提示词 + 命令 + 面板接口。

跑法：`python3 tests/test_examples.py`（不联网、不碰生产 state）
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TMP = Path(tempfile.mkdtemp(prefix="examples-"))
os.environ["KLEE_STATE_DIR"] = str(TMP / "ps")
sys.path.insert(0, str(ROOT))

from bot import examples as ex                             # noqa: E402
from bot.characters import CharacterStore                  # noqa: E402

PASS = FAIL = 0


def check(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}  {detail}")


S = TMP / "state"
S.mkdir()

print("[1] 存储")
check("没有文件 → 空列表", ex.load(S, "klee") == [])
check("没有样例 → 注入为空串", ex.render(S, "klee") == "")

items = ex.save(S, "klee", [{"user": "你好", "bot": "哒哒哒！"}])
check("写进去读得出来", ex.load(S, "klee") == [{"user": "你好", "bot": "哒哒哒！"}])
check("落盘是个文件", (S / "characters" / "klee" / "examples.json").is_file())

check("空条被丢掉", ex.save(S, "klee", [{"user": "", "bot": "x"}, {"user": "a", "bot": " "}]) == [])
check("不是 dict 的条目被丢掉", ex.save(S, "klee", ["x", 5, None, {"user": "a", "bot": "b"}])
      == [{"user": "a", "bot": "b"}])
check("超长会被截断",
      len(ex.save(S, "klee", [{"user": "字" * 500, "bot": "答" * 500}])[0]["user"]) == ex.MAX_USER)
check("最多 8 条",
      len(ex.save(S, "klee", [{"user": f"u{i}", "bot": f"b{i}"} for i in range(20)]))
      == ex.MAX_ITEMS)

(S / "characters" / "klee" / "examples.json").write_text("{坏 json", encoding="utf-8")
check("坏 JSON → 空（不抛）", ex.load(S, "klee") == [])
(S / "characters" / "klee" / "examples.json").write_text('{"items": 5}', encoding="utf-8")
check("items 不是数组 → 空", ex.load(S, "klee") == [])
(S / "characters" / "klee" / "examples.json").write_text('{"items":[{"user":"a","bot":"b"}]}',
                                                        encoding="utf-8")
check("正常文件读得回来", ex.load(S, "klee") == [{"user": "a", "bot": "b"}])

check("非法角色 id → 空", ex.load(S, "../etc") == [])
try:
    ex.save(S, "../etc", [{"user": "a", "bot": "b"}])
    check("非法角色 id 保存被拒", False)
except ValueError:
    check("非法角色 id 保存被拒", True)

print("[2] 增删")
ex.save(S, "hutao", [])
ex.add(S, "hutao", "在吗", "哟，客官～")
ex.add(S, "hutao", "今天忙吗", "忙呀，往生堂的生意可好了")
check("加两条", len(ex.load(S, "hutao")) == 2)
try:
    ex.add(S, "hutao", "", "x")
    check("空内容被拒", False)
except ValueError:
    check("空内容被拒", True)
ex.remove(S, "hutao", 1)
check("按 1 起的位置删", [i["user"] for i in ex.load(S, "hutao")] == ["今天忙吗"])
try:
    ex.remove(S, "hutao", 9)
    check("越界删除被拒", False)
except ValueError as e:
    check("越界删除被拒", "没有第 9 条" in str(e), str(e))
ex.save(S, "full", [{"user": f"u{i}", "bot": f"b{i}"} for i in range(ex.MAX_ITEMS)])
check("先塞满 8 条", len(ex.load(S, "full")) == ex.MAX_ITEMS, str(len(ex.load(S, "full"))))
try:
    ex.add(S, "full", "再来", "一条")
    check("满 8 条 → 拒绝（不是悄悄丢一条）", False)
except ValueError as e:
    check("满 8 条 → 拒绝（不是悄悄丢一条）", "先删一条" in str(e), str(e))

print("[3] 注入文本")
text = ex.render(S, "hutao")
check("有标题句", text.startswith("【说话样例"), text[:40])
check("带「照这个语气、不要照抄」", "照这个语气" in text and "不要照抄" in text)
check("一行一个来回", text.count("用户：") == 1 and text.count("你：") == 1, text)
big = ex.save(S, "big", [{"user": "很长" * 100, "bot": "也很长" * 100} for _ in range(8)])
check("整块不超过上限", len(ex.render(S, "big")) <= ex.MAX_CHARS,
      str(len(ex.render(S, "big"))))
check("超上限时按整条截断（不留半个来回）",
      ex.render(S, "big").count("用户：") == ex.render(S, "big").count("你："))

print("[4] 提示词里真的注入了")
from bot.llm import ModelStore                              # noqa: E402
from bot.runtime import BotRuntime                          # noqa: E402
from bot.service import DEFAULT_CONFIG                      # noqa: E402
from bot.framework import FeatureContext                    # noqa: E402

ST = TMP / "state2"
ST.mkdir()
store = CharacterStore(ST, seeds_dir=ROOT / "bot" / "character_seeds")
cfg = dict(DEFAULT_CONFIG)
cfg.update({"owner_id": "1", "self_id": "2", "kb_enabled": False, "agent_enabled": False})
rt = BotRuntime(features_dir=ROOT / "bot" / "features", config=cfg,
                models=ModelStore(ROOT / "state" / "models.json"),
                soul_path=store.soul_path("klee"), state_dir=ST, characters=store)
rt.active_character_info  # 触发属性
ex.save(ST, "klee", [{"user": "你好呀", "bot": "哒哒哒！可莉来啦"}])
ctx = FeatureContext(adapter=type("A", (), {"state_dir": str(ST)})(), text="嗨",
                     is_group=False, group_id="", user_id="1", user_name="t",
                     message_id="", character="klee")
blocks = rt._compose_blocks(ctx, "嗨", capture=False)
keys = [b["key"] for b in blocks]
check("有 examples 块", "examples" in keys, str(keys))
idx = keys.index("examples")
check("挨着人格（在大段资料之前）", idx <= 4, str(keys))
body = blocks[idx]["text"]
check("样例内容在块里", "你好呀" in body and "哒哒哒" in body, body[:60])
check("examples 属于 system 消息", blocks[idx]["role"] == "system")

ex.save(ST, "klee", [])
blocks2 = rt._compose_blocks(ctx, "嗨", capture=False)
check("清空后不再有 examples 块", "examples" not in [b["key"] for b in blocks2])

ex.save(ST, "hutao", [{"user": "q", "bot": "胡桃答"}])
check("换角色取的是那个角色的样例",
      "胡桃答" in ex.render(ST, "hutao") and "胡桃答" not in ex.render(ST, "klee"))

print("[5] .样例 命令")
FEAT = TMP / "feat"
FEAT.mkdir()
import shutil
shutil.copy(ROOT / "bot" / "features" / "samples.py", FEAT / "samples.py")
from bot.framework import FeatureManager                   # noqa: E402
store3 = CharacterStore(TMP / "state3", seeds_dir=ROOT / "bot" / "character_seeds")


class Ad:
    def __init__(self):
        self.features_dir = str(FEAT)
        self.feature_prefix = "."
        self.active_character = "klee"
        self.active_character_info = {}
        self.owner_id = "42"
        self.state_dir = str(TMP / "state3")
        self.characters = store3


fm = FeatureManager(Ad())
fm.load()
check("samples 装上了", "samples" in fm.plugins and not fm.plugins["samples"].get("error"),
      str(fm.plugins.get("samples")))
check("注册了 .样例", "样例" in fm.commands and "说话样例" in fm.commands)


class C:
    def __init__(self, args, role="owner", uid="42", char="klee"):
        self.args = list(args)
        self.is_group = True
        self.group_id = "1"
        self.user_id = uid
        self.user_name = "主人"
        self.message_id = ""
        self.role = role
        self.character = char
        self.chat_id = "group:1"
        self.arg_text = " ".join(args)
        # 命令从适配器拿 state_dir（真实运行时就是 runtime）
        self.adapter = type("A", (), {"state_dir": str(TMP / "state3")})()

    async def reply(self, text, **kw):
        pass


cmd = fm.commands["样例"]


def say(args, **kw):
    return str(asyncio.run(cmd.handler(C(args, **kw))) or "")


check("空 → 提示还没样例", "还没有说话样例" in say([]), say([]))
_r = say(["加", "你好呀|哟，来啦～"])          # 只调一次：detail 参数里再调一次会加两遍
check("加一条（竖线分隔）", "加好了" in _r, _r)
check("真的落盘了", ex.load(TMP / "state3", "klee") == [{"user": "你好呀", "bot": "哟，来啦～"}],
      str(ex.load(TMP / "state3", "klee")))
say(["加", "在吗|在的"])
out = say([])
check("列表有两条带序号", "1. 用户" in out and "2. 用户" in out, out[:80])
check("全角竖线也认", "加好了" in say(["加", "吃了吗｜吃了"]))
check("缺分隔符 → 说写法", "写法" in say(["加", "只有一句"]))
_d = say(["删", "2"])                    # 同上：只调一次
check("删第 2 条", "删掉了第 2 条" in _d, _d)
check("删完剩两条", len(ex.load(TMP / "state3", "klee")) == 2)
check("删不存在的序号 → 报错", "没删成" in say(["删", "99"]))
check("序号不是数字 → 报错", "不是序号" in say(["删", "abc"]))
check("别名 .说话样例 指向同一条", fm.commands["说话样例"] is cmd)

v = fm.explain_command(FeatureContext(adapter=Ad(), text=".样例", is_group=True, group_id="1",
                                      user_id="9", user_name="m", message_id="", role="member",
                                      character="klee"))
check("普通群成员用不了（只有主人能改人格内容）",
      v["action"] == "skip" and v["kind"] == "permission", str(v))

print("[6] 面板接口")
import server as SV                                        # noqa: E402

SRV = TMP / "srv"
(SRV / "characters").mkdir(parents=True)
SV.BOT = None
SV.STATE_DIR = SRV
res = ex.summary(SRV, "klee")
check("summary 结构", res["ok"] and res["max"] == 8 and "limits" in res, str(res)[:80])
ex.save(SRV, "klee", [{"user": "a", "bot": "b"}])
check("summary 会带条数与字数", ex.summary(SRV, "klee")["count"] == 1 and
      ex.summary(SRV, "klee")["chars"] > 0)

HTML = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
for anchor in ('id="paneEx"', 'id="ptEx"', 'id="exList"', 'id="exUser"', 'id="exBot"',
               "function loadExamples", "function exAdd", "function exDel",
               "/api/characters/examples", 'id="exCount"'):
    check(f"面板有 {anchor}", anchor in HTML)

print(f"\nPASS {PASS} / {PASS + FAIL}")
sys.exit(0 if FAIL == 0 else 1)
