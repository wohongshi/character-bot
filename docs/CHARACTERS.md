# 角色开发规范（Character Bot）

一个角色 = **一个人格（SOUL）+ 一套知识库 + 一个界面主题 + 一组专属插件**。
角色是数据，不是代码：内置五个在 `bot/characters.py`，运行期新增写在 `state/characters.d/<id>.json`。

## 1. 目录与数据

```
bot/character_seeds/<seed>.md        # 初始人格（仓库内置，首次运行播种）
state/characters/<id>/SOUL.md        # 当前人格（面板可编辑，覆盖即生效，不留旧版）
state/characters.d/<id>.json         # 运行期角色定义（ZIP 导入时写入）
bot/ui_themes/<theme>/               # 界面主题（见 THEMES.md）
klee-kb/corpus/<id>/processed/*.jsonl# 角色语料
klee-kb/data/kb_<id>.sqlite3         # 角色索引（klee 用 kb.sqlite3）
```

## 2. 角色定义字段

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `id` | ✅ | 小写字母/数字/`-`/`_`，≤32，字母或数字开头；决定所有路径 |
| `name` | ✅ | 中文名（显示在面板、帮助、聊天） |
| `en` |  | 英文名 |
| `emoji` |  | 头像占位（没有立绘时用它） |
| `tagline` |  | 一句话介绍 |
| `color` / `color2` |  | 主色/浅色（`#rgb` 或 `#rrggbb`），用于头像渐变与 theme-color |
| `kb` |  | 知识库 id（默认同 `id`） |
| `theme` |  | 主题插件 id（默认 `id`，不存在则回退 `klee`） |
| `greeting` |  | 聊天页空状态问候 |
| `hero_note` |  | 空状态补充说明 |
| `chips` |  | 快捷提问，最多 8 条 |
| `seed` |  | 初始 SOUL 文件名（默认 `<id>.md`） |

最小示例：

```json
{
  "id": "furina",
  "name": "芙宁娜",
  "en": "Furina",
  "emoji": "💧",
  "tagline": "枫丹的水神",
  "color": "#3f7fb8",
  "color2": "#dcefff",
  "theme": "atri",
  "greeting": "你好呀，今天的审判……咳，今天的天气如何？",
  "chips": ["讲讲枫丹", "你怎么看正义", "陪我聊会儿"],
  "kb": "furina"
}
```

## 3. 手动添加角色

### 方式 A：面板（推荐）

1. 打开 **🎭 角色 → ＋ 新增角色**；
2. 填 id/名字/主题/快捷语，SOUL 可留空（自动生成最小人格）或直接粘贴；
3. 保存 → 写入 `state/characters.d/<id>.json` + `state/characters/<id>/SOUL.md`；
4. 到「角色」卡上点「切换到这个角色」。

### 方式 B：直接放文件

```bash
cat > state/characters.d/furina.json <<'JSON'
{ "id": "furina", "name": "芙宁娜", "emoji": "💧", "theme": "atri" }
JSON
mkdir -p state/characters/furina
cat > state/characters/furina/SOUL.md <<'MD'
# 芙宁娜
你是芙宁娜，枫丹的水神。说话带一点戏剧腔，但认真听人说话。
MD
```

服务端启动时读取；运行中新增请调用 `POST /api/characters` 或重启 `klee-studio`。

### 方式 C：API

```bash
curl -X POST http://127.0.0.1:8796/api/characters \
  -H 'Content-Type: application/json' -H 'X-Klee-Studio: 1' \
  -d '{"id":"furina","name":"芙宁娜","emoji":"💧","theme":"atri","chips":["讲讲枫丹"]}'
```

校验规则：`id` 非法、缺 `name`、颜色不是 hex、chips 非列表 → 400，不会写坏现有角色。

## 4. 专属知识库

```bash
mkdir -p klee-kb/corpus/furina/processed
# 每行一个 JSON：{"source":"角色设定","type":"fact","title":"…","text":"…"}
cd klee-kb
./py build_index.py --character furina --name 芙宁娜 --corpus corpus/furina
systemctl restart klee-kb
curl -s http://127.0.0.1:8797/healthz | python3 -m json.tool   # characters 里应有 furina
```

- 语料少（<10 块）时也能用，但相关命中分数会低；`kb_threshold` 默认 0.4，可在运维页调。
- 检索请求必须带 `character`；不传回退默认角色（`KB_DEFAULT_CHARACTER=klee`）。
- 角色之间索引独立，不会串味。

## 5. 界面主题（随角色锁定）

一个角色只对应一个主题，**面板不提供主题切换**；主题随角色包一起导入。需要换主题时重新打一个角色包（或替换 `bot/ui_themes/<id>/` 后重启）。

主题目录名必须与角色 id 一致；前端按角色加载对应主题 CSS。

## 6. 角色限定插件

插件模块里声明：

```python
CHARACTERS = ("klee", "hutao")   # 这些角色可用
```

- 当前角色在列表里 → 自动启用，**面板不能关闭**，`config.json` 白名单不生效；
- 不在列表 → 不加载、不注册命令；
- 没有 `CHARACTERS` → 通用插件，按白名单开关；
- 新角色想复用某插件，把它加进该插件的 `CHARACTERS`（或把插件改成通用）。

内置角色的专属插件数量（`CHARACTERS` 里包含该 id 的模块数）：

| 角色 | 数量 | 说明 |
| --- | ---: | --- |
| `klee` | 20 | 原神全家桶 + 炸弹/藏宝/画廊/好感度…… |
| `hutao` | 12 | 打油诗/蝴蝶签/往生堂 + 原神数据 |
| `citlali` | 12 | 烟雾签/观星/小酌 + 原神数据 |
| `cyrene` | 12 | 记忆存档/轮回/回声/迷迷/约定…… |
| `atri` | 12 | 倒计时/自检/潜水/火箭拳/电量/上学…… |

新增插件时记得：命令名与别名**全局唯一**（撞了会被忽略并打日志），
回显用户输入要用 `_plugstore.safe_text()`（会剥掉 `[CQ:…]` 富文本标记），
需要存档用 `db_update()` 而不是 `db_get`+`db_set`（后者并发会丢更新）。

## 7. 切换语义

切换只在面板：`POST /api/characters/switch {"id":"furina"}`。

一次切换会同时：

- 换 SOUL（运行时的 `_persona` 缓存失效）；
- 换知识库（检索带新角色 id）；
- 重载插件（角色限定插件的启用状态按新角色重算）；
- 换界面主题；
- 聊天历史按 `角色 + 会话` 隔离，切回来还记得。

**不要加 QQ 切换命令**：角色切换是管理员操作，不是聊天功能。

## 8. 检查清单

- [ ] `id` 合法、`state/characters.d/<id>.json` 写入成功
- [ ] `state/characters/<id>/SOUL.md` 非空，读起来像这个角色
- [ ] 主题目录与角色 id 一致，切换后背景/字体/气泡确实变了
- [ ] 知识库建了索引，`/healthz` 能看到角色
- [ ] 专属插件在 `CHARACTERS` 里加了这个 id（或明确不需要）
- [ ] 面板「角色」页能切换，聊天回复口吻正确，历史不串
- [ ] 跑 `tests/run.sh` 与 `run_all_tests.sh`


## 角色包（ZIP 导入）

面板「角色 → 📦 导入角色 ZIP」上传一个 zip，一套包含齐：

```
character.json          # 角色元数据（id / name / emoji / 配色 / 问候语 / chips）
SOUL.md                 # 人格全文（≥80 字）
theme/theme.json        # 主题插件（必需 name；tokens 为 CSS 变量白名单）
theme/theme.css         # 可选，复杂样式
plugins/*.py            # 角色限定插件；没写 CHARACTERS 会自动补成 ("<id>",)
kb/*.jsonl              # 知识库语料，每行 {"text": "...", "title": "...", "source": "..."}
```

规则：

- 这些文件可以放在 zip 根目录，也可以统一放在一个顶层文件夹里。
- `id` 必须是小写字母开头、2–30 位字母/数字/`-`/`_`；不能与现有角色重名（导入不覆盖）。
- 一个角色 = 一个主题 + 一套知识库，导入后锁死，面板不再提供主题切换。
- 导入时立刻安装主题与插件、写入 SOUL 和语料；知识库索引在后台构建（约 1–3 分钟）。
- 单包上限 64MB / 240 个文件；只接受 `.json .md .py .css .txt .jsonl .jpg .jpeg .png .webp`。
- 示例包见 `docs/character-pack/`，可直接压缩后上传。
