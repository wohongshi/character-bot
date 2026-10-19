"""适配器包。导入即完成注册，`build()` 按配置造实例。

`adapters` 也是这个框架的「系统插件」：每个适配器就是一个可以被开关的插件，
默认全部关闭（`DEFAULT_OFF = True`），在面板「插件」页里选择性开启。

    from bot.adapters import build, system_plugins
    ad = build(cfg, platform="feishu")
    for item in system_plugins():      # 给面板出卡片
        ...
"""
from .base import (AdapterError, BaseAdapter, Inbound, REGISTRY, available, build,
                   maybe_await, register, system_plugins)
from . import onebot, webhook, feishu, wechat, qqofficial   # noqa: F401  导入即注册
from . import web                                          # noqa: F401  面板自带（常开）

__all__ = ["AdapterError", "BaseAdapter", "Inbound", "REGISTRY", "available",
           "build", "maybe_await", "register", "system_plugins",
           "onebot", "webhook", "feishu", "wechat", "qqofficial", "web"]
