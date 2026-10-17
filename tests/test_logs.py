"""日志保留：裁剪函数 + 开关语义（不碰生产 state）。"""
import json
import sys
import tempfile
import time
from pathlib import Path

STUDIO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(STUDIO))

import server  # noqa: E402

PASS = FAIL = 0


def check(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name} {detail}")


tmp = Path(tempfile.mkdtemp(prefix="logtest-"))

# ---- JSONL 裁剪 ----
p = tmp / "x.jsonl"
now = time.time()
rows = [
    {"ts": now - 40 * 86400, "text": "old"},
    {"ts": now - 2 * 86400, "text": "recent"},
    {"ts": now, "text": "now"},
    {"text": "no-ts"},                       # 没有 ts：保留
]
p.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\nnot json\n",
             encoding="utf-8")
gone = server._prune_jsonl(p, now - 30 * 86400)
left = p.read_text(encoding="utf-8").splitlines()
check("删掉 1 条过期", gone == 1, str(gone))
check("保留近期/无 ts/坏行", len(left) == 4, str(left))
check("近期那条还在", any("recent" in ln for ln in left))

# 没有过期行时不重写文件
mtime = p.stat().st_mtime_ns
check("无过期 → 不动文件", server._prune_jsonl(p, now - 365 * 86400) == 0
      and p.stat().st_mtime_ns == mtime)

# ---- 文本日志裁剪 ----
log = tmp / "bot.log"
cut_day = time.strftime("%Y-%m-%d", time.localtime(now - 3 * 86400))
old_day = time.strftime("%Y-%m-%d", time.localtime(now - 10 * 86400))
log.write_text(f"{old_day} 01:00:00,000 INFO x: old\n"
               f"{cut_day} 02:00:00,000 INFO x: edge\n"
               f"{time.strftime('%Y-%m-%d', time.localtime(now))} 03:00:00,000 INFO x: new\n"
               "Traceback (most recent call last):\n", encoding="utf-8")
gone = server._prune_text_log(log, cut_day)
body = log.read_text(encoding="utf-8")
check("文本日志删 1 行", gone == 1, str(gone))
check("边界当天保留", "edge" in body)
check("新行保留", "new" in body)
check("无日期的续行保留", "Traceback" in body)

# ---- 开关语义 ----
enabled, days = server._log_cfg()
check("默认开日志 + 30 天", enabled is True and days == 30, f"{enabled},{days}")

# ---- 关闭日志：/api/logs 返回提示文字 ----
class _FakeBot:
    def __init__(self, cfg):
        self.config = cfg

_orig = server.BOT
server.BOT = _FakeBot({"log_enabled": False, "log_retention_days": 30})
check("关闭时 _tail_log 给出提示", "已关闭" in server._tail_log(5, "")[0])

# ---- 0 天 = 永不清理 ----
server.BOT = _FakeBot({"log_enabled": True, "log_retention_days": 0})
check("0 天 = 不清理", server._log_janitor_once()["pruned"] == {})

# ---- 恢复默认配置时读回真文件 ----
server.BOT = _orig
check("_tail_log 有内容", bool(server._tail_log(5, "")), "")

print(f"\nPASS {PASS} / {PASS + FAIL}")
sys.exit(1 if FAIL else 0)
