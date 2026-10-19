"""把插件当 skill：索引 → 读详情 → 调用（执行仍走插件代码）。

和 Hermes skill 的对应关系
--------------------------
| Hermes skill            | 这里的 skill                          |
| ----------------------- | ------------------------------------- |
| 名字 + 一句话描述        | 插件 NAME / DESCRIPTION               |
| 需要时才读 SKILL.md      | `read_skill` 返回命令表 + 帮助         |
| 执行脚本                 | `use_skill` 调插件命令（同一套权限）   |

设计约束
--------
- **权限照旧**：owner/admin/all、群/私聊 scope 与普通命令完全一致；不因为「模型想用」就放宽。
- **回复被捕获**：插件通过 `ctx.adapter.send` 发的文字不会直接进 QQ，而是回灌给模型，
  由模型组织成一句自然回复；生成的图片收集起来交给 runtime 统一发。
- **不递归**：skill 调用不会再触发 agent / 聊天，只跑插件自己的 handler。
- **有超时**：插件 handler 在独立线程跑，超时返回错误（线程是 daemon，不会拖住进程）。
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import inspect
import logging
import re
import threading
import time
from types import SimpleNamespace

from .framework import FeatureContext

log = logging.getLogger("klee.bot.skills")

MAX_SKILLS_IN_INDEX = 80
MAX_INDEX_CHARS = 3200
MAX_DETAIL_CHARS = 3500


def _clean(s: str, n: int) -> str:
    s = re.sub(r"\s+", " ", str(s or "")).strip()
    if len(s) <= n:
        return s
    cut = s[:n].rstrip(" /、，,。；;|｜")
    return cut + "…"


def _allowed(cmd, *, is_owner: bool, is_admin: bool, is_group: bool) -> bool:
    """cmd 可以是 `_Command`，也可以是索引里的 dict。"""
    scope = cmd.get("scope") if isinstance(cmd, dict) else cmd.scope
    perm = cmd.get("permission") if isinstance(cmd, dict) else cmd.permission
    if scope == "group" and not is_group:
        return False
    if scope == "dm" and is_group:
        return False
    return (perm == "all"
            or (perm == "owner" and is_owner)
            or (perm == "admin" and (is_admin or is_owner)))


class _Capture:
    """捕获插件发出的文字/图片，其余属性透传真实 adapter。"""

    def __init__(self, real):
        self._real = real
        self.texts: list[str] = []
        self.images: list[dict] = []

    def __getattr__(self, item):
        return getattr(self._real, item)

    async def send(self, chat_id, text, reply_to=None, sentence_split=None, **kw):
        if text:
            self.texts.append(str(text))
        return SimpleNamespace(success=True)

    async def send_image_file(self, chat_id, path, caption=None, **kw):
        self.images.append({"path": str(path), "caption": str(caption or "")})
        return SimpleNamespace(success=True)


class SkillHub:
    def __init__(self, manager):
        self.mgr = manager
        # 每条消息都要算一次 hint（3 ms/次），把「技能 → 2-gram 集合」缓存起来。
        # 插件热重载会换掉 last_reload_ts，缓存自然失效。
        self._gram_cache: dict[str, tuple[float, list]] = {}

    # ---------- 目录 ----------
    def skills(self) -> list[dict]:
        """按插件聚合：名称 / 标题 / 描述 / 命令（含别名、帮助、权限）。"""
        by_plugin: dict[str, list] = {}
        for cmd in {id(c): c for c in self.mgr.commands.values()}.values():
            by_plugin.setdefault(cmd.plugin, []).append(cmd)
        out = []
        for name, info in sorted(self.mgr.plugins.items()):
            if not info.get("enabled") or info.get("error"):
                continue
            cmds = sorted(by_plugin.get(name, []), key=lambda c: (c.priority, c.name))
            if not cmds:
                continue
            out.append({
                "name": name,
                "title": str(info.get("title") or name),
                "description": str(info.get("description") or ""),
                "usage": str(info.get("usage") or ""),
                "author": str(info.get("author") or ""),
                "version": str(info.get("version") or ""),
                "commands": [{
                    "name": c.name,
                    "aliases": list(c.aliases),
                    "help": str(c.help or ""),
                    "scope": c.scope,
                    "permission": c.permission,
                } for c in cmds],
            })
        return out

    def get(self, name: str) -> dict | None:
        """按名字找技能：精确 → 标题 → 包含 → 模糊检索。

        为什么要有后两步：模型看到「今日运势」会写 `read_skill("运势")`
        （人类也是这么叫的），以前直接回「没有技能 '运势'。」——
        模型据此认定「没装这个插件」，然后就放弃了。真机上就是这么翻车的。
        """
        key = str(name or "").strip().lower()
        if not key:
            return None
        items = self.skills()
        for s in items:                       # 插件名优先，避免 title 与别的插件同名时串台
            if s["name"].lower() == key:
                return s
        for s in items:
            if s["title"].lower() == key:
                return s
        for s in items:                       # 「运势」→ 今日运势
            if key in s["title"].lower() or key in s["name"].lower():
                return s
        for s in items:                       # 「今日运势怎么样」这种口语
            t = s["title"].lower()
            if t and (t in key or s["name"].lower() in key):
                return s
        # 最后一步：只对中文做「最长公共片段 ≥2 字」的兜底。
        # 英文名不走这一步 —— 模型看到的是 `fortune` 这种精确名，
        # 模糊匹配只会把 "nope" 匹到 "loopinfo" 这种无关插件。
        if not re.search(r"[\u4e00-\u9fff]", key):
            return None
        from difflib import SequenceMatcher

        def _longest(a: str, b: str) -> tuple[int, int]:
            """(最长公共片段长度, 它在查询里的结束位置)。

            结束位置做平手判据：中文是「动词在前、对象在后」——
            「帮我丢个骰子」同时命中「帮我」（帮我选）和「骰子」（掷骰子），
            靠后的那个才是真正的对象。
            """
            m = SequenceMatcher(None, a, b).find_longest_match(0, len(a), 0, len(b))
            return m.size, m.a + m.size

        # 先比标题（「运势」→ 今日运势），再比描述（「占卜」→ 塔罗占卜）
        best, best_len, best_pos = None, 0, -1
        for s in items:
            n, pos = max(_longest(key, s["title"].lower()), _longest(key, s["name"].lower()))
            if n > best_len or (n == best_len and n >= 2 and pos > best_pos):
                best, best_len, best_pos = s, n, pos
        # 只比标题/名字：描述里「帮我」「今日」这种通用词太多，
        # 「帮我丢个骰子」会匹到「帮我选」——猜错比找不到更糟。
        # 描述层面的匹配交给 list_skills（那是明确在问「有什么」）。
        return best if best_len >= 2 else None

    def index_text(self, *, is_owner: bool = False, is_admin: bool = False,
                   is_group: bool = False, max_chars: int = MAX_INDEX_CHARS) -> str:
        lines = []
        for s in self.skills():
            cmds = [c for c in s["commands"]
                    if _allowed(c, is_owner=is_owner, is_admin=is_admin, is_group=is_group)]
            if not cmds:
                continue
            names = " ".join("." + c["name"] for c in cmds[:10])
            more = "…" if len(cmds) > 10 else ""
            lines.append(f"- {s['name']}（{s['title']}）：{_clean(s['description'], 70)}"
                         f"｜{names}{more}")
            if sum(len(x) + 1 for x in lines) > max_chars:
                lines.append("- …（技能较多，用 list_skills 查全部）")
                break
        return "\n".join(lines)

    def _gram_index(self) -> list[tuple[dict, list, set[str]]]:
        """[(skill, 允许前先不管, 全量 2-gram)]，按重载时间缓存。"""
        stamp = float(getattr(self.mgr, "last_reload_ts", 0.0) or 0.0)
        hit = self._gram_cache.get("all")
        if hit and hit[0] == stamp:
            return hit[1]
        out = []
        for s in self.skills():
            blob = " ".join([s["name"], s["title"], s["description"]]
                            + [c["name"] + " " + str(c["help"] or "") for c in s["commands"]])
            out.append((s, self._grams(blob)))
        self._gram_cache["all"] = (stamp, out)
        return out

    # ---------- 检索 ----------
    def search(self, query: str, *, is_owner: bool = False, is_admin: bool = False,
               is_group: bool = False, limit: int = 12,
               min_score: float = 0.25) -> list[tuple[float, dict, list]]:
        """按 2-gram 重合度给技能打分（**不是**子串匹配）。

        子串匹配的死法：用户说「今天运势」，技能叫「今日运势」，`"今天运势" in blob`
        直接为假 —— 于是模型拿到「没有匹配的技能」，断定自己做不到。
        """
        qg = self._grams(query)
        if not qg:
            return []
        scored = []
        for s, gr in self._gram_index():
            cmds = [c for c in s["commands"]
                    if _allowed(c, is_owner=is_owner, is_admin=is_admin, is_group=is_group)]
            if not cmds:
                continue
            hit = len(qg & gr)
            score = hit / max(1, len(qg))
            # 门槛不能省：长句里蒙上一个 2-gram 就有分，不设下限时
            # 「完全不存在的关键词zzz」也能匹到歇后语，反而误导模型。
            if score >= min_score:
                scored.append((score, s, cmds))
        scored.sort(key=lambda x: (-x[0], -len(x[2]), x[1]["name"]))
        return scored[:max(1, int(limit))]

    # ---------- 提示词里的技能提示 ----------
    @staticmethod
    def _grams(sv: str) -> set[str]:
        t = re.sub(r"\s+", "", str(sv or "")).lower()
        return {t[i:i + 2] for i in range(max(0, len(t) - 1))} if len(t) > 1 else {t} if t else set()

    def hint(self, text: str = "", *, is_owner: bool = False, is_admin: bool = False,
             is_group: bool = False, max_chars: int = 760, pick: int = 6,
             always: int = 8) -> str:
        """给系统提示用的**紧凑**技能提示：按这条消息挑几个相关的，再给几条常备。

        为什么不直接塞 `index_text`（3200 字）：那是每条消息都要付的输入成本，
        而且 40 个技能里 39 个和当前问题无关，反而稀释注意力。
        这里用 2-gram 重合度挑最相关的几个；一个都对不上就只报名字。
        """
        items = []
        for s in self.skills():
            cmds = [c for c in s["commands"]
                    if _allowed(c, is_owner=is_owner, is_admin=is_admin, is_group=is_group)]
            if not cmds:
                continue
            items.append((s, cmds))
        if not items:
            return ""
        qg = self._grams(text)
        grams = {s["name"]: gr for s, gr in self._gram_index()}
        scored = []
        for s, cmds in items:
            overlap = len(qg & grams.get(s["name"], set()))
            scored.append((overlap, s, cmds))
        scored.sort(key=lambda x: (-x[0], -len(x[2]), x[1]["name"]))
        picked = [x for x in scored if x[0] > 0][:pick]
        if len(picked) < always:                      # 相关的不够就补常备的
            names = {x[1]["name"] for x in picked}
            picked += [x for x in scored if x[1]["name"] not in names][:always - len(picked)]
        lines = []
        for _score, s, cmds in picked:
            names = " ".join("." + c["name"] for c in cmds[:5])
            more = "…" if len(cmds) > 5 else ""
            lines.append(f"- {s['name']}（{s['title']}）：{_clean(s['description'], 46)}｜{names}{more}"
                         if s["description"] else
                         f"- {s['name']}（{s['title']}）｜{names}{more}")
            if sum(len(x) + 1 for x in lines) > max_chars:
                break
        tail = (f"（一共 {len(items)} 个技能；看全部用 list_skills，看某个的用法用 read_skill）")
        head = ("【你能用的技能】这些是已经装好的插件，也就是你的技能。"
                "有正好对得上的就直接调 use_skill(name=插件名, text=\"命令 参数\")，"
                "别回答「我做不到」，也别自己编一个替代做法"
                "（问运势就用 fortune，别拿骰子当占卜）。"
                "技能返回的文字就是结果，照它说，不要改写数字。")
        return head + "\n" + "\n".join(lines) + "\n" + tail

    def suggest(self, name: str, limit: int = 2) -> str:
        """找不到技能时的「你是指 X 吗」——模型据此能自己纠正，不用重来一轮。"""
        hits = self.search(str(name or ""), is_owner=True, is_admin=True, is_group=True,
                           limit=limit, min_score=0.2)
        if not hits:
            return ""
        return "；你是指 " + "、".join(f"{s['name']}（{s['title']}）" for _sc, s, _c in hits) + " 吗？"

    def detail(self, name: str) -> str:
        s = self.get(name)
        if not s:
            return f"没有技能 {name!r}" + self.suggest(name)
        out = [f"# {s['title']}（{s['name']}）" + (f" v{s['version']}" if s["version"] else "")]
        if s["description"]:
            out.append(s["description"])
        if s.get("usage"):
            out.append(f"用法：{s['usage']}")
        out.append("命令：")
        for c in s["commands"]:
            alias = f"（别名 .{' .'.join(c['aliases'])}）" if c["aliases"] else ""
            perm = "" if c["permission"] == "all" else f" [{c['permission']}]"
            scope = "" if c["scope"] == "both" else f" [{c['scope']}]"
            out.append(f"- .{c['name']}{alias}{perm}{scope}：{c['help'] or '—'}")
        text = "\n".join(out)
        return text if len(text) <= MAX_DETAIL_CHARS else text[:MAX_DETAIL_CHARS] + "\n…"

    # ---------- 调用 ----------
    def run(self, name: str, text: str, *, adapter, is_group: bool, group_id: str,
            user_id: str, user_name: str, role: str = "", timeout: float = 25.0,
            chat: dict | None = None) -> dict:
        s = self.get(name)
        if not s:
            return {"ok": False,
                    "error": f"没有技能 {name!r}" + self.suggest(name) + "（用 list_skills 看全部）"}
        raw = str(text or "").strip()
        raw = re.sub(r"^[.。．/／]+", "", raw)
        token, _, rest = raw.partition(" ")
        token = token.strip()
        cmd = None
        if token:
            cmd = self.mgr.commands.get(token.lower())
            if cmd is not None and cmd.plugin != s["name"]:
                return {"ok": False, "error": f".{cmd.name} 属于技能 {cmd.plugin}，不是 {s['name']}"}
        if cmd is None:
            cmd = self.mgr.commands.get(s["commands"][0]["name"].lower())
            # 模型常写 "北京" 而不是 "weather 北京"：整段当参数，别把输入吞掉
            rest = raw if rest.strip() else token
        is_owner = bool(getattr(adapter, "owner_id", "")) and str(user_id) == str(
            getattr(adapter, "owner_id", ""))
        is_admin = is_owner or (is_group and role in ("owner", "admin"))
        if not _allowed(cmd, is_owner=is_owner, is_admin=is_admin, is_group=is_group):
            return {"ok": False, "error": f"你在这个会话里没有权限用 .{cmd.name}"}

        # 与手动命令同一套冷却/统计（模型循环不该绕过限流）
        if cmd.cooldown > 0:
            key = (cmd.name.lower(), str(user_id))
            now = time.monotonic()
            wait = float(self.mgr.cooldowns.get(key, 0.0)) - now
            if wait > 0:
                return {"ok": False,
                        "error": f".{cmd.name} 刚用过，{wait:.0f} 秒后再试"}
            if len(self.mgr.cooldowns) > 2000:
                self.mgr.cooldowns = {k: v for k, v in self.mgr.cooldowns.items() if v > now}
            self.mgr.cooldowns[key] = now + cmd.cooldown

        cap = _Capture(adapter)
        ctx = FeatureContext(
            adapter=cap, text=f".{cmd.name}" + (f" {rest}" if rest else ""),
            is_group=is_group, group_id=str(group_id or ""), user_id=str(user_id),
            user_name=str(user_name or user_id), message_id="", mentioned=True, role=role,
        )
        ctx.command, ctx.args = cmd.name, rest.split() if rest else []
        box: dict = {}

        async def _invoke_async():
            try:
                res = cmd.handler(ctx)
                if inspect.isawaitable(res):
                    res = await res
                if isinstance(res, str) and res.strip():
                    cap.texts.append(res)
            except BaseException as e:                            # noqa: BLE001
                box["error"] = f"{type(e).__name__}: {e}"

        def _invoke_thread():
            try:
                asyncio.run(_invoke_async())
            except BaseException as e:                            # noqa: BLE001
                box["error"] = f"{type(e).__name__}: {e}"

        loop = chat.get("loop") if isinstance(chat, dict) else None
        loop_thread = chat.get("loop_thread") if isinstance(chat, dict) else None
        on_bot_loop = (loop is not None and loop.is_running()
                       and threading.current_thread() is not loop_thread)
        if on_bot_loop:
            try:
                asyncio.run_coroutine_threadsafe(_invoke_async(), loop).result(
                    timeout=max(0.5, float(timeout)))
            except concurrent.futures.TimeoutError:
                log.warning("技能 %s（.%s）执行超时（>%.0fs）", s["name"], cmd.name, timeout)
                return {"ok": False, "error": f"技能 {s['name']} 执行超时（>{timeout:.0f}s）"}
            except Exception as e:                                # noqa: BLE001
                log.warning("技能 %s 调度失败：%s", s["name"], e)
                box["error"] = box.get("error") or f"{type(e).__name__}: {e}"
        else:
            th = threading.Thread(target=_invoke_thread, daemon=True,
                                  name=f"skill-{s['name']}")
            th.start()
            th.join(max(0.5, float(timeout)))
            if th.is_alive():
                log.warning("技能 %s（.%s）线程执行超时（>%.0fs）", s["name"], cmd.name, timeout)
                return {"ok": False, "error": f"技能 {s['name']} 执行超时（>{timeout:.0f}s）"}
        if box.get("error"):
            self.mgr.errors[cmd.plugin] = self.mgr.errors.get(cmd.plugin, 0) + 1
            log.warning("技能 %s（.%s）报错：%s", s["name"], cmd.name, box["error"])
            return {"ok": False, "error": f"技能报错：{box['error']}"}
        self.mgr._bump(cmd.plugin)
        out = "\n".join(t for t in cap.texts if t).strip()
        if not out and cap.images:
            caps = [i["caption"] for i in cap.images if i.get("caption")]
            out = "；".join(caps) or "（生成了图片）"
        if not out:
            out = "（这个命令没有输出）"
        return {"ok": True, "skill": s["name"], "command": cmd.name,
                "text": out, "images": cap.images}


async def _await(value):
    return await value
