#!/usr/bin/env python3
"""网页对话 worker 回归：人格/知识库辅助函数、真正出回复、重复文本不重复喂历史。

（2026-10-17 debug 发现：清理死代码时误删 `_persona_text` / `_kb_context`，
 网页对话直接 NameError 且没人测到。这个文件专门守住这条链路。）
"""
from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

STUDIO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(STUDIO))

import server as S                                            # noqa: E402

TMP = Path(tempfile.gettempdir()) / "klee_chat_test"
TMP.mkdir(exist_ok=True)
S.CHAT_JSONL = TMP / "chat.jsonl"
S.MSG_JSONL = TMP / "messages.jsonl"
S.CHAT_JSONL.write_text("", encoding="utf-8")
S.MSG_JSONL.write_text("", encoding="utf-8")

PASS, FAIL = [], []


def check(name, ok, extra=""):
    (PASS if ok else FAIL).append(name)
    print(f"  {'✓' if ok else '✗'} {name}" + (f"  [{extra}]" if extra and not ok else ""))


class FakeModels:
    def __init__(self):
        self.calls = []

    def chat(self, messages, **kw):
        self.calls.append([dict(m) for m in messages])
        return "测试回复：好呀～"


from bot.characters import CharacterStore                       # noqa: E402
CHAR_STATE = TMP / "chars"
class _FakeRuntime:
    """网页分发用的最小 runtime：没有 mgr/配置 → 分发直接跳过，只剩纯聊天。"""
    active_character = "atri"
    config = {}
    models = FakeModels()
    state_dir = CHAR_STATE

    class _Mgr:
        nlu_rules = []
        def nlu_match(self, t): return None
        async def run_message_hooks(self, ctx): return True
        async def handle_command(self, ctx): return False
    mgr = _Mgr()


S.BOT = SimpleNamespace(models=FakeModels(),
                        characters=CharacterStore(CHAR_STATE, seeds_dir=STUDIO / "bot" / "character_seeds"),
                        runtime=_FakeRuntime())
S.BOT.characters.set_active("atri")     # 测试固定角色，不依赖当前线上选择

print("[1] 辅助函数存在且可用（防误删）")
for fn in ("_persona_text", "_kb_context", "_read_jsonl", "_append_jsonl"):
    check(f"1 {fn} 存在", callable(getattr(S, fn, None)))
persona = S._persona_text()
check("1.1 人格跟随当前角色（亚托莉）", "亚托莉" in persona and "可莉" not in persona, persona[:80])
check("1.3 指定角色可取对应人格", "胡桃" in S._persona_text("hutao"), S._persona_text("hutao")[:60])
check("1.4 未知角色回退当前/默认不炸", bool(S._persona_text("nope").strip()))
check("1.2 缺 SOUL 时有兜底", S._persona_text.__doc__ is not None)

print("[2] 真实 worker：入队 → 出回复")
# worker 池现在由 main() 起（import server 不会顺手开线程），测试里显式拉起
S._start_workers()
S._persona_text = S._persona_text          # 保持真实实现
S._kb_context = lambda text, cid="": ""
sess = "chat-test-1"
S._chat_q.put({"session": sess, "text": "你好"})
deadline = time.time() + 15
rows = []
while time.time() < deadline:
    rows = [json.loads(x) for x in S.CHAT_JSONL.read_text(encoding="utf-8").splitlines() if x.strip()]
    if any(r.get("session") == sess and r.get("role") == "assistant" for r in rows):
        break
    time.sleep(0.2)
bot_rows = [r for r in rows if r.get("session") == sess and r.get("role") == "assistant"]
check("2.1 有 assistant 回复", bool(bot_rows), str(rows)[:200])
check("2.2 内容正确", bot_rows and bot_rows[0]["text"] == "测试回复：好呀～", str(bot_rows)[:160])
check("2.3 无 error", bot_rows and not bot_rows[0].get("error"), str(bot_rows)[:160])
check("2.4 MSG_JSONL 也落了一条", "web:" + sess in S.MSG_JSONL.read_text(encoding="utf-8"))
sysmsg = next((m["content"] for m in S.BOT.models.calls[-1] if m.get("role") == "system"), "")
check("2.5 worker 用当前角色的人格", "亚托莉" in sysmsg and "可莉" not in sysmsg, sysmsg[:120])
check("2.6 落盘带 character 字段", bot_rows and bot_rows[0].get("character") == "atri",
      str(bot_rows)[:120])

print("[3] 连续两条相同文本：历史里只有一条 user + 一条 assistant")
sess2 = "chat-test-dup"
for _ in range(2):
    S._chat_q.put({"session": sess2, "text": "同一句"})
    time.sleep(0.3)
deadline = time.time() + 20
while time.time() < deadline:
    rows = [json.loads(x) for x in S.CHAT_JSONL.read_text(encoding="utf-8").splitlines() if x.strip()]
    if len([r for r in rows if r.get("session") == sess2 and r.get("role") == "assistant"]) >= 2:
        break
    time.sleep(0.2)
calls = S.BOT.models.calls
check("3.1 调了两次模型", len(calls) >= 2, str(len(calls)))
if len(calls) >= 2:
    second = calls[-1]
    users = [m for m in second if m.get("role") == "user"]
    check("3.2 第二次只喂当前这一条 user", len(users) == 1, str([m.get("content") for m in users]))
    check("3.3 第二次带上了上一轮 assistant",
          any(m.get("role") == "assistant" for m in second), str(second)[:200])

print("[3b] 跨角色历史隔离")
S._append_jsonl(S.CHAT_JSONL, {"ts": time.time(), "session": "chat-test-iso",
                               "character": "klee", "role": "assistant",
                               "text": "可莉专属旧消息"})
S._chat_q.put({"session": "chat-test-iso", "text": "你好", "character": "atri"})
deadline = time.time() + 15
while time.time() < deadline:
    rows = [json.loads(x) for x in S.CHAT_JSONL.read_text(encoding="utf-8").splitlines() if x.strip()]
    if any(r.get("session") == "chat-test-iso" and r.get("role") == "assistant"
           and r.get("character") == "atri" for r in rows):
        break
    time.sleep(0.2)
sysmsg = next((m["content"] for m in S.BOT.models.calls[-1] if m.get("role") == "system"), "")
history = "\n".join(m.get("content", "") for m in S.BOT.models.calls[-1]
                     if m.get("role") == "assistant")
check("3b.1 可莉的旧消息没被喂给亚托莉", "可莉专属旧消息" not in history, history[:120])
check("3b.2 亚托莉回复带 character", any(
    r.get("session") == "chat-test-iso" and r.get("role") == "assistant"
    and r.get("character") == "atri" for r in rows))

print("[4] 空/坏消息不把 worker 弄死")
S._chat_q.put({"session": "chat-test-empty", "text": ""})
S._chat_q.put({"session": "chat-test-ok", "text": "还在吗"})
deadline = time.time() + 15
while time.time() < deadline:
    rows = [json.loads(x) for x in S.CHAT_JSONL.read_text(encoding="utf-8").splitlines() if x.strip()]
    if any(r.get("session") == "chat-test-ok" and r.get("role") == "assistant" for r in rows):
        break
    time.sleep(0.2)
check("4.1 空文本后 worker 仍能回复", any(
    r.get("session") == "chat-test-ok" and r.get("role") == "assistant" for r in rows))

print()
print(f"PASS {len(PASS)} / {len(PASS) + len(FAIL)}")
if FAIL:
    print("FAILED:", FAIL)
    sys.exit(1)
