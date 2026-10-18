# 多语言：插件用任何语言写，历史用 SQL 查

这个项目的核心是 Python，但**不是只有 Python**。两条扩展线：

| 位置 | 语言 | 为什么 |
|---|---|---|
| 面板前端 | **JavaScript**（单文件，无构建） | 没有构建链，改完刷新就生效 |
| 边缘层 `edge/` | **Go** | 静态资源 + 两个 feed 接口，常驻 19MB |
| 消息索引 `bot/store.py` | **SQL**（SQLite + FTS5） | 20 万条也能毫秒级搜 |
| 外部插件 `bot/features/ext/` | **Node / Rust / C / C++ / Java**（示例全部跑通） | 见下 |
| 主程序、121 个插件 | Python（标准库） | 迭代最快 |

---

## 1. 外部插件协议（任何语言）

插件就是一个**常驻子进程**，用 NDJSON 说话（一行一个 JSON）：

```
宿主 → {"op":"hello","v":1}
插件 → {"ok":true,"name":"uuid","title":"UUID","commands":[{"name":"uuid","help":"…"}]}
宿主 → {"op":"call","id":1,"cmd":"uuid","args":[],"text":"用户打的参数","ctx":{…}}
插件 → {"id":1,"ok":true,"reply":"要发回去的话"}
宿主 → {"op":"bye"}
```

- 首次调用才启动（惰性），之后常驻 —— Node/Java 的启动成本只付一次。
- 崩了自动重启，最多 3 次；之后这个插件标红，面板能看到原因。
- `ctx` 里有：`user_id` / `user_name` / `chat_id` / `is_group` / `character` /
  `platform` / `raw`（整条消息原文）/ `message_id`。
- 超时按插件配置（默认 5s）掐断，不会拖住事件循环 —— 调用跑在线程里。

### 写一个（30 行以内）

规格文件 `bot/features/ext/hello.ext.json`：

```json
{
  "id": "hello",
  "title": "打招呼",
  "description": "最小的外部插件",
  "language": "node",
  "entry": "tools/hello.mjs",
  "timeout": 3
}
```

`tools/hello.mjs`：

```js
import { createInterface } from "node:readline";
const send = o => process.stdout.write(JSON.stringify(o) + "\n");
createInterface({ input: process.stdin }).on("line", line => {
  const m = JSON.parse(line);
  if (m.op === "hello")
    send({ ok: true, name: "hello", title: "打招呼",
           commands: [{ name: "hi", help: ".hi —— 打个招呼" }] });
  else if (m.op === "bye") process.exit(0);
  else if (m.op === "call")
    send({ id: m.id, ok: true, reply: `你好，${m.ctx.user_name}！` });
});
```

放进 `bot/features/config.json` 的 `enabled` 里，8 秒内热重载生效。

**注意**：`text` 是**参数部分**（`.hi 你好` → `你好`），整条消息在 `ctx.raw`。
写 `.json {"a":1}` 这种命令时用 `text`，别用 `raw`。

---

## 2. 五种语言的示例（仓库里都有，跑通了）

本机实测（RK3399 / arm64 / 1GB，`tools/bench_ext.py`，多轮取最好值；Java 波动大所以给了区间）：

| 语言 | 文件 | 命令 | 启动 | 常驻内存 | 一次调用 | 编译 |
|---|---|---|---:|---:|---:|---|
| Rust 1.98 | `tools/textstat.rs` | `.统计` | **5.7 ms** | **1.7 MB** | 2.0 ms | `rustc -O` |
| C (gcc 15) | `tools/crc.c` | `.crc` | **4.3 ms** | **1.4 MB** | 2.6 ms | `gcc -O2` |
| C++ (g++ 15) | `tools/sort.cpp` | `.排序` | 8.1 ms | 3.1 MB | 4.7 ms | `g++ -O2` |
| Node 26 | `tools/uuid.mjs` | `.uuid` / `.json` | 235 ms | 48.5 MB | 8.0 ms | 不需要 |
| Java 21 | `tools/Hash.java` | `.sha` | 212–363 ms | 43 MB | 103–172 ms（JIT 未热） | `javac` |

一次编译全部（缺工具链会跳过并说明）：

```bash
bash bot/features/ext/tools/build.sh
```

实测（本机 RK3399 / 1GB）：

```
node      ok（解释执行，无需编译）
rust      已编译 textstat.bin
c         已编译 crc.bin
cpp       已编译 sort.bin
java      已编译 classes/Hash.class
```

### 为什么这样选

- **Rust / C / C++**：编译成原生二进制，启动 2ms、常驻 2MB 以内。适合「每条消息都跑」
  的小工具（统计、校验、排序）。代价是编译步骤和平台绑定（arm64 编译的二进制
  不能在 x86 上跑）。
- **Node**：零编译、生态最全，起进程 40ms。适合「逻辑比性能重要」的插件。
- **Java**：**不推荐**在这台机器上跑 —— JVM 启动 0.8 秒、常驻 42MB，而
  这机器总共 1GB 还要跑 NapCat 和模型中转。示例保留，是为了证明协议真的是
  语言无关的，而不是「我们只支持轻量语言」。
- **C# / PHP**：本机没装运行时（没有 `dotnet` / `mono` / `php`），所以**没有实测**，
  也就没有放进示例目录 —— 协议本身支持，装了运行时按上面 30 行照抄即可。

---

## 3. SQL：消息索引

`state/messages.db`（SQLite + FTS5）是 `state/*.jsonl` 的**索引**，不是真相：
删掉它会自动重建，JSONL 少一行它也跟着少（按字节偏移增量同步，文件缩水就整份重建）。

为什么值得多一层（20 万条 / 42MB 合成数据，`tools/bench_sql.py`）：

```
场景                     JSONL 扫描      SQLite     加速
关键词搜索（全表）          362.8 ms     12.7 ms     29×
会话最近 50 条             331.3 ms      1.2 ms    276×
按天 × 角色聚合           5143.8 ms    483.7 ms     11×
追加一条                     0.1 ms      8.1 ms    —
```

最后一行是**故意的**：写入还是走 JSONL（0.1ms，纯 append），SQLite 只负责读。
把写入也搬进 SQLite 就为了那 8ms 的事务开销，还要处理锁竞争 —— 不值。

接口：

```
GET /api/search?q=红绳&limit=30&character=klee
    → {ok, count, took_ms, hits:[{ts,session,character,role,text,snippet}]}
GET /api/search/stats?days=14
    → {total, by_character[], by_day[], health:{ok,rows,size}}
```

中文没有空格，FTS5 的 `unicode61` 会把整句当一个 token —— 所以查询是两段式：
先 FTS（带高亮片段），没命中再退回 `LIKE`（单字、整句都能搜）。同一条消息会同时
出现在 `chat.jsonl` 和 `messages.jsonl`，结果按 `(秒级时间, 正文)` 去重。

面板：**看板 → 🔍 历史搜索**。
