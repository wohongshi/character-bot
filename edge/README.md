# klee-edge（Go 边缘层）

## 为什么会有这一层

Python 的 `ThreadingHTTPServer` 发一个静态文件的成本太高（读盘 → 算 sha1 → 写 socket），
在 RK3399 上约 2ms/请求；首屏 20+ 个请求在 20 并发下排队，p99 冲到 1.1s。

这一层只做两件事：

1. **静态资源常驻内存**：原始字节 + 预压缩结果 + ETag，`mtime/size` 变了才重读；
2. **其余路径原样反代**给 Python（`:8798`）。

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
