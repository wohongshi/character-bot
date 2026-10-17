#!/usr/bin/env bash
# 仓库内可移植测试（标准库 + bot 包；KB/主题 HTTP 套件需要本机服务）
cd "$(dirname "$0")" || exit 1
rc=0
for f in test_agent.py test_skills.py test_plugin_meta.py test_themes.py \
         test_studio_chat.py test_characters.py test_character_runtime.py \
         test_kb_characters.py test_theme_http.py test_bot_runtime.py \
         test_character_plugins.py test_character_import.py test_plugin_import.py \
         test_plugins_all.py test_logs.py; do
  echo "== $f"
  python3 "$f" || rc=1
done
exit $rc
