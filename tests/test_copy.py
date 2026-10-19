"""面板文案体检：不许把内部实现词露给用户。

这一类问题不会报错、也不会崩，但用户一眼就看出「这是给开发者看的」：
  - 内部枚举：waiting_qrcode / panel-bot
  - CSS 变量名：--red / --gold
  - 配置键名：settings.bad_words
  - 文件路径：state/bot_config.json
  - 中英混排：开 / true、120s
  - 泛用插件里写死某个角色名（切到别的角色就穿帮）

这里把规则钉死，改文案时跑一遍就知道有没有回退。
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HTML = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
# 插件页 iframe 的基线样式是 JS 模板字符串里的一段 CSS（FRAME_BASE），
# 里面当然全是 var(--x)。它不面向用户，扫文案前先摘掉。
COPY_SRC = re.sub(r"const FRAME_BASE = `.*?`;", "", HTML, flags=re.S)
SERVER = (ROOT / "server.py").read_text(encoding="utf-8")

PASS = FAIL = 0


def check(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}  {detail}")


# ---------- 1) 内部词不许出现在用户可见的字符串里 ----------
# 只看 <script> 块，并且跳过注释与纯代码行（CSS 里的 --red 是正当用法）
def user_copy(html: str) -> list[tuple[int, str]]:
    rows = []
    in_script = False
    in_block_comment = False
    for i, line in enumerate(html.splitlines(), 1):
        t = line.strip()
        if "<script" in line:
            in_script = True
            continue
        if "</script" in line:
            in_script = False
            continue
        if not in_script:
            continue
        if in_block_comment:
            if "*/" in t:
                in_block_comment = False
            continue
        if t.startswith("/*"):
            in_block_comment = "*/" not in t
            continue
        if not t or t.startswith("//") or t.startswith("*"):
            continue
        if re.match(r"^[a-zA-Z_$][\w$.]*\s*[=:(\[]", t):       # 纯赋值/函数调用行
            continue
        if t.startswith("const ") or t.startswith("let ") or t.startswith("function "):
            continue
        # 纯字符串数组的续行（比如 FRAME_VARS 里的 "--paper","--ink"）：是代码不是文案
        if re.fullmatch(r'"[^"]*"(\s*,\s*"[^"]*")*\s*,?\s*;?', t):
            continue
        rows.append((i, line))
    return rows


BANNED = [
    (r"waiting_qrcode|reconnecting\"|\bpanel-bot\b", "内部枚举值"),
    (r"--red\b|--gold\b|--ui-accent", "CSS 变量名"),
    (r"settings\.[a-z_]+", "配置键路径"),
    (r"state/[a-z_]+\.json", "服务器文件路径"),
    (r"开 / true|关 / false", "中英混排布尔"),
    (r"\b\d+s\b(?![）)])", "秒数用了裸 s"),
]
bad: list[str] = []
for pat, why in BANNED:
    for ln, line in user_copy(COPY_SRC):
        if re.search(pat, line):
            # style/class/title 是样式与悬停原文，不算给用户看的文案
            seg = line
            for attr in ("style", "class", "title", "id"):
                seg = re.sub(rf'{attr}="[^"]*"', "", seg)
            if re.search(pat, seg):
                bad.append(f"L{ln} {why}: {line.strip()[:80]}")
check("用户文案里没有内部实现词", not bad, "\n      ".join(bad[:5]))

# ---------- 2) 工具名都要有中文标签 ----------
# 工具清单的权威定义搬到 bot/toolbox.py（工具即插件），这里跟着改
toolbox = (ROOT / "bot" / "toolbox.py").read_text(encoding="utf-8")
block = re.search(r"DEFAULT_TOOLS[^=]*=\s*\[([^\]]+)\]", toolbox, re.S)
names = re.findall(r'"([a-z_]+)"', block.group(1)) if block else []
labels = dict(re.findall(r'"([a-z_]+)":\s*"([^"]+)"', SERVER.split("_TOOL_LABELS = {")[1].split("}")[0])) \
    if "_TOOL_LABELS = {" in SERVER else {}
missing = [n for n in names if n not in labels]
check(f"{len(names)} 个工具都有中文名", bool(names) and not missing, str(missing))
check("工具中文名不是英文原样", all(not re.fullmatch(r"[a-z_ ]+", v) for v in labels.values()),
      str([v for v in labels.values() if re.fullmatch(r"[a-z_ ]+", v)]))

# ---------- 3) NapCat 阶段枚举都有中文 ----------
phases = re.findall(r"(waiting_qrcode|qrcode|ready|reconnecting|offline)", HTML)
block = HTML.split("const PHASE_TEXT = {")[1].split("}")[0] if "const PHASE_TEXT = {" in HTML else ""
unmapped = sorted({p for p in ["waiting_qrcode", "qrcode", "ready", "reconnecting"] if p not in block})
check("NapCat 阶段都有中文映射", not unmapped, str(unmapped))
check("未知阶段不直接吐原值", '"未知阶段"' in HTML)

# ---------- 4) 通用插件不许写死角色名 ----------
CHAR_NAMES = ["可莉", "胡桃", "茜特菈莉", "昔莲", "涂山红红", "亚托莉"]
leaks = []
for f in sorted((ROOT / "bot" / "features").glob("*.py")):
    src = f.read_text(encoding="utf-8")
    if "CHARACTERS" in src.split("def register")[0]:
        continue                                    # 角色限定插件，写死是对的
    for ln, line in enumerate(src.splitlines(), 1):
        if not re.match(r"^(NAME|TITLE|DESCRIPTION|USAGE|HELP)\s*=", line.strip()):
            continue
        for c in CHAR_NAMES:
            if c in line:
                leaks.append(f"{f.name}:L{ln} {c}")
check("通用插件的标题/说明不写死角色名", not leaks, str(leaks[:5]))

# ---------- 5) 配置项文案不许出现路径/键名 ----------
cfg = HTML.split("const CFG_GROUPS = [")[1].split("const CFG_FIELDS")[0]
cfg_bad = re.findall(r'(?:label|hint|note|ph):"[^"]*(?:state/|settings\.|\.json|--[a-z]{3,})', cfg)
check("配置项标签不露路径/键名", not cfg_bad, str(cfg_bad[:3]))

# ---------- 6) 尺寸单位统一（秒不写 s） ----------
check("时长统一用「秒」", "近 60s 消息" not in HTML and "上次事件 " not in HTML)

# ---------- 7) 适配器能力名要给人话，别把内部键印在面板上 ----------
check("能力键有中文表", "CAP_CN" in HTML and "读群成员" in HTML)
_BT = chr(96)
RAW_CAP = "marks.map(k => " + _BT + '<span class="tag">${esc(k)}</span>' + _BT + ")"
check("能力不从 cap 键直接渲染", RAW_CAP not in HTML)
# 告警流水（「更早 N 条」）现在住在 onebot 插件页里，两个面都要查
ONE_BOT_PAGE = (ROOT / "bot" / "adapters" / "onebot.py").read_text(encoding="utf-8")
check("日志条数用「条」", ("更早 " in HTML) or ("更早 " in ONE_BOT_PAGE))

print(f"\nPASS {PASS} / {PASS + FAIL}")
sys.exit(1 if FAIL else 0)
