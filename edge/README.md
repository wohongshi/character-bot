# klee-edge（Go 边缘层）

## 为什么会有这一层

Python 的 `ThreadingHTTPServer` 发一个静态文件的成本太高（读盘 → 算 sha1 → 写 socket），
在 RK3399 上约 2ms/请求；首屏 20+ 个请求在 20 并发下排队，p99 冲到 1.1s。

这一层做三件事：

1. **静态资源常驻内存**：原始字节 + 预压缩结果 + ETag，`mtime/size` 变了才重读；
2. **消息流两个读接口自己算**（`/api/feed`、`/api/feed/sources`）；
3. **其余路径原样反代**给 Python（`:8798`）。

## 构建与部署

```bash
cd edge && GO111MODULE=off go build -o bin/klee-edge .
sudo cp ../../../  # 见下方 systemd
```

生产布局：

```
:8796  klee-edge      (Go, 对外)
  └─→  :8798  klee-studio (Python, 只监听 127.0.0.1)
```

- `klee-studio` 的端口/绑定由 `KLEE_STUDIO_PORT` / `KLEE_STUDIO_BIND` 控制，
  在 `/etc/systemd/system/klee-studio.service.d/edge.conf` 里改成 `8798` / `127.0.0.1`。
- `klee-edge.service`：`-listen :8796 -upstream http://127.0.0.1:8798 -root <repo>`。

## 注意

- 静态目录是 `<root>/static/`，首页是 `<root>/static/index.html`。
- 压缩只针对 `text/*`、javascript、json、svg 且 >1KB；图片本身已是 WebP/JPEG，不再压。
- `DisableCompression: true` —— 让客户端的 `Accept-Encoding` 原样透传给 Python，
  否则 Go 会自己先解压再发，白折腾一次。
- 改 `static/` 里的文件不用重启（每次按 mtime/size 校验）。

## 为什么 /api/feed 放在这一层

这两个接口是面板上唯一「成本随数据量线性增长」的：Python 的 `_read_jsonl` 为了
不 O(n) 已经退化成「只读最后 4MB」。实测 74MB / 20 万行的 `state/messages.jsonl`：

| 接口 | Python | Go | 倍数 |
|---|---:|---:|---:|
| `GET /api/feed?limit=80` | 185 ms | 13 ms | 14× |
| `GET /api/feed?limit=200` | 183 ms | 57 ms | 3.2× |
| `GET /api/feed/sources` | 207 ms | 4.6 ms | 45× |

做法：

- `/api/feed`：从文件末尾按 **64KB → 256KB → 1MB → 4MB → 整份** 逐级放大，
  凑够 `limit` 条就停。常见情况只读 64KB，不碰历史。
- `/api/feed/sources`：**增量聚合** —— 记住上次读到的字节偏移，每次只吃
  新增的行，所以计数是全量的、成本是 O(新增)。
- 顺带修掉一个静默 bug：Python 版只统计尾部 4MB，文件一大
  「（2825 条）」会和真实条数（5 万）对不上；现在是真值。

**任何一步失败都退回反代**（文件不存在 / 解析异常 / 没带鉴权头），
行为与改动前一致 —— 这层是加速器，不是唯一实现。
