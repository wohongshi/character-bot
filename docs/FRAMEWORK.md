# 框架：适配器、插件、事件

这套东西不是「一个 QQ 机器人」，而是一个**聊天机器人框架**：消息从哪个平台来、
机器人怎么想、要挂哪些能力，是三件可以分别替换的事。

```
        ┌─────────────┐   Event    ┌──────────────┐   send/reply   ┌─────────────┐
        │   Adapter   │ ─────────► │   Runtime    │ ─────────────► │   Adapter   │
        │ QQ / Webhook│            │ 插件·LLM·知识库│                │  (发回去)    │
        └─────────────┘            └──────────────┘                └─────────────┘
               ▲                          │
               │                          ├── Plugins（118 个，可热插拔）
        平台负责收消息                    ├── Persona（每个角色一套 SOUL）
                                         └── Knowledge Base（每个角色一套索引）
```

- **Adapter 只管传输**：发文本、发图、发语音、拉群成员、取消息、审批请求。
- **Runtime 管业务**：拆句、限流、历史、人格、知识库、插件分发、Agent 工具循环。
- **Plugin 管能力**：命令、消息钩子、出站改写、非消息事件、定时任务。

新增一个平台，不需要动 Runtime 和任何插件。

---

## 1. 写一个适配器

在 `bot/adapters/` 下新建一个文件，继承 `BaseAdapter`、标上 `platform`，导入即注册：

```python
# bot/adapters/telegram.py
from .base import AdapterError, BaseAdapter, register

@register
class TelegramAdapter(BaseAdapter):
    """Telegram Bot API。"""
    platform = "telegram"           # 配置里 adapter 的取值
    title = "Telegram"
    CAPABILITIES = {
        "text": True, "image": True, "voice": False,
        "group_member_list": False, "message_lookup": False,
        "request_approval": False, "recall": False,
    }

    def __init__(self, config=None, **kw):
        super().__init__(config, **kw)
        self.token = str(self.config.get("telegram_token") or "")

    def chat_id_of(self, is_group, group_id, user_id):
        # 会话 ID 由你定义，框架只要求「稳定 + 你自己能解析回来」
        return f"tg:{group_id or user_id}"

    def send_text(self, chat_id, text, *, reply_to=None, at_uid=None):
        ...  # 调 Telegram API，返回 {"ok": True, "message_id": ...}
```

然后在 `bot/adapters/__init__.py` 里加一行 `from . import telegram`，面板配置
`adapter` 就能选到它。

### 约定

| 规则 | 为什么 |
|---|---|
| 只实现**能支持**的动词，其余留空 | 基类会抛 `AdapterError("xxx 不支持…")`，比 `AttributeError` 好排查 |
| 能力要如实写进 `CAPABILITIES` | 插件用 `ctx.supports("image")` 判断，写假了插件会撞墙 |
| I/O 动词同步 / `async def` 都可以 | 框架的 `_adapter_call()` 两种都认：同步丢线程池，协程直接 await |
| `describe()` / `health()` / `reconfigure()` 必须**同步** | 它们是元数据，不该做 I/O；写成 async 面板只会显示一条错误说明 |
| `health()` 不抛异常 | 面板「运维」页轮询它，抛了就整页错 |
| 异常统一成 `AdapterError` | 上层只处理一种类型 |

### 已内置

| platform | 平台 | 出站 | 入站 |
|---|---|---|---|
| `onebot11` | QQ（NapCat / Lagrange / go-cqhttp） | ✅ 文本/图片/语音 | 平台 POST 到 `/onebot/event` |
| `webhook` | 任意「收 JSON 的 HTTP 端点」 | ✅ 文本/图片(base64) | 同上（自己转 Event 结构） |

`webhook` 适配器的存在有个额外作用：**证明抽象是真的**。只有 OneBot 一个实现时，
`BaseAdapter` 里那些 `chat_id_of` / `CAPABILITIES` / 降级路径全都是摆设。

---

## 2. 写一个插件

放进 `bot/features/*.py`（或面板「插件 → 安装插件包」上传 ZIP），8 秒内自动热重载。

```python
NAME = "打招呼"
DESCRIPTION = "有人进群就欢迎一下"
VERSION = "1.0"

def register(api):
    async def on_cmd(ctx):
        await ctx.reply(f"你好，{ctx.user_name}")

    async def on_notice(ctx):                    # 非消息事件：入群/戳一戳/撤回
        if ctx.notice_type == "group_increase":
            await api.send(ctx.chat_id, "欢迎新朋友～")

    api.on_command("打招呼", on_cmd, aliases=("hi",), cooldown=3,
                   help=".打招呼 —— 我回你一句")
    api.on_notice(on_notice)
```

### 能注册什么

| 装饰方法 | 触发 | 返回值语义 |
|---|---|---|
| `api.on_command(name, h, aliases=, scope=, permission=, cooldown=, help=, priority=)` | 收到 `.name` 命令 | 无 |
| `api.on_message(h, priority=)` | 每条消息，**在命令之前的入站链** | 返回 `str` 改写文本 / 返回 `api.BLOCK` 拦截 |
| `api.on_reply(h, priority=)` | 每条出站消息 | 返回 `str` 改写要发的内容 |
| `api.on_nlu(pattern, cmd, priority=)` | 正则整句命中 → 等价于发了 `.cmd` | 无 |
| `api.on_notice(h, priority=)` | 戳一戳 / 入群 / 退群 / 撤回 | 旁听，返回值只进日志 |
| `api.on_request(h, priority=)` | 好友申请 / 入群申请 | **旁听**，不能自动通过 |

### 上下文里有什么

`ctx`（消息事件）：
`text` `args` `arg_text` `user_id` `user_name` `group_id` `is_group` `mentioned`
`role` `chat_id` `is_owner` `is_admin` `platform` `supports(cap)` `reply(text)`

`ctx`（notice/request 事件，`EventContext`）：
`kind` `raw` `notice_type` `request_type` `sub_type` `user_id` `group_id`
`is_group` `chat_id` `is_owner` `platform` `supports(cap)`

`api`：
`settings`（插件的 `CONFIG_SCHEMA` 值）`state`（内存态，重载即清）
`send(chat_id, text)`（主动发）`character` / `character_id`
`commands_help()` `log()`

### 三条硬规矩

1. **命令名 + 别名是全局命名空间**。撞了不是报错，是**整条命令静默消失**。
   改名字前先 `grep` 一遍。
2. **审批绝不自动通过**。`on_request` 只能旁听 —— 这是 2026-10-16 那次事故之后
   定下的铁律，谁也不能在插件里绕过去。
3. **`register(api)` 抛异常 → 已注册的半截命令/钩子会被整体回滚**，
   插件标记为加载失败。所以宁可拆小，也别在一个 register 里做十件事。

### 按平台能力降级

```python
if ctx.supports("group_member_list"):
    raw   = await api.adapter.get_group_member_list(ctx.group_id)  # 原样 list[dict]
    names = await api.adapter.member_names(ctx.group_id)           # {uid: 名片}，带缓存
else:
    names = {}          # 这个平台没有，走降级分支

await api.adapter.recall(ctx.message_id)      # 撤回；不支持时抛 AdapterError
await api.adapter.send_raw(chat_id, segs)     # 原始消息段（不是每个平台都有）
```

`api.adapter` 就是运行时本身，这些动词都由运行时转给当前适配器，
**同步/异步实现都认**，而且平台不支持时抛的是 `AdapterError` 而不是 `AttributeError`。

别用 `hasattr(api.adapter, "send_image")` —— 基类有兜底方法，永远为真。

---

## 3. 事件模型

平台推过来的原始 JSON 按 `post_type` 分三类：

| post_type | 走哪 | 插件能介入吗 |
|---|---|---|
| `message` | `FeatureManager.handle_command` → `on_message` → 命令 / NLU → LLM | ✅ 入站拦截、改写、命令 |
| `notice` | `run_event_hooks("notice")` → 内置（戳一戳回应等） | ✅ 旁听 |
| `request` | `run_event_hooks("request")` → 内置（提醒主人、落盘） | ✅ 旁听（不能审批） |

钩子**先于**内置行为执行，但内置行为一定继续 —— 插件写崩了顶多自己计数 +1，
不会让「好友申请提醒主人」也跟着失效。

想加一种新事件类型？在 `runtime._handle` 里加一个分支，调
`await self._run_event_hooks("<新kind>", ev)`，再在 `FrameworkManager` 里加一条
对应 list 即可。

---

## 4. 与 AstrBot / NoneBot 的对应关系

| 能力 | 本项目 | AstrBot | NoneBot |
|---|---|---|---|
| 适配器 | `BaseAdapter` + 注册表，配置里选 | `Platform` 适配器 | Driver + Adapter |
| 命令注册 | `api.on_command(name, aliases, permission, cooldown)` | 装饰器 + 事件过滤器 | `on_command` |
| 消息钩子 | `on_message` / `on_reply` | `@filter.on_llm_request` 等 | `on_message` |
| 非消息事件 | `on_notice` / `on_request` | `@filter.on_notice` | `on_notice` / `on_request` |
| 插件元数据 | `NAME/DESCRIPTION/VERSION/AUTHOR/USAGE` | `__plugin_meta__` | `PluginMetadata` |
| 插件配置 | `CONFIG_SCHEMA` → 面板自动出表单 | `_conf_schema.json` | `Config` / `get_plugin_config` |
| 热重载 | 改文件 / `.reload` → ≤8 秒 | ✅ | 需 `nonebot reload` |
| 插件包 | ZIP 上传 / 目录放置 | 插件市场 | 目录 + pip |
| 面板 | 内置（:8796） | 内置 WebUI | 需 third-party（NoneBoard 等） |
| 多角色人格 | 一等公民（每角色 SOUL+KB+主题+插件组） | 无（靠会话配置） | 无 |

**本项目的差异点**：人格（角色）是一等公民 —— 一个角色 = 一套 SOUL + 一套知识库
+ 一套界面主题 + 一组限定插件，整体跟着切换。AstrBot/NoneBot 要自己拼。

---

## 5. 目录

```
bot/
  adapters/            ← 适配器层（新平台加在这里）
    base.py             BaseAdapter / 注册表 / maybe_await
    onebot.py           OneBot 11（QQ）
    webhook.py          通用 HTTP 出站
  framework.py         插件管理器：命令 / 钩子 / NLU / 能力门控 / 热重载
  runtime.py           业务运行时：分发、LLM、知识库、限流、Agent
  service.py           BotService 外壳 + DEFAULT_CONFIG
  characters.py        角色存储（SOUL / KB / 主题 / 限定插件）
  features/            插件目录（118+ 个，可热插拔）
  character_seeds/     内置角色的人格种子
  ui_themes/           角色主题
```
