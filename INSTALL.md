# 安装与配置

本文从零开始：一台干净的 Linux 机器 → 一个能在 QQ 里说话的 Character Bot → 一个可远程访问的面板。

> 已经在跑的老部署想升级？直接跳到 [升级](#升级)。

---

## 0. 它是怎么跑的

```
QQ ←→ NapCat（OneBot 11） ←→ klee-studio（面板 + 机器人本体） ←→ 模型接口
                                    │
                                    └→ klee-kb（可选，本地知识库检索）
```

- **klee-studio**：一个 Python 进程。既是网页面板（默认 `:8796`），也是机器人本体
  （收 NapCat 推来的事件、跑插件、调模型、发消息）。
- **NapCat**：QQ 协议端，负责登录 QQ 并把消息转成 OneBot 11 事件。
- **klee-kb**：可选的本地向量检索服务（`:8797`），给每个角色一份独立知识库。
- **klee-edge**：可选的 Go 边缘层（静态资源 + 反代），只在追求并发/首屏时部署。

不装 klee-kb 也能跑（只是没有知识库注入）；不装 klee-edge 也能跑（Python 直接对外）。

---

## 1. 环境要求

| 项 | 要求 |
| --- | --- |
| 系统 | Linux（Debian/Ubuntu/Armbian/Arch 都行），macOS 也可跑面板 |
| Python | **3.10+**（用到 `X \| None` 类型标注）；面板与机器人**零 pip 依赖** |
| 内存 | 256MB 起步；跑 klee-kb（ONNX 小模型）建议 ≥1GB |
| 磁盘 | 代码 + 素材约 10MB；知识库每个角色 50–300MB（含模型） |
| 可选 | Go 1.21+（编译 `edge/`）、Node 不需要 |
| 端口 | `8796`（面板/机器人）、`8797`（知识库，可选）、`3101`（NapCat 的 OneBot HTTP，仅本机） |

---

## 2. 三步跑起来（最小可用）

```bash
# 1) 拿代码
git clone <你的仓库地址> character-bot
cd character-bot

# 2) 配置模型 key（写进 .env，不要提交）
cp .env.example .env
$EDITOR .env          # 至少填一个 provider 的 key

# 3) 启动
python3 server.py
```

打开 `http://<机器IP>:8796/`，能看到面板就说明起来了。此时**还不能收发 QQ 消息**——
需要 NapCat（下一节）。

`.env.example` 长这样（key 名可以自己定，只要和面板里 provider 的 `key_env` 对上）：

```ini
# 任意 OpenAI 兼容服务
MY_LLM_API_KEY=sk-xxxx
# 可选：视觉模型 / 语音
MY_VISION_API_KEY=sk-xxxx
```

> 面板里所有 provider 的 key 都**只从环境变量读**，不会写进 `state/models.json`，
> 也不会出现在任何 API 返回里。

---

## 3. 安装并接上 NapCat

NapCat 官方文档：<https://napneko.github.io/guide/napcat>

### 3.1 安装

按官方文档装好（Docker / 一键脚本 / 手动都行），能打开它的 WebUI（默认 `:6099`）即可。
本项目的部署脚本参考了这套布局：

```
/root/Napcat/opt/QQ/            # NapCat + QQ 本体
/etc/systemd/system/napcat.service
```

### 3.2 配置 OneBot 11

编辑 NapCat 的 `config/onebot11_<QQ号>.json`，只保留 **HTTP 服务端** 和 **HTTP 上报**：

```jsonc
{
  "network": {
    "httpServers": [
      {
        "name": "studio-http",
        "enable": true,
        "host": "127.0.0.1",
        "port": 3101,
        "token": "换成一个随机串"
      }
    ],
    "httpClients": [
      {
        "name": "studio-post",
        "enable": true,
        "url": "http://127.0.0.1:8796/onebot/event",
        "token": "和上面同一个随机串",
        "messagePostFormat": "array",
        "reportSelfMessage": false
      }
    ],
    "websocketServers": []
  }
}
```

要点：

- **`token` 两边必须一致**。面板在「运维 · 机器人 → 常用配置」里填同一个值（`napcat_token`），
  否则事件入口会返回 503/403（这是故意的：没 token 不收事件）。
- `port: 3101` 是 NapCat 的 API 端口，只监听本机；面板通过它主动发消息。
- 不需要 WebSocket；`websocketServers` 留空即可。
- NapCat **未登录时网络服务不会启动**，`:3101` 连不上是正常现象。

### 3.3 登录

重启 NapCat，然后：

1. 打开面板 → **⚙️ 运维 · 机器人**
2. 点「📷 QQ 扫码登录」，用手机 QQ 扫
3. 登录成功后 `:3101` 才会监听

> 面板会自动显示二维码并每 20 秒刷新。二维码属于敏感接口，需要面板头 `X-Klee-Studio: 1`
> （浏览器里点按钮自带，脚本调用要手动加）。

### 3.4 面板里补两项

- **主人 QQ**（`owner_id`）：决定 `.agent`、管理命令、以及「Agent 仅主人可用」。
- **群聊策略**（`group_policy`）：
  - `mention`（默认）：只有 @ 她或叫名字才回
  - `open`：群里所有消息都看（容易刷屏，慎用）
  - `disabled`：群聊完全不理

保存后**下一条消息即生效**，不用重启。

---

## 4. 配置模型

面板 → **⚙️ 运维 · 机器人 → 常用配置** 里的模型相关项，配合 `state/models.json`：

| 槽位 | 用途 | 说明 |
| --- | --- | --- |
| `active` | 普通聊天 | 必填 |
| `vision` | 看图 | 留空 = 收到图片只回「看不懂」 |
| `agent` | 工具循环 | 留空 = 跟聊天同一个模型 |

新增 provider：面板「机器人」页 → 模型供应商 → 新增，填：

```json
{
  "id": "my-llm",
  "name": "我的中转",
  "api": "https://api.example.com/v1",
  "key_env": "MY_LLM_API_KEY",
  "models": ["gpt-4o-mini", "deepseek-chat"]
}
```

- `api` 是 **OpenAI 兼容**的 base URL（结尾带 `/v1`，不要带 `/chat/completions`）。
- `key_env` 是**环境变量名**，不是 key 本身。
- 模型名可以随便写，服务端支持就行。

> 推理模型注意：有些模型（如 DeepSeek-R1 系）**非流式**返回时 `message.content` 为空，
> 内容在 `reasoning_content` 里。本项目已做回退，两种都能读。

---

## 5. 知识库（可选）

不装也能聊天；装了之后每个角色会有自己的语料检索，回答更贴合设定。

### 5.1 装依赖

`klee-kb` 需要 `onnxruntime` + `numpy` + 一个中文 embedding 模型（默认 `bge-small-zh`）。
官方仓库不附带模型文件，首次建索引时会下载/需要你放好：

```
klee-kb/
  model.onnx
  tokenizer.json
  data/kb.sqlite3
  data/kb_<角色>.sqlite3
```

### 5.2 建索引

```bash
cd klee-kb
./py build_index.py --character klee --name 可莉                 # 默认语料 corpus/klee
./py build_index.py --character hutao --name 胡桃 --corpus corpus/hutao
./py build_index.py --character atri --name 亚托莉 --corpus corpus/atri
```

语料是 `jsonl`，每行至少一个 `text` 字段：

```json
{"text": "可莉是蒙德城的火花骑士……", "source": "角色设定", "title": "简介"}
```

### 5.3 启动

```bash
python3 server.py            # 默认 :8797
```

面板里把 `kb_url` 指过去（默认就是 `http://127.0.0.1:8797`），`kb_enabled` 打开。
面板「人格 · 知识库」页能看到每个角色的块数、检索测试和最近命中。

调参：

| 配置 | 默认 | 说明 |
| --- | --- | --- |
| `kb_topk` | 3 | 每次注入几条 |
| `kb_threshold` | 0.4 | 相似度阈值；小语料角色调低一点（0.35–0.45） |
| `kb_max_chars` | 800 | 注入上限，防止吃掉上下文 |

---

## 6. 角色与插件

### 6.1 内置五个角色

`klee`（可莉）、`hutao`（胡桃）、`citlali`（茜特菈莉）、`cyrene`（昔莲）、`atri`（亚托莉）。
每个角色 = **一套 SOUL + 一套知识库 + 一套主题 + 一组专属插件**，全部锁死，切换只在面板发生。

- 人格文件：`state/characters/<id>/SOUL.md`（首次从 `bot/character_seeds/<id>.md` 播种）
- 主题：`bot/ui_themes/<id>/{theme.json,theme.css}`
- 专属插件：插件文件头 `CHARACTERS = ("hutao",)` 决定归属

### 6.2 加一个角色

打包一个 ZIP（结构见 `docs/character-pack/`，示例 `docs/character-pack-example.zip`）：

```
character.json      # id / name / emoji / tagline / color / greeting / chips ...
SOUL.md
theme/theme.json
theme/theme.css
plugins/hello.py    # 可选，CHARACTERS 要包含这个 id
kb/sample.jsonl     # 可选，每行 {"text": "..."}
```

面板 → 角色页 → 「📦 导入角色 ZIP」。导入会：

- 校验路径穿越、扩展名白名单、文件数（≤240）、解压体积（≤64MB）、id 格式与冲突
- 覆盖同名主题目录（**替换，不留旧版**）
- 后台建索引并重启 klee-kb
- 不阻塞请求

### 6.3 加一个插件

单个 `.py`，放进 `bot/features/`，或用面板「插件 → 📦 安装插件包」上传 ZIP（≤2MB / ≤20 个文件）。

最小插件：

```python
"""示例：.你好"""
NAME = "打招呼"
DESCRIPTION = "回一句你好"
VERSION = "1.0"
CHARACTERS = ("klee",)          # 空元组 = 通用插件

def register(api):
    async def hi(ctx):
        await ctx.reply("你好呀！")
    api.on_command("你好", hi, aliases=("hi",), help=".你好 —— 打个招呼", cooldown=3)
```

保存后 **8 秒内热重载**，不用重启进程。插件只用标准库；需要 PIL 时参考
`bot/features/_cardimg.py` 的借用方式。

---

## 7. 日志

面板 → 运维 · 机器人 → 常用配置：

| 配置 | 默认 | 行为 |
| --- | --- | --- |
| `log_enabled` | 开 | 关掉后不再写 `state/bot.log`，运维页日志区显示「已关闭」 |
| `log_retention_days` | 30 | 自动清理 `alerts / messages / kb_hits / requests / bot.log` 中更早的内容；**0 = 永不清理** |

- 清理每 6 小时一次，启动时先跑一次。
- **聊天记录 `state/chat.jsonl` 不受保留天数影响**：那是会话内容，只在聊天页点「删」时删除。
- 这个项目**没有回档机制**：SOUL、插件、主题覆盖后就是新的，不生成 `.bak`。
  需要版本管理请用 git / 外部备份。

---

## 8. systemd 部署

### 8.1 Python 本体

`/etc/systemd/system/klee-studio.service`：

```ini
[Unit]
Description=Character Bot (klee-studio)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/character-bot
Environment=HOME=/root
ExecStart=/usr/bin/python3 /opt/character-bot/server.py
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now klee-studio
sudo systemctl status klee-studio --no-pager
```

### 8.2 可选：Go 边缘层

```bash
cd edge && GO111MODULE=off go build -o bin/klee-edge .
```

然后让 Python 退到回环地址（`/etc/systemd/system/klee-studio.service.d/edge.conf`）：

```ini
[Service]
Environment=KLEE_STUDIO_PORT=8798
Environment=KLEE_STUDIO_BIND=127.0.0.1
```

`/etc/systemd/system/klee-edge.service`：

```ini
[Unit]
Description=Character Bot edge (Go)
After=klee-studio.service
Wants=klee-studio.service

[Service]
Type=simple
WorkingDirectory=/opt/character-bot
ExecStart=/opt/character-bot/edge/bin/klee-edge \
  -listen :8796 -upstream http://127.0.0.1:8798 -root /opt/character-bot
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
```

### 8.3 可选：知识库

```ini
[Service]
WorkingDirectory=/opt/character-bot/klee-kb
ExecStart=/usr/bin/python3 server.py
```

---

## 9. 反向代理 / HTTPS（可选）

面板没有内建鉴权（只有写操作要求 `X-Klee-Studio: 1` 头，防跨站表单）。**要暴露到公网，请务必加一层**：

Caddy（最省事）：

```
bot.example.com {
    reverse_proxy 127.0.0.1:8796
    request_body { max_size 70MB }      # 角色包导入要传 zip
}
```

Nginx：

```nginx
server {
    listen 443 ssl http2;
    server_name bot.example.com;
    client_max_body_size 70m;

    location / {
        proxy_pass http://127.0.0.1:8796;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_buffering off;            # 轮询接口多，别攒
        proxy_read_timeout 120s;
    }
}
```

如果设了 `KLEE_STUDIO_TOKEN`，面板写操作还要额外带 `X-Klee-Studio-Token` 头（脚本调用时用）。

---

## 10. 升级

```bash
cd /opt/character-bot
git pull
python3 -m py_compile server.py
sudo systemctl restart klee-studio
```

- 只改插件 `.py`：不用重启，8 秒内自动重载（也可以 `touch bot/features/.reload`）。
- 改 `static/index.html`：刷新页面即可（ETag 会变）。
- 改 `bot/*.py`、`server.py`、SOUL、种子：**要重启**。
- 重建知识库后：`systemctl restart klee-kb`。
- **重启 NapCat 会掉登录**，需要重新扫码（NapCat 有快速登录，但未必成功）。

---

## 11. 常见问题

**面板打不开**
`ss -ltnp | grep 8796` 看有没有监听；`journalctl -u klee-studio -n 50 --no-pager` 看报错。

**QQ 收不到消息**
按顺序查：NapCat 是否登录 → `:3101` 是否监听 → 面板 `napcat_token` 与 NapCat 配置是否一致 →
面板「运维」页 QQ 状态是否 `online` → `journalctl -u napcat -n 50`。

**二维码不显示**
二维码接口要求 `X-Klee-Studio: 1` 头；用浏览器点按钮没问题，脚本要自己加。
另外 NapCat 已登录时没有二维码，会返回 404。

**日志区显示「已关闭」**
去「常用配置」把 `log_enabled` 打开。

**知识库没命中**
先看「人格 · 知识库」页的块数是不是 0；再点检索测试，看相似度是不是低于 `kb_threshold`。
小语料角色把阈值降到 0.35 试试。

**手机上深色/浅色不对**
面板默认跟随系统；在「自定义 → 外观」里可以强制浅色或深色（只影响这台设备）。

**改完插件没生效**
看 `journalctl` 里有没有插件加载报错；`/api/plugins` 的 `meta` 里会带 `error` 字段。
命令重名会被忽略并打日志（`命令 X 与已有命令重名`）。

---

## 12. 安全说明

- 面板**默认没有登录**，只适合内网。要公网访问请加反向代理 + Basic Auth / OAuth。
- 写操作要求 `X-Klee-Studio: 1`，只是为了挡住跨站表单提交，不是鉴权。
- 二维码、日志、聊天记录、人格等 GET 接口在服务端另有校验（同样这个头）。
- Agent 工具集**没有 shell、没有文件写入**：群聊里任何人都能触发对话，
  给执行权限等于把主机交出去。
- 好友/入群请求**只记录不自动通过**，必须人在面板点。
- 所有 key 只从环境变量读；`.env`、`state/` 都在 `.gitignore` 里。
