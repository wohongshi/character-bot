"""系统自检：把「进程还在跑、但其实已经不对劲」的状态查出来。

为什么要有它
------------
这个项目栽过的几次坑，全都是「面板看着正常、实际已经废了」：

- 面板说 QQ 已登录，但 NapCat 的事件每条都被 403 挡掉（三天零回复）；
- `ob.get_status` 根本不存在，健康检查变成恒定的「连不上」；
- 索引与 chat.jsonl 漂移，搜索搜不到刚聊的话；
- tts_cache / cards 一路涨，最后把 4 GB 的机器吃满。

这些都不是崩溃，没有异常、没有红字，只能靠「主动去量一遍」发现。
所以这里把能量的都量一遍，并且**能自动修的给出修复动作**。

设计口径
--------
- 纯标准库，不 import server / bot 的任何模块：调用方把「运行时事实」
  （索引健康、适配器状态、模型配置、线程存活）作为参数传进来。
  这样它可以被单测直接喂临时目录，不会碰生产状态。
- 每项统一返回 `{id, name, level, detail, hint, fix, fix_label}`。
  `level` 只有 ok / warn / bad 三档；`fix` 是修复动作 id（不可修为 None）。
- **不猜**：量不到的东西（比如读不到温度传感器）就说「不适用」，不算通过。
"""

from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path

# 阈值都写在这里，别散落在各处：以后调只改一行
DISK_WARN_B = 2 * 1024 ** 3          # 剩余 < 2 GB 提醒
DISK_BAD_B = 500 * 1024 ** 2         # 剩余 < 500 MB 算严重
MEM_WARN_B = 400 * 1024 ** 2         # 可用内存 < 400 MB 提醒
TEMP_WARN = 75.0                     # 温度 > 75℃ 提醒（这台是 fanless）
TEMP_BAD = 85.0                      # > 85℃ 算严重（thermal-gate 的红线）
LOG_WARN_B = 20 * 1024 ** 2          # state 下日志类文件合计 > 20 MB
TTS_MAX_FILES = 80                   # 与 server.TTS_DIR 的回收上限一致
USAGE_MAX_LINES = 20000              # 与 server 的截断上限一致


def _item(iid: str, name: str, level: str, detail: str, *, hint: str = "",
          fix: str = "", fix_label: str = "") -> dict:
    return {"id": iid, "name": name, "level": level, "detail": detail,
            "hint": hint, "fix": fix or "", "fix_label": fix_label or ""}


def _size(path: Path) -> int:
    try:
        if path.is_dir():
            return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
        return path.stat().st_size
    except OSError:
        return 0


def _human(n: float) -> str:
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return (f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}")
        n /= 1024
    return f"{n:.1f} TB"


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _json_ok(path: Path) -> tuple[bool, str]:
    if not path.exists():
        return False, "文件不存在"
    try:
        json.loads(_read_text(path) or "null")
        return True, ""
    except (ValueError, TypeError) as e:
        return False, str(e)[:120]


def _count_lines(path: Path, cap: int = 500_000) -> int:
    """数行数（大文件也有上界，别为了自检把 4 GB 机器读爆）。"""
    n = 0
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for _ in f:
                n += 1
                if n >= cap:
                    break
    except OSError:
        return -1
    return n


def _mem_available() -> int:
    try:
        for line in _read_text(Path("/proc/meminfo")).splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) * 1024
    except (ValueError, IndexError):
        pass
    return -1


def _max_temp() -> float:
    best = -1.0
    base = Path("/sys/class/thermal")
    try:
        for zone in base.glob("thermal_zone*"):
            try:
                raw = int(_read_text(zone / "temp").strip() or 0)
            except ValueError:
                continue
            t = raw / 1000.0 if raw > 200 else float(raw)      # 有的驱动给的是度
            if t > best:
                best = t
    except OSError:
        pass
    return best


def _usage_tail(path: Path, keep_days: int = 3) -> dict:
    """用量文件：条数与最近一次调用时间。超过上限就值得清一次。"""
    rows = 0
    last_ts = 0.0
    since = time.time() - keep_days * 86400
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                rows += 1
                try:
                    ts = float(json.loads(line).get("ts") or 0)
                except (ValueError, TypeError, AttributeError):
                    continue
                if ts > last_ts:
                    last_ts = ts
    except OSError:
        return {"rows": -1, "last_ts": 0.0, "recent": 0}
    return {"rows": rows, "last_ts": last_ts, "recent": 1 if last_ts >= since else 0}


# ---------------------------------------------------------------- 检查项

def run(env: dict) -> list[dict]:
    """跑一遍全部检查，返回条目列表（顺序即面板展示顺序）。"""
    state = Path(env.get("state_dir") or ".")
    feats = Path(env.get("features_dir") or state)
    items: list[dict] = []

    # 1) 磁盘：4 GB 的机器上，磁盘满 = 聊天记录写不进去
    try:
        du = shutil.disk_usage(str(state))
        free = du.free
        level = "bad" if free < DISK_BAD_B else ("warn" if free < DISK_WARN_B else "ok")
        pct = free / max(1, du.total) * 100
        items.append(_item("disk", "磁盘余量", level,
                           f"剩 {_human(free)}（{pct:.0f}% 可用，共 {_human(du.total)}）",
                           hint="存储卡快满了：先清缓存或删旧的图片/语音" if level != "ok" else "",
                           fix="trim_cache" if level != "ok" else "",
                           fix_label="清缓存" if level != "ok" else ""))
    except OSError as e:
        items.append(_item("disk", "磁盘余量", "warn", f"量不到：{e}"))

    # 2) 内存
    avail = _mem_available()
    if avail < 0:
        items.append(_item("mem", "可用内存", "warn", "量不到"))
    else:
        level = "bad" if avail < 150 * 1024 ** 2 else ("warn" if avail < MEM_WARN_B else "ok")
        items.append(_item("mem", "可用内存", level, _human(avail),
                           hint="内存吃紧：可能有浏览器/进程没退" if level != "ok" else ""))

    # 3) 温度（fanless 主机最怕这个）
    temp = _max_temp()
    if temp < 0:
        items.append(_item("temp", "CPU 温度", "ok", "这台没有温度传感器（不适用）"))
    else:
        level = "bad" if temp >= TEMP_BAD else ("warn" if temp >= TEMP_WARN else "ok")
        items.append(_item("temp", "CPU 温度", level, f"{temp:.1f} ℃",
                           hint="太热了：会自己降频，重活先停一停" if level != "ok" else ""))

    # 4) 状态目录可写（只读挂载/满盘时最典型的「静默失败」）
    probe = state / ".selfcheck.tmp"
    try:
        state.mkdir(parents=True, exist_ok=True)
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        items.append(_item("state_write", "状态目录可写", "ok", str(state)))
    except OSError as e:
        items.append(_item("state_write", "状态目录可写", "bad", f"写不进去：{e}"))

    # 5) 配置文件能解析
    bad_cfg = []
    for name, path in (("机器人配置", state / "bot_config.json"),
                       ("插件开关", feats / "config.json")):
        ok, err = _json_ok(path)
        if not ok and path.exists():
            bad_cfg.append(f"{name}（{err}）")
    if bad_cfg:
        items.append(_item("config", "配置文件", "bad", "解析失败：" + "；".join(bad_cfg),
                           hint="启动时会自动备份坏文件并用默认值，但你的设置要重填"))
    else:
        items.append(_item("config", "配置文件", "ok", "都能解析"))

    # 6) 索引与聊天记录是否一致（漂移了搜索就会「漏消息」）
    idx = env.get("index") or {}
    chat_lines = _count_lines(state / "chat.jsonl") if (state / "chat.jsonl").exists() else 0
    if not idx.get("ok", True):
        items.append(_item("index", "消息索引", "bad",
                           f"索引报错：{idx.get('error') or '打不开'}",
                           hint="重建一次就好", fix="index_rebuild", fix_label="重建"))
    elif chat_lines < 0:
        items.append(_item("index", "消息索引", "warn", "聊天记录读不了，无法比对"))
    else:
        rows = int(idx.get("rows") or 0)
        # 索引里还有 messages.jsonl 的行，所以只比「索引不少于聊天行数」
        gap = chat_lines - rows
        if chat_lines > 0 and gap > 20:
            items.append(_item("index", "消息索引", "warn",
                               f"聊天记录 {chat_lines} 行，索引里只有 {rows} 行（差 {gap}）",
                               hint="重建索引后就能搜到全部消息",
                               fix="index_rebuild", fix_label="重建"))
        else:
            items.append(_item("index", "消息索引", "ok",
                               f"{rows} 条（聊天记录 {chat_lines} 行）"))

    # 7) 模型：配了没有 + 最近还通不通
    model = env.get("model") or {}
    usage = _usage_tail(state / "usage.jsonl")
    if not model.get("model"):
        items.append(_item("model", "模型", "bad", "还没选模型",
                           hint="运维 → 模型里选一个"))
    elif not model.get("has_key") and not model.get("local"):
        items.append(_item("model", "模型", "warn",
                           f"{model.get('provider')} / {model.get('model')}（没填密钥）",
                           hint="本地网关可以不填；云端要填 Key"))
    elif usage["rows"] > 0 and not usage["recent"]:
        age = (time.time() - usage["last_ts"]) / 86400 if usage["last_ts"] else 0
        items.append(_item("model", "模型", "warn",
                           f"{model.get('model')} · 最近 3 天没有成功调用"
                           + (f"（最后一次 {age:.1f} 天前）" if age else ""),
                           hint="可能密钥过期/网关挂了，去运维试一句"))
    else:
        items.append(_item("model", "模型", "ok",
                           f"{model.get('provider')} / {model.get('model')}"))

    # 8) 外部平台连通 + 被拒次数
    ad = env.get("adapter") or {}
    if not ad.get("platforms"):
        items.append(_item("adapter", "平台连接", "ok", "没有启用外部平台（只在网页里聊）"))
    else:
        bad = [p for p in ad["platforms"] if not p.get("connected")]
        streak = int(ad.get("auth_streak") or 0)
        if streak >= 3:
            items.append(_item("adapter", "平台连接", "bad",
                               f"{ad.get('name') or '平台'} 连续 {streak} 条事件被拒（鉴权失败）",
                               hint="token 不一致：重启一次协议端让它重读配置"))
        elif bad:
            items.append(_item("adapter", "平台连接", "bad",
                               "、".join(f"{p['name']}：{p.get('detail') or '连不上'}" for p in bad),
                               hint="看一下那个插件页的账号配置"))
        else:
            items.append(_item("adapter", "平台连接", "ok",
                               "、".join(f"{p['name']} 已连通" for p in ad["platforms"])))

    # 9) 后台线程（worker 死了 = 消息永远排队，但界面一点异常都没有）
    workers = env.get("workers") or {}
    dead = [k for k, alive in workers.items() if not alive]
    if not workers:
        items.append(_item("workers", "后台任务", "warn", "量不到（可能刚启动）"))
    elif dead:
        items.append(_item("workers", "后台任务", "bad", "已停止：" + "、".join(dead),
                           hint="重启一次面板（运维 → 重载服务）"))
    else:
        items.append(_item("workers", "后台任务", "ok", f"{len(workers)} 个都在跑"))

    # 10) 运行目录体积（这几处以前都是无限长的）
    tts_n = len(list((state / "tts_cache").glob("*.wav"))) if (state / "tts_cache").is_dir() else 0
    cards = _size(Path(env.get("cards_dir") or (state / "images")))
    grow = []
    if tts_n > TTS_MAX_FILES:
        grow.append(f"语音缓存 {tts_n} 个")
    if usage["rows"] > USAGE_MAX_LINES:
        grow.append(f"用量记录 {usage['rows']} 行")
    if cards > 64 * 1024 ** 2:
        grow.append(f"图片缓存 {_human(cards)}")
    if grow:
        items.append(_item("cache", "缓存体积", "warn", "、".join(grow),
                           hint="清一次不会丢聊天记录",
                           fix="trim_cache", fix_label="清理"))
    else:
        items.append(_item("cache", "缓存体积", "ok",
                           f"语音 {tts_n} 个 · 图片 {_human(cards)} · 用量 {max(0, usage['rows'])} 行"))

    # 11) 会话标签指向已经不存在的会话
    orphan = env.get("orphan_sessions") or []
    if orphan:
        items.append(_item("sessions", "会话清单", "warn",
                           f"{len(orphan)} 个会话名没有对应的聊天记录",
                           hint="清掉不影响聊天内容",
                           fix="drop_orphan_sessions", fix_label="清掉"))
    else:
        items.append(_item("sessions", "会话清单", "ok", "没有残留"))

    # 12) 定时任务文件（调度线程每 20 秒读一次，坏了会一直报错）
    ok, err = _json_ok(state / "scheduled.json")
    if not ok and (state / "scheduled.json").exists():
        items.append(_item("sched_file", "定时任务", "bad", f"文件坏了：{err}"))
    else:
        items.append(_item("sched_file", "定时任务", "ok", "正常"))

    return items


def overall(items: list[dict]) -> str:
    """整体状态：bad > warn > ok。"""
    levels = {i.get("level") for i in items}
    if "bad" in levels:
        return "bad"
    if "warn" in levels:
        return "warn"
    return "ok"


# ---------------------------------------------------------------- 修复

def fix(env: dict, action: str, hooks: dict | None = None) -> dict:
    """执行一个修复动作。返回 `{ok, action, done, error}`。

    `hooks` 里放需要 server 介入的动作（索引重建），其余都在这里做 ——
    这样「能自动修的」和「要人来按的」分得清。
    """
    hooks = hooks or {}
    state = Path(env.get("state_dir") or ".")
    done: list[str] = []
    try:
        if action == "index_rebuild":
            fn = hooks.get("index_rebuild")
            if not callable(fn):
                return {"ok": False, "action": action, "error": "这个环境没有索引可重建"}
            res = fn() or {}
            added = int(res.get("added") or res.get("rows") or 0)
            rebuilt = res.get("rebuilt") if isinstance(res.get("rebuilt"), list) else []
            done.append(f"重建了 {added} 条" + (f"（{len(rebuilt)} 份来源）" if rebuilt else ""))
            return {"ok": True, "action": action, "done": done, "detail": res}

        if action == "trim_cache":
            # 语音：只留最新的 40 段（上限的一半，别一清就全没）
            tts = state / "tts_cache"
            if tts.is_dir():
                files = sorted(tts.glob("*.wav"), key=lambda f: f.stat().st_mtime, reverse=True)
                for fp in files[40:]:
                    fp.unlink(missing_ok=True)
                if len(files) > 40:
                    done.append(f"删了 {len(files) - 40} 段语音")
            # 图片：只删「30 天前 **且** 排在最新 100 个之外」的。
            # 为什么要留最新的 100 个：这些是卡片/画画的结果，用户可能还想翻回去看，
            # 「清理缓存」不该变成「把上周的图全删了」。
            for sub in ("images", "cards"):
                d = state / sub
                if not d.is_dir():
                    continue
                cutoff = time.time() - 30 * 86400
                try:
                    files = [f for f in d.rglob("*") if f.is_file()]
                    files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
                except OSError:
                    continue
                freed = 0
                for fp in files[100:]:
                    try:
                        if fp.stat().st_mtime >= cutoff:
                            continue
                        freed += fp.stat().st_size
                        fp.unlink()
                    except OSError:
                        continue
                if freed:
                    done.append(f"{sub} 清出 {_human(freed)}")
            # 用量：只留最后 USAGE_MAX_LINES 行
            up = state / "usage.jsonl"
            if up.exists():
                lines = up.read_text(encoding="utf-8", errors="replace").splitlines()
                if len(lines) > USAGE_MAX_LINES:
                    tmp = up.with_suffix(".tmp")
                    tmp.write_text("\n".join(lines[-USAGE_MAX_LINES:]) + "\n", encoding="utf-8")
                    os.replace(tmp, up)
                    done.append(f"用量记录裁到 {USAGE_MAX_LINES} 行")
            return {"ok": True, "action": action, "done": done}

        if action == "drop_orphan_sessions":
            fn = hooks.get("drop_orphan_sessions")
            if not callable(fn):
                return {"ok": False, "action": action, "error": "这个环境不能改会话清单"}
            n = fn() or 0
            done.append(f"清掉 {n} 个")
            return {"ok": True, "action": action, "done": done}

        return {"ok": False, "action": action, "error": "不认识的修复动作"}
    except OSError as e:
        return {"ok": False, "action": action, "error": f"{type(e).__name__}: {e}"}
