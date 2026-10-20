#!/usr/bin/env bash
# 仓库内可移植测试（标准库 + bot 包；KB/主题 HTTP 套件需要本机服务）
cd "$(dirname "$0")" || exit 1
rc=0
for f in test_agent.py test_skills.py test_plugin_meta.py test_themes.py \
         test_studio_chat.py test_characters.py test_character_runtime.py \
         test_kb_characters.py test_theme_http.py test_bot_runtime.py \
         test_character_plugins.py test_character_import.py test_plugin_import.py \
         test_plugins_all.py test_logs.py test_ui_refs.py \
         test_http_hygiene.py test_config_fields.py test_model_discover.py test_model_selection.py test_plugins_config.py test_copy.py test_feed_sources.py test_feed_parity.py test_perf_assets.py test_responsive.py test_framework_adapters.py test_ocr6_fixes.py test_system_plugins.py test_store_index.py test_ext_plugins.py test_agent_always_on.py test_perf_round.py test_tools_as_plugins.py test_api_lite.py test_i18n.py test_polyglot.py test_debug_round.py test_agent_skills.py test_chat_features.py test_chat_round2.py test_chat_round3.py test_chat_round4.py test_plugin_pages.py test_plugins_expansion.py test_round6.py test_round7.py test_clickables.py test_adapter_credentials.py test_stickers.py test_export_html.py test_plugin_audit.py test_mood.py test_chat_tools.py test_kb_manual.py test_onebot_auth.py test_ocr13.py \
         test_selfcheck.py test_insights.py test_pluginpack.py \
         test_cmd_perms.py test_chat_policy.py test_simulator.py \
         test_promptdbg.py test_kbingest.py test_proactive.py \
         test_model_pinning.py test_pick_sel.py \
         test_budget.py test_people.py test_memtidy.py test_chat_char.py \
         test_charpack.py test_examples.py test_usage_chat.py; do
  echo "== $f"
  python3 "$f" || rc=1
done
exit $rc
