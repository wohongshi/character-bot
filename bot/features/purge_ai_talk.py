"""出戏话术清洗（on_reply 钩子示例）：把「作为 AI…」这类词抠掉再发出去。"""

import re

NAME = "出戏清洗"
DESCRIPTION = "出站清洗：去掉「作为AI/语言模型」之类的出戏话术（on_reply 钩子示例）"
VERSION = "1.0"

DEFAULT_PATTERNS = [
    r"作为(?:一个)?(?:AI|人工智能|语言模型|智能助手|机器人)[，,、：:]?",
    r"我是(?:一个)?(?:AI|人工智能|语言模型|智能助手)[，,、：:]?",
    r"(?i)as an ai[^,.\n]*[,.]?",
    r"(?i)i am an ai[^,.\n]*[,.]?",
]


def register(api):
    pats = []
    for p in (api.settings.get("patterns") or DEFAULT_PATTERNS):
        try:
            pats.append(re.compile(str(p)))
        except re.error as e:
            api.log("正则无效被跳过：%s — %s", p, e)
    if not pats:
        return

    def clean(sc, text):
        out = text
        for p in pats:
            out = p.sub("", out)
        return out if out != text else None

    api.on_reply(clean)
