"""可莉画画：``.画画 <提示词>`` —— 让可莉给你画一张（云端文生图，零第三方依赖）。

提供方（按顺序自动挑第一个可用的）：
  1. Cloudflare Workers AI ``@cf/black-forest-labs/flux-1-schnell``（快，~3s，无水印）
  2. 智谱 ``cogview-3-flash``（~8s，有官方水印）

Key 来源：插件 settings 优先，其次进程环境变量，最后回退读 ``/root/.hermes/.env``。
生成图存 ``<onebot_features>/state/images/``，再交给适配器 ``send_image_file`` 发出。
"""

from __future__ import annotations

import asyncio
import base64
import datetime
import hashlib
import json
import logging
import os
import re
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

CONFIG_SCHEMA = {
    "keep_images": {"type": "int", "default": 50, "min": 0, "max": 500,
                    "description": "state/images 只留最近几张（0 = 不限）"},
    "draw_daily_limit": {"type": "int", "default": 0, "min": 0, "max": 1000,
                         "description": "每天最多画几张（0 = 不限）"},
    "timeout": {"type": "int", "default": 90, "min": 10, "max": 300, "description": "生图接口超时（秒）"},
}

NAME = "画画"
DESCRIPTION = "让她画一张图：.画画 <提示词>（云端文生图）"
VERSION = "1.1"

# 角色限定：只有这些角色启用；空 = 通用插件（可在面板关闭）
CHARACTERS = ('klee',)

MAX_PROMPT = 120
IMAGES_SUBDIR = Path("state") / "images"
USAGE_SUBDIR = Path("state") / "draw_usage.json"
ENV_FILE = Path(__file__).resolve().parents[2] / ".env"
CF_MODEL = "@cf/black-forest-labs/flux-1-schnell"
GLM_URL = "https://open.bigmodel.cn/api/paas/v4/images/generations"

KEEP_IMAGES_DEFAULT = 50          # state/images/ 只留最近这么多张（0 = 不限）
WAIT_LINES = ["✍️ 可莉在画啦，等一下下哦～", "🖍️ 唔……让可莉想想怎么画！", "✨ 画笔挥挥——马上就好！"]
FAIL_LINES = "呜……这次的画笔不太听话（{why}），等会儿再让可莉试试嘛"
QUOTA_LINES = "呜……今天的画纸用光光啦（每天只画 {limit} 张哦），明天再来找可莉好不好～"
QUOTA_TAIL = "（今天还能画 {left} 张）"


# ---------- 配置 / Key ----------

_ENV_CACHE: dict | None = None


def _env_file_values() -> dict:
    global _ENV_CACHE
    if _ENV_CACHE is not None:
        return _ENV_CACHE
    out: dict = {}
    try:
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    _ENV_CACHE = out
    return out


def _secret(name: str, settings: dict, key: str = "") -> str:
    """settings → 环境变量 → ~/.hermes/.env。"""
    if key and settings.get(key):
        return str(settings[key]).strip()
    val = os.environ.get(name)
    if val:
        return val.strip()
    return (_env_file_values().get(name) or "").strip()


# ---------- HTTP（同步，跑在线程里） ----------

def _post_json(url: str, payload: dict, headers: dict, timeout: int = 90) -> dict:
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                headers={"Content-Type": "application/json", **headers},
                                method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:      # noqa: S310
        return json.loads(resp.read().decode("utf-8"))


def _download(url: str, timeout: int = 90) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "klee-onebot/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:      # noqa: S310
        return resp.read()


# ---------- 提供方 ----------

def _gen_cloudflare(prompt: str, settings: dict) -> bytes:
    token = _secret("CLOUDFLARE_API_TOKEN", settings, "cloudflare_token")
    acct = _secret("CLOUDFLARE_ACCOUNT_ID", settings, "cloudflare_account")
    if not token or not acct:
        raise RuntimeError("no-cloudflare-key")
    url = f"https://api.cloudflare.com/client/v4/accounts/{acct}/ai/run/{CF_MODEL}"
    data = _post_json(url, {"prompt": prompt, "steps": int(settings.get("steps") or 4)},
                      {"Authorization": f"Bearer {token}"}, timeout=int(settings.get("timeout") or 90))
    b64 = ((data.get("result") or {}).get("image")) if isinstance(data, dict) else None
    if not b64:
        raise RuntimeError(f"cloudflare-bad-payload:{str(data)[:120]}")
    return base64.b64decode(b64)


def _gen_glm(prompt: str, settings: dict) -> bytes:
    key = _secret("GLM_API_KEY", settings, "glm_key") or _secret("ZAI_API_KEY", settings, "glm_key")
    if not key:
        raise RuntimeError("no-glm-key")
    data = _post_json(GLM_URL, {"model": settings.get("glm_model") or "cogview-3-flash",
                                "prompt": prompt, "size": settings.get("size") or "1024x1024"},
                      {"Authorization": key}, timeout=int(settings.get("timeout") or 90))
    items = (data or {}).get("data") or []
    if not items or not items[0].get("url"):
        raise RuntimeError(f"glm-bad-payload:{str(data)[:120]}")
    return _download(items[0]["url"])


PROVIDER_NAMES = ("cloudflare", "glm")
_PROVIDER_FNS = {"cloudflare": "_gen_cloudflare", "glm": "_gen_glm"}


def _provider_fn(name: str):
    """按名字在运行时取提供方函数（方便配置切换与测试替换）。"""
    return globals()[_PROVIDER_FNS[name]]


def _pick_providers(settings: dict):
    want = str(settings.get("provider") or "auto").strip().lower()
    if want and want != "auto":
        return [want] if want in _PROVIDER_FNS else list(PROVIDER_NAMES)
    return list(PROVIDER_NAMES)


def _slug(prompt: str) -> str:
    h = hashlib.sha1(prompt.encode("utf-8")).hexdigest()[:8]
    ts = datetime.datetime.now().strftime("%m%d-%H%M%S")
    return f"{ts}-{h}"


def _state_root() -> Path:
    """状态目录：`KLEE_STATE_DIR` 可覆盖（干跑测试台靠它把状态写到临时目录）。"""
    base = os.environ.get("KLEE_STATE_DIR")
    return Path(base) if base else Path(__file__).resolve().parent / "state"


def _images_dir(settings: dict) -> Path:
    """图存哪儿：settings.images_dir 优先，默认 <插件目录>/state/images/。"""
    root = settings.get("images_dir")
    d = Path(str(root)) if root else _state_root() / "images"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---------- 维护：配额 + 旧图清理 ----------

def _plugin_root() -> Path:
    return Path(__file__).resolve().parent


def _usage_file(settings: dict) -> Path:
    """每日配额记账文件：settings.usage_file 优先，默认 <插件目录>/state/draw_usage.json。"""
    p = settings.get("usage_file")
    path = Path(str(p)) if p else _state_root() / "draw_usage.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _today() -> str:
    return datetime.date.today().isoformat()


def read_usage(settings: dict) -> int:
    """今天已经画了几张（跨天自动清零，脏文件当 0）。"""
    try:
        data = json.loads(_usage_file(settings).read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("date") == _today():
            return max(0, int(data.get("count") or 0))
    except (OSError, ValueError, TypeError):
        pass
    return 0


def reserve_usage(settings: dict) -> int:
    """记账 +1 并返回今天的新计数（写失败静默，不拦功能）。"""
    path = _usage_file(settings)
    n = read_usage(settings) + 1
    try:
        path.write_text(json.dumps({"date": _today(), "count": n}, ensure_ascii=False),
                        encoding="utf-8")
    except OSError:
        logger.warning("OneBot draw: 配额记账写失败 %s", path)
    return n


def daily_limit(settings: dict) -> int:
    """每日总配额；0/缺省 = 不限。"""
    try:
        return max(0, int(settings.get("draw_daily_limit") or 0))
    except (TypeError, ValueError):
        return 0


_QUOTA_LOCKS: dict = {}


def _quota_lock(path: Path) -> asyncio.Lock:
    key = str(path)
    lk = _QUOTA_LOCKS.get(key)
    if lk is None:
        lk = asyncio.Lock()
        _QUOTA_LOCKS[key] = lk
    return lk


async def claim_quota(settings: dict):
    """原子地占一个配额位：返回 ``(allowed, left)``；不限时 ``(True, None)``。

    被拒的请求**不记账**（同学数量对得上），加锁是防同一进程内并发绕过上限。
    """
    limit = daily_limit(settings)
    if limit <= 0:
        return True, None
    async with _quota_lock(_usage_file(settings)):
        used = read_usage(settings)
        if used >= limit:
            return False, 0
        reserve_usage(settings)
        return True, limit - used - 1


def keep_images(settings: dict) -> int:
    try:
        v = settings.get("keep_images")
        return KEEP_IMAGES_DEFAULT if v is None or v == "" else max(0, int(v))
    except (TypeError, ValueError):
        return KEEP_IMAGES_DEFAULT


def prune_images(out_dir: Path, keep: int) -> list:
    """只留最近 keep 张（按 mtime），返回被删掉的文件名；keep<=0 = 不限。"""
    if keep <= 0:
        return []
    try:
        files = [p for p in out_dir.iterdir() if p.is_file() and not p.name.startswith(".")]
    except OSError:
        return []
    if len(files) <= keep:
        return []
    def _mtime(p: Path) -> float:
        try:
            return p.stat().st_mtime
        except OSError:
            return 0.0
    files.sort(key=_mtime)                     # 老 → 新
    removed = []
    for p in files[: len(files) - keep]:
        try:
            p.unlink()
            removed.append(p.name)
        except OSError:
            logger.warning("OneBot draw: 旧图删不掉 %s", p)
    if removed:
        logger.info("OneBot draw: 清理旧图 %d 张（保留最近 %d）", len(removed), keep)
    return removed


def generate_image(prompt: str, settings: dict, out_dir: Path):
    """同步：依次试提供方，成功返回 (path, provider)；全失败抛最后一个异常。"""
    last: Exception | None = None
    for name in _pick_providers(settings):
        try:
            raw = _provider_fn(name)(prompt, settings)
            if not raw or len(raw) < 512:
                raise RuntimeError(f"{name}-too-small:{len(raw) if raw else 0}")
            path = out_dir / f"{_slug(prompt)}.jpg"
            path.write_bytes(raw)
            return path, name
        except Exception as e:                                   # noqa: BLE001
            last = e
            logger.warning("OneBot draw: 提供方 %s 失败 — %s", name, e)
    raise last or RuntimeError("no-provider")


def _why(e: Exception) -> str:
    msg = str(e)
    if "no-cloudflare-key" in msg or "no-glm-key" in msg:
        return "没有可用的画图钥匙"
    if "timed out" in msg.lower() or "timeout" in msg.lower():
        return "云朵有点忙"
    m = re.search(r"\b(4\d\d|5\d\d)\b", msg)
    if m:
        return f"服务打了个嗝 {m.group(1)}"
    return type(e).__name__


# ---------- 插件入口 ----------

def register(api):
    settings = dict(getattr(api, "settings", None) or {})
    tasks: set = set()

    async def draw_cmd(ctx):
        prompt = " ".join(ctx.args).strip()
        if not prompt:
            await ctx.reply("用法：.画画 <想要什么>，比如 .画画 可莉抱着嘟嘟可在草地上")
            return

        # 每日配额（0 = 不限）：先原子占位再画，防并发绕过上限
        allowed, left = await claim_quota(settings)
        if not allowed:
            await ctx.reply(QUOTA_LINES.format(limit=daily_limit(settings)))
            return

        cut = False
        if len(prompt) > MAX_PROMPT:
            prompt, cut = prompt[:MAX_PROMPT], True
        prefix = "（提示词太长啦，可莉只画前 120 字哦）\n" if cut else ""
        wait = WAIT_LINES[hash(prompt) % len(WAIT_LINES)]
        if left is not None:
            wait += "　" + QUOTA_TAIL.format(left=left)
        await ctx.reply(prefix + wait)

        adapter, chat_id = ctx.adapter, ctx.chat_id
        out_dir = _images_dir(settings)
        keep = keep_images(settings)

        async def worker():
            try:
                path, provider = await asyncio.to_thread(generate_image, prompt, settings, out_dir)
                caption = "🎨 画好啦！是你要的「%s」" % prompt[:30] + ("…" if len(prompt) > 30 else "")
                await adapter.send_image_file(chat_id, str(path), caption=caption)
                logger.info("OneBot draw: 出图成功（%s）%s", provider, path.name)
                if keep > 0:
                    await asyncio.to_thread(prune_images, out_dir, keep)
            except Exception as e:                               # noqa: BLE001
                logger.warning("OneBot draw: 出图失败 — %s", e)
                try:
                    await adapter.send(chat_id, FAIL_LINES.format(why=_why(e)))
                except Exception:                                # noqa: BLE001
                    logger.exception("OneBot draw: 失败提示也发不出去")

        task = asyncio.get_running_loop().create_task(worker())
        tasks.add(task)
        task.add_done_callback(tasks.discard)

    api.on_command("画画", draw_cmd, aliases=("画", "作画", "draw"), cooldown=60,
                   help=".画画 <提示词> —— 让可莉给你画一张图（最多 120 字，冷切 60s）")
