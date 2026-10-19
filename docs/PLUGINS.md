# 插件开放规范（Character Bot）

面板「插件」页里其实有两种插件，用筛选栏的 **系统 / 限定** 区分：

| 种类 | 是什么 | 放在哪 | 默认 |
|---|---|---|---|
| **功能插件** | 命令、钩子、小游戏、工具 —— 机器人「会做什么」 | `bot/features/<name>.py` | 按 `config.json` 的 `enabled` |
| **系统插件** | 适配器 —— 消息「从哪个平台来」 | `bot/adapters/<platform>.py` | **全部关闭**，在面板里逐个开 |

系统插件的写法见 [FRAMEWORK.md](FRAMEWORK.md#适配器--系统插件)；这一份只讲功能插件。

功能插件 = `bot/features/<name>.py` + 在 `bot/features/config.json` 的 `enabled` 白名单里登记（角色限定插件不需要登记）。
改完文件后 8 秒内热重载；语法错误会在加载时被记录到该插件的 `error`。面板可在线开关插件、改 `CONFIG_SCHEMA` 里的配置项，但不提供在线改代码。

## 1. 最小插件

```python
NAME = "打招呼"
DESCRIPTION = "有人发 .hi 就回一句"
VERSION = "1.0"

def register(api):
    def hi(ctx):
        return f"你好呀，{ctx.user_name}！"
    api.on_command("hi", hi, aliases=("hello",), help=".hi —— 打个招呼")
```

约定：

- 文件名 = 插件 id（`hi.py` → `hi`），不要下划线开头（`_xxx.py` 是共享支撑模块，不装载）。
- `register(api)` 是唯一入口，**不要**在模块顶层做网络请求/起线程；注册要快。
- 异常要自己兜住：命令抛异常会被框架记一次 `errors`，但不会回消息。

## 2. 元数据

两种写法等价，`__plugin_meta__` 优先：

```python
__plugin_meta__ = {
    "name": "打招呼",
    "description": "有人发 .hi 就回一句",
    "usage": ".hi / .hello",
    "author": "you",
    "homepage": "https://example.com",
    "version": "1.0",
}
```

面板会展示标题/描述/用法/作者；帮助菜单按 `help` 文案分组。

## 2.5 按会话存状态（`_plugstore`）

需要“谁在哪个群说过什么”的插件，别自己 `open()`：用同目录的 `_plugstore`。

```python
from _plugstore import chat_key, db_get, db_set, state_path, clip

FILE = state_path("my_plugin")          # KLEE_STATE_DIR 或 features/state/

def _key(ctx) -> str:
    return chat_key(ctx)                # 群聊 g:<群号>、私聊 u:<QQ>

def save(ctx):
    items = [x for x in (db_get(FILE, _key(ctx), []) or []) if isinstance(x, dict)]
    items.append({"text": clip(ctx.arg_text, 160)})
    db_set(FILE, _key(ctx), items[-50:])
```

- `load()` 只把 **JSON 解析失败 / 结构不是对象** 当损坏并备份为 `*.corrupt-<ts>.json`；普通 IO 错误只记日志。
- `db_set()` 对同一文件加进程内锁，读-改-写不会互相覆盖。
- `clip(s, n)` 保证结果 **≤ n 字**（含省略号）。
- 状态文件是纯 JSON，方便面板/脚本排查；别存二进制。

## 3. 命令

```python
api.on_command(
    name="练度",              # 命令名（不带前缀）
    handler=fn,              # 同步或 async 函数
    aliases=("面板",),        # 别名
    scope="both",            # both / group / dm
    permission="all",        # all / admin / owner
    cooldown=8,              # 秒；按 (命令, 用户) 计
    help=".练度 <UID> [角色] —— 查展柜",   # 帮助文案
    priority=100,            # 数字小的先匹配（重名时先注册者胜）
)
```

`ctx` 常用字段：

| 字段 | 说明 |
| --- | --- |
| `ctx.text` | 展平文本（图片是 `[图片]` 占位） |
| `ctx.args` / `ctx.arg_text` | 命令参数列表 / 拼好的字符串 |
| `ctx.is_group` / `ctx.group_id` | 群聊 |
| `ctx.user_id` / `ctx.user_name` / `ctx.role` | 发送者（role: owner/admin/member） |
| `ctx.message_id` | 入站消息 id（引用用） |
| `ctx.mentioned` | 是否被 @ / 被回复 |
| `ctx.is_owner` / `ctx.is_admin` | 权限判断 |
| `ctx.chat_id` | `group:<gid>` / `private:<uid>` |
| `ctx.reply(text)` | 回一条（不拆句、走限流） |
| `ctx.adapter` | 适配器（发图/发语音/取群成员） |

命令返回值：返回字符串 = 回一条；`await ctx.reply(...)` 也行；返回 `None` 不回。

## 4. 钩子与自然语言

```python
# 入站钩子：返回 api.BLOCK 拦下（不再进命令/聊天），返回字符串 = 改写文本
async def guard(ctx):
    if "广告" in ctx.text:
        return api.BLOCK
api.on_message(guard, priority=10)      # 数字小的先跑

# 出站钩子：改写将要发出的文本
def polish(send_ctx, text):
    return text.replace("AI", "我")
api.on_reply(polish, priority=50)

# 自然语言 → 命令（整句 fullmatch；{1} 引用捕获组）
api.on_nlu(r"(?:查|看看)([\\u4e00-\\u9fa5]{2,8})的练度", "练度 {1}")
```

## 5. 配置：settings + schema

`api.settings` 是 `config.json` 里 `settings.<插件名>` 的字典。声明 `CONFIG_SCHEMA` 后面板「插件 → 插件设置」自动出表单：

```python
CONFIG_SCHEMA = {
    "threshold": {"type": "int", "default": 3, "min": 2, "max": 10, "description": "连续几条触发"},
    "chance":    {"type": "float", "default": 0.9, "min": 0, "max": 1},
    "reply":     {"type": "str", "default": "好呀", "max_len": 200},
    "words":     {"type": "list", "default": [], "description": "一行一个"},
    "mode":      {"type": "select", "default": "fast", "options": ["fast", "slow"]},
}
```

- 类型：`bool / int / float / str / text / list / json / select`；
- 后端保存时做类型转换、范围夹取、枚举校验、未知键拒绝；
- 没声明 schema 的插件仍可用，但只能手改 `config.json`。

## 6. 角色限定

```python
CHARACTERS = ("klee", "hutao", "citlali")   # 空/不写 = 通用插件
```

- 当前角色在列表 → 自动启用，面板显示「锁定」，**不能关闭**，白名单不生效；
- 不在列表 → 不加载、不注册命令；
- 新增角色想用某插件：把 id 加进该插件的 `CHARACTERS`，或把插件改成通用。

面板把插件分成三类（过滤栏也是这三个）：

| 分类 | 判定 | 例子 |
|---|---|---|
| **限定** | 写了 `CHARACTERS` | 抽卡、塔罗（每个角色各 20 个） |
| **通用** | 没写 `CHARACTERS`，也不是系统 | 帮助、复读、记事、技能盒子 |
| **系统** | `SYSTEM = True`，或就是平台适配器 | 6 个平台 + 联网工具 |

## 6.5 工具与页面

插件除了命令/钩子，还能挂两样**不是钩子**的东西：

```python
def register(api):
    api.add_tool("now", description="现在几点", handler=tool_now,
                 parameters={"type": "object", "properties": {}})   # agent 工具
    api.add_page("<div>…</div>", title="我的小面板")                 # 自带页面
```

- `add_tool`：登记进 `bot/toolbox.py`；**关掉插件，模型的可调用列表里就没有这个工具**。
  `SYSTEM = True` 的插件登记的工具归「系统」分类。
- `add_page`：面板抽屉里会出现「🧩 打开插件页」，内容渲染在同源 iframe 里。
  平台自己的扫码、诊断、控制台都放这里，别堆到运维页。

### 插件页能用的桥（`ks.*`）

| 用法 | 说明 |
|---|---|
| `ks.api(path)` / `ks.post(path, body)` | 调面板的同源接口，自动带 `X-Klee-Studio`；非 2xx 也会把 `ok:false` 带回来 |
| `ks.send(text, {session, character, timeout})` | **发一句并等回复**。`POST /api/chat` 只是入队（返回 `{queued:true}`，没有正文），所以别自己读 `d.messages` —— 这个原语把「入队 → 轮询到那条助手回复」包掉了 |
| `ks.char` / `ks.session` | 当前角色 / 当前会话。**别依赖服务端默认值**：`/api/chat/delete` 这类接口不带 `character` 就会落到「当前角色」，换角色后会删错人的会话 |
| `ks.toast(text)` | 面板右上角的一行提示 |
| `ks.goto(view)` / `ks.reload()` | 跳到某个页面 / 重载插件列表 |
| `ks.esc(s)` | HTML 转义 |

页面里可以正常用 `confirm()` / `alert()`（iframe 沙箱带 `allow-modals`）、`<a download>`（带 `allow-downloads`）。

写页面时的两个坑：
- **别在 Python 源码里用 `\n` 拼 JS**：`_PAGE_HTML = """..."""` 会把 `\n` 变成真换行，整段 `<script>` 静默语法错误、页面只剩静态文字。要么用 `r"""`，要么在 JS 里用 `String.fromCharCode(10)`。
- 主题变量由面板在打开时抄进 iframe，取不到值的会跳过 —— 所以 CSS 里用 `var(--x, fallback)` 写兜底。

## 7. 状态与并发

- 插件状态写到 `state/` 下；**必须**用 `KLEE_STATE_DIR` 环境变量可覆盖的写法，测试/干跑才不会污染生产：

```python
import os
from pathlib import Path

def _state_root() -> Path:
    base = os.environ.get("KLEE_STATE_DIR")
    return Path(base) if base else Path(__file__).resolve().parent / "state"
```

- 写盘用「临时文件 + `os.replace`」；失败只记日志，不要因为存不下就不回消息。
- handler 在 **bot 事件循环**里执行；不要在里面 `time.sleep` / 阻塞网络，重活丢 `asyncio.to_thread`。
- 不要在插件里起永不退出的线程；需要定时任务用现有 `remind`/`anniversary` 的模式。

## 8. 发消息 / 发图 / 语音

```python
async def card(ctx):
    from pathlib import Path
    out = Path("/tmp/x.png")
    res = await ctx.adapter.send_image_file(ctx.chat_id, str(out), caption="给你")
    if not res.success:                 # 掉线时不会抛异常，而是 success=False
        await ctx.reply("图没发出去，稍后再试")
```

- 主动发消息（没有入站事件）用 `api.send(chat_id, text)`；它同样在失败时返回 `success=False`。
- 语音：`ctx.adapter.send_voice(chat_id, path)`（面板运行时支持）。

## 9. 热重载与调试

- 保存插件文件 / `config.json` / 触摸 `bot/features/.reload` → 8 秒内热重载；
- 命令行干跑：`python3 dryrun.py '{"text":".r 2d6","is_group":true}'` —— 喂一条虚构消息，
  看命中/拦截/回复，状态写到临时目录、不碰生产（面板不提供在线干跑，也没有在线改代码）；
- 日志：`state/bot.log`（面板「运维」页可看）。

## 10. 测试

最小测试（不联网、不碰生产状态）：

```python
import asyncio, sys
from pathlib import Path
sys.path.insert(0, "/path/to/klee-studio")
from bot.framework import FeatureManager, FeatureContext, _Command

class Ad:
    owner_id = "1001"; feature_prefix = "."; features_dir = "bot/features"
    active_character = "klee"; active_character_info = {"id": "klee", "name": "可莉"}
    def _chat_id(self, g, gid, uid): return f"private:{uid}"
    async def send(self, *a, **k): ...
    async def send_image_file(self, *a, **k): ...

mgr = FeatureManager(Ad()); mgr.load()
assert "你的命令" in mgr.commands
```

完整套件在 `testkit/`（`test_live_plugins.py` 会盘点所有插件：加载错误、命令齐全、help 分组、空插件）。
新增插件后请把它挂进 `help.py` 的 `_GROUPS`，否则会掉进「🧩 其他」堆（有测试守着）。

## 11. 检查清单

- [ ] 有 `NAME` / `DESCRIPTION` / `VERSION` 和 `register(api)`
- [ ] 命令有 `help`，并挂进 `help.py` 的分组表
- [ ] 需要配置就声明 `CONFIG_SCHEMA`，默认值可用
- [ ] 角色专属就写 `CHARACTERS`
- [ ] 状态走 `KLEE_STATE_DIR`，写盘原子
- [ ] 网络/写盘失败有降级，不吞掉整条消息
- [ ] 不阻塞事件循环、不起常驻线程
- [ ] `testkit/test_live_plugins.py` + 自己的单测通过


## 插件包（ZIP 安装）

面板「插件 → 📦 安装插件包」上传一个 zip：

```
demo.py            # 插件文件（小写字母开头，字母/数字/_，≤41 字符）
```

- 可放根目录，也可统一放一个顶层文件夹；不要放子目录、不要 `_xxx.py`。
- 单包 ≤2MB、≤20 个文件；只接受 `.py`（可附带 `plugin.json` / `README.md`，不安装）。
- 安装前会 `compile()` 校验语法；`CHARACTERS` 里的角色 id 必须是已存在的角色。
- 同名插件默认拒绝，确认后带 `?overwrite=1` 覆盖；**覆盖即替换，不留旧版**（本项目没有回档）。
- 安装后 touch `.reload`，8 秒内热重载；面板可开关、可改配置，不提供在线改代码。
