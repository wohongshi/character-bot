#!/usr/bin/env bash
# 仓库内可移植测试（标准库 + bot 包；KB/主题 HTTP 套件需要本机服务）
cd "$(dirname "$0")" || exit 1
rc=0
for f in test_agent.py test_skills.py test_plugin_meta.py test_themes.py \
         test_studio_chat.py test_characters.py test_character_runtime.py \
         test_kb_characters.py test_theme_http.py test_bot_runtime.py \
         test_character_plugins.py test_character_import.py test_plugin_import.py \
         test_plugins_all.py test_logs.py test_ui_refs.py \
         test_http_hygiene.py test_config_fields.py test_model_discover.py test_model_selection.py test_plugins_config.py test_copy.py test_feed_sources.py test_feed_parity.py test_perf_assets.py test_responsive.py test_framework_adapters.py test_ocr6_fixes.py test_system_plugins.py test_store_index.py test_ext_plugins.py test_agent_always_on.py test_perf_round.py; do
  echo "== $f"
  python3 "$f" || rc=1
done
exit $rc
