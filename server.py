#!/usr/bin/env python3
"""Character Bot（klee-studio）—— 多角色 QQ bot 控制台 + 面板自持运行时。

- **对话**：网页里直接调面板自己的 bot 运行时（同一份 SOUL、同一套插件、同一个模型），
  与 QQ 共用人格但不共用会话；QQ 掉线也能用。
- **人格**：state/SOUL.md 在线编辑（字符/预估 token 统计，自动备份）。
- **插件**：bot/features 浏览 / 在线编辑（保存前 py_compile 校验）/ 干跑测试台。
- **消息**：state/messages.jsonl 的言行时间线（QQ + 网页）。
- **运维**：NapCat 状态、扫码登录、掉线告警、配置、日志。
- **机器人**：模型供应商 / 聊天模型 / 看图模型 / agent 模型 + Agent 工具开关 + 指标。

后端只用标准库（urllib、sqlite3、http.server）。写操作要 `X-Klee-Studio: 1` 头。
"""

from __future__ import annotations

import ast
import email.utils
import glob
import gzip
from collections import OrderedDict
import io
import hashlib
import hmac
import json
import logging
import mimetypes
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import qrimg

logger = logging.getLogger("klee.studio")

# —— 面板自持 bot（不依赖 Hermes 网关）——
def _load_env() -> None:
    """读 studio 自己的 .env（KEY=VALUE），只补缺不覆盖已有环境变量。"""
    try:
        for line in (Path(__file__).resolve().parent / ".env").read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    except OSError:
        pass


_load_env()
try:
    from bot import themes as theme_mod
    from bot.characters import CHARACTERS, CharacterStore
    from bot.framework import merge_plugin_settings
    from bot.service import BotService
    _BOT_IMPORT_ERR = ""
except Exception as _e:                                            # noqa: BLE001
    BotService = None                                             # type: ignore[assignment]
    _BOT_IMPORT_ERR = f"{type(_e).__name__}: {_e}"

PORT = int(os.environ.get("KLEE_STUDIO_PORT", "8796"))
BIND = os.environ.get("KLEE_STUDIO_BIND", "0.0.0.0")
STUDIO_HOME = Path(__file__).resolve().parent
STATIC = STUDIO_HOME / "static"
STATE_DIR = STUDIO_HOME / "state"
STATE_DIR.mkdir(parents=True, exist_ok=True)
CHAT_JSONL = STATE_DIR / "chat.jsonl"
MSG_JSONL = STATE_DIR / "messages.jsonl"           # 面板自有消息流（QQ + 网页）
STATE_DB = STATE_DIR / "legacy-state.db"           # 已弃用（保留常量避免旧引用炸）
FEATURES_DIR = STUDIO_HOME / "bot" / "features"    # 面板自持的插件目录
RELOAD_MARK = FEATURES_DIR / ".reload"
SOUL = STATE_DIR / "SOUL.md"
KLEE_STATE = STATE_DIR / "bot_status.json"
ALERTS_JSONL = STATE_DIR / "alerts.jsonl"          # 掉线/恢复事件流水（晨报、面板都读它）
# 掉线判定：心跳文件超过 STALE_S 秒没更新 = 适配器没在跑；connected=false 但心跳新鲜 = QQ 被踢
OFFLINE_STALE_S = 90.0
OFFLINE_ALERT_S = float(os.environ.get("KLEE_OFFLINE_ALERT_S", "120"))   # 连续掉线多久才落一条告警
ALERT_POLL_S = float(os.environ.get("KLEE_ALERT_POLL_S", "15"))          # 巡检间隔（测试可调小）
NAPCAT_WEBUI = Path("/root/Napcat/opt/QQ/resources/app/app_launcher/napcat/config/webui.json")
CONFIG_YAML = STATE_DIR / "bot_config.json"
GATEWAY_LOG = STATE_DIR / "bot.log"

# 运维页「常用配置」表单的白名单（全部是 platforms.onebot.extra.* 标量）
CONFIG_KEYS = [
    "group_policy",
    "group_chime_probability", "group_chime_cooldown",
    "send_interval_min", "send_interval_max",
    "notify_poke", "greet_on_group_add",
    "owner_id", "quote_reply", "at_sender", "max_images",
    "feature_prefix",
    "tts_enabled", "tts_voice", "tts_max_chars",
    "rate_limit_per_min", "rate_limit_burst",
    "vision_enabled", "vision_max_bytes", "vision_timeout",
    "kb_enabled", "kb_url", "kb_topk", "kb_threshold", "kb_max_chars",
    "agent_enabled", "agent_owner_only", "agent_max_steps", "agent_timeout", "agent_tools",
    "agent_skills", "request_notify",
]

# 本地知识库（klee-kb，:8797）：对话前检索可莉语料，命中就附给她
KB_URL = "http://127.0.0.1:8797"

CHAT_TIMEOUT = 240

_chat_q: "queue.Queue[dict]" = queue.Queue()
_chat_state = {"running": None}                 # {"session","text","started"} | None
_lock = threading.Lock()
_napcat_cache: dict = {"ts": 0.0, "data": None}
BOT = None                     # BotService（main() 里启动）


# ---------- 基础 ----------
def _pm_python() -> str:
    """带 PIL 的解释器：优先本机 Hermes 的 venv，否则用当前解释器。"""
    cands = sorted(glob.glob("/root/.hermes/installs/*/environments/*/venv/bin/python"))
    return cands[-1] if cands else sys.executable


def _run(cmd: list, timeout: int = 30, env: dict | None = None) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
        return p.returncode, (p.stdout + p.stderr).strip()
    except Exception as e:                                     # noqa: BLE001
        return -1, f"{type(e).__name__}: {e}"


MAX_BODY = 64 * 1024 * 1024          # 与角色包上限一致；JSON 接口远小于这个数
JSONL_TAIL_BYTES = 4 * 1024 * 1024      # 只读最后 4MB：日志是 append-only，面板只要最近的行


def _read_jsonl(path: Path) -> list:
    """读 jsonl；大文件只读尾部（避免每次轮询把整份历史重新解析一遍）。"""
    out = []
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            start = max(0, size - JSONL_TAIL_BYTES)
            f.seek(start)
            data = f.read().decode("utf-8", "replace")
        lines = data.splitlines()
        if start > 0 and lines:
            lines = lines[1:]              # 第一条多半被截断，丢掉
        for line in lines:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):      # 非对象行对调用方没意义，直接跳过
                out.append(row)
    except OSError:
        pass
    return out


def _append_jsonl(path: Path, entry: dict) -> None:
    with _lock:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _chat_drop_session(session: str, cid: str) -> tuple[int, int]:
    """删掉某角色某会话的全部聊天行；返回 (删掉条数, 剩余条数)。

    这里必须全量读：_read_jsonl() 只读尾部（轮询优化），拿它来重写会吃掉历史。
    """
    removed = total = 0
    tmp = CHAT_JSONL.with_name("chat.jsonl.tmp.%d" % os.getpid())
    with _lock:
        with open(CHAT_JSONL, "r", encoding="utf-8", errors="replace") as src, \
                open(tmp, "w", encoding="utf-8") as dst:
            for line in src:
                if not line.strip():
                    continue
                try:
                    e = json.loads(line)
                except json.JSONDecodeError:
                    dst.write(line)                      # 坏行原样留着，不吞数据
                    continue
                if not isinstance(e, dict):
                    dst.write(line)
                    continue
                total += 1
                if (e.get("session") == session
                        and str(e.get("character") or "klee") == cid):
                    removed += 1
                    continue
                dst.write(json.dumps(e, ensure_ascii=False) + "\n")
        if removed:
            os.replace(tmp, CHAT_JSONL)
        else:
            try:
                tmp.unlink()
            except OSError:
                pass
    return removed, total - removed


# ---------- QQ 掉线巡检（N10-1） ----------
def _hhmmss(ts: float) -> str:
    return time.strftime("%H:%M:%S", time.localtime(ts))


def qq_health(state_path: Path | None = None) -> dict:
    """QQ 健康度。

    - 生产（state_path=None）：问 OneBot HTTP，面板自持数据库。
    - 兼容（state_path 指定）：读旧心跳 JSON（离线巡检的老测试还会这么调）。
    """
    if state_path is not None:
        try:
            d = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"connected": False, "age": None, "reason": "no_heartbeat",
                    "detail": "读不到心跳文件"}
        age = time.time() - float(d.get("ts") or 0)
        if age >= OFFLINE_STALE_S:
            return {"connected": False, "age": age, "reason": "no_heartbeat",
                    "detail": f"心跳停在 {round(age)}s 前"}
        if not d.get("connected"):
            return {"connected": False, "age": age, "reason": "qq_offline",
                    "detail": "适配器在跑但 QQ 未连接"}
        return {"connected": True, "age": age, "reason": "", "detail": ""}
    if BOT is None or not getattr(BOT, "runtime", None):
        return {"connected": False, "age": None, "reason": "no_bot",
                "detail": _BOT_IMPORT_ERR or "面板 bot 运行时未启动"}
    try:
        st = BOT.runtime.ob.get_status()
        if st.get("online") is False or st.get("good") is False:
            return {"connected": False, "age": 0.0, "reason": "qq_offline",
                    "detail": "NapCat 在跑但 QQ 未登录（可能被踢，需要扫码）"}
        return {"connected": True, "age": 0.0, "reason": "", "detail": ""}
    except Exception as e:                                     # noqa: BLE001
        return {"connected": False, "age": None, "reason": "napcat_unreachable",
                "detail": f"OneBot HTTP 连不上：{type(e).__name__}: {e}"[:160]}


def read_alerts(n: int = 20, path: Path | None = None) -> list:
    return _read_jsonl(path or ALERTS_JSONL)[-max(1, int(n)):]


_alert_state = {"down_since": None, "alerted": False, "last_seen_up": None, "restored": False}


def _restore_down_state(path: Path) -> None:
    """进程重启后接着上一轮的账：文件末尾是 `offline`（没有配对的 recovered）说明掉线还在继续，
    不能因为 studio 重启就再告警一次 —— 只把 alerted 恢复，恢复时照样能报出真实离线时长。"""
    st = _alert_state
    try:
        last = _read_jsonl(path)[-1:]
    except OSError:
        last = []
    if last and last[0].get("kind") == "offline":
        ts = float(last[0].get("ts") or time.time())
        st["alerted"] = True
        # 告警里记了「落这条时已经掉了多久」→ 减回去就是真正的掉线起点，恢复时能报全时长
        st["down_since"] = ts - float(last[0].get("down_s") or 0)
    st["restored"] = True


def alert_tick(now: float | None = None, health: dict | None = None,
               path: Path | None = None, alert_after: float | None = None) -> dict | None:
    """巡检一次；发生「掉线超时」或「恢复」时落一条到 alerts.jsonl，返回那条事件。

    去抖：同一次掉线只落一条告警（alerted 标记），恢复时落一条 recovered 并带上离线时长。
    """
    now = time.time() if now is None else now
    target = path or ALERTS_JSONL
    if not _alert_state["restored"]:
        _restore_down_state(target)
    h = health if health is not None else qq_health()
    st = _alert_state
    if h.get("connected"):
        ev = None
        if st["alerted"]:
            down_s = round(now - st["down_since"]) if st["down_since"] else None
            ev = {"ts": round(now, 1), "time": _hhmmss(now), "kind": "recovered",
                  "down_s": down_s,
                  "text": f"QQ 已恢复连接（本次离线约 {down_s}s）" if down_s else "QQ 已恢复连接"}
        st["down_since"], st["alerted"], st["last_seen_up"] = None, False, now
        if ev:
            _append_jsonl(target, ev)
        return ev
    if st["down_since"] is None:
        st["down_since"] = now
    down = now - st["down_since"]
    limit = OFFLINE_ALERT_S if alert_after is None else float(alert_after)
    if down >= limit and not st["alerted"]:
        st["alerted"] = True
        ev = {"ts": round(now, 1), "time": _hhmmss(now), "kind": "offline",
              "down_s": round(down), "reason": h.get("reason") or "qq_offline",
              "detail": h.get("detail") or "",
              "text": f"QQ 掉线已超过 {round(down)}s：{h.get('detail') or h.get('reason') or '未知原因'}"}
        _append_jsonl(target, ev)
        return ev
    return None


def _alert_watcher() -> None:
    while True:
        try:
            alert_tick()
        except Exception as e:                                # noqa: BLE001
            print(f"[klee-studio] alert watcher: {type(e).__name__}: {e}", flush=True)
        time.sleep(ALERT_POLL_S)


def _read_config_keys() -> dict:
    """读面板 bot 配置（运维页表单用）。"""
    if not BOT:
        return {"ok": False, "error": _BOT_IMPORT_ERR or "bot 未启动"}
    out = {}
    for k in CONFIG_KEYS:
        v = BOT.config.get(k)
        if isinstance(v, (list, tuple)):
            v = ",".join(str(x) for x in v)
        out[k] = v
    return {"ok": True, "values": out}


def _int_param(q: dict, key: str, default: int,
               lo: int | None = None, hi: int | None = None) -> int:
    """查询参数转 int：坏值回默认，再夹到 [lo, hi]（OCR：别让 ?after=abc 把连接打崩）。"""
    try:
        v = int((q.get(key) or [str(default)])[0])
    except (TypeError, ValueError):
        v = default
    if lo is not None:
        v = max(lo, v)
    if hi is not None:
        v = min(hi, v)
    return v


_ANNIVERSARY_JSON = FEATURES_DIR / "state" / "anniversary.json"


def _anniversary_module():
    """加载线上「纪念日」插件模块，复用它的日期口径（别在面板里再抄一份算法）。"""
    import importlib.util
    spec = importlib.util.spec_from_file_location("studio_anniversary", FEATURES_DIR / "anniversary.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _anniversaries(top_n: int = 100) -> dict:
    """读她小本本里的纪念日（只读；写操作走 QQ 命令）。天数/文案口径直接用插件自己的函数。"""
    from datetime import date as _date
    try:
        d = json.loads(_ANNIVERSARY_JSON.read_text(encoding="utf-8"))
        items = d.get("items") if isinstance(d.get("items"), list) else []
    except (OSError, ValueError):
        items = []
    items = [it for it in items if isinstance(it, dict)]
    try:
        mod = _anniversary_module()
    except Exception as e:                                    # noqa: BLE001
        return {"ok": False, "error": f"读不到插件口径：{type(e).__name__}: {e}",
                "now": time.time(), "items": []}
    today = _date.today()
    rows = []
    for it in items:
        try:
            days, kind = mod.status(it, today)
        except Exception:                                     # noqa: BLE001
            continue                                          # 脏条目跳过，别让一条坏数据打崩面板
        rows.append({
            "id": int(it.get("id") or 0),
            "chat_id": str(it.get("chat_id") or ""),
            "name": str(it.get("name") or ""),
            "label": mod.date_label(it),
            "yearly": it.get("year") is None,
            "days": int(days),
            "kind": kind,
            "desc": mod.describe(it, today),
            "user_name": str(it.get("user_name") or ""),
            "rank": {"today": 0, "upcoming": 1, "past": 2}.get(kind, 3),
        })
    rows.sort(key=lambda x: (x["rank"], x["days"], x["name"]))
    return {"ok": True, "now": time.time(), "total": len(rows), "items": rows[:top_n]}


def _reminders() -> dict:
    """读她小本本里的待提醒（只读；写操作仍走 QQ 命令，避免和插件内存态打架）。"""
    try:
        d = json.loads((FEATURES_DIR / "state" / "reminders.json")
                       .read_text(encoding="utf-8"))
        items = d.get("items") if isinstance(d.get("items"), list) else []
    except (OSError, ValueError):
        items = []
    def _due(x):
        try:
            return float(x.get("due") or 0)
        except (TypeError, ValueError):
            return 0.0
    items = sorted((it for it in items if isinstance(it, dict)), key=_due)
    return {"ok": True, "now": time.time(), "items": items[:200]}


# ---------- 日志保留 ----------
LOG_JSONL = (ALERTS_JSONL, MSG_JSONL, STATE_DIR / "kb_hits.jsonl", STATE_DIR / "requests.jsonl")


def _log_cfg() -> tuple[bool, int]:
    """(是否写运行日志, 流水保留天数)。0 天 = 永不清理。"""
    cfg = (BOT.config if BOT is not None else {}) or {}
    try:
        days = int(cfg.get("log_retention_days", 30))
    except (TypeError, ValueError):
        days = 30
    return bool(cfg.get("log_enabled", True)), max(0, days)


def _prune_jsonl(path: Path, cutoff: float) -> int:
    """删掉 ts 早于 cutoff 的行；返回删掉条数。坏行保留（不吞数据）。

    整段持 `_lock`：`_append_jsonl` 也在同一把锁里追加，
    否则「读完 → 替换」之间追加的那一行会被旧 inode 覆盖掉（静默丢数据）。
    """
    with _lock:
        return _prune_jsonl_locked(path, cutoff)


def _prune_jsonl_locked(path: Path, cutoff: float) -> int:
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0
    keep, gone = [], 0
    for line in raw.splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            keep.append(line)
            continue
        ts = row.get("ts") if isinstance(row, dict) else None
        if isinstance(ts, (int, float)) and ts < cutoff:
            gone += 1
            continue
        keep.append(line)
    if not gone:
        return 0
    tmp = path.with_name(f"{path.name}.{os.getpid()}.prune.tmp")
    try:
        tmp.write_text("\n".join(keep) + ("\n" if keep else ""), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return 0
    return gone


def _prune_text_log(path: Path, cutoff_day: str) -> int:
    """按行首日期（YYYY-MM-DD）裁 bot.log；没有日期的行原样保留。"""
    with _lock:
        return _prune_text_log_locked(path, cutoff_day)


def _prune_text_log_locked(path: Path, cutoff_day: str) -> int:
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0
    keep, gone = [], 0
    for line in raw.splitlines():
        head = line[:10]
        if len(head) == 10 and head[4] == "-" and head[7] == "-" and head < cutoff_day:
            gone += 1
            continue
        keep.append(line)
    if not gone:
        return 0
    tmp = path.with_name(f"{path.name}.{os.getpid()}.prune.tmp")
    try:
        tmp.write_text("\n".join(keep) + ("\n" if keep else ""), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return 0
    return gone


def _log_janitor_once() -> dict:
    _enabled, days = _log_cfg()
    if days <= 0:
        return {"ok": True, "days": 0, "pruned": {}}
    cutoff = time.time() - days * 86400
    cutoff_day = time.strftime("%Y-%m-%d", time.localtime(cutoff))
    pruned = {}
    for path in LOG_JSONL:
        n = _prune_jsonl(path, cutoff)
        if n:
            pruned[path.name] = n
    n = _prune_text_log(GATEWAY_LOG, cutoff_day)
    if n:
        pruned[GATEWAY_LOG.name] = n
    return {"ok": True, "days": days, "pruned": pruned}


def _log_janitor() -> None:
    """每 6 小时清一次过期流水；启动时先跑一次。"""
    while True:
        try:
            res = _log_janitor_once()
            if res.get("pruned"):
                logger.info("日志清理：%s", res["pruned"])
        except Exception:                                        # noqa: BLE001
            logger.warning("日志清理失败", exc_info=True)
        time.sleep(6 * 3600)


def _tail_log(n: int, grep: str) -> list:
    enabled, _days = _log_cfg()
    if not enabled:
        return ["(运行日志已关闭：面板 → 运维 · 机器人 → 常用配置 → 记录运行日志)"]
    try:
        with open(GATEWAY_LOG, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - 400_000))
            data = f.read().decode("utf-8", "replace")
        lines = data.splitlines()
        if grep:
            lines = [ln for ln in lines if grep in ln]
        return lines[-n:]
    except OSError:
        return ["(读不到日志)"]


def _web_agent_call(rt, msgs: list, cid: str, reasoning: str = "default") -> dict:
    """网页端的 agent 工具循环：直接用 bot 的 agent 配置（工具白名单/步数/超时）。"""
    import asyncio as _aio
    from bot import agent as agent_mod

    cfg = (rt.config or {})
    tools = agent_mod.resolve_tools(cfg.get("agent_tools"))
    if not cfg.get("agent_skills", True):
        tools = [t for t in tools if t not in ("list_skills", "read_skill", "use_skill")]
    kw = {}
    if reasoning and reasoning != "default":
        kw["temperature"] = 0.4 if reasoning == "high" else 0.7
    return agent_mod.run(
        rt.models, msgs,
        provider="", model="",
        tools=tools,
        skills=(rt.skills if cfg.get("agent_skills", True) else None),
        chat={"adapter": None, "is_group": False, "group_id": "",
              "user_id": "panel", "user_name": "主人", "role": "owner",
              "is_owner": True, "loop": None, "loop_thread": None},
        max_steps=int(cfg.get("agent_max_steps") or 3),
        timeout=float(cfg.get("agent_timeout") or 120),
        max_tokens=int(cfg.get("max_tokens") or 800),
        kb_url=str(cfg.get("kb_url") or ""),
        state_dir=rt.state_dir,
        scope="web:panel",
        character=cid,
        **kw,
    )


# ---------- 网页端插件分发（与 QQ 同一套命令/钩子/NLU） ----------
class _WebAdapter:
    """给 FeatureContext 用的假适配器：reply 捕获进列表，不再真的发消息。"""

    def __init__(self, rt, cid: str):
        self._rt = rt
        self._cid = cid
        self.owner_id = str((BOT.config if BOT else {}).get("owner_id") or "")
        self.owner_id = self.owner_id or "1"          # 网页端视为操作者本人
        self.features_dir = ""
        self.feature_prefix = str(rt.config.get("feature_prefix") or ".")

    def _chat_id(self, is_group, group_id, user_id):
        return f"web:{user_id or 'panel'}"


def _web_plugin_dispatch(rt, job: dict, text: str, cid: str):
    """网页消息先走插件层：命中命令/NLU 就直接用插件的回复。

    返回 str = 插件已回复；None = 没命中，继续普通聊天。
    在 worker 线程里同步执行（命令 handler 是 async，用独立小循环跑）。
    """
    import asyncio as _aio
    # 测试桩 / 半初始化的 runtime 没有 mgr：直接退回纯聊天，别把 worker 弄挂
    if not hasattr(rt, "mgr") or getattr(rt, "mgr", None) is None or not hasattr(rt, "config"):
        return None
    text = str(text or "").strip()
    if not text or not text.startswith(str(rt.config.get("feature_prefix") or ".")):
        # 没带前缀：试试 NLU（自然语言路由到命令），没路由就直接聊天
        hit = rt.mgr.nlu_match(text) if text else None
        if not hit:
            return None
        text = str(rt.config.get("feature_prefix") or ".") + hit.lstrip("./／")

    from bot.framework import FeatureContext
    adapter = _WebAdapter(rt, cid)
    ctx = FeatureContext(adapter=adapter, text=text, is_group=False,
                         group_id="", user_id="panel", user_name="主人",
                         message_id="", mentioned=True, role="owner")
    captured: list[str] = []

    async def _fake_reply(t, **kw):
        for line in str(t).split("\n"):
            if line.strip():
                captured.append(line)

    ctx.reply = _fake_reply

    loop = _aio.new_event_loop()
    try:
        # 先跑入站钩子（违禁词等），再跑命令
        ok = loop.run_until_complete(rt.mgr.run_message_hooks(ctx))
        if ok:
            ok = loop.run_until_complete(rt.mgr.handle_command(ctx))
    except Exception:                                        # noqa: BLE001
        logger.exception("网页插件分发失败")
        return None
    finally:
        loop.close()
    if not captured:
        return None
    return "\n".join(captured)


# ---------- 对话：队列 + worker ----------
def _chat_worker() -> None:
    while True:
        job = _chat_q.get()
        if job is None:
            return
        with _lock:
            _chat_state["running"] = {"session": job["session"], "text": job["text"],
                                      "started": time.time()}
        t0 = time.time()
        ok, text, err = True, "", None
        cid = str(job.get("character") or _active_cid())
        try:
            if BOT is None:
                raise RuntimeError(_BOT_IMPORT_ERR or "面板 bot 未启动")
            rt = BOT.runtime

            # ---- 网页端也走插件层：命令 / 钩子 / NLU 与 QQ 同一套 ----
            # 以前网页聊天只会「纯聊天」，.祈愿 这类命令在面板里永远不命中。
            raw = str(job.get("text") or "")
            if rt is not None:
                web_reply = _web_plugin_dispatch(rt, job, raw, cid)
                if web_reply is not None:
                    _append_jsonl(MSG_JSONL, {"ts": time.time(),
                                              "chat_id": "web:" + job["session"],
                                              "role": "assistant", "text": str(web_reply)[:800]})
                    _append_jsonl(CHAT_JSONL, {
                        "ts": time.time(), "session": job["session"], "character": cid,
                        "role": "assistant", "text": str(web_reply),
                        "elapsed": round(time.time() - t0, 1),
                        "via": "plugin",
                    })
                    with _lock:
                        _chat_state["running"] = None
                    continue

            persona = _persona_text(cid)
            cname = (_char_store().get(cid) or {}).get("name") or cid
            reasoning = str(job.get("reasoning") or "default")
            use_agent = bool(job.get("agent")) and rt is not None
            msgs = [{"role": "system", "content": persona +
                     f"\n\n【对话场景】你现在在网页面板里和主人聊天，你就是{cname}。像手机聊天一样说短句，别用 Markdown。"}]
            kb_ctx = _kb_context(job["text"], cid)
            if kb_ctx:
                msgs.append({"role": "system", "content": "【相关背景资料】\n" + kb_ctx})
            hist = [e for e in _read_jsonl(CHAT_JSONL)
                    if e.get("session") == job["session"] and e.get("text")
                    and str(e.get("character") or "klee") == cid][-12:]
            # 当前这轮已在入队时落盘：按入队时生成的 entry id 精确移除。
            # （以前按文本从后往前找，连发两条相同消息时会删错别人的那条）
            eid = job.get("eid")
            if eid:
                hist = [e for e in hist if e.get("id") != eid]
            else:
                for i in range(len(hist) - 1, -1, -1):
                    if hist[i].get("role") == "user" and str(hist[i].get("text")) == job["text"]:
                        hist.pop(i)
                        break
            for e in hist:
                if e.get("role") in ("user", "assistant"):
                    msgs.append({"role": e["role"], "content": str(e.get("text"))[:800]})
            msgs.append({"role": "user", "content": job["text"]})

            # ---- Agent 工具循环（面板当 agent 用；与 QQ 侧同一套） ----
            if use_agent:
                try:
                    res = _web_agent_call(rt, msgs, cid, reasoning)
                    reply_agent = str(res.get("text") or "").strip()
                    if reply_agent:
                        text = reply_agent
                        tool_calls = len(res.get("tool_calls") or [])
                        _append_jsonl(MSG_JSONL, {"ts": time.time(),
                                                  "chat_id": "web:" + job["session"],
                                                  "role": "assistant", "text": text[:800]})
                        _append_jsonl(CHAT_JSONL, {
                            "ts": time.time(), "session": job["session"], "character": cid,
                            "role": "assistant", "text": text,
                            "elapsed": round(time.time() - t0, 1),
                            "tools": tool_calls, "via": "agent",
                        })
                        with _lock:
                            _chat_state["running"] = None
                        continue
                except Exception as e:                             # noqa: BLE001
                    logger.warning("网页 agent 失败，回退普通聊天: %s", e)

            kw = {}
            if reasoning and reasoning != "default":
                # 推理强度：映射到常见参数；不认识的原样透传
                kw["reasoning_effort"] = reasoning
            try:
                text = BOT.models.chat(msgs, timeout=CHAT_TIMEOUT, **kw)
            except TypeError:
                # 模型层不认 reasoning_effort 就不带它
                text = BOT.models.chat(msgs, timeout=CHAT_TIMEOUT)
            _append_jsonl(MSG_JSONL, {"ts": time.time(), "chat_id": "web:" + job["session"],
                                      "role": "assistant", "text": text[:800]})
        except Exception as e:                                 # noqa: BLE001
            ok, err = False, f"{type(e).__name__}: {e}"[:300]
        try:
            _append_jsonl(CHAT_JSONL, {
                "ts": time.time(), "session": job["session"], "character": cid,
                "role": "assistant", "text": text if ok else "", "error": err,
                "elapsed": round(time.time() - t0, 1),
            })
        except Exception as e:                                 # noqa: BLE001
            logger.error("写 chat.jsonl 失败（worker 继续跑）：%s", e)
        finally:
            with _lock:
                _chat_state["running"] = None


threading.Thread(target=_chat_worker, daemon=True).start()


# ---------- 人格 ----------
def _active_cid() -> str:
    rt = getattr(BOT, "runtime", None) if BOT is not None else None
    if rt is not None:
        return str(getattr(rt, "active_character", "") or "klee")
    return _char_store().active_id()


def _kb_context(text: str, cid: str = "") -> str:
    """网页对话的知识库注入：按角色查对应的库，命中就附在 system 里。失败静默跳过。"""
    if not text:
        return ""
    cid = str(cid or "").strip().lower() or _active_cid()
    try:
        body = json.dumps({"text": text, "k": 4, "threshold": 0.4,
                           "max_chars": 900, "character": cid}).encode("utf-8")
        req = urllib.request.Request(KB_URL.rstrip("/") + "/search", data=body,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=6) as r:          # noqa: S310
            d = json.loads(r.read().decode("utf-8", "replace") or "{}")
    except Exception:                                               # noqa: BLE001
        return ""
    chunks = d.get("hits") or d.get("chunks") or d.get("results") or []
    out = []
    for c in chunks[:4]:
        if isinstance(c, dict):
            t = str(c.get("text") or c.get("content") or "").strip()
            src = str(c.get("source") or c.get("title") or "").strip()
            if t:
                out.append(f"（{src}）{t[:500]}" if src else t[:500])
    return "\n".join(out)[:1200]


def _persona_text(cid: str = "") -> str:
    """网页对话用的人格：当前角色自己的 SOUL.md。读不到给带角色名的兜底。"""
    store = _char_store()
    cid = str(cid or "").strip().lower() or store.active_id()
    info = store.get(cid) or {}
    name = info.get("name") or "角色"
    try:
        text = store.soul_path(cid).read_text(encoding="utf-8").strip()
    except OSError as e:
        logger.warning("读取角色 %s 的 SOUL 失败（%s），用兜底人格", cid, e)
        text = ""
    return text or f"你是{name}。像朋友一样用短句聊天，别用 Markdown。"


def _persona_get(cid: str = "") -> dict:
    store = _char_store()
    cid = str(cid or "").strip().lower() or store.active_id()
    info = store.get(cid)
    if not info:
        return {"ok": False, "error": f"没有角色 {cid!r}"}
    p = store.soul_path(cid)
    try:
        soul = p.read_text(encoding="utf-8")
    except OSError as e:
        return {"ok": False, "error": str(e)}
    st = p.stat()
    return {"ok": True, "character": cid, "name": info["name"], "soul": soul,
            "chars": len(soul), "est_tokens": int(len(soul) * 0.65), "mtime": st.st_mtime}


# ---------- 插件 ----------
def _char_store() -> CharacterStore:
    if BOT is not None and getattr(BOT, "characters", None) is not None:
        return BOT.characters
    return CharacterStore(STATE_DIR)


_KB_HEALTH = {"ts": 0.0, "data": {}}


def _kb_health(max_age: float = 3.0) -> dict:
    """短缓存：角色页/导入轮询会连续打这个接口，别每次都等 KB。"""
    now = time.time()
    if now - _KB_HEALTH["ts"] < max_age and _KB_HEALTH["data"]:
        return _KB_HEALTH["data"]
    try:
        with urllib.request.urlopen(KB_URL + "/healthz", timeout=4) as r:  # noqa: S310
            data = json.loads(r.read().decode("utf-8", "replace") or "{}")
        _KB_HEALTH.update(ts=now, data=data)
        return data
    except Exception:                                            # noqa: BLE001
        return _KB_HEALTH["data"] or {}


UI_PREFS = STATE_DIR / "ui_prefs.json"
UI_DEFAULTS = {
    "accent": "", "accent2": "",
    "radius": 20, "density": "comfy", "font_scale": 100,
    "bg": "aurora", "bubble": "comic", "effects": True,
    "card_style": "glass",
    "modules": ["status", "quick", "recent", "char", "plugins", "kb"],
}
UI_OPTIONS = {
    "density": ["compact", "comfy", "wide"],
    "bg": ["aurora", "sakura", "grid", "plain"],
    "bubble": ["comic", "round", "minimal"],
    "card_style": ["glass", "solid", "outline"],
    "modules": ["status", "quick", "recent", "char", "plugins", "kb", "ops", "requests"],
}
_HEX_RE = re.compile(r"^#[0-9a-fA-F]{3,8}$")


def _ui_prefs(cid: str) -> dict:
    try:
        d = json.loads(UI_PREFS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        d = {}
    saved = (d.get("characters") or {}).get(cid) if isinstance(d, dict) else None
    out = dict(UI_DEFAULTS)
    if isinstance(saved, dict):
        out.update({k: v for k, v in saved.items() if k in UI_DEFAULTS})
    return out


def _ui_clean(raw: dict) -> dict:
    """只接受已知键 + 严格值域；非法值直接丢弃（不 500、不写坏文件）。"""
    out = {}
    if not isinstance(raw, dict):
        return out
    for key in ("accent", "accent2"):
        v = raw.get(key)
        if isinstance(v, str) and (v == "" or _HEX_RE.match(v)):
            out[key] = v
    if "radius" in raw:
        try:
            out["radius"] = max(6, min(34, int(raw["radius"])))
        except (TypeError, ValueError):
            pass
    if "font_scale" in raw:
        try:
            out["font_scale"] = max(85, min(120, int(raw["font_scale"])))
        except (TypeError, ValueError):
            pass
    for key in ("density", "bg", "bubble", "card_style"):
        v = raw.get(key)
        if isinstance(v, str) and v in UI_OPTIONS[key]:
            out[key] = v
    if "effects" in raw:
        out["effects"] = bool(raw["effects"])
    if "modules" in raw and isinstance(raw["modules"], list):
        mods = [str(x) for x in raw["modules"] if str(x) in UI_OPTIONS["modules"]]
        out["modules"] = list(dict.fromkeys(mods))[:12]
    return out


def _ui_get(cid: str) -> dict:
    return {"ok": True, "character": cid, "prefs": _ui_prefs(cid),
            "defaults": UI_DEFAULTS, "options": UI_OPTIONS}


UI_LOCK = threading.Lock()


def _ui_save(cid: str, patch: dict) -> dict:
    clean = _ui_clean(patch)
    with UI_LOCK:
        return _ui_save_locked(cid, clean)


def _ui_save_locked(cid: str, clean: dict) -> dict:
    try:
        d = json.loads(UI_PREFS.read_text(encoding="utf-8"))
        if not isinstance(d, dict):
            d = {}
    except (OSError, ValueError):
        d = {}
    chars = d.get("characters")
    if not isinstance(chars, dict):
        chars = {}
    cur = chars.get(cid) if isinstance(chars.get(cid), dict) else {}
    cur.update(clean)
    chars[cid] = cur
    d["characters"] = chars
    tmp = UI_PREFS.with_name(f"{UI_PREFS.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(UI_PREFS)
    return {"ok": True, "character": cid, "prefs": _ui_prefs(cid), "saved": clean}


# ---------- 角色 ZIP 导入 ----------
KB_DIR = STUDIO_HOME.parent / "klee-kb"
IMPORT_MAX_BYTES = 64 * 1024 * 1024
IMPORT_MAX_FILES = 240
IMPORT_ALLOWED = {".json", ".md", ".py", ".css", ".txt", ".jsonl",
                  ".jpg", ".jpeg", ".png", ".webp"}
IMPORT_ID = re.compile(r"^[a-z][a-z0-9_-]{1,29}$")
IMPORT_CHARS = re.compile(r"^CHARACTERS\s*=\s*\(([^)]*)\)", re.M)


class ImportError_(Exception):
    pass


def _zip_rel(name: str) -> str:
    """规范化 zip 内路径；拒绝绝对路径、上跳、盘符。"""
    raw = str(name or "").replace("\\", "/")
    if raw.startswith("/") or re.match(r"^[A-Za-z]:", raw):
        raise ImportError_("zip 里有非法路径")
    n = raw.strip("/")
    if not n:
        raise ImportError_("zip 里有非法路径")
    parts = []
    for seg in n.split("/"):
        if seg in ("", "."):
            continue
        if seg == "..":
            raise ImportError_("zip 里有上跳路径（..）")
        parts.append(seg)
    if not parts:
        raise ImportError_("zip 里有空路径")
    return "/".join(parts)


def _zip_read(zf: zipfile.ZipFile, name: str) -> str:
    try:
        data = zf.read(name)
    except KeyError:
        raise ImportError_(f"zip 里缺少 {name}") from None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        raise ImportError_(f"{name} 不是 UTF-8 文本") from None


def _install_plugins(files: dict[str, str], cid: str) -> list[str]:
    """把 plugins/*.py 装进 features；没写 CHARACTERS 的自动补成该角色限定。"""
    installed = []
    for rel, text in files.items():
        fname = Path(rel).name
        if not fname.endswith(".py") or fname.startswith("_"):
            raise ImportError_(f"插件文件名不合法：{rel}（不能以下划线开头）")
        dest = FEATURES_DIR / fname
        if dest.exists():
            raise ImportError_(f"插件 {fname} 已存在，换一个文件名")
        m = IMPORT_CHARS.search(text)
        if m:
            ids = {x.strip().strip("\"'") for x in m.group(1).split(",") if x.strip()}
            if cid not in ids:
                raise ImportError_(f"{fname} 的 CHARACTERS 没包含 {cid}")
        else:
            text = text.rstrip() + f'\n\nCHARACTERS = ("{cid}",)   # ZIP 导入时自动补上\n'
        compile(text, fname, "exec")          # 语法错误在写入前就拦住
        tmp = dest.with_name(f".{fname}.{os.getpid()}.tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(dest)
        installed.append(fname)
    return installed


def _build_kb_async(cid: str, name: str) -> None:
    """后台建索引 + 重启 klee-kb；失败只记日志，不影响导入结果。"""
    def run() -> None:
        try:
            py = KB_DIR / "py"
            proc = subprocess.run(
                [str(py), "build_index.py", "--character", cid, "--name", name,
                 "--corpus", f"corpus/{cid}"],
                cwd=str(KB_DIR), capture_output=True, text=True, timeout=1800)
            if proc.returncode != 0:
                logger.error("KB 建索引失败 %s：%s", cid, (proc.stderr or proc.stdout)[-800:])
                return
            subprocess.run(["systemctl", "restart", "klee-kb"], timeout=90, check=False)
            logger.info("KB 建索引完成：%s", cid)
        except Exception as e:                                     # noqa: BLE001
            logger.error("KB 建索引异常 %s：%s", cid, e)

    threading.Thread(target=run, name=f"kb-build-{cid}", daemon=True).start()


PLUGIN_MAX_BYTES = 2 * 1024 * 1024
PLUGIN_MAX_FILES = 20
PLUGIN_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{1,40}\.py$")


def _plugin_zip_files(raw: bytes) -> dict[str, str]:
    if not raw:
        raise ImportError_("空文件")
    if len(raw) > PLUGIN_MAX_BYTES:
        raise ImportError_("插件包超过 2MB")
    try:
        zf = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile:
        raise ImportError_("不是有效的 zip") from None
    infos = [i for i in zf.infolist() if not i.is_dir()]
    if len(infos) > PLUGIN_MAX_FILES:
        raise ImportError_(f"文件太多（>{PLUGIN_MAX_FILES}）")
    if sum(int(i.file_size) for i in infos) > PLUGIN_MAX_BYTES * 2:
        raise ImportError_("解压后超过 4MB")
    names = [_zip_rel(i.filename) for i in infos]
    tops = {n.split("/")[0] for n in names}
    prefix = (next(iter(tops)) + "/") if len(tops) == 1 and all("/" in n for n in names) else ""
    out: dict[str, str] = {}
    for info, n in zip(infos, names):
        rel = n[len(prefix):] if prefix and n.startswith(prefix) else n
        if "/" in rel:
            raise ImportError_(f"插件文件不能放在子目录：{rel}")
        if rel in ("plugin.json", "README.md"):
            continue
        if not rel.endswith(".py"):
            raise ImportError_(f"不支持的文件：{rel}（只能放 .py）")
        if not PLUGIN_NAME_RE.match(rel):
            raise ImportError_(f"文件名不合法：{rel}（小写字母开头，字母/数字/_）")
        try:
            out[rel] = zf.read(info).decode("utf-8")
        except UnicodeDecodeError:
            raise ImportError_(f"{rel} 不是 UTF-8") from None
    if not out:
        raise ImportError_("zip 里没有 .py 插件")
    return out


def _import_plugin_zip(raw: bytes, overwrite: bool = False) -> dict:
    files = _plugin_zip_files(raw)
    known = {c["id"] for c in _char_store().list()}
    for fname, text in files.items():
        try:
            compile(text, fname, "exec")
        except SyntaxError as e:
            raise ImportError_(f"{fname} 语法错误：{e.msg}（第 {e.lineno} 行）") from None
        m = IMPORT_CHARS.search(text)
        if m:
            ids = {x.strip().strip("\"'") for x in m.group(1).split(",") if x.strip()}
            unknown = ids - known
            if unknown:
                raise ImportError_(f"{fname} 的 CHARACTERS 含未知角色：{', '.join(sorted(unknown))}")
        dest = FEATURES_DIR / fname
        if dest.exists() and not overwrite:
            raise ImportError_(f"{fname} 已存在（勾选覆盖才会替换）")
    # 两阶段：先把每个文件都写成临时文件，全部成功后再逐个 replace。
    # 这样中途失败只会留下/删掉临时文件，绝不会把「已经被替换掉的旧插件」当成回滚目标删掉。
    staged = []
    try:
        for fname, text in files.items():
            dest = FEATURES_DIR / fname
            tmp = dest.with_name(f".{fname}.{os.getpid()}.{time.time_ns() % 1_000_000}.tmp")
            tmp.write_text(text, encoding="utf-8")
            staged.append((tmp, dest))
        for tmp, dest in staged:
            tmp.replace(dest)          # 覆盖就是覆盖：不留旧版（这个项目没有回档）
    except OSError as e:
        for tmp, _dest in staged:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
        raise ImportError_(f"写入失败：{e}") from None
    RELOAD_MARK.touch()
    return {"ok": True, "installed": written, "overwrite": overwrite,
            "note": "插件已安装，8 秒内热重载"}


def _import_character_zip(raw: bytes) -> dict:
    if not raw:
        raise ImportError_("空文件")
    if len(raw) > IMPORT_MAX_BYTES:
        raise ImportError_("zip 超过 64MB")
    try:
        zf = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile:
        raise ImportError_("不是有效的 zip") from None

    infos = [i for i in zf.infolist() if not i.is_dir()]
    if len(infos) > IMPORT_MAX_FILES:
        raise ImportError_(f"zip 里文件太多（>{IMPORT_MAX_FILES}）")
    total = sum(int(i.file_size) for i in infos)
    if total > IMPORT_MAX_BYTES:
        raise ImportError_("解压后超过 64MB")
    names = [_zip_rel(i.filename) for i in infos]
    # 允许所有文件放在一个顶层目录里
    tops = {n.split("/")[0] for n in names}
    prefix = (next(iter(tops)) + "/") if len(tops) == 1 and all("/" in n for n in names) else ""
    files: dict[str, bytes] = {}
    for info, n in zip(infos, names):
        rn = n[len(prefix):] if prefix and n.startswith(prefix) else n
        suf = Path(rn).suffix.lower()
        if suf not in IMPORT_ALLOWED:
            raise ImportError_(f"不支持的文件类型：{rn}")
        files[rn] = zf.read(info)

    def text_of(key: str) -> str:
        if key not in files:
            raise ImportError_(f"zip 里缺少 {key}")
        try:
            return files[key].decode("utf-8")
        except UnicodeDecodeError:
            raise ImportError_(f"{key} 不是 UTF-8 文本") from None

    payload = json.loads(text_of("character.json"))
    if not isinstance(payload, dict):
        raise ImportError_("character.json 必须是对象")
    cid = str(payload.get("id") or "").strip().lower()
    if not IMPORT_ID.match(cid):
        raise ImportError_("id 只能是小写字母开头、2-30 位的字母/数字/-/_")
    if _char_store().get(cid):
        raise ImportError_(f"角色 {cid} 已存在（不能覆盖）")
    soul = text_of("SOUL.md")
    if len(soul.strip()) < 80:
        raise ImportError_("SOUL.md 太短（至少 80 字）")
    if len(soul) > 60000:
        raise ImportError_("SOUL.md 超过 60000 字")

    theme_files = {k[len("theme/"):]: v for k, v in files.items() if k.startswith("theme/")}
    if "theme.json" not in theme_files:
        raise ImportError_("缺少 theme/theme.json")
    try:
        meta = json.loads(theme_files["theme.json"].decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise ImportError_("theme/theme.json 不是合法 JSON") from None
    if not isinstance(meta, dict) or not str(meta.get("name") or "").strip():
        raise ImportError_("theme/theme.json 至少要有一个 name")

    plugin_files = {k[len("plugins/"):]: v.decode("utf-8", "replace")
                    for k, v in files.items() if k.startswith("plugins/")}
    if not plugin_files:
        raise ImportError_("至少放一个角色限定插件到 plugins/")
    if len(plugin_files) > 60:
        raise ImportError_("插件太多（>60）")

    kb_lines = []
    for k, v in files.items():
        if not k.startswith("kb/"):
            continue
        try:
            txt = v.decode("utf-8")
        except UnicodeDecodeError:
            raise ImportError_(f"{k} 不是 UTF-8") from None
        for line in txt.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                raise ImportError_(f"{k} 有非 JSON 行") from None
            if isinstance(row, dict) and str(row.get("text") or "").strip():
                kb_lines.append(row)
    if not kb_lines:
        raise ImportError_("knowledge/ 里没有可用语料（kb/*.jsonl，每行要有 text）")

    # —— 落盘 ——
    # 先把主题写到临时目录，全部写完再一次性换掉旧目录：
    # 中途写失败不会留下半个主题，也不会先把能用的旧主题删掉。
    theme_root = STUDIO_HOME / "bot" / "ui_themes"
    theme_dest = theme_root / cid
    staging = theme_root / f".{cid}.{os.getpid()}.staging"
    if staging.exists():
        shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True, exist_ok=True)
    try:
        for name, data in theme_files.items():
            rel = _zip_rel(name)
            out = staging / rel
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(data)
        if theme_dest.is_symlink() or theme_dest.is_file():
            theme_dest.unlink()        # 目录才是正常形态；符号链接/文件直接替换
        elif theme_dest.exists():
            shutil.rmtree(theme_dest)  # 覆盖安装 = 替换，不留旧主题
        staging.rename(theme_dest)
    except OSError as e:
        shutil.rmtree(staging, ignore_errors=True)
        raise ImportError_(f"主题写入失败：{e}") from None

    installed = _install_plugins(plugin_files, cid)

    corpus = KB_DIR / "corpus" / cid / "processed"
    corpus.mkdir(parents=True, exist_ok=True)
    with (corpus / "imported.jsonl").open("w", encoding="utf-8") as f:
        for row in kb_lines:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    payload = {**payload, "theme": cid, "seed": f"{cid}.md"}
    info = _char_store().add(payload, soul)
    RELOAD_MARK.touch()
    _build_kb_async(cid, str(info.get("name") or cid))
    return {"ok": True, "character": info, "theme": cid, "plugins": installed,
            "kb_entries": len(kb_lines), "building": True,
            "note": "角色已创建；知识库索引在后台构建（约 1-3 分钟）"}


_CHAR_CACHE = {"ts": 0.0, "data": None}
_CHAR_LOCK = threading.Lock()


def _characters_get(max_age: float = 2.0) -> dict:
    """角色列表（带插件/主题/知识库统计）一次要 ~40ms；面板会反复拉，2 秒内复用。"""
    now = time.time()
    with _CHAR_LOCK:
        if _CHAR_CACHE["data"] is not None and now - _CHAR_CACHE["ts"] < max_age:
            return _CHAR_CACHE["data"]
    data = _characters_uncached()
    with _CHAR_LOCK:
        _CHAR_CACHE.update(ts=now, data=data)
    return data


def _characters_uncached() -> dict:
    store = _char_store()
    active = store.active_id()
    health = _kb_health()
    kb_chars = health.get("characters") or {}
    snap = {}
    if BOT is not None and getattr(BOT, "runtime", None) is not None:
        try:
            snap = BOT.runtime.mgr.snapshot()
        except Exception:                                        # noqa: BLE001
            snap = {}
    plugins = snap.get("plugins") or []
    items = []
    for c in store.list():
        cid = c["id"]
        soul = store.soul(cid)
        own = [p for p in plugins if cid in (p.get("characters") or [])]
        theme_info = theme_mod.get(c.get("theme") or c["id"])
        items.append({
            **{k: c[k] for k in ("id", "name", "en", "emoji", "tagline", "color", "color2",
                                "kb", "theme", "greeting", "hero_note", "chips")},
            "theme_name": theme_info["name"], "theme_version": theme_info["version"],
            "active": cid == active,
            "builtin": bool(c.get("builtin")),
            "soul_chars": len(soul),
            "kb": kb_chars.get(c["kb"]) or kb_chars.get(cid) or {},
            "plugins": [{"name": p.get("name"), "title": p.get("title"),
                         "enabled": bool(p.get("enabled"))} for p in own],
        })
    return {"ok": True, "active": active, "items": items,
            "kb_online": bool(health.get("ok")), "kb_default": health.get("default", "")}


def _requests(n: int = 30) -> list:
    rows = _read_jsonl(STATE_DIR / "requests.jsonl")
    items = [r for r in rows if isinstance(r, dict) and r.get("kind")]
    return items[-n:][::-1]


def _plugin_meta() -> dict:
    """插件元数据（title/description/usage/author/schema），来自运行中的 manager。"""
    if not BOT or not getattr(BOT, "runtime", None):
        return {}
    try:
        snap = BOT.runtime.mgr.snapshot().get("plugins") or []
    except Exception:                                            # noqa: BLE001
        return {}
    return {str(p.get("name")): p for p in snap if isinstance(p, dict)}


def _plugin_schema(name: str) -> dict:
    meta = _plugin_meta().get(Path(name).stem) or {}
    schema = meta.get("schema") if isinstance(meta.get("schema"), dict) else {}
    cfg = (_plugins_get().get("config") or {})
    settings = (cfg.get("settings") or {}).get(Path(name).stem) or {}
    values = {k: settings.get(k, f.get("default")) for k, f in schema.items()}
    return {"ok": True, "name": Path(name).stem, "title": meta.get("title", name),
            "usage": meta.get("usage", ""), "author": meta.get("author", ""),
            "schema": schema, "values": values, "has_schema": bool(schema)}


_PLUG_CACHE = {"ts": 0.0, "data": None}


def _plugins_get(max_age: float = 2.0) -> dict:
    """插件页要读 config.json + 47 个文件头；2 秒内复用。"""
    now = time.time()
    if _PLUG_CACHE["data"] is not None and now - _PLUG_CACHE["ts"] < max_age:
        return _PLUG_CACHE["data"]
    data = _plugins_uncached()
    _PLUG_CACHE.update(ts=now, data=data)
    return data


def _plugins_uncached() -> dict:
    cfg = None
    try:
        cfg = json.loads((FEATURES_DIR / "config.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    files = []
    try:
        for p in sorted(FEATURES_DIR.glob("*.py")):
            if p.name.startswith("_"):
                continue
            st = p.stat()
            files.append({"name": p.name, "size": st.st_size, "mtime": st.st_mtime})
    except OSError:
        pass
    return {"ok": True, "config": cfg, "files": files, "dir": str(FEATURES_DIR),
            "meta": _plugin_meta()}


# ---------- 消息流（直读会话库） ----------
def _feed(limit: int = 80, src: str = "") -> list:
    """src: "" = 全部 | "web" = 网页/其他 | 具体 chat_id（如 private:10001 / group:12345）

    顺序：最新在上（DESC）。
    """
    rows = [e for e in _read_jsonl(MSG_JSONL)
            if e.get("role") in ("user", "assistant") and e.get("text")]
    if src == "web":
        rows = [e for e in rows if str(e.get("chat_id", "")).startswith("web:")]
    elif src:
        rows = [e for e in rows if str(e.get("chat_id", "")) == src]
    rows = rows[-limit:]
    out = []
    for e in reversed(rows):
        cid = str(e.get("chat_id") or "")
        if cid.startswith("group:"):
            where = "QQ 群 · " + cid.split(":")[-1]
        elif cid.startswith("private:"):
            where = "QQ 私聊 · " + cid.split(":")[-1]
        elif cid.startswith("web:"):
            where = "网页 · " + cid.split(":", 1)[-1]
        else:
            where = "其他"
        out.append({"ts": float(e.get("ts") or 0), "where": where,
                    "role": e.get("role"), "text": str(e.get("text"))[:800]})
    return out


KB_HITS = STATE_DIR / "kb_hits.jsonl"


def _kb_recent(n: int = 20, character: str = "") -> list:
    """读「最近命中」日志的末尾 n 行（新 → 旧）。文件不存在/读不动都返回空表。

    适配器（adapter.write_kb_hit）每次真的把 KB 内容注给可莉就追一行：
    {ts, query, chars, hits:[{source,title,dense}]}
    """
    if not KB_HITS.is_file():
        return []
    tail_bytes = 262144
    try:
        size = KB_HITS.stat().st_size
        with KB_HITS.open("rb") as f:
            if size > tail_bytes:
                f.seek(-tail_bytes, os.SEEK_END)
            raw = f.read().decode("utf-8", "replace")
    except OSError:
        return []
    lines = [ln for ln in raw.splitlines() if ln.strip()]
    if size > tail_bytes and lines:
        lines = lines[1:]                       # 第一行可能被 seek 切残，丢掉
    out = []
    for ln in reversed(lines[-max(1, n):]):
        try:
            row = json.loads(ln)
        except ValueError:
            continue
        if not isinstance(row, dict):
            continue
        cid = str(row.get("character") or "klee")      # 旧日志没这个字段 = 可莉
        if character and cid != character:
            continue
        out.append(row)
    return out


def _feed_sources() -> list:
    """「消息」页的来源筛选项：QQ 私聊/群（按号码）+ 网页。"""
    agg: dict = {}
    for e in _read_jsonl(MSG_JSONL):
        if e.get("role") not in ("user", "assistant") or not e.get("text"):
            continue
        cid = str(e.get("chat_id") or "")
        cur = agg.setdefault(cid, [0, 0.0])
        cur[0] += 1
        cur[1] = max(cur[1], float(e.get("ts") or 0))
    out = [{"value": "", "label": "全部来源"}]
    web = 0
    for cid, (cnt, _ts) in sorted(agg.items(), key=lambda kv: -kv[1][0]):
        if cid.startswith("group:"):
            out.append({"value": cid, "label": f"QQ 群 · {cid.split(':')[-1]}（{cnt} 条）"})
        elif cid.startswith("private:"):
            out.append({"value": cid, "label": f"QQ 私聊 · {cid.split(':')[-1]}（{cnt} 条）"})
        elif cid.startswith("web:"):
            web += cnt
    if web:
        out.append({"value": "web", "label": f"网页 / 其他（{web} 条）"})
    return out


# ---------- 看板（消息量 / 活跃来源 / 好感度 Top5） ----------

AFFINITY_JSON = FEATURES_DIR / "state" / "affinity.json"

# 与 onebot_features/affinity.py 的 LEVELS 同源：这里优先用 ast 从插件源码里读，
# 读不出来才退回这份兜底（避免面板和插件各写一套等级表越跑越偏）。
LEVELS_FALLBACK = [(0, "路过蒙德的旅人"), (50, "眼熟的路人"), (150, "聊得来的朋友"),
                   (350, "骑士团的好朋友"), (700, "蒙德城的老熟人"),
                   (1200, "火花骑士的挚友"), (2000, "嘟嘟可的守护者")]


def _levels() -> list:
    try:
        tree = ast.parse((FEATURES_DIR / "affinity.py").read_text(encoding="utf-8"))
        for node in tree.body:
            if not isinstance(node, ast.Assign):
                continue
            if not any(isinstance(t, ast.Name) and t.id == "LEVELS" for t in node.targets):
                continue
            val = ast.literal_eval(node.value)
            if isinstance(val, list) and val:
                return [(int(x[0]), str(x[1])) for x in val]
    except Exception:                                          # noqa: BLE001
        pass
    return list(LEVELS_FALLBACK)


def _level_of(exp: int, levels: list):
    """→ (等级号, 称号, 本级起点, 下一级门槛|None)，口径与 affinity.py 一致。"""
    idx = 0
    for i, (need, _) in enumerate(levels):
        if exp >= need:
            idx = i
    nxt = levels[idx + 1][0] if idx + 1 < len(levels) else None
    return idx + 1, levels[idx][1], levels[idx][0], nxt


def _dashboard(days: int = 14) -> dict:
    """看板数据：消息量（今日/累计/近 N 天逐日）+ 活跃来源 + 好感度 Top5。

    只读：state.db 用 `mode=ro` 打开，affinity.json 只读文本；任何一步失败都返回
    带 `errors` 的降级结果，绝不让面板整页 500。
    """
    days = max(1, min(int(days or 14), 60))
    now = time.time()
    levels = _levels()
    out: dict = {"ok": True, "generated": now, "days": days,
                 "messages": {"today": 0, "total": 0, "user": 0, "assistant": 0},
                 "daily": [], "sources": [], "affinity": [], "errors": []}

    today = time.strftime("%Y-%m-%d", time.localtime(now))
    day_rows, src_map = {}, {}
    total = n_user = n_bot = 0
    try:
        for e in _read_jsonl(MSG_JSONL):
            if e.get("role") not in ("user", "assistant") or not e.get("text"):
                continue
            ts = float(e.get("ts") or 0)
            if ts <= 0:
                continue
            d = time.strftime("%Y-%m-%d", time.localtime(ts))
            day_rows[d] = day_rows.get(d, 0) + 1
            total += 1
            if e.get("role") == "user":
                n_user += 1
            else:
                n_bot += 1
            cid = str(e.get("chat_id") or "")
            cur = src_map.setdefault(cid, {"n": 0, "last": 0.0})
            cur["n"] += 1
            cur["last"] = max(cur["last"], ts)
        src_rows = sorted(
            ((cid, "group" if cid.startswith("group:") else
              ("dm" if cid.startswith("private:") else "web"), v["n"], v["last"])
             for cid, v in src_map.items()), key=lambda x: -x[2])
    except Exception as e:                                     # noqa: BLE001
        out["errors"].append(f"messages.jsonl: {type(e).__name__}: {e}")
        day_rows, src_rows = {}, []

    out["messages"] = {"today": int(day_rows.get(today, 0)), "total": int(total or 0),
                       "user": int(n_user or 0), "assistant": int(n_bot or 0)}
    # 逐日补齐（没消息的那天也要有 0，前端画柱状图才不会错位）
    daily = []
    for i in range(days - 1, -1, -1):
        d = time.strftime("%Y-%m-%d", time.localtime(now - i * 86400))
        daily.append({"date": d, "label": d[5:], "n": int(day_rows.get(d, 0))})
    out["daily"] = daily

    for cid, ctype, n, last in src_rows[:8]:
        cid = str(cid or "")
        if ":onebot:" in cid or cid.startswith(("group:", "private:")) or ctype in ("group", "dm"):
            kind = "群" if (ctype == "group" or cid.startswith("group:")) else "私聊"
            label = f"QQ {kind} · {cid.split(':')[-1]}"
        else:
            kind, label = "网页", f"网页 / 其他 · {cid or '默认'}"
        out["sources"].append({"label": label, "kind": kind, "n": int(n or 0),
                               "last": float(last or 0)})

    try:
        aff = json.loads(AFFINITY_JSON.read_text(encoding="utf-8"))
        aff = aff if isinstance(aff, dict) else {}
    except (OSError, ValueError):
        aff = {}
    rows = []
    for u, v in aff.items():
        try:
            exp = int((v or {}).get("exp", 0))
        except (AttributeError, TypeError, ValueError):
            continue
        if exp > 0:
            rows.append((exp, str(u)))
    rows.sort(key=lambda x: (-x[0], x[1]))
    for exp, uid in rows[:5]:
        rec = aff.get(uid) or {}
        lv, title, lo, nxt = _level_of(exp, levels)
        # 目前没有可靠的花名来源（messages.jsonl 不带 sender 名），统一用 UID 尾号，
        # 别拿空字典假装查过名字（OCR #43）
        nm = f"旅行者·{uid[-4:]}"
        out["affinity"].append({"uid": uid, "name": nm, "exp": exp, "level": lv, "title": title,
                                "prev_at": lo, "next_at": nxt,
                                "signed": rec.get("signed") == today,
                                "today_msgs": int(rec.get("day_msgs") or 0)})
    out["affinity_total"] = len(rows)
    return out


# ---------- 状态条 ----------
_OVERVIEW_CACHE: dict = {"ts": 0.0, "data": {}}


def _overview(max_age: float = 2.0) -> dict:
    """面板顶栏 + 看板都会打这个接口；2 秒内复用，避免重复读插件快照。"""
    now = time.time()
    if _OVERVIEW_CACHE["data"] and now - _OVERVIEW_CACHE["ts"] < max_age:
        return _OVERVIEW_CACHE["data"]
    data = _overview_uncached()
    _OVERVIEW_CACHE.update(ts=now, data=data)
    return data


def _overview_uncached() -> dict:
    bst = BOT.status() if BOT else {"ok": False, "error": _BOT_IMPORT_ERR or "bot 未启动"}
    stats = bst.get("stats") or {}
    adapter = {"connected": bool(bst.get("online")),
               "age": bst.get("last_event_age_s"),
               "uptime": bst.get("uptime_s") or 0,
               "in": stats.get("events", 0), "stats": stats,
               "features": bst.get("feature_list") or []}
    nap = _napcat_status_cached()
    qlen = _chat_q.qsize()
    with _lock:
        running = _chat_state["running"]
    return {"gateway": {"active": bool(bst.get("ok")), "state": "panel-bot",
                        "restarts": "0", "since": ""},
            "adapter": adapter, "napcat": nap,
            "alerts": read_alerts(5),
            "health": qq_health(),
            "chat": {"queue": qlen, "running": running},
            "bot": bst}


def _napcat_status_cached() -> dict:
    now = time.time()
    with _lock:
        if _napcat_cache["data"] and now - _napcat_cache["ts"] < 20:
            return _napcat_cache["data"]
    result = {"ok": False}
    try:
        cfg = json.loads(NAPCAT_WEBUI.read_text(encoding="utf-8"))
        base = f"http://127.0.0.1:{cfg['port']}"

        def call(path: str, body: dict, cred: str = "") -> dict:
            hdr = {"Content-Type": "application/json"}
            if cred:
                hdr["Authorization"] = "Bearer " + cred
            req = urllib.request.Request(base + "/api" + path, data=json.dumps(body).encode(), headers=hdr)
            return json.loads(urllib.request.urlopen(req, timeout=8).read())

        h = hashlib.sha256((cfg["token"] + ".napcat").encode()).hexdigest()
        cred = call("/auth/login", {"hash": h})["data"]["Credential"]
        st = call("/QQLogin/CheckLoginStatus", {}, cred).get("data") or {}
        try:
            who = call("/QQLogin/GetQQLoginInfo", {}, cred).get("data") or {}
        except Exception:                                     # noqa: BLE001
            who = {}
        result = {"ok": True, "is_login": bool(st.get("isLogin")),
                  "phase": st.get("loginPhase") or "",
                  "uin": str(who.get("uin") or ""), "nick": str(who.get("nick") or "")}
    except Exception:                                          # noqa: BLE001
        result = {"ok": False}
    with _lock:
        _napcat_cache.update({"ts": now, "data": result})
    return result


# ---------- HTTP ----------
def _models_get() -> dict:
    """给前端：provider 列表（keys 只暴露 has_key）。"""
    if not BOT:
        return {"ok": False, "error": _BOT_IMPORT_ERR or "bot 未启动"}
    data = BOT.models.data
    provs = []
    for p in BOT.models.providers():
        q = dict(p)
        key = os.environ.get(str(p.get("key_env") or ""), "")
        q["has_key"] = bool(key)
        q.pop("key", None)
        provs.append(q)
    safe = data if isinstance(data, dict) else {}
    safe = {**safe}
    if isinstance(safe.get("providers"), dict):
        safe["providers"] = {
            k: {kk: vv for kk, vv in v.items() if kk != "key"} if isinstance(v, dict) else v
            for k, v in safe["providers"].items()
        }
    return {"ok": True, "active": BOT.models.active(), "providers": provs,
            "vision": BOT.models.vision(),
            "agent": BOT.models.agent() if hasattr(BOT.models, "agent") else {},
            "data": safe}


def _agent_get() -> dict:
    from bot.agent import ALL_TOOL_NAMES, TOOLS, resolve_tools
    cfg = BOT.config
    return {
        "ok": True,
        "enabled": bool(cfg.get("agent_enabled", True)),
        "owner_only": bool(cfg.get("agent_owner_only", True)),
        "max_steps": int(cfg.get("agent_max_steps") or 3),
        "timeout": float(cfg.get("agent_timeout") or 120),
        "tools": resolve_tools(cfg.get("agent_tools")),
        "available_tools": [
            {"name": n, "description": TOOLS[n]["spec"]["function"]["description"]}
            for n in ALL_TOOL_NAMES],
        "model": BOT.models.agent() if hasattr(BOT.models, "agent") else {},
        "skills": bool(cfg.get("agent_skills", True)),
    }


# ---------- 响应缓存 ----------
# 静态文件：每次 stat 判断有没有变，命中就直接发内存里的字节（含预压缩结果）。
# RK3399 上 gzip 一份 176KB 的 index.html 要 ~70ms，之前每个请求都在重算。
_STATIC_CACHE: dict[str, tuple] = {}
_STATIC_LOCK = threading.Lock()

# 动态响应：按 ETag（= body 的 sha1）缓存压缩结果，相同内容只压一次。
_GZ_CACHE: "OrderedDict[str, bytes]" = OrderedDict()
_GZ_CACHE_MAX = 96
_GZ_LOCK = threading.Lock()


def _static_get(path: Path) -> tuple[bytes, str] | None:
    """读静态文件（带 mtime 缓存）。返回 (字节, ETag) 或 None。"""
    key = str(path)
    try:
        st = path.stat()
    except OSError:
        return None
    if not path.is_file():
        return None
    stamp = (st.st_mtime_ns, st.st_size)
    with _STATIC_LOCK:
        hit = _STATIC_CACHE.get(key)
        if hit and hit[0] == stamp:
            return hit[1], hit[2]
    try:
        data = path.read_bytes()
    except OSError:
        return None
    tag = '"' + hashlib.sha1(data).hexdigest()[:20] + '"'
    with _STATIC_LOCK:
        _STATIC_CACHE[key] = (stamp, data, tag)
        if len(_STATIC_CACHE) > 256:                    # 别让缓存无限长（角色图可能很多）
            for k in list(_STATIC_CACHE)[:64]:
                _STATIC_CACHE.pop(k, None)
    return data, tag


def _gz(body: bytes, tag: str) -> bytes:
    """压缩结果按 ETag 复用：同一份内容反复请求只压一次。"""
    with _GZ_LOCK:
        got = _GZ_CACHE.get(tag)
        if got is not None:
            _GZ_CACHE.move_to_end(tag)
            return got
    out = gzip.compress(body, 6)
    with _GZ_LOCK:
        _GZ_CACHE[tag] = out
        if len(_GZ_CACHE) > _GZ_CACHE_MAX:
            _GZ_CACHE.popitem(last=False)
    return out


_SENSITIVE_GET = frozenset({
    "/api/napcat/qr", "/api/requests", "/api/feed", "/api/feed/sources",
    "/api/logs", "/api/logs/meta", "/api/persona", "/api/chat",
    "/api/chat/sessions", "/qr.png", "/qr",
    # 下面是「个人数据/配置」，不登录也该挡：插件配置、看板、提醒、纪念日、知识库命中
    "/api/plugins", "/api/dashboard", "/api/reminders", "/api/anniversaries",
    "/api/kb/recent",
})


class Handler(BaseHTTPRequestHandler):
    server_version = "klee-studio/1.0"
    protocol_version = "HTTP/1.1"      # 默认是 1.0 = 每个请求新建 TCP；手机端首屏 20+ 请求差别很大
    timeout = 65                       # 空闲连接最多挂 65 秒（小于常见代理的 75s）
    # TCP_NODELAY：socket 默认开着 Nagle。响应是「头 + 体」两次小写，
    # 在 keep-alive 连接上会被对端的 delayed ACK 卡住 40ms（Go 边缘层实测 5ms → 48ms）。
    disable_nagle_algorithm = True

    def log_message(self, fmt, *args):                         # noqa: A003
        # 每个静态请求都往 journald 写一行，是这里最贵的一步（一次 ~2ms，还带磁盘 IO）。
        # 只留 4xx/5xx，正常访问不记。
        try:
            code = int(args[1]) if len(args) > 1 else 0
        except (TypeError, ValueError):
            code = 0
        if code and code < 400:
            return
        super().log_message(fmt, *args)

    def _accepts_gzip(self) -> bool:
        return "gzip" in (self.headers.get("Accept-Encoding") or "").lower()

    def _send_bytes(self, body: bytes, ctype: str, *, code: int = 200,
                    cache: str = "no-store", etag_body: bytes | None = None,
                    extra: list[tuple[str, str]] | None = None,
                    etag: str | None = None) -> None:
        """统一的字节响应：ETag/304 + gzip + 断连静默。"""
        tag = etag or ('"' + hashlib.sha1(
            etag_body if etag_body is not None else body).hexdigest()[:20] + '"')
        if self.headers.get("If-None-Match") == tag:
            self.send_response(304)
            self.send_header("ETag", tag)
            self.send_header("Cache-Control", cache)
            self.end_headers()
            return
        payload = body
        gz = self._accepts_gzip() and len(body) > 1024 and ctype.split(";")[0] in (
            "application/json", "text/html", "text/css", "text/javascript",
            "application/javascript", "image/svg+xml", "text/plain")
        if gz:
            payload = _gz(body, tag)      # 同一份内容只压一次（ETag 即 sha1）
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", cache)
        self.send_header("ETag", tag)
        if gz:
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Vary", "Accept-Encoding")
        for k, v in (extra or []):
            self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(payload)
        except (BrokenPipeError, ConnectionResetError):
            # 客户端超时/关页先走了（机器满载时很常见）——不是服务端错误，别往 journal 里灌 traceback
            pass

    def _json(self, obj, code: int = 200):
        body = json.dumps(obj, ensure_ascii=False).encode()
        # GET 允许缓存但每次校验（ETag → 304，轮询省流量）；POST/PUT 与敏感 GET 一律不存
        path = urlparse(self.path).path
        cache = ("no-cache, must-revalidate"
                 if self.command == "GET" and path not in _SENSITIVE_GET else "no-store")
        self._send_bytes(body, "application/json; charset=utf-8", code=code, cache=cache)

    def _body(self, max_bytes: int = MAX_BODY) -> dict:
        """读 JSON 请求体。负数/超限直接拒绝：`read(-1)` 会一直读到 EOF 把连接挂住。"""
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except (TypeError, ValueError):
            return {}
        if n <= 0:
            return {}
        if n > max_bytes:
            self._body_too_big = True
            return {}
        try:
            raw = self.rfile.read(n)
            data = json.loads(raw.decode("utf-8"))
        except (ValueError, OSError):
            return {}
        return data if isinstance(data, dict) else {}

    def _guard(self) -> bool:
        """面板写操作鉴权：默认 X-Klee-Studio: 1；设了 KLEE_STUDIO_TOKEN 则再校验它。"""
        if self.headers.get("X-Klee-Studio") != "1":
            self._json({"ok": False, "error": "缺少 X-Klee-Studio 头"}, 403)
            return False
        need = os.environ.get("KLEE_STUDIO_TOKEN") or ""
        if need:
            got = str(self.headers.get("X-Klee-Studio-Token") or "")
            if not hmac.compare_digest(got, need):
                self._json({"ok": False, "error": "面板 token 不匹配"}, 403)
                return False
        return True

    # ---- GET ----
    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        # 这些读接口会泄露登录码 / 聊天 / 日志 / 源码，不能因为「只读」就裸奔
        if u.path in _SENSITIVE_GET and not self._guard():
            return
        if u.path in ("/", "/index.html"):
            got = _static_get(STATIC / "index.html")
            if not got:
                self.send_error(500, "index.html missing")
                return
            data, tag = got
            self._send_bytes(data, "text/html; charset=utf-8", etag=tag,
                             cache="no-cache, must-revalidate")   # 治「手机上看到旧页面」
            return
        if u.path == "/api/overview":
            self._json(_overview())
            return
        if u.path == "/api/bot":
            self._json(BOT.status() if BOT else {"ok": False, "error": _BOT_IMPORT_ERR or "bot 未启动"})
            return
        if u.path == "/api/bot/metrics":
            if not BOT:
                self._json({"ok": False, "error": "bot 未启动"}, 503)
                return
            st = BOT.status()
            self._json({"ok": True, "metrics": st.get("metrics") or {},
                        "features": st.get("features") or {},
                        "inflight": st.get("inflight", 0),
                        "uptime_s": st.get("uptime_s")})
            return
        if u.path == "/api/models":
            self._json(_models_get())
            return
        if u.path == "/api/napcat/qr":
            self._json(qrimg.qr_info(with_b64=True))
            return
        if u.path == "/api/alerts":
            try:                                                # 坏参数不 500（面板/脚本都会乱传）
                n = max(1, min(200, int((q.get("n") or ["20"])[0])))
            except (TypeError, ValueError):
                n = 20
            self._json({"ok": True, "n": n, "health": qq_health(),
                        "alerts": read_alerts(n)})
            return
        if u.path in ("/qr.png", "/qr"):
            if not self._guard():
                return
            png, _decoded = qrimg.qr_bytes()
            if not png:
                self._json({"ok": False, "error": "没有二维码（可能已登录）"}, 404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(png)))
            self.send_header("Cache-Control", "no-store, must-revalidate")
            self.end_headers()
            self.wfile.write(png)
            return
        if u.path == "/api/chat":
            session = (q.get("session") or ["studio-main"])[0]
            after = _int_param(q, "after", 0, lo=0)
            cid = str((q.get("character") or [""])[0] or "").strip().lower() or _active_cid()
            entries = [e for e in _read_jsonl(CHAT_JSONL)
                       if e.get("session") == session
                       and str(e.get("character") or "klee") == cid]
            with _lock:
                running = _chat_state["running"]
            self._json({"session": session, "character": cid, "after": after,
                        "total": len(entries), "messages": entries[after:],
                        "queue": _chat_q.qsize(),
                        "running": running if running and running["session"] == session else None})
            return
        if u.path == "/api/chat/sessions":
            cid = str((q.get("character") or [""])[0] or "").strip().lower() or _active_cid()
            sessions = []
            for e in _read_jsonl(CHAT_JSONL):
                if str(e.get("character") or "klee") != cid:
                    continue
                s = e.get("session")
                if s and s not in sessions:
                    sessions.append(s)
            if "studio-main" not in sessions:
                sessions.insert(0, "studio-main")
            self._json({"sessions": sessions})
            return
        if u.path == "/api/docs":
            name = (q.get("name") or ["characters"])[0]
            files = {"characters": "CHARACTERS.md", "plugins": "PLUGINS.md",
                     "themes": "THEMES.md"}
            fn = files.get(str(name).strip().lower())
            if not fn:
                self._json({"ok": False, "error": "unknown doc"}, 404)
                return
            try:
                text = (STUDIO_HOME / "docs" / fn).read_text(encoding="utf-8")
            except OSError as e:
                self._json({"ok": False, "error": str(e)}, 404)
                return
            self._json({"ok": True, "name": name, "file": fn, "text": text})
            return
        if u.path == "/api/themes":
            items = theme_mod.list_themes()
            used = {c.get("theme"): c for c in _char_store().list()}
            for t in items:
                c = used.get(t["id"]) or {}
                t["used_by"] = c.get("name", "")
                t["used_by_id"] = c.get("id", "")
            self._json({"ok": True, "items": items, "dir": str(theme_mod.THEME_DIR)})
            return
        if u.path == "/api/theme.css":
            cid = (q.get("character") or [""])[0]
            info = _char_store().get(cid) if cid else _char_store().active()
            theme_id = (info or {}).get("theme") or cid or theme_mod.DEFAULT_THEME
            body = theme_mod.css_for(theme_id).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/css; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache, must-revalidate")
            self.end_headers()
            self.wfile.write(body)
            return
        if u.path == "/api/characters":
            self._json(_characters_get())
            return
        if u.path == "/api/ui":
            cid = (q.get("character") or [""])[0].strip().lower() or _char_store().active_id()
            self._json(_ui_get(cid))
            return
        if u.path == "/api/persona":
            self._json(_persona_get((q.get("character") or [""])[0]))
            return
        if u.path == "/api/plugins":
            self._json(_plugins_get())
            return
        if u.path == "/api/requests":
            n = _int_param(q, "n", 30, lo=1, hi=200)
            self._json({"ok": True, "items": _requests(n)})
            return
        if u.path == "/api/plugins/schema":
            name = (q.get("name") or [""])[0]
            if not name:
                self._json({"ok": False, "error": "缺少 name"}, 400)
                return
            self._json(_plugin_schema(name))
            return
        if u.path == "/api/feed":
            limit = _int_param(q, "limit", 80, lo=10, hi=200)
            src = (q.get("src") or [""])[0]
            self._json({"messages": _feed(limit, src), "src": src})
            return
        if u.path == "/api/feed/sources":
            self._json({"sources": _feed_sources()})
            return
        if u.path == "/api/kb":
            cid = (q.get("character") or [""])[0].strip().lower() or _active_cid()
            try:
                with urllib.request.urlopen(KB_URL + "/healthz", timeout=4) as r:
                    j = json.load(r)
                entry = ((j.get("characters") or {}).get(cid) or {})
                cfg = BOT.config if BOT is not None else {}
                self._json({"ok": True, "character": cid,
                            "threshold": float(cfg.get("kb_threshold") or 0.4),
                            "topk": int(cfg.get("kb_topk") or 3), **j, **entry})
            except Exception as e:  # noqa: BLE001
                self._json({"ok": False, "character": cid,
                            "error": f"{type(e).__name__}: {e}"})
            return
        if u.path == "/api/kb/recent":
            n = _int_param(q, "n", 20, lo=1, hi=100)
            cid = (q.get("character") or [""])[0].strip().lower() or _active_cid()
            self._json({"ok": True, "character": cid, "file": str(KB_HITS),
                        "hits": _kb_recent(n, cid)})
            return
        if u.path == "/api/kb/search":
            text = (q.get("q") or [""])[0].strip()
            k = _int_param(q, "k", 6, lo=1, hi=10)
            cid = (q.get("character") or [""])[0].strip().lower() or _active_cid()
            if not text:
                self._json({"ok": False, "error": "缺少 q"})
                return
            try:
                req = urllib.request.Request(
                    KB_URL + "/search",
                    data=json.dumps({"text": text, "k": k, "threshold": 0.0,
                                     "max_chars": 4000, "character": cid}).encode(),
                    headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=15) as r:
                    self._json(json.load(r))
            except Exception as e:  # noqa: BLE001
                self._json({"ok": False, "error": f"{type(e).__name__}: {e}"})
            return
        if u.path == "/api/skills":
            if not BOT or not getattr(BOT, "runtime", None):
                self._json({"ok": False, "error": "bot 未启动"}, 503)
                return
            hub = getattr(BOT.runtime, "skills", None)
            self._json({"ok": True, "skills": hub.skills() if hub else []})
            return
        if u.path == "/api/agent":
            if not BOT:
                self._json({"ok": False, "error": "bot 未启动"}, 503)
                return
            self._json(_agent_get())
            return
        if u.path == "/api/config":
            self._json(_read_config_keys())
            return
        if u.path == "/api/reminders":
            self._json(_reminders())
            return
        if u.path == "/api/anniversaries":
            self._json(_anniversaries())
            return
        if u.path == "/api/dashboard":
            days = _int_param(q, "days", 14, lo=1, hi=60)
            self._json(_dashboard(days))
            return
        if u.path == "/api/logs":
            n = _int_param(q, "n", 200, lo=10, hi=1000)
            grep = (q.get("grep") or [""])[0]
            enabled, days = _log_cfg()
            self._json({"lines": _tail_log(n, grep), "enabled": enabled, "days": days})
            return
        if u.path == "/api/logs/meta":
            enabled, days = _log_cfg()
            files = []
            for p in (GATEWAY_LOG,) + LOG_JSONL:
                try:
                    st = p.stat()
                    files.append({"name": p.name, "bytes": st.st_size,
                                  "mtime": int(st.st_mtime)})
                except OSError:
                    files.append({"name": p.name, "bytes": 0, "mtime": 0})
            self._json({"ok": True, "enabled": enabled, "days": days, "files": files})
            return
        if u.path.startswith("/static/"):
            rel = u.path[len("/static/"):]
            fp = (STATIC / rel).resolve()
            base = STATIC.resolve()
            if fp.is_file() and str(fp).startswith(str(base) + os.sep):
                ctype = mimetypes.guess_type(str(fp))[0]
                if not ctype:
                    ctype = {".ttf": "font/ttf", ".woff2": "font/woff2",
                             ".webp": "image/webp"}.get(fp.suffix, "application/octet-stream")
                got = _static_get(fp)
                if not got:
                    self.send_error(500)
                    return
                data, tag = got
                mtime = fp.stat().st_mtime
                self._send_bytes(data, ctype, etag=tag, cache="public, max-age=86400",
                                 extra=[("Last-Modified", email.utils.formatdate(mtime, usegmt=True))])
                return
            self.send_error(404)
            return
        self.send_error(404)

    # ---- POST ----
    def do_POST(self):
        u = urlparse(self.path)
        _CHAR_CACHE.update(ts=0.0, data=None)      # 写操作之后别再吐旧的角色/插件快照
        _PLUG_CACHE.update(ts=0.0, data=None)
        if u.path == "/api/plugins/import":
            if not self._guard():
                return
            try:
                n = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                n = 0
            if n <= 0:
                self._json({"ok": False, "error": "空的请求体"}, 400)
                return
            if n > PLUGIN_MAX_BYTES:
                self._json({"ok": False, "error": "插件包超过 2MB"}, 413)
                return
            try:
                raw = self.rfile.read(n)
                overwrite = (parse_qs(u.query).get("overwrite") or ["0"])[0] in ("1", "true", "yes")
                self._json(_import_plugin_zip(raw, overwrite))
            except ImportError_ as e:
                self._json({"ok": False, "error": str(e)}, 400)
            except Exception as e:                                 # noqa: BLE001
                logger.exception("插件导入失败")
                self._json({"ok": False, "error": f"{type(e).__name__}: {e}"}, 500)
            return
        if u.path == "/api/characters/import":
            if not self._guard():
                return
            try:
                n = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                n = 0
            if n <= 0:
                self._json({"ok": False, "error": "空的请求体"}, 400)
                return
            if n > IMPORT_MAX_BYTES:
                self._json({"ok": False, "error": "zip 超过 64MB"}, 413)
                return
            try:
                raw = self.rfile.read(n)
            except OSError as e:
                self._json({"ok": False, "error": f"读取失败：{e}"}, 400)
                return
            try:
                self._json(_import_character_zip(raw))
            except ImportError_ as e:
                self._json({"ok": False, "error": str(e)}, 400)
            except Exception as e:                                 # noqa: BLE001
                logger.exception("角色导入失败")
                self._json({"ok": False, "error": f"{type(e).__name__}: {e}"}, 500)
            return
        # NapCat HTTP POST 事件入口（用自己的 token 鉴权，不走面板头）
        # 先验 token 再读 body：未鉴权的客户端不该有机会让服务端分配大块内存。
        if u.path == "/onebot/event":
            tok = str((BOT.config.get("napcat_token") if BOT else "") or "")
            auth = str(self.headers.get("Authorization") or "")
            if not tok:
                self._json({"ok": False, "error": "napcat_token 未配置，拒绝事件"}, 503)
                return
            if not (hmac.compare_digest(auth, f"Bearer {tok}") or hmac.compare_digest(auth, tok)):
                self._json({"ok": False, "error": "bad token"}, 403)
                return
            if BOT is None:
                self._json({"ok": False, "error": _BOT_IMPORT_ERR or "bot 未启动"}, 503)
                return
            body = self._body(max_bytes=8 * 1024 * 1024)      # 单条事件不会更大
            if getattr(self, "_body_too_big", False):
                self._json({"ok": False, "error": "事件体过大"}, 413)
                return
            BOT.submit(body)
            self._json({"ok": True})
            return

        body = self._body()
        if getattr(self, "_body_too_big", False):
            self._json({"ok": False, "error": "请求体超过 64MB"}, 413)
            return

        if not self._guard():
            return

        if u.path == "/api/models":
            if not BOT:
                self._json({"ok": False, "error": "bot 未启动"}, 503)
                return
            try:
                prov = body.get("provider")
                if isinstance(prov, dict):
                    BOT.models.upsert_provider(prov)
                active = body.get("active")
                if isinstance(active, dict) and active.get("provider"):
                    BOT.models.set_active(str(active["provider"]), str(active.get("model") or ""))
                vis = body.get("vision")
                if isinstance(vis, dict):
                    BOT.models.set_vision(str(vis.get("provider") or ""), str(vis.get("model") or ""))
                ag = body.get("agent")
                if isinstance(ag, dict):
                    BOT.models.set_agent(str(ag.get("provider") or ""), str(ag.get("model") or ""))
                if body.get("delete_provider"):
                    BOT.models.delete_provider(str(body["delete_provider"]))
                self._json(_models_get())
            except Exception as e:                                 # noqa: BLE001
                self._json({"ok": False, "error": f"{type(e).__name__}: {e}"}, 400)
            return

        if u.path == "/api/models/test":
            if not BOT:
                self._json({"ok": False, "error": "bot 未启动"}, 503)
                return
            model = str(body.get("model") or "") or None
            t0 = time.time()
            try:
                reply = BOT.models.chat(
                    [{"role": "user", "content": "用一句很短的话跟可莉打个招呼"}],
                    model=model, timeout=90, max_tokens=160)
                self._json({"ok": True, "reply": reply[:300],
                            "ms": int((time.time() - t0) * 1000)})
            except Exception as e:                                 # noqa: BLE001
                self._json({"ok": False, "error": str(e)[:400]})
            return

        if u.path == "/api/bot/config":
            if not BOT:
                self._json({"ok": False, "error": "bot 未启动"}, 503)
                return
            cfg = BOT.save_config(body.get("config") or {})
            self._json({"ok": True, "config": cfg})
            return

        if u.path == "/api/bot/reload":
            if not BOT:
                self._json({"ok": False, "error": "bot 未启动"}, 503)
                return
            BOT.reload()
            self._json({"ok": True, "note": "插件已重载"})
            return

        if u.path == "/api/chat":
            session = re.sub(r"[^A-Za-z0-9_\-]", "", str(body.get("session") or "studio-main"))[:48]
            text = str(body.get("text") or "").strip()
            if not text:
                self._json({"ok": False, "error": "空消息"}, 400)
                return
            cid = str(body.get("character") or "").strip().lower() or _active_cid()
            if _char_store().get(cid) is None:
                cid = _active_cid()
            eid = f"{time.time():.6f}-{os.urandom(4).hex()}"
            _append_jsonl(CHAT_JSONL, {"id": eid, "ts": time.time(),
                                       "session": session or "studio-main",
                                       "character": cid, "role": "user", "text": text[:4000]})
            _append_jsonl(MSG_JSONL, {"ts": time.time(),
                                      "chat_id": "web:" + (session or "studio-main"),
                                      "role": "user", "text": text[:800]})
            _chat_q.put({"session": session or "studio-main", "text": text[:4000],
                         "character": cid, "eid": eid,
                         "agent": bool(body.get("agent")),
                         "reasoning": str(body.get("reasoning") or "default")[:16]})
            self._json({"ok": True, "queued": True,
                        "position": _chat_q.qsize(),
                        "note": "可莉正在从蒙德赶过来（约 35 秒）"})
            return

        if u.path == "/api/chat/delete":
            session = re.sub(r"[^A-Za-z0-9_\-]", "", str(body.get("session") or ""))[:48] or "studio-main"
            cid = str(body.get("character") or "").strip().lower() or _active_cid()
            with _lock:
                run = _chat_state["running"]
            if run and run.get("session") == session:
                self._json({"ok": False, "error": "这个会话正在生成，等它说完再删"}, 409)
                return
            removed, left = _chat_drop_session(session, cid)
            self._json({"ok": True, "removed": removed, "left": left, "session": session})
            return

        if u.path == "/api/ui":
            cid = str(body.get("character") or "").strip().lower() or _char_store().active_id()
            patch = body.get("prefs") if isinstance(body.get("prefs"), dict) else body
            self._json(_ui_save(cid, patch))
            return

        if u.path == "/api/characters":
            payload = {k: body.get(k) for k in
                       ("id", "name", "en", "emoji", "tagline", "color", "color2",
                        "kb", "seed", "theme", "greeting", "hero_note", "chips")}
            try:
                info = _char_store().add(payload, str(body.get("soul") or ""))
            except Exception as e:                                 # noqa: BLE001
                self._json({"ok": False, "error": f"{type(e).__name__}: {e}"}, 400)
                return
            self._json({"ok": True, "character": info, "note": "角色已保存（state/characters.d/）"})
            return

        if u.path == "/api/characters/theme":
            cid = str(body.get("id") or "").strip().lower()
            theme_id = str(body.get("theme") or "").strip().lower()
            if not theme_mod.known(theme_id):
                self._json({"ok": False, "error": f"没有主题插件 {theme_id!r}"}, 400)
                return
            try:
                info = _char_store().set_theme(cid, theme_id)
            except Exception as e:                                 # noqa: BLE001
                self._json({"ok": False, "error": f"{type(e).__name__}: {e}"}, 400)
                return
            if BOT is not None and getattr(BOT, "runtime", None) is not None:
                try:
                    if BOT.runtime.active_character == cid:
                        BOT.runtime.active_character_info = info
                except Exception:                                  # noqa: BLE001
                    pass
            self._json({"ok": True, "character": info, "theme": theme_id,
                        "note": "已绑定（面板刷新后生效）"})
            return

        if u.path == "/api/characters/switch":
            cid = str(body.get("id") or "").strip().lower()
            try:
                info = BOT.switch_character(cid) if BOT is not None else _char_store().set_active(cid)
            except Exception as e:                                 # noqa: BLE001
                self._json({"ok": False, "error": f"{type(e).__name__}: {e}"}, 400)
                return
            # 切换后才失效缓存：POST 开头清过一次，但并发 GET 可能在切换过程中把旧值又缓存上
            _CHAR_CACHE.update(ts=0.0, data=None)
            _PLUG_CACHE.update(ts=0.0, data=None)   # snapshot 里的 enabled 是按角色算的
            self._json({"ok": True, "active": info["id"], "character": info,
                        "note": "已切换（人格 / 知识库 / 角色插件一起生效）"})
            return

        if u.path == "/api/persona":
            soul = body.get("soul")
            cid = str(body.get("character") or "").strip().lower() or _char_store().active_id()
            if not isinstance(soul, str) or not soul.strip():
                self._json({"ok": False, "error": "内容为空"}, 400)
                return
            try:
                res = _char_store().save_soul(cid, soul)
            except Exception as e:                                 # noqa: BLE001
                self._json({"ok": False, "error": f"{type(e).__name__}: {e}"}, 400)
                return
            if BOT is not None and getattr(BOT, "runtime", None) is not None:
                try:
                    BOT.runtime._persona_cache = {"mtime": 0.0, "text": ""}
                except Exception:                                  # noqa: BLE001
                    pass
            self._json({"ok": True, "character": cid, **res,
                        "note": "已保存（下一轮对话即生效，没有回档）"})
            return

        if u.path == "/api/plugins/config":
            cfg = body.get("config")
            if not isinstance(cfg, dict):
                self._json({"ok": False, "error": "config 必须是对象"}, 400)
                return
            tmp = FEATURES_DIR / f"config.json.{os.getpid()}.tmp"
            try:
                tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
                os.replace(tmp, FEATURES_DIR / "config.json")
                RELOAD_MARK.touch()
            except OSError as e:
                try:
                    tmp.unlink(missing_ok=True)
                except OSError:
                    pass
                self._json({"ok": False, "error": str(e)}, 500)
                return
            self._json({"ok": True, "note": "已保存并触发重载（≤20 秒）"})
            return

        if u.path == "/api/requests/action":
            flag = str(body.get("flag") or "")
            kind = str(body.get("kind") or "")
            approve = bool(body.get("approve"))
            if not flag or kind not in ("friend", "group"):
                self._json({"ok": False, "error": "参数不对（flag + kind）"}, 400)
                return
            rec = next((r for r in _requests(200) if r.get("flag") == flag), {})
            sub = str(body.get("sub_type") or rec.get("sub_type") or "add")
            rt = getattr(BOT, "runtime", None) if BOT else None
            if not rt:
                self._json({"ok": False, "error": "bot 未启动"}, 503)
                return
            try:
                if kind == "friend":
                    rt.ob.set_friend_add_request(flag, approve,
                                                 str(body.get("remark") or ""))
                else:
                    rt.ob.set_group_add_request(flag, sub, approve,
                                                str(body.get("reason") or ""))
            except Exception as e:                                 # noqa: BLE001
                self._json({"ok": False, "error": f"{type(e).__name__}: {e}"}, 502)
                return
            try:
                with open(STATE_DIR / "requests.jsonl", "a", encoding="utf-8") as f:
                    f.write(json.dumps({"ts": time.time(), "action": "approve" if approve else "reject",
                                        "flag": flag, "kind": kind, "sub_type": sub}, ensure_ascii=False) + "\n")
            except OSError:
                pass
            self._json({"ok": True, "approved": approve, "kind": kind})
            return
        if u.path == "/api/plugins/settings":
            name = Path(str(body.get("name") or "")).stem
            values = body.get("values")
            if not name or not isinstance(values, dict):
                self._json({"ok": False, "error": "参数不对（要 name + values）"}, 400)
                return
            meta = _plugin_meta().get(name) or {}
            schema = meta.get("schema") if isinstance(meta.get("schema"), dict) else {}
            if not schema:
                self._json({"ok": False, "error": f"{name} 没有声明 CONFIG_SCHEMA"}, 400)
                return
            cfg_path = FEATURES_DIR / "config.json"
            try:
                cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
                if not isinstance(cfg, dict):
                    raise ValueError("config.json 不是对象")
            except (OSError, ValueError) as e:
                self._json({"ok": False, "error": f"读配置失败：{e}"}, 500)
                return
            cfg2, errors = merge_plugin_settings(cfg, name, values, schema)
            tmp = cfg_path.with_name(f"config.json.{os.getpid()}.{threading.get_ident()}.tmp")
            try:
                tmp.write_text(json.dumps(cfg2, ensure_ascii=False, indent=2) + "\n",
                               encoding="utf-8")
                os.replace(tmp, cfg_path)
                RELOAD_MARK.touch()
            except OSError as e:
                self._json({"ok": False, "error": f"写配置失败：{e}"}, 500)
                return
            finally:
                try:
                    tmp.unlink(missing_ok=True)
                except OSError:
                    pass
            if BOT:
                try:
                    BOT.reload()
                except Exception:                                # noqa: BLE001
                    pass
            self._json({"ok": True, "name": name, "errors": errors,
                        "values": _plugin_schema(name)["values"]})
            return
        if u.path == "/api/agent":
            if not BOT:
                self._json({"ok": False, "error": "bot 未启动"}, 503)
                return
            try:
                upd = {}
                for k, typ in (("enabled", bool), ("owner_only", bool),
                               ("max_steps", int), ("timeout", float)):
                    if k in body and body[k] is not None:
                        upd[k] = typ(body[k])
                if "tools" in body:
                    from bot.agent import resolve_tools
                    upd["tools"] = resolve_tools(body.get("tools"))
                if "skills" in body:
                    upd["skills"] = bool(body.get("skills"))
                cfg_patch = {}
                if "enabled" in upd:
                    cfg_patch["agent_enabled"] = upd["enabled"]
                if "owner_only" in upd:
                    cfg_patch["agent_owner_only"] = upd["owner_only"]
                if "max_steps" in upd:
                    cfg_patch["agent_max_steps"] = max(0, min(6, upd["max_steps"]))
                if "timeout" in upd:
                    cfg_patch["agent_timeout"] = max(10.0, min(600.0, upd["timeout"]))
                if "tools" in upd:
                    cfg_patch["agent_tools"] = upd["tools"]
                if "skills" in upd:
                    cfg_patch["agent_skills"] = upd["skills"]
                if cfg_patch:
                    BOT.save_config(cfg_patch)
                mdl = body.get("model")
                if isinstance(mdl, dict):
                    BOT.models.set_agent(str(mdl.get("provider") or ""),
                                         str(mdl.get("model") or ""))
                self._json(_agent_get())
            except Exception as e:                                 # noqa: BLE001
                self._json({"ok": False, "error": f"{type(e).__name__}: {e}"}, 400)
            return
        if u.path == "/api/config":
            if not BOT:
                self._json({"ok": False, "error": _BOT_IMPORT_ERR or "bot 未启动"}, 503)
                return
            updates = body.get("updates") or {}
            clean, out = {}, {}
            for k, v in updates.items():
                if k not in CONFIG_KEYS:
                    out[k] = "skipped (不在白名单)"
                    continue
                clean[k] = v
                out[k] = "ok"
            BOT.save_config(clean)
            self._json({"ok": True, "results": out})
            return

        if u.path == "/api/action":
            action = body.get("action")
            if action == "restart_gateway":
                if BOT:
                    BOT.reload()
                self._json({"ok": True, "msg": "面板 bot 插件已重载（不依赖 Hermes 网关）"})
            elif action == "reload_features":
                RELOAD_MARK.touch()
                self._json({"ok": True, "msg": "已触发重载（≤20 秒）"})
            elif action == "restart_napcat":
                rc, msg = _run(["systemctl", "restart", "napcat"], timeout=90)
                self._json({"ok": rc == 0, "msg": msg or "NapCat 重启中（会自动快速登录）"})
            else:
                self._json({"ok": False, "error": "未知动作"}, 400)
            return

        self.send_error(404)


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 128      # 默认 5：并发一高，连 accept 都排队


def main():
    global BOT
    if BotService is not None:
        BOT = BotService(STUDIO_HOME)
        BOT.start()
        print(f"[klee-studio] bot runtime: {BOT.status().get('features', {})}", flush=True)
    else:
        print(f"[klee-studio] bot runtime 启动失败：{_BOT_IMPORT_ERR}", flush=True)
    threading.Thread(target=_alert_watcher, daemon=True, name="qq-alert-watcher").start()
    threading.Thread(target=_log_janitor, daemon=True, name="log-janitor").start()
    srv = Server((BIND, PORT), Handler)
    print(f"[klee-studio] http://{BIND}:{PORT}/  (STUDIO_HOME={STUDIO_HOME})", flush=True)
    print(f"[klee-studio] QQ 掉线巡检：每 {ALERT_POLL_S:g}s 一次，连续掉线 ≥{OFFLINE_ALERT_S:g}s 落一条告警 "
          f"→ {ALERTS_JSONL}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
