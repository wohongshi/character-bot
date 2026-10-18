"""适配器包。导入即完成注册，`build()` 按配置造实例。

    from bot.adapters import build, available
    ad = build(config)          # config["adapter"] 缺省 = onebot11
    await ad.send_text("group:123", "hi")
"""
from .base import (AdapterError, BaseAdapter, REGISTRY, available, build,
                   maybe_await, register)
from . import onebot, webhook   # noqa: F401  导入即注册

__all__ = ["AdapterError", "BaseAdapter", "REGISTRY", "available", "build",
           "maybe_await", "register", "onebot", "webhook"]
