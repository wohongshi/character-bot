"""HTTP 层卫生：keep-alive / NO_DELAY / 队列 / 静态缓存。

2026-10-18 实测：Python 侧没开 TCP_NODELAY 时，Go 边缘层复用连接调 API 每个请求多 40ms
（delayed ACK）。这类退化肉眼看不出来，只会在「面板怎么有点慢」里抱怨，所以钉进测试。
"""
import sys
from pathlib import Path

STUDIO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(STUDIO))

import server  # noqa: E402

PASS = FAIL = 0


def check(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name} {detail}")


H = server.Handler
check("HTTP/1.1（keep-alive）", H.protocol_version == "HTTP/1.1", H.protocol_version)
check("TCP_NODELAY 已开（防 40ms delayed ACK）",
      getattr(H, "disable_nagle_algorithm", False) is True,
      str(getattr(H, "disable_nagle_algorithm", None)))
check("空闲连接超时已设", 0 < getattr(H, "timeout", 0) <= 120, str(getattr(H, "timeout", None)))

S = server.Server
check("accept 队列 ≥128（默认 5 会排队）", S.request_queue_size >= 128, str(S.request_queue_size))
check("daemon 线程（重启不被长连接拖住）", S.daemon_threads is True)

check("静态文件缓存函数存在", callable(getattr(server, "_static_get", None)))
check("gzip 结果缓存函数存在", callable(getattr(server, "_gz", None)))
check("概览 2 秒缓存存在", callable(getattr(server, "_overview", None)))
check("角色列表 2 秒缓存存在", callable(getattr(server, "_characters_get", None)))

# 缓存函数行为：第二次调用不再重算
import tempfile  # noqa: E402
tmp = Path(tempfile.mkdtemp()) / "a.txt"
tmp.write_text("hello", encoding="utf-8")
d1, t1 = server._static_get(tmp)
d2, t2 = server._static_get(tmp)
check("静态缓存命中（同一对象/同 ETag）", d1 is d2 and t1 == t2, f"{d1 is d2}")
tmp.write_text("hello world", encoding="utf-8")     # 内容变了（同秒内 mtime_ns 会变）
d3, t3 = server._static_get(tmp)
check("文件变化后缓存失效", d3 != d1 and t3 != t1, f"{d3!r} {t3}")

check("敏感 GET 名单覆盖插件/看板等",
      {"/api/plugins", "/api/dashboard", "/api/logs", "/api/persona"} <= server._SENSITIVE_GET)

print(f"\nPASS {PASS} / {PASS + FAIL}")
sys.exit(1 if FAIL else 0)
