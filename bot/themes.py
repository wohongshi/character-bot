"""界面主题插件：每个角色一套 `ui_themes/<id>/theme.json` + 可选 `theme.css`。

「万物皆插件」在这里的落地：
- 主题是**目录插件**，丢一个目录进去就被发现，不需要改前端；
- `theme.json` 只允许声明一组 CSS 变量（白名单）+ 元信息；
- 需要更复杂的样式再放 `theme.css`（只作为 CSS 文本下发，不当 JS 执行）。

服务端按角色生成 CSS：`/api/theme.css?character=klee`。
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

log = logging.getLogger("klee.bot.themes")

THEME_DIR = Path(__file__).with_name("ui_themes")
DEFAULT_THEME = "klee"

# 只允许覆盖这些变量（都是 index.html 里真实使用的）
ALLOWED_TOKENS = {
    "--bg", "--paper", "--ink", "--dim", "--line",
    "--red", "--red-deep", "--red-soft", "--gold", "--gold-soft",
    "--sky", "--sky-soft", "--green", "--green-soft", "--shadow",
    "--font-cute", "--font-body",
}
_BAD_VALUE = re.compile(r"[;{}<>]|url\s*\(", re.I)
_ID_RE = re.compile(r"^[a-z0-9_-]{1,32}$")
MAX_CSS = 24 * 1024


def _clean_value(v) -> str:
    s = str(v or "").strip()
    if not s or len(s) > 300 or _BAD_VALUE.search(s):
        return ""
    return s


def _load_one(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        log.warning("主题 %s 读取失败：%s", path, e)
        return None
    if not isinstance(data, dict):
        return None
    cid = str(data.get("id") or path.parent.name).strip().lower()
    if not _ID_RE.match(cid) or cid != path.parent.name:
        log.warning("主题目录名与 id 不一致或非法：%s / %r", path.parent.name, cid)
        return None
    raw_tokens = data.get("tokens") if isinstance(data.get("tokens"), dict) else {}
    tokens = {}
    for k, v in raw_tokens.items():
        k = str(k).strip()
        if k not in ALLOWED_TOKENS:
            log.warning("主题 %s 声明了不允许的变量 %s（忽略）", cid, k)
            continue
        val = _clean_value(v)
        if val:
            tokens[k] = val
    css_file = path.parent / "theme.css"
    extra = ""
    if css_file.is_file():
        try:
            extra = css_file.read_text(encoding="utf-8")[:MAX_CSS]
        except OSError as e:
            log.warning("主题 %s 的 theme.css 读取失败：%s", cid, e)
    return {"id": cid, "name": str(data.get("name") or cid)[:60],
            "version": str(data.get("version") or "")[:20],
            "tokens": tokens, "extra_css": extra,
            "dir": str(path.parent), "source": str(path)}


def discover() -> dict:
    out = {}
    if not THEME_DIR.is_dir():
        return out
    for d in sorted(THEME_DIR.iterdir()):
        f = d / "theme.json"
        if d.is_dir() and f.is_file():
            t = _load_one(f)
            if t:
                out[t["id"]] = t
    return out


def known(theme_id: str) -> bool:
    return str(theme_id or "").strip().lower() in discover()


def get(theme_id: str) -> dict:
    themes = discover()
    key = str(theme_id or "").strip().lower()
    return themes.get(key) or themes.get(DEFAULT_THEME) or next(iter(themes.values()), {
        "id": DEFAULT_THEME, "name": DEFAULT_THEME, "version": "",
        "tokens": {}, "extra_css": "", "dir": "", "source": ""})


def list_themes() -> list[dict]:
    return [{"id": t["id"], "name": t["name"], "version": t["version"],
             "tokens": t["tokens"], "dir": t["dir"], "source": t["source"],
             "has_css": bool(t["extra_css"])}
            for t in discover().values()]


def css_for(theme_id: str) -> str:
    t = get(theme_id)
    head = f'/* theme plugin: {t["name"]} ({t["id"]}) v{t["version"]} */\n'
    if t["tokens"]:
        body = "".join(f"  {k}: {v};\n" for k, v in t["tokens"].items())
        head += f'body[data-theme="{t["id"]}"] {{\n{body}}}\n'
    if t["extra_css"]:
        head += t["extra_css"].rstrip() + "\n"
    return head
