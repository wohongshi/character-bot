"""把 bot 运行时接进 studio：独立 asyncio 线程 + 配置/模型存储。"""
from __future__ import annotations

import asyncio
import concurrent.futures
import copy
import json
import logging
import os
import threading
import time
from pathlib import Path

from .agent import DEFAULT_TOOLS, resolve_tools
from .characters import CharacterStore
from .llm import ModelStore
from .runtime import BotRuntime

logger = logging.getLogger("klee.bot")

DEFAULT_CONFIG = {
    "self_id": "",              # 首次收到事件时自动学习；也可在面板填
    "owner_id": "",             # 主人 QQ（agent/.agent 权限、部分插件命令用）
    "napcat_http": "http://127.0.0.1:3101",
    "napcat_token": "",
    "feature_prefix": ".",
    "split_sentences": True,
    "split_soft_max": 100,
    "send_interval_min": 0.5,
    "send_interval_max": 1.5,
    "quote_reply": True,
    "at_sender": True,
    "group_policy": "mention",
    "group_chime_probability": 0,
    "group_chime_cooldown": 120,
    "notify_poke": True,
    "greet_on_group_add": False,
    "chat_enabled": True,
    "request_notify": True,        # 好友/入群请求私聊提醒主人（不自动通过）
    "max_reply_chars": 1200,
    "max_tokens": 800,
    "history_messages": 12,
    "kb_enabled": True,
    "kb_url": "http://127.0.0.1:8797",
    "kb_topk": 3,
    "kb_threshold": 0.4,          # 小语料角色（6~7 块）相关命中约 0.45，0.5 会全被滤掉
    "kb_max_chars": 800,
    # 图片理解（vision 模型在 models.json 的 vision 段选；空 = 走友好降级）
    "vision_enabled": True,
    "vision_max_bytes": 8 * 1024 * 1024,
    "vision_timeout": 60,
    "max_images": 3,
    # agent（工具循环）
    "agent_enabled": True,
    "agent_owner_only": True,
    "agent_max_steps": 3,
    "agent_timeout": 120,
    "agent_tools": list(DEFAULT_TOOLS),
    "agent_skills": True,          # 允许 agent 用插件（list/read/use_skill）
    # 语音回复（模型写 [语音:…] 才发；TTS 失败只发文字）
    "tts_enabled": False,
    "tts_voice": "冰糖",
    "tts_max_chars": 80,
    # 每会话发送限流（0 = 不限）
    "rate_limit_per_min": 30,
    "rate_limit_burst": 10,
    # 日志：log_enabled=False 不写 state/bot.log；log_retention_days=0 表示永不自动清理
    "log_enabled": True,
    "log_retention_days": 30,
}


class BotService:
    def __init__(self, home: Path):
        self.home = Path(home)
        self.state_dir = self.home / "state"
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.config_path = self.state_dir / "bot_config.json"
        self.config = self._load_config()
        self._persist_initial_config()
        self.models = ModelStore(self.state_dir / "models.json")
        self.characters = CharacterStore(self.state_dir)
        self.loop = None
        self._thread = None
        self.runtime: BotRuntime | None = None
        self.started_at = time.time()
        self.error = ""
        self._started = False
        self._log_handler = None
        self._inflight = 0
        self._epoch = 0                     # stop()/start() 递增：旧回调不再改计数
        self._inflight_lock = threading.Lock()
        self._cfg_lock = threading.RLock()

    def _sync_log_handler(self) -> None:
        """按 log_enabled 挂/摘文件日志；配置热更新时也走这里。"""
        with self._cfg_lock:            # 两个面板请求同时保存时别挂出两个 FileHandler
            self._sync_log_handler_locked()

    def _sync_log_handler_locked(self) -> None:
        want = bool(self.config.get("log_enabled", True))
        if want and self._log_handler is None:
            try:
                fh = logging.FileHandler(self.state_dir / "bot.log", encoding="utf-8")
                fh.setFormatter(logging.Formatter(
                    "%(asctime)s %(levelname)s %(name)s: %(message)s"))
                logging.getLogger("klee").addHandler(fh)
                logging.getLogger("klee").setLevel(logging.INFO)
                self._log_handler = fh
            except OSError:
                pass
        elif not want and self._log_handler is not None:
            try:
                logging.getLogger("klee").removeHandler(self._log_handler)
                self._log_handler.close()
            except (OSError, ValueError):
                pass
            self._log_handler = None

    def _persist_initial_config(self) -> None:
        """首次运行 / 迁移后写回一次；坏配置不写（原文件已备份）。"""
        if not getattr(self, "_config_needs_persist", False):
            return
        try:
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.config_path.with_name(f"{self.config_path.name}.{os.getpid()}.init.tmp")
            tmp.write_text(json.dumps(self.config, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
            os.replace(tmp, self.config_path)
        except OSError as e:
            logger.warning("写入初始配置失败: %s", e)

    # ---------- 配置 ----------
    def _load_config(self) -> dict:
        cfg = copy.deepcopy(DEFAULT_CONFIG)     # 浅拷贝会让 agent_tools 这类列表跨实例共享
        self._config_needs_persist = False
        try:
            text = self.config_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            self._config_needs_persist = True          # 首次运行：落一份默认配置
            return cfg
        except OSError as e:
            logger.error("读取 bot 配置失败（%s），本次用默认值但不覆盖原文件", e)
            return cfg
        try:
            disk = json.loads(text)
        except ValueError as e:
            backup = self.config_path.with_name(
                f"{self.config_path.name}.corrupt-{int(time.time())}.bak")
            try:
                backup.write_text(text, encoding="utf-8")
            except OSError:
                backup = None
            logger.error("bot 配置不是合法 JSON（%s），已备份到 %s；本次用默认值，原文件保留",
                         e, backup)
            return cfg
        try:
            if isinstance(disk, dict):
                # 一次性迁移：老配置的 agent_tools 早于「插件即技能」存在，补进三个技能工具；
                # 之后用户手动去掉就真的去掉了（不再覆盖）。
                legacy_tools = "agent_tools" in disk and "agent_skills" not in disk
                # 只接受已知键，陈旧键别带进来
                cfg.update({k: v for k, v in disk.items()
                            if k in DEFAULT_CONFIG and v is not None})
                if legacy_tools and cfg.get("agent_skills", True):
                    cfg["agent_tools"] = list(dict.fromkeys(
                        resolve_tools(cfg.get("agent_tools"))
                        + ["list_skills", "read_skill", "use_skill"]))
                    self._config_needs_persist = True
        except (TypeError, ValueError) as e:                 # 结构怪但能解析：不覆盖
            logger.warning("bot 配置结构异常（%s），忽略磁盘内容", e)
        return cfg

    def save_config(self, patch: dict) -> dict:
        with self._cfg_lock:
            cfg = dict(self.config)                       # 写时复制：读者永远看到完整快照
            cfg.update({k: v for k, v in (patch or {}).items() if k in DEFAULT_CONFIG})
            if "agent_tools" in cfg:
                cfg["agent_tools"] = resolve_tools(cfg["agent_tools"])
            self.config = cfg
            tmp = self.config_path.with_name(
                f"{self.config_path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
            try:
                tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n",
                               encoding="utf-8")
                os.replace(tmp, self.config_path)
            except OSError as e:
                logger.warning("保存 bot 配置失败: %s", e)
            finally:
                try:
                    tmp.unlink(missing_ok=True)
                except OSError:
                    pass
        self._sync_log_handler()
        rt, loop = self.runtime, self.loop
        if rt and loop is not None:
            try:
                loop.call_soon_threadsafe(rt.apply_config, cfg)
            except RuntimeError:
                logger.warning("bot 循环已停止，配置未热更新")
        return cfg

    # ---------- 生命周期 ----------
    def _run_loop(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def start(self) -> None:
        # 整段持锁：stop() 不能看到「_started=True 但 loop/runtime 还是半截」的状态
        with self._cfg_lock:
            if self._started:
                return
            self._started = True
            self.error = ""
            self.started_at = time.time()      # 重启后 uptime 从这次启动算，不含停机时间
            self.loop = asyncio.new_event_loop()
            self._thread = threading.Thread(target=self._run_loop, name="klee-bot-loop",
                                            daemon=True)
            try:
                self.runtime = BotRuntime(
                    features_dir=self.home / "bot" / "features",
                    config=self.config,
                    models=self.models,
                    soul_path=self.characters.soul_path(self.characters.active_id()),
                    state_dir=self.state_dir,
                    characters=self.characters,
                )
            except Exception as e:                                 # noqa: BLE001
                self.error = f"{type(e).__name__}: {e}"
                self._started = False
                self.runtime = None
                try:
                    self.loop.close()
                except Exception:                                  # noqa: BLE001
                    pass
                self.loop = None
                self._thread = None
                logger.exception("bot 运行时启动失败")
                return
            self._sync_log_handler()
            self._thread.start()
            self.runtime.loop = self.loop
            try:
                asyncio.run_coroutine_threadsafe(self.runtime.start(), self.loop)
            except Exception:                                      # noqa: BLE001
                logger.warning("插件看护任务启动失败")
            self.runtime.loop_thread = self._thread

    def stop(self) -> None:
        with self._cfg_lock:
            if not self._started:
                return
            self._started = False
            rt, th, loop = self.runtime, self._thread, self.loop
            self.runtime = None
        if loop is not None and rt is not None:
            try:
                asyncio.run_coroutine_threadsafe(rt.stop(), loop).result(timeout=2)
            except Exception:                                      # noqa: BLE001
                pass
        if loop is not None:
            try:
                def _cancel_all():
                    for task in asyncio.all_tasks(loop):
                        task.cancel()
                loop.call_soon_threadsafe(_cancel_all)
                loop.call_soon_threadsafe(loop.stop)
            except RuntimeError:
                pass
        if th is not None and th.ident is not None:
            th.join(timeout=3.0)
            if th.is_alive():
                logger.warning("bot 循环线程 3 秒内没有退出（可能卡在插件里）")
        with self._inflight_lock:
            self._inflight = 0
            self._epoch += 1                # 让 stop 前发出的旧回调失效，别再减计数
        if loop is not None and not loop.is_running():
            try:
                loop.close()
            except RuntimeError:
                pass
        self.loop = None
        self._thread = None
        if self._log_handler:
            try:
                logging.getLogger("klee").removeHandler(self._log_handler)
                self._log_handler.close()
            except OSError:
                pass
            self._log_handler = None

    def reload(self) -> None:
        rt, loop = self.runtime, self.loop
        if rt and loop is not None:
            # 在 bot 循环线程里重载，避免和事件分发抢同一份 manager
            try:
                loop.call_soon_threadsafe(rt.mgr.load)
            except RuntimeError:
                logger.warning("bot 循环已停止，插件未重载")

    def submit(self, ev: dict) -> None:
        rt, loop = self.runtime, self.loop
        if not rt or loop is None:
            logger.warning("bot 未启动，丢弃事件")
            return
        with self._inflight_lock:
            if self._inflight >= 200:                 # 背压：别让事件无限堆积
                logger.warning("事件积压 %d 条，丢弃一条", self._inflight)
                return
            self._inflight += 1
            epoch = self._epoch
        coro = rt.handle_event(ev)
        try:
            fut = asyncio.run_coroutine_threadsafe(coro, loop)
        except RuntimeError:
            coro.close()                              # 没排上队的协程要显式关掉，否则 RuntimeWarning
            with self._inflight_lock:
                if epoch == self._epoch:
                    self._inflight = max(0, self._inflight - 1)
            logger.warning("bot 事件循环已停止，丢弃事件")
            return

        def _done(f):
            with self._inflight_lock:
                if epoch == self._epoch:          # stop() 已经把计数清零过：旧回调不再减
                    self._inflight = max(0, self._inflight - 1)
            if f.cancelled():                     # CancelledError 是 BaseException，不能靠 except Exception
                return
            try:
                exc = f.exception()
            except Exception:                                     # noqa: BLE001
                return
            if exc is not None:
                logger.error("事件处理异常：%s: %s", type(exc).__name__, exc)

        fut.add_done_callback(_done)

    def switch_character(self, cid: str) -> dict:
        """切换角色：落盘 + 运行时热切换（SOUL / 知识库 / 插件门控）。

        `rt.set_character` 会改 active_character / soul_path / 插件门控，必须在 bot 循环线程里跑，
        否则和 handle_event 并发改同一份状态（面板点切换时正好来消息，就可能用错人格）。
        """
        info = self.characters.set_active(cid)
        rt, loop = self.runtime, self.loop
        if rt is None:
            return info
        if loop is None:
            raise RuntimeError("bot 循环未启动")
        fut = concurrent.futures.Future()

        def _do() -> None:
            try:
                fut.set_result(rt.set_character(cid))
            except BaseException as e:                             # noqa: BLE001
                fut.set_exception(e)

        try:
            loop.call_soon_threadsafe(_do)
        except RuntimeError:
            raise RuntimeError("bot 循环已停止") from None
        try:
            return fut.result(timeout=15)
        except Exception:
            logger.exception("运行时切换角色失败：%s", cid)
            raise

    # ---------- 状态 ----------
    def status(self) -> dict:
        with self._cfg_lock:
            rt = self.runtime
        if rt is None:
            return {"ok": False, "error": self.error or "not started",
                    "uptime_s": round(time.time() - self.started_at, 1)}
        st = rt.status()
        st["uptime_s"] = round(time.time() - self.started_at, 1)
        with self._inflight_lock:
            st["inflight"] = self._inflight
        return st
