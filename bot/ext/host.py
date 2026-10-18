"""外部插件宿主：让插件用任何语言写。

协议就一条：**stdin/stdout 各一行 JSON**（NDJSON）。没有 gRPC、没有 FFI、
没有共享内存 —— 因为要支持的是「Node 写个脚本」「Rust 编译个二进制」
「C 写个 30 行的工具」这类场景，越薄越好：

    宿主 → {"op":"hello","v":1}
    插件 → {"ok":true,"name":"uuid","title":"UUID","commands":[{"name":"uuid","help":"…"}]}
    宿主 → {"op":"call","id":1,"cmd":"uuid","args":[],"text":"","ctx":{…}}
    插件 → {"id":1,"ok":true,"reply":"…"}           # 或 {"ok":false,"error":"…"}
    宿主 → {"op":"bye"}

进程是长连接（首次调用时才起），所以 Node/Java 这种启动慢的语言只付一次启动成本。
崩了就重起，最多 `max_restarts` 次；之后这个插件标记为故障，面板上能看到原因。

安全边界：`entry` 与 `run` 里的路径都必须在插件目录内（挡住 `../../`），
二进制必须已经有可执行位。**不**做沙箱 —— 能写插件的人在跑这个服务时本来就有
同等权限，假装隔离反而误导。
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

log = logging.getLogger("klee.ext")

__all__ = ["ExtPlugin", "ExtError", "LANG_RUN"]

#: 语言 → 默认启动命令（entry 会接在后面）
LANG_RUN = {
    "node": ["node"],
    "javascript": ["node"],
    "python": ["python3"],
    "php": ["php"],
    "ruby": ["ruby"],
    "java": ["java", "-XX:+UseSerialGC", "-Xss256k", "-cp"],
    "sh": ["sh"],
}


class ExtError(RuntimeError):
    pass


class ExtPlugin:
    """一个外部插件 = 一个常驻子进程 + 一份元信息。"""

    def __init__(self, spec: dict, base_dir: Path, *, max_restarts: int = 3):
        self.spec = dict(spec or {})
        self.base = Path(base_dir)
        self.name = str(self.spec.get("id") or "").strip().lower()
        self.title = str(self.spec.get("title") or self.name)
        self.language = str(self.spec.get("language") or "").strip().lower()
        self.timeout = float(self.spec.get("timeout") or 5.0)
        self.max_restarts = int(self.spec.get("max_restarts") or max_restarts)
        self.entry = str(self.spec.get("entry") or "").strip()
        self.cmd: list[str] = []
        self.meta: dict = {}
        self.restarts = 0
        self.error = ""
        self.calls = 0
        self.last_ms = 0.0
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._seq = 0
        self._build_cmd()

    # ---------- 准备 ----------
    def _resolve(self, p: str) -> Path:
        """把相对路径钉在插件目录里（拒绝绝对路径与 ..）。"""
        raw = str(p or "").strip()
        if not raw:
            raise ExtError("entry 为空")
        if os.path.isabs(raw) or raw.startswith("~"):
            raise ExtError(f"entry 必须是插件目录内的相对路径：{raw}")
        target = (self.base / raw).resolve()
        base = self.base.resolve()
        if base != target and base not in target.parents:
            raise ExtError(f"entry 越界：{raw}")
        return target

    def _build_cmd(self) -> None:
        run = self.spec.get("run")
        launcher = [str(x) for x in run] if isinstance(run, list) and run else None
        if launcher:
            # `run` 给了就照抄，`args` 里的参数原样追加（Java 要的是类名，不是路径）
            args = self.spec.get("args")
            self.cmd = [*launcher, *[str(a) for a in (args or [])]]
            if not self.cmd or not self.cmd[-1:]:
                raise ExtError("run 为空")
            return
        if self.language in LANG_RUN:
            self.cmd = [*LANG_RUN[self.language], str(self._resolve(self.entry))]
            return
        # 原生可执行文件：entry 本身就是二进制
        path = self._resolve(self.entry)
        if not path.exists():
            raise ExtError(f"找不到可执行文件 {self.entry}"
                           "（先跑 bash bot/features/ext/tools/build.sh）")
        if not os.access(path, os.X_OK):
            raise ExtError(f"{self.entry} 没有可执行位（chmod +x）")
        self.cmd = [str(path)]

    # ---------- 进程 ----------
    def _spawn(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            return
        if self.restarts > self.max_restarts:
            raise ExtError(f"{self.name} 反复崩溃（{self.restarts} 次），已停用：{self.error}")
        t0 = time.perf_counter()
        try:
            self._proc = subprocess.Popen(
                self.cmd, cwd=str(self.base),
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", errors="replace", bufsize=1)
        except OSError as e:
            self.error = f"启动失败：{e}"
            raise ExtError(self.error) from e
        hello = self._rpc({"op": "hello", "v": 1}, timeout=max(3.0, self.timeout))
        self.meta = hello or {}
        self.error = ""
        log.info("外部插件 %s 就绪（%s，启动 %.0f ms）", self.name,
                 " ".join(self.cmd[:2]), (time.perf_counter() - t0) * 1000)

    def _readline(self, timeout: float) -> dict:
        proc = self._proc
        if proc is None or proc.stdout is None:
            raise ExtError("进程不在")
        box: dict = {}

        def _read():
            try:
                box["line"] = proc.stdout.readline()
            except Exception as e:                                 # noqa: BLE001
                box["err"] = e

        th = threading.Thread(target=_read, daemon=True)
        th.start()
        th.join(timeout)
        if th.is_alive():
            raise ExtError(f"{self.name} 超时（{timeout:.1f}s 没有响应）")
        if "err" in box:
            raise ExtError(f"读输出失败：{box['err']}")
        line = (box.get("line") or "").strip()
        if not line:
            raise ExtError(f"{self.name} 没有输出就退出了（退出码 {proc.poll()}）")
        try:
            d = json.loads(line)
        except ValueError as e:
            raise ExtError(f"{self.name} 输出不是 JSON：{line[:120]}") from e
        if not isinstance(d, dict):
            raise ExtError(f"{self.name} 输出不是对象：{line[:120]}")
        return d

    def _rpc(self, payload: dict, *, timeout: float) -> dict:
        proc = self._proc
        if proc is None or proc.stdin is None:
            raise ExtError("进程不在")
        proc.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
        proc.stdin.flush()
        return self._readline(timeout)

    def start(self) -> dict:
        with self._lock:
            self._spawn()
            return dict(self.meta)

    def call(self, cmd: str, args: list[str], text: str, ctx: dict) -> dict:
        with self._lock:
            self._spawn()
            self._seq += 1
            rid = self._seq
            t0 = time.perf_counter()
            try:
                res = self._rpc({"op": "call", "id": rid, "cmd": cmd,
                                 "args": list(args or []), "text": text,
                                 "ctx": ctx}, timeout=self.timeout)
            except ExtError:
                self._kill()
                raise
            finally:
                self.last_ms = (time.perf_counter() - t0) * 1000
            self.calls += 1
            if not res.get("ok"):
                raise ExtError(str(res.get("error") or "插件返回失败"))
            return res

    def _kill(self) -> None:
        proc, self._proc = self._proc, None
        if not proc:
            return
        self.restarts += 1
        try:
            if proc.stdin:
                proc.stdin.close()
        except OSError:
            pass
        try:
            proc.terminate()
            proc.wait(timeout=2)
        except Exception:                                          # noqa: BLE001
            try:
                proc.kill()
            except Exception:                                      # noqa: BLE001
                pass

    def close(self) -> None:
        with self._lock:
            proc = self._proc
            self._proc = None
            if not proc:
                return
            try:
                if proc.stdin:
                    proc.stdin.write('{"op":"bye"}\n')
                    proc.stdin.flush()
                    proc.stdin.close()
            except OSError:
                pass
            try:
                proc.wait(timeout=1.5)
            except Exception:                                      # noqa: BLE001
                try:
                    proc.terminate()
                except Exception:                                  # noqa: BLE001
                    pass

    # ---------- 自检 ----------
    def describe(self) -> dict:
        return {
            "name": self.name, "title": self.title,
            "description": str(self.spec.get("description") or ""),
            "version": str(self.spec.get("version") or "1.0"),
            "language": self.language or "binary",
            "entry": self.entry, "cmd": list(self.cmd),
            "timeout": self.timeout, "calls": self.calls,
            "last_ms": round(self.last_ms, 1), "restarts": self.restarts,
            "error": self.error, "alive": bool(self._proc and self._proc.poll() is None),
            "commands": list(self.spec.get("commands") or []),
        }

    def __repr__(self) -> str:                                    # pragma: no cover
        return f"<ExtPlugin {self.name} lang={self.language}>"


def load_spec(path: Path) -> dict:
    """读一份 *.ext.json，把 base_dir 定成它所在目录。"""
    d: Any = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(d, dict):
        raise ExtError(f"{path.name} 不是对象")
    d.setdefault("id", Path(path).stem.replace(".ext", ""))
    return d
