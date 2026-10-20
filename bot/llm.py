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
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

log = logging.getLogger("klee.bot.llm")

# ---------------------------------------------------------------- 用量记录
# 模型返回里的 `usage` 是最准的（网关给什么就是什么）；网关不给时按字符数估一个，
# 并打上 `est=True` —— 看板上「估算」和「实测」必须能分开，否则数字没法对账。
_USAGE_HOOK = None


def _require_text(res: dict) -> None:
    """HTTP 200 但正文空、也没有工具调用 = 没拿到答案（docstring 一直这么写）。"""
    if not str(res.get("text") or "").strip() and not res.get("tool_calls"):
        raise RuntimeError("模型返回空内容（正文和工具调用都是空的）")


def set_usage_hook(fn) -> None:
    """注册一个回调：每次模型调用结束都会被叫一次（失败不影响主流程）。"""
    global _USAGE_HOOK
    _USAGE_HOOK = fn


def _emit_usage(provider: str, model: str, usage: dict, ms: float, kind: str,
                fails: int = 0, character: str = "") -> None:
    hook = _USAGE_HOOK
    if hook is None:
        return
    try:
        hook({"ts": time.time(), "provider": provider, "model": model,
              # 哪个角色花的钱：看板要能按角色拆开（以前只有模型维度）
              "character": str(character or ""),
              "prompt_tokens": int(usage.get("prompt_tokens") or 0),
              "completion_tokens": int(usage.get("completion_tokens") or 0),
              "total_tokens": int(usage.get("total_tokens") or 0),
              "est": bool(usage.get("est")), "ms": round(float(ms), 1), "kind": kind,
              # 这一轮之前失败了几次（>0 = 走了备用模型）。只有成功那一跳才记一行，
              # 失败不记 —— 否则「请求次数」会把重试也算进去，对不上账。
              "fails": int(fails or 0)})
    except Exception as e:                                       # noqa: BLE001
        log.info("用量回调出错（已忽略）：%s", e)


def _estimate(messages: list, text: str) -> dict:
    """按字符估 token：中文约 1 字 ≈ 1 token，英文约 4 字符 ≈ 1 token。

    不追求精确 —— 只为了让「网关不返回 usage」时看板还有个量级。
    """
    def size(s: str) -> float:
        cjk = sum(1 for ch in str(s) if "\u4e00" <= ch <= "\u9fff")
        return cjk + (len(str(s)) - cjk) / 4.0
    pin = sum(size(str(m.get("content") or "")) + 4 for m in (messages or []))
    pout = size(text)
    return {"prompt_tokens": int(pin), "completion_tokens": int(pout),
            "total_tokens": int(pin + pout), "est": True}
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
# models.json 的结构版本。2 = 「agent/vision 的 model 为空就是『跟聊天同一个』」，
# 且面板读到的必须是**原始配置**而不是解析后的结果（见 ModelStore._load 的迁移）。
SCHEMA = 2
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


def _open_url(req, timeout: float):
    """打一次 HTTP（本机/内网绕开代理）。

    单独抽出来是为了让测试能替换：测试里以前 patch `urllib.request.urlopen`，
    而这里必须用 opener 才能绕代理 —— 不抽这一层，测试的桩就失效，
    单测会真的去请求线上模型（已经发生过一次）。
    """
    return _opener(req.full_url).open(req, timeout=timeout)


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
                # `v` 也可能是「模型名 → 元数据」的映射，直接迭代会拿到一堆键名
                inner = v.get("data")
                if not isinstance(inner, list):
                    inner = v.get("models")
                if isinstance(inner, list):
                    for it in inner:
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


def _read_sse(resp, on_delta=None) -> dict | None:
    """读 OpenAI 兼容的 SSE 流，返回和 complete_raw 一样的结果。

    解析不了就返回 None，让调用方退回非流式（宁可慢，也不能把话弄丢）。
    """
    content: list[str] = []
    reason: list[str] = []
    usage: dict = {}
    calls: dict[int, dict] = {}
    finish = ""
    saw_any = False
    try:
        for raw_line in resp:
            line = raw_line.decode("utf-8", "replace").strip()
            if not line or line.startswith(":"):
                continue                     # 心跳注释
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload in ("[DONE]", "DONE"):
                break
            try:
                obj = json.loads(payload)
            except ValueError:
                continue
            saw_any = True
            # 有的网关不在 HTTP 层报错，而是在流里塞一条 {"error": {...}}（配额/超长/过载）。
            # 当成普通 chunk 跳过的话，最后只剩「模型返回空内容」，真正的原因全丢了。
            # 用了 stream_options.include_usage 的网关会在最后单独发一条带 usage 的包
            u = obj.get("usage")
            if isinstance(u, dict) and u.get("total_tokens") is not None:
                usage = {"prompt_tokens": int(u.get("prompt_tokens") or 0),
                         "completion_tokens": int(u.get("completion_tokens") or 0),
                         "total_tokens": int(u.get("total_tokens") or 0)}
            if obj.get("error"):
                err = obj.get("error")
                msg = err.get("message") if isinstance(err, dict) else str(err)
                log.warning("SSE 流里报错：%s", msg)
                return None                  # 退回非流式，让 HTTP 层把原因抛出来
            choices = obj.get("choices") or []
            if not isinstance(choices, list) or not choices:
                continue
            ch = choices[0] or {}
            if ch.get("finish_reason"):
                finish = str(ch["finish_reason"])
            delta = ch.get("delta") or {}
            if not isinstance(delta, dict):
                delta = {}
            piece = delta.get("content")
            if isinstance(piece, str) and piece:
                content.append(piece)
            think = delta.get("reasoning_content") or delta.get("reasoning")
            if isinstance(think, str) and think:
                reason.append(think)
            for tc in (delta.get("tool_calls") or []):
                if not isinstance(tc, dict):
                    continue
                idx = int(tc.get("index") or 0)
                slot = calls.setdefault(idx, {"id": "", "name": "", "args": ""})
                if tc.get("id"):
                    slot["id"] = str(tc["id"])
                fn = tc.get("function") or {}
                if isinstance(fn, dict):
                    if fn.get("name"):
                        slot["name"] = str(fn["name"])
                    arg = fn.get("arguments")
                    if isinstance(arg, str):
                        slot["args"] += arg
            if on_delta and content:
                try:
                    on_delta("".join(content))
                except Exception:            # noqa: BLE001  回调坏了不能影响取字
                    pass
    except Exception as e:                   # noqa: BLE001
        # 中途断流一律退回非流式。以前这里把「已经收到的半截」当成功返回：
        # 用户会看到一句被截断的回复，而 agent 那边半截的 tool_call 参数
        # 会被 _parse_args 兜成 {} —— 工具拿着空参数照样跑，错得更隐蔽。
        log.warning("SSE 流读取失败（已收到 %d 字），退回非流式：%s",
                    sum(len(x) for x in content), e)
        return None
    text = "".join(content).strip() or "".join(reason).strip()
    if not text and not calls:
        return None if not saw_any else {"text": "", "tool_calls": [], "finish_reason": finish}
    out = []
    for idx in sorted(calls):
        slot = calls[idx]
        if not slot["name"]:
            continue
        out.append({"id": slot["id"] or f"call_{len(out)}", "name": slot["name"],
                    "arguments": slot["args"] or "{}"})
    return {"text": text, "tool_calls": out, "finish_reason": finish,
            "usage": usage}


class ModelStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._migrated = False
        self.data = self._load()
        if self._migrated:            # 迁移当场落盘：别等到下一次保存才生效
            try:
                self.save()
            except OSError as e:
                log.warning("迁移后的模型配置写不回去：%s", e)

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
                d = self._migrate(d)
                return d
        except (OSError, ValueError) as e:
            log.warning("读取模型配置失败，使用默认配置：%s", e)
        return {"_schema": SCHEMA,
                "active": {"provider": DEFAULT_PROVIDERS[0]["id"],
                           "model": DEFAULT_PROVIDERS[0]["models"][0]},
                "providers": copy.deepcopy(DEFAULT_PROVIDERS)}

    def _migrate(self, d: dict) -> dict:
        """把老配置里「被面板写死的伪值」清掉（schema < 2 → 2）。

        老面板把**解析后的** agent/vision 模型当成用户选择回填到表单里：
        - `/api/agent` 返回 `agent()`，而 `agent()` 在没配时会返回当前 active
          → 点一次「保存 Agent 设置」就把主模型钉成「工具模型」；
        - 「看图模型」下拉在未配置时没有 selected 项，浏览器默认选第一项
          （= 模型清单第一个，通常就是默认模型）→ 点一次「保存看图模型」就把它
          写成看图模型，而那多半是个纯文本模型。
        结果就是「不管把主模型改成什么，聊天永远回到那一个」。

        这两条伪值的特征都一样：**等于该 provider 模型清单的第一项**。
        真·用户选择（比如专挑 `glm-5.3`）不会被误删。
        """
        if d.get("_schema") == SCHEMA:
            return d
        first: dict = {}
        for p in d.get("providers") or []:
            ms = p.get("models") if isinstance(p.get("models"), list) else []
            if ms:
                first[str(p.get("id"))] = str(ms[0])
        cleared = []
        for sec in ("agent", "vision"):
            v = d.get(sec)
            if not isinstance(v, dict):
                continue
            model = str(v.get("model") or "").strip()
            provider = str(v.get("provider") or "").strip()
            if not model:
                continue
            if model == first.get(provider):
                v["model"] = ""
                cleared.append(f"{sec}={model}")
        if cleared:
            log.warning("模型配置迁移（schema<2）：清掉面板回填写死的 %s —— "
                        "这两项现在「空 = 跟聊天同一个模型」", "、".join(cleared))
        d["_schema"] = SCHEMA
        self._migrated = True
        return d

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

    def _ensure_model(self, provider_id: str, model: str) -> None:
        """把选中的模型补进 provider 的 models 列表。

        `models` 是「面板下拉里显示哪些」，不是「上游支持哪些」。之前它被当成
        白名单用：vision/agent 选了一个刚 discover 到、但没勾进清单的模型，
        保存后立刻被判成「未配置」—— 用户看到的是「明明配了却变回没配」。
        选中即登记，别再让配置自己消失。
        """
        if not model:
            return
        for p in self.providers():
            if p.get("id") != provider_id:
                continue
            models = p.get("models") if isinstance(p.get("models"), list) else []
            if model not in models:
                p["models"] = [*models, model]
                self._save_locked()
            return

    def set_active(self, provider: str, model: str) -> None:
        with _SAVE_LOCK:
            if not self.get_provider(provider):
                raise ValueError(f"未知 provider: {provider}")
            # 选中即登记：否则 active() 会拿「不在 models 清单」当失效，静默回退到
            # 清单第一项 —— 面板显示保存成功，实际换了个模型。
            self._ensure_model(provider, model)
            self.data["active"] = {"provider": provider, "model": model}
            self._save_locked()

    # ---------- vision（看图用的模型，和聊天模型可以分开） ----------
    def vision_raw(self) -> dict:
        """`models.json` 里 vision 段的**原样**（`model` 为空 = 没配 = 跟聊天同一个）。

        面板必须读这个：`vision()` 是「解析后的可用配置」，拿它回填表单就会
        把「没配」显示成「配了某个模型」，用户一保存就把默认模型钉成看图模型。
        """
        v = self.data.get("vision")
        v = v if isinstance(v, dict) else {}
        return {"provider": str(v.get("provider") or ""), "model": str(v.get("model") or "")}

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
        return {"provider": p.get("id", provider), "model": model, "configured": True,
                # 不在下拉清单里 ≠ 不能用：上游可能只是没勾选。调用失败会走友好降级，
                # 但别在状态里假装「没配过」。
                "not_in_list": bool(models) and model not in models}

    def set_vision(self, provider: str = "", model: str = "") -> None:
        """设置看图模型；`model` 传空 = 关闭图片理解。provider 空 = 跟聊天同一个。"""
        provider = str(provider or "").strip()
        model = str(model or "").strip()
        with _SAVE_LOCK:
            if model and provider and not self.get_provider(provider):
                raise ValueError(f"未知 provider: {provider}")
            self.data["vision"] = {"provider": provider, "model": model}
            self._ensure_model(provider or self.active().get("provider", ""), model)
            self._save_locked()

    # ---------- agent（工具循环用的模型，可以和聊天模型分开） ----------
    def agent_raw(self) -> dict:
        """`models.json` 里 agent 段的原样（`model` 为空 = 跟聊天同一个）。

        同 `vision_raw()`：表单回填必须用这个，否则「没配」会被当成
        「配了当前主模型」，保存一次就把主模型钉死 —— 之后再改主模型也不生效。
        """
        a = self.data.get("agent")
        a = a if isinstance(a, dict) else {}
        return {"provider": str(a.get("provider") or ""), "model": str(a.get("model") or "")}

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
        return {"provider": p.get("id", act["provider"]), "model": model,
                "not_in_list": bool(models) and model not in models}

    def set_agent(self, provider: str = "", model: str = "") -> None:
        provider = str(provider or "").strip()
        model = str(model or "").strip()
        with _SAVE_LOCK:
            if model and provider and not self.get_provider(provider):
                raise ValueError(f"未知 provider: {provider}")
            self.data["agent"] = {"provider": provider, "model": model}
            self._ensure_model(provider or self.active().get("provider", ""), model)
            self._save_locked()

    # ---------- 备用模型（主模型挂了自动换一个） ----------
    def fallbacks(self) -> list:
        """`models.json` 的 `fallbacks` 段：`[{provider, model}]`，按顺序试。

        只在**主模型彻底没拿到答案**时才用（见 `complete_raw`）。已经被删掉的
        provider、空模型、以及和当前 active 完全一样的条目一律跳过 —— 否则会
        出现「主模型超时 → 备用还是它 → 再超时一遍」。
        """
        raw = self.data.get("fallbacks")
        act = self.active()
        seen = {(act["provider"], act["model"])}
        out = []
        for i in raw if isinstance(raw, list) else []:
            if not isinstance(i, dict):
                continue
            p = self.get_provider(str(i.get("provider") or ""))
            if not p:
                continue
            m = str(i.get("model") or "").strip()
            if not m:
                models = p.get("models") if isinstance(p.get("models"), list) else []
                m = str(models[0]) if models else ""
            if not m:
                continue
            k = (str(p.get("id")), m)
            if k in seen:
                continue
            seen.add(k)
            out.append({"provider": k[0], "model": m})
        return out[:4]

    def set_fallbacks(self, items) -> list:
        """整段覆盖备用模型清单。空列表 = 只用主模型。"""
        clean = []
        for i in items if isinstance(items, list) else []:
            if not isinstance(i, dict):
                raise ValueError("备用模型每项必须是 {provider, model}")
            pid = str(i.get("provider") or "").strip()
            m = str(i.get("model") or "").strip()
            p = self.get_provider(pid)
            if not p:
                raise ValueError(f"未知 provider: {pid or '（空）'}")
            if not m:
                models = p.get("models") if isinstance(p.get("models"), list) else []
                m = str(models[0]) if models else ""
            if not m:
                raise ValueError(f"provider {pid} 没有可用模型")
            clean.append({"provider": pid, "model": m})
        if len(clean) > 4:
            raise ValueError("备用模型最多 4 个（再多也只是排队等超时）")
        # 和主模型一模一样的「备用」没有任何意义，写进去也会被 fallbacks() 过滤掉
        # —— 与其静默消失，不如当场说清楚。
        act = self.active()
        for c in clean:
            if (c["provider"], c["model"]) == (act["provider"], act["model"]):
                raise ValueError("这条就是当前主模型，起不到备用作用")
        with _SAVE_LOCK:
            self.data["fallbacks"] = clean
            for c in clean:
                self._ensure_model(c["provider"], c["model"])
            self._save_locked()
        return clean

    # ---------- 角色专属模型 ----------
    def characters(self) -> dict:
        """每个角色单独指定的模型：`{cid: {provider, model}}`。没配的角色不出现。"""
        raw = self.data.get("characters")
        out = {}
        if isinstance(raw, dict):
            for cid, v in raw.items():
                if not isinstance(v, dict) or not v.get("model"):
                    continue
                p = self.get_provider(str(v.get("provider") or ""))
                if not p:
                    continue
                out[str(cid)] = {"provider": str(p.get("id")), "model": str(v["model"])}
        return out

    def for_character(self, cid: str) -> dict:
        """这个角色这一轮该用哪个模型：**角色专属 > 全局 active**。

        配置坏了（provider 被删 / model 为空）就静默回退到全局 —— 不能让某个
        角色因为一条坏配置而完全不能说话。
        """
        c = self.characters().get(str(cid or ""))
        if c:
            return {**c, "own": True}
        return {**self.active(), "own": False}

    def for_agent(self, cid: str) -> dict:
        """agent 工具循环用哪个：角色专属 > 「工具模型」配置 > 全局 active。"""
        c = self.characters().get(str(cid or ""))
        if c:
            return {**c, "own": True}
        return self.agent()

    def set_char_model(self, cid: str, provider: str = "", model: str = "") -> None:
        """给某个角色指定模型；`model` 传空 = 恢复「跟全局一样」。"""
        cid = str(cid or "").strip()
        if not cid:
            raise ValueError("缺少角色 id")
        provider = str(provider or "").strip()
        model = str(model or "").strip()
        with _SAVE_LOCK:
            raw = self.data.get("characters")
            raw = dict(raw) if isinstance(raw, dict) else {}
            if not model:
                raw.pop(cid, None)
                self.data["characters"] = raw
                self._save_locked()
                return
            p = self.get_provider(provider)
            if not p:
                raise ValueError(f"未知 provider: {provider or '（空）'}")
            raw[cid] = {"provider": str(p.get("id")), "model": model}
            self.data["characters"] = raw
            self._ensure_model(str(p.get("id")), model)
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
            # 正在用的模型不能因为「编辑供应商时没勾选」而从清单里消失：
            # active()/vision()/agent() 都拿「不在清单里」当失效，会静默回退到
            # 清单第一项 —— 表现就是「改完模型，过一会儿又变回默认那个」。
            keep = set()
            for sec in ("active", "vision", "agent"):
                v = self.data.get(sec)
                if isinstance(v, dict) and str(v.get("provider") or "") == pid \
                        and v.get("model"):
                    keep.add(str(v["model"]))
            for v in (self.data.get("characters") or {}).values():
                if isinstance(v, dict) and str(v.get("provider") or "") == pid and v.get("model"):
                    keep.add(str(v["model"]))
            prov["models"] = list(dict.fromkeys([*prov["models"], *sorted(keep)]))
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
                # vision/agent 的「关闭」就是 model 为空，此时并不真的在用这个 provider
                if isinstance(sec, dict) and sec.get("provider") == pid \
                        and (key == "active" or sec.get("model")):
                    raise ValueError(f"provider {pid} 正在被 {key} 使用，先换掉再删")
            self.data["providers"] = [p for p in provs if p.get("id") != pid]
            self._save_locked()

    # ---------- 调用 ----------
    def complete_raw(self, messages: list, *, model: str | None = None,
                     provider: str | None = None, timeout: float = 90,
                     temperature: float = 0.85, max_tokens: int = 800,
                     tools: list | None = None, tool_choice: str | None = None,
                     extra_body: dict | None = None,
                     stream: bool = False, on_delta=None,
                     allow_fallback: bool = True, character: str = "") -> dict:
        """调一次 chat/completions；主模型没拿到答案时按 `fallbacks` 顺序换一个。

        什么时候降级：连接失败 / HTTP 错误 / 返回里没有正文。
        什么时候**不**降级：
        - `allow_fallback=False`（降级链自己再调时，防止套娃）；
        - 流式已经吐过字了 —— 换模型把前半句再念一遍，比直接报错更糟；
        - 报的是「配置错」（没有 provider / 没有模型）：换一个也救不了。
        """
        if not allow_fallback:
            return self._complete_once(
                messages, model=model, provider=provider, timeout=timeout,
                temperature=temperature, max_tokens=max_tokens, tools=tools,
                tool_choice=tool_choice, extra_body=extra_body,
                stream=stream, on_delta=on_delta, _character=character)
        emitted = {"any": False}

        def _delta(txt):
            emitted["any"] = True
            if on_delta is not None:
                on_delta(txt)

        act = self.active()
        first = {"provider": provider or act["provider"], "model": model or act["model"]}
        fails: list = []
        t0 = time.monotonic()
        try:
            res = self._complete_once(
                messages, model=model, provider=provider, timeout=timeout,
                temperature=temperature, max_tokens=max_tokens, tools=tools,
                tool_choice=tool_choice, extra_body=extra_body,
                stream=stream, on_delta=_delta if on_delta else None,
                _character=character)
            # 「HTTP 200 但正文是空的」也算没拿到答案：docstring 一直是这么写的，
            # 但以前这条路径直接 return 了 —— 空回复被当成成功，降级链根本没走，
            # 调用方（chat()）才抛「模型返回空内容」，用户白等一轮。
            _require_text(res)
            res["fails"] = fails
            res["fallback"] = False
            return res
        except RuntimeError as e:
            if emitted["any"]:
                # 已经显示了一半，不能再换个模型从头说
                raise
            fails.append({**first, "error": str(e)[:200]})
            log.warning("模型 %s/%s 失败，准备降级：%s", first["provider"], first["model"], e)
            # 注意：这一整段必须在 except 里。挪到外面之后 `raise` 会变成
            # 「No active exception to reraise」—— 原本的错误信息全丢了，
            # 面板上只看到一句莫名其妙的 RuntimeError。
            chain = self.fallbacks()
            if not chain:
                raise
            last = None
            for fb in chain:
                # 备用模型给短一点的预算：主模型已经耗掉一截，用户等不起第二遍全量超时。
                # 上限必须是「调用方给的 timeout」减去已经花掉的时间 ——
                # 以前写死 max(15.0, …)，agent 传 timeout=5 时备用反而拿到 15 秒，
                # 一步 5 秒的预算会被拖成 5+15×N。
                base = float(timeout or 0) or 90.0
                spent = time.monotonic() - t0
                remain = base - spent
                if remain < 2.0:
                    # 预算已经耗光：再发一次只会继续超时，直接收工把原错误抛出去
                    raise last or RuntimeError("模型调用超时，剩余预算不够再试备用模型")
                left = max(2.0, min(base * 0.6, remain))
                try:
                    res = self._complete_once(
                        messages, model=fb["model"], provider=fb["provider"], timeout=left,
                        temperature=temperature, max_tokens=max_tokens, tools=tools,
                        tool_choice=tool_choice, extra_body=extra_body,
                        stream=stream, on_delta=_delta if on_delta else None,
                        _fails=len(fails), _character=character)
                    # 备用模型也要判空：否则空回复被当成成功，链上剩下的不再尝试，
                    # 最终还是 chat() 抛「模型返回空内容」—— 白等一轮。
                    _require_text(res)
                    res["fails"] = fails
                    res["fallback"] = True
                    log.info("已降级到备用模型 %s/%s（前面失败 %d 次）",
                             fb["provider"], fb["model"], len(fails))
                    return res
                except RuntimeError as e2:
                    if emitted["any"]:
                        raise
                    fails.append({**fb, "error": str(e2)[:200]})
                    last = e2
                    log.warning("备用模型 %s/%s 也失败：%s", fb["provider"], fb["model"], e2)
            raise last or RuntimeError("所有备用模型都失败了")

    def _complete_once(self, messages: list, *, model: str | None = None,
                       provider: str | None = None, timeout: float = 90,
                       temperature: float = 0.85, max_tokens: int = 800,
                       tools: list | None = None, tool_choice: str | None = None,
                       extra_body: dict | None = None,
                       stream: bool = False, on_delta=None, _fails: int = 0,
                       _character: str = "") -> dict:
        """真正的单次调用（不含降级）。

        返回 {text, tool_calls, finish_reason, usage}。

        `tool_calls` 已归一化成 [{id, name, arguments}]；没有工具调用就是 []。

        `stream=True` 时走 SSE：每收到一段增量就回调 `on_delta(已累积的正文)`，
        返回值结构不变 —— 调用方不用区分流式/非流式。
        任何流式解析异常都退回「未流式」的结果（宁可慢一点，也别把话弄丢）。
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
            "stream": bool(stream),
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice or "auto"
        if payload.get("stream"):
            # 让网关在流末尾补一条 usage；不支持的会忽略这个字段（少数会 400，
            # 那种情况下面 _read_sse 退回非流式，仍然能拿到用量）
            payload["stream_options"] = {"include_usage": True}
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
        _t0 = time.monotonic()
        # 一次调用只有一份超时预算：流式先花掉一部分，退回非流式时只能用剩下的。
        # 以前是在 `with` 里直接再发一次、又给满额 timeout —— 等于把 agent 的
        # 整步预算翻倍，最后变成「agent 超时」，明明是能救回来的。
        deadline = time.monotonic() + max(5.0, float(timeout or 0))
        fallback = False
        try:
            # 和 discover_models 用同一个 opener：本机/内网 provider 绕开 HTTP(S)_PROXY。
            # 之前这里用全局 urlopen —— 环境里一有 http_proxy，127.0.0.1:7863 就被
            # 送去代理，报 "via 127.0.0.1" 或干脆超时。
            with _open_url(req, timeout) as r:                           # noqa: S310
                if stream:
                    got = _read_sse(r, on_delta)
                    if got is not None:
                        # 流式：网关给了 usage 就用，没给就按字符估
                        gu = got.get("usage") if isinstance(got.get("usage"), dict) else {}
                        if not gu:
                            gu = _estimate(messages, str(got.get("text") or ""))
                        got["usage"] = gu
                        got["provider"] = str(prov.get("id") or provider or "")
                        got["model"] = str(model or "")
                        _emit_usage(str(prov.get("id") or provider or ""), str(model or ""), gu,
                                    (time.monotonic() - _t0) * 1000.0, "stream", _fails,
                                    _character)
                        return got                # 流式成功：结构与非流式完全一致
                    # 服务端没按 SSE 回（直接吐了整段 JSON / 或者干脆不支持）：
                    # 这一份响应已经被读掉了，**先退出 with** 再重发。
                    log.info("服务端不支持流式，改走非流式")
                    fallback = True
                else:
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
        if fallback:
            left = max(5.0, deadline - time.monotonic())
            return self._complete_once(
                messages, model=model, provider=provider, timeout=left,
                temperature=temperature, max_tokens=max_tokens, tools=tools,
                tool_choice=tool_choice, extra_body=extra_body, stream=False,
                _fails=_fails, _character=_character)
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
            fn = c.get("function")
            if not isinstance(fn, dict):      # 上游偶尔给字符串/None
                continue
            name = str(fn.get("name") or "").strip()
            if not name:
                continue
            calls.append({"id": str(c.get("id") or f"call_{len(calls)}"),
                          "name": name,
                          "arguments": fn.get("arguments") if isinstance(fn.get("arguments"), str)
                          else json.dumps(fn.get("arguments") or {}, ensure_ascii=False)})
        u = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        usage = ({"prompt_tokens": int(u.get("prompt_tokens") or 0),
                  "completion_tokens": int(u.get("completion_tokens") or 0),
                  "total_tokens": int(u.get("total_tokens") or 0)} if u
                 else _estimate(messages, text))
        _emit_usage(str(prov.get("id") or provider or ""), str(model or ""), usage,
                    (time.monotonic() - _t0) * 1000.0, "chat", _fails, _character)
        # 带上实际用了哪个：降级之后调用方（和看板）要能看出「这句是备用模型答的」
        return {"text": text, "tool_calls": calls,
                "finish_reason": str(choice.get("finish_reason") or ""),
                "usage": usage, "provider": str(prov.get("id") or provider or ""),
                "model": str(model or "")}

    def chat(self, messages: list, *, model: str | None = None, provider: str | None = None,
             timeout: float = 90,
             temperature: float = 0.85, max_tokens: int = 800,
             reasoning_effort: str | None = None,
             stream: bool = False, on_delta=None,
             character: str = "") -> str:
        """`character` 只用于用量归账（哪个角色花的钱），不影响生成。"""
        extra = {"reasoning_effort": reasoning_effort} if reasoning_effort else None
        res = self.complete_raw(messages, model=model, provider=provider, timeout=timeout,
                                temperature=temperature, max_tokens=max_tokens,
                                extra_body=extra, stream=stream, on_delta=on_delta,
                                character=character)
        text = str(res.get("text") or "").strip()
        if not text:
            raise RuntimeError("模型返回空内容")
        return text
