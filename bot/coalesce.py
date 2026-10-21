"""连发合并：一个人噼里啪啦发三句，她只答一次。

群聊里太常见了：

    甲：在吗
    甲：那个
    甲：我想问下明天几点集合

不合并的话是三次模型调用、三次回复（前两次基本是「怎么了？」「你说」），
既贵又吵，而且她看到第三句时已经不知道第一句在问什么了。开这个开关后，
同一会话 `window` 秒内的消息先攒着，到点合并成一段再回。

不参与合并的（`should_merge` 一律 False）：
- **命令**：`.help` 攒一秒再执行，用的人会以为卡住了；
- **带图的消息**：图已经过了一遍 vision，合并进来只会让上下文错位；
- **太长的一条**：能一次说完说明不用等，攒着反而是延迟。

配置在 `bot_config.json`：`coalesce_on` / `coalesce_window` / `coalesce_max`。
"""

from __future__ import annotations

DEFAULTS = {"on": False, "window": 1.5, "max_msgs": 4}
LIMITS = {"window": (0.3, 10.0), "max_msgs": (1, 10)}
MAX_SINGLE = 200          # 一条超过这么长就不合并（见上）
MAX_MERGED = 1000         # 合并后正文上限


def _bool(v) -> bool:
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "on")
    return bool(v)


def _num(v, default: float, lo: float, hi: float) -> float:
    try:
        n = float(v)
    except (TypeError, ValueError):
        return default
    if n != n:                      # NaN
        return default
    return min(max(n, lo), hi)


def clamp_cfg(cfg: dict | None) -> dict:
    cfg = cfg or {}
    return {
        "on": _bool(cfg.get("on", DEFAULTS["on"])),
        "window": _num(cfg.get("window", DEFAULTS["window"]),
                       DEFAULTS["window"], *LIMITS["window"]),
        "max_msgs": int(_num(cfg.get("max_msgs", DEFAULTS["max_msgs"]),
                             DEFAULTS["max_msgs"], *LIMITS["max_msgs"])),
    }


def should_merge(cfg: dict, *, text: str, has_images: bool = False,
                 command: bool = False, max_chars: int = MAX_SINGLE) -> bool:
    """这条消息该不该进合并窗口。"""
    cfg = clamp_cfg(cfg)
    if not cfg["on"]:
        return False
    if command or has_images:
        return False
    body = str(text or "").strip()
    if not body or len(body) > max_chars:
        return False
    return True


def merge_texts(texts) -> str:
    """合成一段。相邻的完全重复只留一句 —— 群里刷「哈哈哈哈」时不至于占满上下文。"""
    out: list[str] = []
    for t in texts or ():
        body = str(t or "").strip()
        if not body:
            continue
        if out and out[-1] == body:
            continue
        out.append(body)
    return "\n".join(out)[:MAX_MERGED]
