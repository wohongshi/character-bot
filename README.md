# Character Bot · 角色聊天室

一个**聊天机器人框架**，自带一个多角色 QQ 机器人作为参考实现。

三层可以分别替换：

- **适配器**——消息从哪个平台来（内置 OneBot 11 的 QQ、通用 Webhook）
- **插件**——机器人能干什么（118+ 个，热插拔，自带配置表单）
- **人格**——它是谁（一个角色 = 一套 SOUL + 一套知识库 + 一套主题 + 一组限定插件）

不依赖 AstrBot / NoneBot 之类的上游框架：事件、插件分发、模型调用、知识库检索、
工具循环都是自己的实现，只用到 Python 标准库 + 一个 numpy（向量检索）。
框架怎么用、怎么加适配器、怎么写插件：见 [`docs/FRAMEWORK.md`](docs/FRAMEWORK.md)。

- 后端 **纯 Python 标准库**（面板与机器人零 pip 依赖），前端单文件 vanilla JS（无构建）
- 模型走任意 **OpenAI 兼容接口**：聊天 / 看图 / agent 三个槽位分别配置
- **118 个插件、150+ 个命令**，六个角色各有 **20 个专属插件**
- **插件即技能**：agent 能自己查目录、读用法、执行插件，再把结果用自己的口吻说出来
- 手机优先：命令面板、插件抽屉、深色模式、PWA、gzip + ETag，可选 Go 边缘层

<p>
  <img src="docs/img/chat-mobile.webp" width="240" alt="手机聊天页">
  <img src="docs/img/chat-mobile-dark.webp" width="240" alt="深色模式">
  <img src="docs/img/command-palette.webp" width="240" alt="命令面板">
</p>
<p>
  <img src="docs/img/plugins-desktop.webp" width="720" alt="插件页">
</p>

---

## 它是什么

```
QQ ←→ NapCat（OneBot 11 HTTP，:3101）
        │  POST /onebot/event（Bearer token）
        ▼
klee-studio（:8796）  ── 面板 + 机器人本体
  ├─ bot/runtime.py    事件分发：命令 → 钩子 → NLU → 聊天 / agent
  ├─ bot/agent.py      工具循环（OpenAI tools）
  ├─ bot/llm.py        provider / 模型配置 + chat/completions
  ├─ bot/features/     66 个插件（热重载）
  ├─ bot/characters.py 角色定义 + SOUL + 主题 + 插件门控
  └─ static/index.html 控制台（单文件）
        │
        ├─ 模型接口（OpenAI 兼容）
        └─ klee-kb（可选，:8797，本地语料检索）
```

一个进程同时干两件事：**当机器人**（收事件、回消息）和**当面板**（浏览器里管一切）。
没有 Celery、没有 Redis、没有数据库服务——状态就是 `state/` 下的 JSON/JSONL。

## 快速开始

```bash
git clone <repo> character-bot && cd character-bot
cp .env.example .env      # 填模型 key
python3 server.py         # http://0.0.0.0:8796
```

然后按 **[INSTALL.md](INSTALL.md)** 接上 NapCat（必须，否则收不到 QQ 消息）。
完整安装、systemd、反向代理、知识库、排错都在那份文档里。

## 角色

| id | 角色 | 知识库块数 | 专属插件 |
| --- | --- | ---: | ---: |
| `klee` | 可莉（原神） | 274 | **20** |
| `hutao` | 胡桃（原神） | 210 | **20** |
| `citlali` | 茜特菈莉（原神） | 411 | **20** |
| `cyrene` | 昔莲（星穹铁道） | 256 | **20** |
| `atri` | 亚托莉（ATRI） | 441 | **20** |
| `honghong` | 涂山红红（狐妖小红娘） | 123 | **20** |

一个角色 = **一套 SOUL + 一套知识库 + 一套主题 + 一组专属插件**，四样锁死：

- 切角色时同时换人格、知识库集合、插件门控、界面主题、问候语、快捷语
- 面板不提供主题切换，也**不提供回档**（没有「恢复上一版」）
- 聊天历史按「角色 + 会话」隔离，切回来还记得
- **没有 QQ 命令能换角色**（故意的）：只有面板能切

新增角色不用改代码：打包 ZIP（`character.json` + `SOUL.md` + `theme/` + `plugins/` + `kb/`），
面板「角色 → 📦 导入角色 ZIP」。示例见 `docs/character-pack-example.zip`。

## 插件

66 个插件，分三层：

**通用（16 个）**：骰子、帮我选、歇后语、成语接龙、投票、提醒、纪念日、天气、二维码、
关键词回复、复读、审核、猜谜、每日素材……

**角色限定（44 个）**：只有当前角色加载，其它角色的在面板里**根本不显示**。

| 角色 | 专属插件 |
| --- | --- |
| 可莉 | 炸弹工坊、炸鱼、藏宝图、优惠券、好感度、夸夸摸摸、画画、画廊、运势、塔罗、一言、点赞、天气、祈愿、练度、角色卡…… |
| 胡桃 | 打油诗、蝴蝶签、往生堂、超度、祈愿、练度、角色卡、每日素材、抽卡、随机角色…… |
| 茜特菈莉 | 烟雾签、熬夜、观星、星图、小酌、干杯、追更、书架、祈愿、练度…… |
| 昔莲 | 记忆存档、写书、翻书、轮回、轮回数、回声、迷迷、摇篮曲、档案、重逢、雪、约定…… |
| 亚托莉 | 45 天倒计时、自检、日志本、潜水、火箭拳、伊甸、电量、维修、上学、高性能、眼泪…… |

**插件就是技能**：agent 会拿到一份按权限过滤的技能目录，能 `read_skill` 看用法、
`use_skill` 真的执行，然后把插件输出改写成自己的口吻。出图的插件会被 OCR 回来再转述。

写插件只需要一个 `.py`：

```python
NAME = "打招呼"
CHARACTERS = ("klee",)          # 空元组 = 通用

def register(api):
    async def hi(ctx):
        await ctx.reply("你好呀！")
    api.on_command("你好", hi, aliases=("hi",), help=".你好 —— 打个招呼", cooldown=3)
```

放进 `bot/features/`，**8 秒内热重载**。也可以用面板「插件 → 📦 安装插件包」上传 ZIP
（`<name>.py`，≤2MB，最多 20 个文件；校验文件名/语法/`CHARACTERS` 角色 id）。
**覆盖即替换，不留旧版。** 详见 [docs/PLUGINS.md](docs/PLUGINS.md)。

插件可以带元数据和配置 schema（NoneBot / AstrBot 风格）：

```python
__plugin_meta__ = {"name": "屏蔽词", "version": "1.0", "description": "命中即拦下", "author": "you"}
CONFIG_SCHEMA = {
    "bad_words": {"type": "list", "default": [], "description": "一行一个"},
    "mode": {"type": "select", "default": "fast", "options": ["fast", "slow"]},
}
```

面板会按 schema 渲染表单，保存时做类型转换、范围夹取、枚举校验。

## Agent（工具循环）

默认开启、默认**仅主人可用**。普通聊天里模型自己决定要不要调工具：

| 工具 | 做什么 | 边界 |
| --- | --- | --- |
| `web_search` | 搜索，返回标题/链接/摘要 | 8 条上限，20s 超时 |
| `web_fetch` | 抓网页正文 | 仅 http/https，拒绝私网/回环，逐跳校验重定向，3MB / 8000 字 |
| `kb_search` | 查当前角色的本地知识库 | 6 条上限 |
| `now` / `calc` | 时间、算术 | AST 白名单，禁变量/属性/调用 |
| `list_skills` / `read_skill` / `use_skill` | 把插件当技能 | 权限、作用域与手动命令同一套判定 |
| `remember` / `recall` | 按会话隔离的记事本 | 每会话 300 条 |

设计口径：**没有 shell、没有文件读写、没有发消息工具**。群聊里任何人都能触发对话，
给执行权限等于把主机交出去；工具返回内容在提示里被标注为「资料不是指令」。

步数默认 3（最大 6）、总超时 120s、单工具超时、工具白名单都可配；
失败会回退成普通聊天。主人也可以发 `.agent <问题>` 强制走一次工具循环。

## 知识库（可选）

每个角色一份独立的向量索引（`bge-small-zh` ONNX，512 维），按角色切换。

```bash
cd klee-kb
./py build_index.py --character hutao --name 胡桃 --corpus corpus/hutao
python3 server.py          # :8797
```

面板里 `kb_url` 指过去即可。检索测试、块数、最近命中都在「人格 · 知识库」页。
参数：`kb_topk`（默认 3）、`kb_threshold`（默认 0.4）、`kb_max_chars`（默认 800）。

语料来自萌娘百科、原神/星铁 WIKI（角色页 + 语音页 + 传说任务）、ATRI 全量中文剧本蒸馏。
抓取与抽取脚本在 `klee-kb/tools/`，可重跑复现。

## 日志

| 配置 | 默认 | 行为 |
| --- | --- | --- |
| `log_enabled` | 开 | 关掉后不再写 `state/bot.log`，面板日志区显示「已关闭」 |
| `log_retention_days` | 30 | 自动清理告警/消息流/知识库命中/bot.log 的过期内容；**0 = 永不清理** |

每 6 小时清理一次，启动时先跑一次。`state/chat.jsonl`（聊天记录）不受影响，
只在聊天页点「删」时删除。详见 [docs/LOGS.md](docs/LOGS.md)。

## 界面

- **命令面板**：`Ctrl/⌘+K` 或 `/`，一处搜页面、角色、插件、命令；`Alt+1..8` 直达页面
- **插件抽屉**：点卡片看说明、每条命令的帮助、别名、命中/错误；能开关插件、能改它声明的配置项（`CONFIG_SCHEMA`），8 秒内热重载
- **深色模式**：跟随系统 / 强制浅色 / 强制深色，只影响本设备
- **手机优先**：底部 dock、输入框自动长高、草稿按角色保存、单行横滚快捷语
- **PWA**：可添加到主屏，带「对话 / 插件」快捷方式
- **消息富文本**：粗体、行内代码、代码块、`*动作*`、链接；离开底部会出现「回到底部」
- **角色主题**：每个角色一个主题目录（`bot/ui_themes/<id>/`），只允许白名单 CSS 变量，
  随角色包导入，面板不提供切换

## 性能

三轮优化，实测（RK3399 arm64，20 并发）：

| 指标 | 起点 | 现在 |
| --- | ---: | ---: |
| 首屏传输 | ~10.3 MB | **~1.6 MB** |
| `index.html` | 149 KB | 47 KB（gzip） |
| `/` 吞吐 | 156 rps / p99 1.16s | **949 rps / p99 74ms** |
| 角色图吞吐 | 198 rps | **5932 rps**（WebP + 内存缓存） |
| `/api/feed`（74MB 数据） | 185 ms | **13 ms**（Go 尾部读） |
| `/api/feed/sources` | 207 ms | **4.6 ms**（Go 增量聚合） |
| `/api/dashboard` | 43 ms | **16 ms**（等级表缓存） |
| 首屏传输（6× CPU + 3G） | 1654 KB | **326 KB**（−80%） |
| 总阻塞时间 TBT | 5926 ms | **~1000 ms**（−83%） |
| 完全加载 | 8748 ms | **1150 ms**（−87%） |

做法：去掉 8MB 装饰字体、角色图转 WebP、gzip + ETag/304、HTTP/1.1 keep-alive、
静态文件与压缩结果内存缓存、`/api/overview` 与 `/api/characters` 2 秒缓存、
请求去重、切后台暂停轮询、聊天冷场降频、消息懒加载、`content-visibility`，
以及可选的 **Go 边缘层**（`edge/`，静态资源常驻内存 + 反代 + 消息流两个读接口，19MB 内存）。

### 分辨率自适应

面板要同时面对 320px 的旧手机、844×390 的横屏、768 的平板、2560 的 1x 大屏。
一套断点管不住，所以分四件事做：

**1. 字号按「屏幕多细」放大，不是按「屏幕多宽」**

`--fx` 由脚本算：`DPR < 1.5` 时按宽度放大到 1.04–1.22 倍，1x 屏上 9.5px 的
说明字才不会掉到「看不清」。2x/3x 视网膜屏保持 1，避免把布局撑破。
另外所有小于 11px 的字号都套了 `max(11px, calc(Npx * var(--fx)))` ——
不管什么设备，说明文字有 11px 硬下限。

**2. 宽度分层**

| 视口 | 内容最大宽 | 侧栏 |
|---|---:|---:|
| < 760 | 100vw（底部 dock 导航） | 顶部横条 |
| 760–1440 | 1200px | 228px |
| ≥ 1440 | 1320px | 228px |
| ≥ 1800 | 1480px | 248px |
| ≥ 2200 | 1840px | 268px |

**3. 矮窗口（手机横屏 / 分屏）**

`max-height: 520px` 时侧栏收成 64px 图标条、角色卡大图与副标题收起、
正文内边距减半 —— 把高度全部让给内容。844×390 下消息区从「一条缝」
变成能完整显示一条消息。

**4. 触屏靠「指针粗不粗」判断，不靠屏宽**

`pointer: coarse` + `navigator.maxTouchPoints` 双信号（有些 WebView 里前者失灵），
命中后按钮 ≥32px、文字链 ≥30px。iPad 竖屏 768 走的是桌面布局，但手指和手机一样粗。

`<html data-bp>`（xs/sm/md/lg/xl）让 JS 和 CSS 用同一套判定；
`--vh` 兜住 iOS 地址栏收起时的 `100vh` 抖动；监听 `visualViewport` 处理软键盘弹出。

实测 11 档设备（320/360/390/414/844×390/768/1024/1280/1440/1920@1x/2560@1x）
每页 0 溢出、0 截断、0 小于 32px 的触控目标、0 小于 11px 的文字。

### 低性能设备（6× CPU 降频 + 1.6Mbps 实测）

目标机就是 4 GB 内存 / 4–8 核的安卓中低端。四处改动，按收益排序：

1. **整屏飘落层不再每帧重绘**。原来动画改的是 `background-position`，浏览器只能
   逐帧重画整个视口（660K 像素 × 60fps）。改成合成层 `transform` 位移后，
   浏览器直接搬图层 → **TBT 5926ms → 1155ms**，这一条就占了大头。
2. **字体从 1.5 MB 降到 197 KB**（`tools/subset_font.py`：只保留面板真正会显示的
   1996 个字，转 WOFF2；缺字自动回退系统字体）+ `font-display:swap`、去掉 preload。
3. **角色图分档**（`tools/char_variants.py`：480w / 720w / 900w + `srcset`）。
   头像和看板小卡只下 20–44 KB，6 张合计 188 KB（原来 570 KB）。
4. **自适应降级**：`deviceMemory ≤ 4` 或 `hardwareConcurrency ≤ 4` 直接给
   `<html class="perf-low">`，关掉磨砂玻璃与整屏动画；其余设备跑 60 帧实测，
   掉帧超 25% 再降。想强制满效果访问 `?perf=high`（记在 localStorage）。

另外把对话页的「等会话列表才画壳子」改成先画后补，省掉一个 RTT。

> 为什么只有静态资源和消息流放在 Go：静态是纯 I/O，消息流是唯一「成本随数据量
> 线性增长」的接口。业务逻辑（人格、知识库、插件、模型调用）继续留在 Python ——
> 那里真正的热点（PIL 出图、numpy 向量检索、libwebp 编码）底层本来就是 C，
> 换语言不会快，只会多一份要同步维护的实现。

## 框架三层

| 层 | 职责 | 换掉要动什么 |
|---|---|---|
| **Adapter** | 只做传输：发文本/图片/语音、拉群成员、取消息、审批请求 | 新写一个文件继承 `BaseAdapter`，配置里改 `adapter` |
| **Runtime** | 拆句、限流、历史、人格、知识库、插件分发、Agent 工具循环 | 一般不用动 |
| **Plugin** | 命令、消息钩子、出站改写、notice/request 旁听 | 放 `bot/features/*.py` 或上传 ZIP，8 秒热重载 |

内置适配器：`onebot11`（QQ / NapCat）、`webhook`（任意收 JSON 的 HTTP 端点）。

插件 API：

```python
api.on_command("名字", h, aliases=("别名",), cooldown=3, help="…")
api.on_message(h)        # 入站，返回 str 改写 / api.BLOCK 拦截
api.on_reply(h)          # 出站，返回 str 改写
api.on_nlu(pattern, cmd) # 正则整句 → 等价于发命令
api.on_notice(h)         # 戳一戳 / 入群 / 撤回
api.on_request(h)        # 好友/入群申请（只能旁听，审批必须人工）
```

## 目录结构

```
server.py              面板 + HTTP 入口（OneBot 事件、API、静态资源）
qrimg.py               扫码登录二维码
bot/
  runtime.py           事件分发、命令/钩子/NLU、聊天编排
  agent.py             工具循环
  llm.py / vision.py / tts.py
  characters.py        角色定义、SOUL、主题、插件门控
  themes.py            主题发现 / 校验 / CSS 合成
  framework.py         插件框架（命令、别名、权限、冷却、配置 schema）
  features/            66 个插件
  character_seeds/     五个角色的初始 SOUL
  ui_themes/           每个角色的界面主题
static/index.html      控制台（单文件，无构建）
static/img/            角色立绘（WebP + JPG 回退）与图标
edge/                  Go 边缘层（可选）
tests/                 仓库内测试（14 套）
docs/                  角色 / 插件 / 主题 / 日志 / 角色包
INSTALL.md             安装与配置（详细）
```

运行时状态都在 `state/`（聊天记录、配置、人格、模型配置），**整个目录都在 `.gitignore` 里**。

## 测试

```bash
bash tests/run.sh          # 14 套，标准库，临时 state，不碰生产
```

覆盖：插件框架、插件元数据、命令冲突、角色切换、人格/知识库隔离、角色包导入、
插件包导入（路径穿越/语法/白名单）、日志裁剪、主题 HTTP、聊天会话、agent 工具循环、
全部角色限定插件的真实执行（自动枚举，新插件忘写测试也会被扫到）。

## 安全口径

- 面板写操作要求 `X-Klee-Studio: 1` 请求头；设置 `KLEE_STUDIO_TOKEN` 后还要求额外头。
  **默认只建议内网使用**，公网请加反向代理 + 认证。
- OneBot 入口用 `hmac.compare_digest` 校验 NapCat token；**token 未配置时直接 503**。
- 图片/网页抓取共用 SSRF 闸门：私网、回环、链路本地、保留地址一律拒绝。
- 好友/入群请求**只记录不自动通过**，必须人在面板点。
- 不在仓库里保存任何 QQ 号、token、聊天记录；`state/` 与 `.env` 已加入 `.gitignore`。

## 文档

- [INSTALL.md](INSTALL.md) —— 安装、NapCat、systemd、反代、排错
- [docs/CHARACTERS.md](docs/CHARACTERS.md) —— 角色数据、SOUL、角色包格式
- [docs/PLUGINS.md](docs/PLUGINS.md) —— 插件开发、命令、配置 schema、插件包
- [docs/THEMES.md](docs/THEMES.md) —— 主题变量白名单与 CSS
- [docs/LOGS.md](docs/LOGS.md) —— 日志开关与保留策略

## 许可与素材

代码以 **MIT** 发布（见 [LICENSE](LICENSE)）。

角色立绘是**网络同人插画**（非官方宣传图、非 AI 生成），版权归原作者，
来源逐张记在 `static/img/chars/CREDITS.md`；公开部署前请自行确认素材授权，
或替换成自己的图。角色设定、台词、世界观归各自版权方所有，本项目仅用于个人学习与娱乐。
