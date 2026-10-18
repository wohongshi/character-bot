"""外部插件（任何语言）：见 host.py 顶部那段协议。"""
from .host import ExtError, ExtPlugin, LANG_RUN, load_spec

__all__ = ["ExtPlugin", "ExtError", "LANG_RUN", "load_spec"]
