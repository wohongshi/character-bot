# 日志：开关与保留

这个项目**没有回档**，但日志可以控制大小，也可以整个关掉。

## 两个配置

面板 → **⚙️ 运维 · 机器人 → 常用配置**，或直接改 `state/bot_config.json`：

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `log_enabled` | `true` | 是否写运行日志 `state/bot.log`。关掉后运维页日志区显示「运行日志已关闭」 |
| `log_retention_days` | `30` | 流水保留天数。**`0` = 永不自动清理** |

保存后立刻生效（文件 handler 会当场挂上/摘掉），不用重启。

## 清理哪些文件

`log_retention_days` 到期清理：

| 文件 | 内容 | 依据 |
| --- | --- | --- |
| `state/bot.log` | Python 运行日志 | 行首 `YYYY-MM-DD` |
| `state/alerts.jsonl` | QQ 掉线/恢复告警 | 每行 `ts` |
| `state/messages.jsonl` | 面板消息流（QQ + 网页） | 每行 `ts` |
| `state/kb_hits.jsonl` | 知识库命中记录 | 每行 `ts` |
| `state/requests.jsonl` | 好友/入群请求记录 | 每行 `ts` |

**不清理**：`state/chat.jsonl`（聊天记录）、`state/characters/**`（人格）、
`bot/features/state/**`（插件存档）。聊天记录只在聊天页点「删」时删除。

## 清理策略

- 每 **6 小时**一次；进程启动时先跑一次。
- JSONL 按行判断 `ts`，过期行删掉；**解析不了的行原样保留**（不吞数据）。
- 文本日志按行首日期判断；没有日期的续行（如 traceback）保留。
- 写入用 `临时文件 + os.replace`，中途崩了不会留下半截文件。
- 没有过期行时**不重写文件**（不动 mtime）。

## 看当前占用

```bash
curl -s -H 'X-Klee-Studio: 1' http://127.0.0.1:8796/api/logs/meta
```

```json
{"ok": true, "enabled": true, "days": 30,
 "files": [{"name": "bot.log", "bytes": 23256, "mtime": 1792241293}, ...]}
```

手动跑一次清理（不重启）：

```bash
python3 - <<'EOF'
import sys; sys.path.insert(0, '.')
import server
print(server._log_janitor_once())
EOF
```

## 关掉日志会怎样

- 不再产生新的 `state/bot.log`，已有文件保留（想立刻清空可以删掉）。
- 面板「📜 面板 bot 日志」显示一行提示，不再读文件。
- 告警、消息流、知识库命中仍然记录（它们是功能数据，面板要用），
  但依然受 `log_retention_days` 约束。

## 为什么不提供回档

用户明确要求：**回档机制不需要**。所以：

- 保存 SOUL 不生成 `SOUL.md.bak`
- 覆盖安装插件不留 `*.bak-<ts>`
- 覆盖导入角色主题直接替换旧目录
- 没有 `/api/persona/restore`

需要版本管理请用 git 或外部备份；面板里的「删」和「覆盖」都是不可逆的。
