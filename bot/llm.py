"""模型配置 + OpenAI 兼容聊天客户端（纯标准库）。

- 模型配置存 `state/models.json`（面板可改）：providers[] + active。
- API key 只从环境变量读（key_env），不落盘；`.env` 由 server 启动时加载。
- 兼容推理模型：content 为空时回退 reasoning_content（cn:deepseek-v4.1 系列的老坑）。
- `complete_raw()` 返回工具调用（agent 用）；`chat()` 是它的纯文本封装。
"""
from __future__ import annotations

import copy
import json
import logging
import os
import re
import socket
import threading
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

log = logging.getLogger("klee.bot.llm")
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
_SAVE_LOCK = threading.Lock()

DEFAULT_PROVIDERS = [
    {
        "id": "workbuddy",
        "name": "WorkBuddy 中转",
        "api": "http://127.0.0.1:7863/v1",
        "key_env": "WORKBUDDY_API_KEY",
        "models": [
            "cn:deepseek-v4.1-flash", "cn:deepseek-v4-pro", "cn:deepseek-v4-flash",
            "cn:kimi-k3-1", "cn:kimi-k2.8-preview", "cn:glm-5.3-flash", "cn:glm-5.1",
            "cn:glm-5.3", "cn:hy3-x", "cn:minimax-m3", "cn:auto",
        ],
    },
    {
        "id": "mimo",
        "name": "MiMo 本地（8080）",
        "api": "http://127.0.0.1:8080/v1",
        "key_env": "MIMO_API_KEY",
        "models": ["mimo-v2.5-tts"],
    },
]


# ---------- 供应商模型发现 ----------
_LOCAL_HOST_RE = re.compile(r"^(?:localhost|127\.|0\.0\.0\.0|::1|10\.|192\.168\.|172\.(?:1[6-9]|2\d|3[01])\.)")


def _opener(url: str):
    """本机/内网地址绕开 HTTP(S)_PROXY（否则代理会把 127.0.0.1 拦掉）。"""
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    if _LOCAL_HOST_RE.match(host) or host in ("localhost", ""):
        return urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return urllib.request.build_opener()


def _models_from_payload(obj) -> list[str]:
    """兼容几种常见 /models 返回：OpenAI、Gemini 风格、Ollama。"""
    out: list[str] = []

    def take(item):
        if isinstance(item, str):
            out.append(item)
        elif isinstance(item, dict):
            for k in ("id", "name", "model", "title"):
                v = item.get(k)
                if isinstance(v, str) and v.strip():
                    out.append(v.strip())
                    return

    if isinstance(obj, list):
        for it in obj:
            take(it)
    elif isinstance(obj, dict):
        for key in ("data", "models", "result"):
            v = obj.get(key)
            if isinstance(v, list):
                for it in v:
                    take(it)
            elif isinstance(v, dict):
                for it in (v.get("data") or v.get("models") or []):
                    take(it)
    return out


def discover_models(api: str, key: str = "", timeout: float = 15) -> dict:
    """打 {api}/models 拿模型清单。

    返回 {ok, models, url, error}；`error` 里带 HTTP 状态码和上游原话，
    这样面板能直接告诉用户是地址写错、还是 key 不对。
    """
    api = str(api or "").strip().rstrip("/")
    if not api.startswith(("http://", "https://")):
        return {"ok": False, "models": [], "error": "地址要以 http:// 或 https:// 开头"}
    urls = [f"{api}/models"]
    if not re.search(r"/v\d+$", api):
        urls.append(f"{api}/v1/models")
        urls.append(f"{api}/api/tags")          # Ollama
    headers = {"Accept": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    errs: list[str] = []
    for url in urls:
        req = urllib.request.Request(url, headers=headers)
        try:
            with _opener(url).open(req, timeout=timeout) as r:
                body = r.read(4 * 1024 * 1024).decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            raw = ""
            try:
                raw = e.read(600).decode("utf-8", "replace").strip().replace("\n", " ")
            except Exception:                                   # noqa: BLE001
                pass
            errs.append(f"{e.code} {e.reason}" + (f"：{raw}" if raw else ""))
            if e.code in (401, 403):
                return {"ok": False, "models": [], "url": url,
                        "error": f"{e.code} 鉴权失败（key 不对或没设置）：{raw}"[:400]}
            continue
        except urllib.error.URLError as e:
            r = e.reason
            if isinstance(r, ConnectionRefusedError):
                errs.append(f"连不上 {url}（端口没开或地址写错）")
            elif isinstance(r, (TimeoutError, socket.timeout)):
                errs.append(f"{url} 超时（{timeout:.0f}s 没响应）")
            else:
                errs.append(f"{url} 打不开：{r}")
            continue
        except Exception as e:                                  # noqa: BLE001
            errs.append(f"{type(e).__name__}: {e}")
            continue
        try:
            obj = json.loads(body)
        except Exception:                                       # noqa: BLE001
            errs.append(f"{url} 返回的不是 JSON（前 80 字：{body[:80]!r}）")
            continue
        models = sorted({m for m in _models_from_payload(obj) if m})
        if models:
            return {"ok": True, "models": models, "url": url, "error": ""}
        errs.append(f"{url} 里没有模型清单")
    # 404 只是「这个路径没有」，别盖掉「返回的不是 JSON / 401」这种更有用的信息
    useful = next((e for e in errs if not e.startswith("404")), errs[0] if errs else "")
    return {"ok": False, "models": [], "error": useful or "拉不到模型清单"}


class ModelStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.data = self._load()

    def _load(self) -> dict:
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
            provs = d.get("providers") if isinstance(d, dict) else None
            if isinstance(provs, list):
                provs = [p for p in provs if isinstance(p, dict) and p.get("id")
                         and isinstance(p.get("models"), list) and p["models"]]
                if not provs:
                    raise ValueError("providers 为空或结构不合法")
                d["providers"] = provs
                act = d.get("active")
                if not (isinstance(act, dict) and act.get("provider")):
                    d["active"] = {"provider": provs[0]["id"],
                                   "model": str(provs[0]["models"][0])}
                return d
        except (OSError, ValueError) as e:
            log.warning("读取模型配置失败，使用默认配置：%s", e)
        return {"active": {"provider": DEFAULT_PROVIDERS[0]["id"],
                           "model": DEFAULT_PROVIDERS[0]["models"][0]},
                "providers": copy.deepcopy(DEFAULT_PROVIDERS)}

    def save(self) -> None:
        with _SAVE_LOCK:
            self._save_locked()

    def _save_locked(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(f"{self.path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        try:
            tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
            os.replace(tmp, self.path)
        finally:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass

    # ---------- 查询 ----------
    def providers(self) -> list:
        return [p for p in self.data.get("providers", []) if isinstance(p, dict)]

    def get_provider(self, pid: str) -> dict | None:
        return next((p for p in self.providers() if p.get("id") == pid), None)

    def active(self) -> dict:
        a = self.data.get("active") or {}
        p = self.get_provider(a.get("provider"))
        if not p:                                  # provider 被删/改名 → 连 model 一起回退
            p = (self.providers() or [{}])[0]
        models = p.get("models") if isinstance(p.get("models"), list) else []
        model = str(a.get("model") or "")
        if model not in models:                    # 模型被删了 → 用该 provider 第一个
            model = str(models[0]) if models else ""
        return {"provider": p.get("id", ""), "model": model}

    def set_active(self, provider: str, model: str) -> None:
        with _SAVE_LOCK:
            if not self.get_provider(provider):
                raise ValueError(f"未知 provider: {provider}")
            self.data["active"] = {"provider": provider, "model": model}
            self._save_locked()

    # ---------- vision（看图用的模型，和聊天模型可以分开） ----------
    def vision(self) -> dict:
        """`models.json` 的 `vision` 段：{provider, model, configured}。

        provider 可空（=跟聊天同一个）；**model 为空就是「没配 vision 模型」**，
        调用方据此走友好降级，而不是拿纯文本模型硬塞图片。
        """
        v = self.data.get("vision")
        v = v if isinstance(v, dict) else {}
        act = self.active()
        model = str(v.get("model") or "").strip()
        provider = str(v.get("provider") or "").strip() or act["provider"]
        p = self.get_provider(provider)
        if not model or not p:
            return {"provider": provider, "model": "", "configured": False}
        models = p.get("models") if isinstance(p.get("models"), list) else []
        if model not in models:                    # provider 换了/模型下线：宁可「未配置」也不瞎发
            return {"provider": p.get("id", provider), "model": "", "configured": False}
        return {"provider": p.get("id", provider), "model": model, "configured": True}

    def set_vision(self, provider: str = "", model: str = "") -> None:
        """设置看图模型；`model` 传空 = 关闭图片理解。provider 空 = 跟聊天同一个。"""
        provider = str(provider or "").strip()
        model = str(model or "").strip()
        with _SAVE_LOCK:
            if model and provider and not self.get_provider(provider):
                raise ValueError(f"未知 provider: {provider}")
            self.data["vision"] = {"provider": provider, "model": model}
            self._save_locked()

    # ---------- agent（工具循环用的模型，可以和聊天模型分开） ----------
    def agent(self) -> dict:
        """`models.json` 的 `agent` 段：{provider, model}。空 = 跟聊天同一个。"""
        a = self.data.get("agent")
        a = a if isinstance(a, dict) else {}
        act = self.active()
        provider = str(a.get("provider") or "").strip()
        model = str(a.get("model") or "").strip()
        if not model:
            return dict(act)
        p = self.get_provider(provider or act["provider"])
        if not p:
            return dict(act)
        models = p.get("models") if isinstance(p.get("models"), list) else []
        if model not in models:
            if not models:
                return dict(act)
            model = str(models[0])
        return {"provider": p.get("id", act["provider"]), "model": model}

    def set_agent(self, provider: str = "", model: str = "") -> None:
        provider = str(provider or "").strip()
        model = str(model or "").strip()
        with _SAVE_LOCK:
            if model and provider and not self.get_provider(provider):
                raise ValueError(f"未知 provider: {provider}")
            self.data["agent"] = {"provider": provider, "model": model}
            self._save_locked()

    def upsert_provider(self, prov: dict) -> None:
        pid = str(prov.get("id") or "").strip()
        if not pid:
            raise ValueError("provider 缺少 id")
        if not _ID_RE.match(pid):
            raise ValueError("provider id 只能用字母/数字/下划线/短横线（≤32 字）")
        api = str(prov.get("api") or "").strip()
        if not api.startswith(("http://", "https://")):
            raise ValueError(f"provider {pid} 的 api 必须是 http(s) 地址")
        key_env = str(prov.get("key_env") or "").strip()
        if key_env and not _ID_RE.match(key_env):
            raise ValueError("key_env 只能是环境变量名（字母/数字/下划线）")
        prov = {**prov, "id": pid, "api": api.rstrip("/"),
                "name": str(prov.get("name") or pid)[:60], "key_env": key_env}
        models = prov.get("models")
        if not isinstance(models, list) or not [m for m in models if str(m).strip()]:
            raise ValueError(f"provider {pid} 的 models 必须是非空列表")
        prov["models"] = [str(m).strip() for m in models if str(m).strip()]
        with _SAVE_LOCK:
            raw = self.data.setdefault("providers", [])
            for i, p in enumerate(raw):
                if isinstance(p, dict) and p.get("id") == pid:
                    raw[i] = prov
                    break
            else:
                raw.append(prov)
            self._save_locked()

    def delete_provider(self, pid: str) -> None:
        pid = str(pid or "").strip()
        with _SAVE_LOCK:
            provs = [p for p in self.data.get("providers", []) if isinstance(p, dict)]
            if len(provs) <= 1:
                raise ValueError("最后一个 provider 不能删")
            if not any(p.get("id") == pid for p in provs):
                raise ValueError(f"没有 provider {pid}")
            for key in ("active", "vision", "agent"):
                sec = self.data.get(key)
                if isinstance(sec, dict) and sec.get("provider") == pid:
                    raise ValueError(f"provider {pid} 正在被 {key} 使用，先换掉再删")
            self.data["providers"] = [p for p in provs if p.get("id") != pid]
            self._save_locked()

    # ---------- 调用 ----------
    def complete_raw(self, messages: list, *, model: str | None = None,
                     provider: str | None = None, timeout: float = 90,
                     temperature: float = 0.85, max_tokens: int = 800,
                     tools: list | None = None, tool_choice: str | None = None,
                     extra_body: dict | None = None) -> dict:
        """调一次 chat/completions，返回 {text, tool_calls, finish_reason}。

        `tool_calls` 已归一化成 [{id, name, arguments}]；没有工具调用就是 []。
        """
        act = self.active()
        prov = self.get_provider(provider) if provider else self.get_provider(act["provider"])
        if not prov:
            raise RuntimeError(f"没有可用的模型 provider（{provider or act['provider']}，去面板加一个）")
        if not model and prov.get("id") == act["provider"]:
            model = act["model"]
        if not model:
            models = prov.get("models") if isinstance(prov.get("models"), list) else []
            if not models:
                raise RuntimeError(f"provider {prov.get('id')} 没有可用模型")
            model = str(models[0])
        api = str(prov.get("api") or "").rstrip("/")
        key = os.environ.get(str(prov.get("key_env") or ""), "")
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice or "auto"
        if extra_body:                       # reasoning_effort 等可选参数（不认识的模型会忽略）
            for k, v in extra_body.items():
                if v is not None:
                    payload[k] = v
        req = urllib.request.Request(
            f"{api}/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     **({"Authorization": f"Bearer {key}"} if key else {})},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:      # noqa: S310
                raw = r.read().decode("utf-8", "replace") or "{}"
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", "replace")[:200]
            except (OSError, AttributeError):
                pass
            raise RuntimeError(f"模型接口 HTTP {e.code}: {body}") from e
        except (OSError, urllib.error.URLError) as e:
            raise RuntimeError(f"模型接口请求失败：{type(e).__name__}: {e}") from e
        try:
            data = json.loads(raw)
        except ValueError as e:
            raise RuntimeError(f"模型返回非 JSON：{raw[:120]}") from e
        if not isinstance(data, dict):
            raise RuntimeError(f"模型返回格式异常：{str(data)[:160]}")
        choices = data.get("choices") or []
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise RuntimeError(f"模型返回异常：{str(data)[:200]}")
        choice = choices[0]
        msg = choice.get("message")
        if not isinstance(msg, dict):
            msg = {}
        text = str(msg.get("content") or "").strip()
        if not text:                                   # 推理模型：正文可能在 reasoning_content
            text = str(msg.get("reasoning_content") or msg.get("reasoning") or "").strip()
        calls = []
        for c in (msg.get("tool_calls") or []):
            if not isinstance(c, dict):
                continue
            fn = c.get("function") or {}
            name = str(fn.get("name") or "").strip()
            if not name:
                continue
            calls.append({"id": str(c.get("id") or f"call_{len(calls)}"),
                          "name": name,
                          "arguments": fn.get("arguments") if isinstance(fn.get("arguments"), str)
                          else json.dumps(fn.get("arguments") or {}, ensure_ascii=False)})
        return {"text": text, "tool_calls": calls,
                "finish_reason": str(choice.get("finish_reason") or "")}

    def chat(self, messages: list, *, model: str | None = None, provider: str | None = None,
             timeout: float = 90,
             temperature: float = 0.85, max_tokens: int = 800,
             reasoning_effort: str | None = None) -> str:
        extra = {"reasoning_effort": reasoning_effort} if reasoning_effort else None
        res = self.complete_raw(messages, model=model, provider=provider, timeout=timeout,
                                temperature=temperature, max_tokens=max_tokens,
                                extra_body=extra)
        text = str(res.get("text") or "").strip()
        if not text:
            raise RuntimeError("模型返回空内容")
        return text
