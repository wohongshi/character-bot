"""agent 常开：面板本身就是半个 agent，不该再有开关。

这一套守四件事：
  1. 界面上没有 agent 开关（聊天工具条一个、插件页一个，都拆了）；
  2. 旧配置里 agent_enabled=false 会在加载时被纠回 true 并落盘；
  3. /api/agent 不再接受 enabled（老客户端带 enabled=false 也不生效）；
  4. 网页聊天不再看 job["agent"]，一律走工具循环。
"""
from __future__ import annotations

import json
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bot.service import BotService          # noqa: E402

PASS = FAIL = 0


def check(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}  {detail}")


HTML = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
SERVER = (ROOT / "server.py").read_text(encoding="utf-8")

print("· 界面：没有开关")
check("聊天工具条没有 🧠 Agent 开关", 'id="agentBtn"' not in HTML)
check("插件页没有「启用工具循环」", "agEnabled" not in HTML)
check("不再读写 ks_agent 偏好", 'localStorage.getItem("ks_agent")' not in HTML)
check("保存 Agent 设置不再提交 enabled", "enabled: $(\"agEnabled\")" not in HTML)
check("发消息不再带 agent 字段",
      'agent: chatOpts.agent' not in HTML and "chatOpts.agent" not in HTML)
check("卡片说明改成常开", "常开 · 她自己查资料" in HTML)

print("\n· 后端：谁也不能把它关掉")
check("网页聊天不再读 job['agent']",
      'use_agent = bool(job.get("agent"))' not in SERVER
      and "use_agent = rt is not None" in SERVER)
check("/api/agent 的白名单里没有 enabled",
      '(\"enabled\", bool)' not in SERVER)
check("_agent_get 恒为 True",
      '"enabled": True,' in SERVER and '_agent_get' in SERVER)

print("\n· 迁移：旧配置里的 false 会被纠正")
tmp = Path(tempfile.mkdtemp())
(tmp / "state").mkdir(parents=True)
(tmp / "state" / "bot_config.json").write_text(
    json.dumps({"agent_enabled": False, "system_plugins": ["onebot11"]}),
    encoding="utf-8")
svc = BotService(home=tmp)
check("加载后 agent_enabled 是 True", svc.config.get("agent_enabled") is True,
      str(svc.config.get("agent_enabled")))
saved = json.loads((tmp / "state" / "bot_config.json").read_text(encoding="utf-8"))
check("并且落盘了（不会每次加载都再纠一遍）",
      saved.get("agent_enabled") is True, str(saved.get("agent_enabled")))

print("\n· 默认值")
tmp2 = Path(tempfile.mkdtemp())
(tmp2 / "state").mkdir(parents=True)
svc2 = BotService(home=tmp2)
check("全新配置默认就是开的", svc2.config.get("agent_enabled") is True)

# 运行时仍然读这个键（留一个排障后门），但界面/接口够不到它
RT = (ROOT / "bot" / "runtime.py").read_text(encoding="utf-8")
check("运行时保留排障后门（注释写清为什么）",
      "只作排障用的后门" in RT)

print(f"\nPASS {PASS} / {PASS + FAIL}")
sys.exit(1 if FAIL else 0)
