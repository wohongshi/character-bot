"""角色限定插件共用的小工具：按会话存 JSON、原子写、坏文件备份。

所有新插件都用 `KLEE_STATE_DIR`（干跑测试台）或 `bot/features/state/` 作为根目录，
不碰生产之外的位置。
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)
_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


def state_root() -> Path:
    base = os.environ.get("KLEE_STATE_DIR")
    return Path(base) if base else Path(__file__).resolve().parent / "state"


def state_path(name: str) -> Path:
    return state_root() / f"{name}.json"


def _lock_for(path: Path) -> threading.RLock:
    key = str(path.resolve())
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, threading.RLock())


def load(path: Path) -> dict:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError as e:              # 权限/IO 抖动：不是内容坏了，不隔离文件
        logger.warning("插件状态读取失败（%s: %s），本次按空表处理", path.name, e)
        return {}
    try:
        d = json.loads(raw)
    except ValueError:
        d = None
    if not isinstance(d, dict):        # 合法 JSON 但不是对象 = 结构损坏，按坏文件备份
        logger.warning("插件状态结构异常（%s），已备份后重置", path.name)
        try:
            path.replace(path.with_name(f"{path.stem}.corrupt-{int(time.time())}.json"))
        except OSError as e:
            logger.warning("备份坏状态文件失败（%s: %s）", path.name, e)
        return {}
    return d


def save(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}-{time.time_ns() % 1_000_000}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def chat_key(ctx) -> str:
    """群聊按群、私聊按人 —— 不同会话的存档互不串。

    缺 id 时退化到用户维度，绝不把所有会话塞进同一个空 key。
    """
    uid = str(getattr(ctx, "user_id", "") or "")
    gid = str(getattr(ctx, "group_id", "") or "")
    if getattr(ctx, "is_group", False) and gid:
        return "g:" + gid
    return "u:" + (uid or "anon")


def clip(s: str, n: int) -> str:
    """压成一行并截到 **最多 n 个字符**（含省略号）。"""
    s = " ".join(str(s or "").split())
    if n <= 0:
        return ""
    return s if len(s) <= n else s[:n - 1] + "…"


# 出站会解析 [CQ:at,qq=all] / [CQ:image,…] 这类富文本标记：凡是回显用户输入，
# 先把方括号与实体清掉，否则一条 `.雪 [CQ:at,qq=all]` 就能 @ 全体。
_MARKUP = re.compile(r"[\[\]<>]|&#\d+;")


def safe_text(s: str, n: int) -> str:
    """clip() + 去掉富文本标记：用于把用户输入原样回显到消息里的地方。"""
    return clip(_MARKUP.sub("", str(s or "")), n)


def db_update(path: Path, key: str, fn, default=None):
    """原子读-改-写：`db_get` + `db_set` 分开调用会丢并发更新。"""
    with _lock_for(path):
        d = load(path)
        cur = d.get(key, default)
        new = fn(cur)
        d[key] = new
        save(path, d)
        return new


def db_get(path: Path, key: str, default):
    return load(path).get(key, default)


def db_set(path: Path, key: str, value) -> None:
    with _lock_for(path):             # 读-改-写整段加锁，避免并发保存互相覆盖
        d = load(path)
        d[key] = value
        save(path, d)
