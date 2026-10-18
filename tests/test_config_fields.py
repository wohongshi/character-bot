"""常用配置：字段必须真实可写（防「假选项」回归）。

背景：面板「常用配置」曾经有 9 个后端根本不读的字段（ws_url / dm_policy / ...），
而且 `POST /api/config` 的白名单是手写的，和 DEFAULT_CONFIG 一脱节就出现
「改了、点保存、提示 skipped」。这里把三件事钉死：

1. UI 的每个字段都在 DEFAULT_CONFIG 里（否则存了也会被丢）
2. 每个字段都在 CONFIG_KEYS 里（否则 POST 直接 skipped）
3. 字符串 → 布尔/数字 的类型收敛（`"false"` 不能变成 True）
"""
import re
import sys
from pathlib import Path

STUDIO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(STUDIO))

import server  # noqa: E402
from bot.service import DEFAULT_CONFIG, coerce_config  # noqa: E402

PASS = FAIL = 0


def check(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name} {detail}")


html = (STUDIO / "static" / "index.html").read_text(encoding="utf-8")
blk = html[html.index("const CFG_GROUPS"):html.index("const CFG_FIELDS = CFG_GROUPS")]
ui_keys = re.findall(r'\{k:"([a-z0-9_]+)"', blk)

check("UI 至少 20 个配置项", len(ui_keys) >= 20, str(len(ui_keys)))
check("UI 字段无重复", len(ui_keys) == len(set(ui_keys)),
      str([k for k in set(ui_keys) if ui_keys.count(k) > 1]))

not_in_defaults = [k for k in ui_keys if k not in DEFAULT_CONFIG]
check("UI 字段全在 DEFAULT_CONFIG（没有假选项）", not not_in_defaults, str(not_in_defaults))

not_in_whitelist = [k for k in ui_keys if k not in server.CONFIG_KEYS]
check("UI 字段全在 CONFIG_KEYS（保存不会被 skipped）", not not_in_whitelist, str(not_in_whitelist))

# 分组结构
group_ids = re.findall(r'\{id: "([a-z]+)"', blk)
check("分组 ≥5 个", len(group_ids) >= 5, str(group_ids))
check("分组 id 唯一", len(group_ids) == len(set(group_ids)))
check("有折叠开关（details/cfggrp）", 'class="cfggrp"' in html and "ontoggle=\"cfgToggle(" in html)
check("折叠状态持久化", "ks_cfg_open" in html)
check("有空分组搜索", "function cfgFilter(" in html)

# 类型收敛
c = coerce_config({"quote_reply": "false", "chat_enabled": "true",
                   "max_images": "5", "split_soft_max": "120",
                   "kb_threshold": "0.55", "tts_voice": "冰糖"})
check("bool：'false' → False", c["quote_reply"] is False, repr(c["quote_reply"]))
check("bool：'true' → True", c["chat_enabled"] is True, repr(c["chat_enabled"]))
check("int：'5' → 5（int）", c["max_images"] == 5 and isinstance(c["max_images"], int))
check("int：'120' → 120", c["split_soft_max"] == 120)
check("float：'0.55' → 0.55", abs(c["kb_threshold"] - 0.55) < 1e-9)
check("str 原样", c["tts_voice"] == "冰糖")
bad = coerce_config({"max_images": "abc"})
check("坏数字回落到默认值", bad["max_images"] == DEFAULT_CONFIG["max_images"], str(bad))

print(f"\nPASS {PASS} / {PASS + FAIL}")
sys.exit(1 if FAIL else 0)
