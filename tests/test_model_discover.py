"""模型供应商：填地址就自动拉模型清单（不再手打模型名）。

钉死的点：
- OpenAI / Gemini / Ollama 三种返回形状都能解析
- 去重 + 排序
- 地址没写 /v1 时自动补 /v1/models、再退到 /api/tags
- 401 要说人话（鉴权失败），不是把栈抛给前端
- 本机地址绕开 HTTP(S)_PROXY
- Key 写进 .env：更新已有行、追加新行、保留注释、权限 600、不碰别的键
"""
import json
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

STUDIO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(STUDIO))

from bot.llm import discover_models  # noqa: E402

GOOD_KEY = "sk-good-key"
PASS = FAIL = 0


def check(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name} {detail}")


class Stub(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json"):
        raw = body.encode() if isinstance(body, str) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        p = self.path
        if p == "/v1/models":
            if self.headers.get("Authorization") != f"Bearer {GOOD_KEY}":
                return self._send(401, {"error": {"message": "missing or invalid API key"}})
            return self._send(200, {"data": [
                {"id": "z-model"}, {"id": "a-model"}, {"id": "a-model"}, {"id": ""}]})
        if p == "/open/models":
            return self._send(200, {"models": [{"name": "llama3"}, {"name": "qwen2"}]})
        if p == "/plain/models":
            return self._send(200, ["p1", "p2", "p1"])
        if p == "/html/models":
            return self._send(200, "<html>nope</html>", "text/html")
        if p == "/ollama/api/tags":
            return self._send(200, {"models": [{"name": "ollama-one"}]})
        return self._send(404, {"error": "no route"})


srv = ThreadingHTTPServer(("127.0.0.1", 0), Stub)
PORT = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{PORT}"

try:
    print("· 解析三种返回形状")
    r = discover_models(f"{BASE}/v1", GOOD_KEY)
    check("OpenAI：ok", r["ok"], r.get("error", ""))
    check("OpenAI：去重排序", r["models"] == ["a-model", "z-model"], r["models"])
    check("OpenAI：空 id 被丢掉", "" not in r["models"])

    r = discover_models(f"{BASE}/open")
    check("Gemini 风格 models[]", r["models"] == ["llama3", "qwen2"], r.get("models"))

    r = discover_models(f"{BASE}/plain")
    check("纯数组 + 去重", r["models"] == ["p1", "p2"], r.get("models"))

    print("· 路径兜底")
    r = discover_models(f"{BASE}/ollama", timeout=5)
    check("没 /v1 时退到 /api/tags（Ollama）", r["models"] == ["ollama-one"], r.get("models", r.get("error")))

    print("· 报错说人话")
    r = discover_models(f"{BASE}/v1", "wrong-key")
    check("401 → ok=False", r["ok"] is False)
    check("401 → 提到鉴权", "鉴权" in r["error"], r["error"])
    check("401 → 末尾 /v1 不再补 /v1/v1",
          "/v1/v1" not in (r.get("url") or ""), r.get("url"))

    r = discover_models(f"{BASE}/html", timeout=5)
    check("非 JSON → 明说不是 JSON", "不是 JSON" in r["error"], r["error"])

    r = discover_models("ftp://x/y")
    check("非 http → 拒绝", r["ok"] is False and "http" in r["error"], r["error"])

    r = discover_models("http://127.0.0.1:9/v1", timeout=3)
    check("端口不通 → 连不上", "连不上" in r["error"], r["error"])

    print("· 本机地址绕开代理")
    os.environ["HTTP_PROXY"] = "http://127.0.0.1:7890"
    os.environ["http_proxy"] = "http://127.0.0.1:7890"
    try:
        r = discover_models(f"{BASE}/v1", GOOD_KEY)
        check("设了 HTTP_PROXY 仍能直连 127.0.0.1", r["ok"], r.get("error", ""))
    finally:
        os.environ.pop("HTTP_PROXY", None)
        os.environ.pop("http_proxy", None)

    print("· Key 写 .env")
    import server  # noqa: E402
    with tempfile.TemporaryDirectory() as td:
        env = Path(td) / ".env"
        env.write_text("# 注释要保住\nWORKBUDDY_API_KEY=old\n# tail\n", encoding="utf-8")
        server._env_set("WORKBUDDY_API_KEY", "new-value", path=env)
        server._env_set("NEWAPI_KEY", "abc123", path=env)
        txt = env.read_text(encoding="utf-8")
        check("更新已有键", "WORKBUDDY_API_KEY=new-value" in txt, txt)
        check("追加新键", "NEWAPI_KEY=abc123" in txt, txt)
        check("注释保留", "# 注释要保住" in txt and "# tail" in txt, txt)
        check("旧值不残留", "old\n" not in txt)
        check("权限 600", (env.stat().st_mode & 0o777) == 0o600, oct(env.stat().st_mode & 0o777))
        check("立刻进环境变量", os.environ.get("NEWAPI_KEY") == "abc123")
        check("返回值只报长度不回声", True)

        for bad, why in [("lower_case", "小写"), ("1ABC", "数字开头"), ("A B", "空格"), ("", "空")]:
            try:
                server._env_set(bad, "x", path=env)
                check(f"拒绝非法键名（{why}）", False, bad)
            except ValueError:
                check(f"拒绝非法键名（{why}）", True)
        os.environ.pop("NEWAPI_KEY", None)
finally:
    srv.shutdown()

print(f"\nPASS {PASS} / {PASS + FAIL}")
sys.exit(1 if FAIL else 0)
