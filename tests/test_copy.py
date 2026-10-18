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
    for ln, line in user_copy(HTML):
        if re.search(pat, line):
            # style/class/title 是样式与悬停原文，不算给用户看的文案
            seg = line
            for attr in ("style", "class", "title", "id"):
                seg = re.sub(rf'{attr}="[^"]*"', "", seg)
            if re.search(pat, seg):
                bad.append(f"L{ln} {why}: {line.strip()[:80]}")
check("用户文案里没有内部实现词", not bad, "\n      ".join(bad[:5]))

# ---------- 2) 工具名都要有中文标签 ----------
agent = (ROOT / "bot" / "agent.py").read_text(encoding="utf-8")
tools = re.search(r"ALL_TOOL_NAMES\s*=\s*list\(([^)]+)\)", agent)
names = []
if tools:
    m = re.search(r"ALL_TOOL_NAMES\s*=\s*list\(DEFAULT_TOOLS\)", agent)
    block = re.search(r"DEFAULT_TOOLS\s*=\s*\[([^\]]+)\]", agent)
    if block:
        names = re.findall(r'"([a-z_]+)"', block.group(1))
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
check("日志条数用「条」", "更早 " in HTML)

print(f"\nPASS {PASS} / {PASS + FAIL}")
sys.exit(1 if FAIL else 0)
