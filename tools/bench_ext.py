"""量各语言外部插件的启动耗时与常驻内存，给 docs/POLYGLOT.md 用真实数字。"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXT = ROOT / "bot" / "features" / "ext"
sys.path.insert(0, str(ROOT))

from bot.ext import ExtPlugin, load_spec          # noqa: E402

CASES = [
    ("node", "uuid.ext.json", "uuid"),
    ("rust", "textstat.ext.json", "统计"),
    ("c", "crc.ext.json", "crc"),
    ("cpp", "sort.ext.json", "排序"),
    ("java", "sha.ext.json", "sha"),
]


def rss_kb(pid: int) -> int:
    try:
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1])
    except OSError:
        pass
    return 0


print(f"{'语言':6} {'启动(ms)':>9} {'常驻RSS':>9} {'一次调用(ms)':>12}")
for label, spec_file, cmd in CASES:
    spec = load_spec(EXT / spec_file)
    p = ExtPlugin(spec, EXT)
    t = time.perf_counter()
    try:
        p.start()
    except Exception as e:                                     # noqa: BLE001
        print(f"{label:6} 启动失败: {e}")
        continue
    boot = (time.perf_counter() - t) * 1000
    rss = rss_kb(p._proc.pid) if p._proc else 0
    t2 = time.perf_counter()
    p.call(cmd, [], "hello 世界", {})
    call = (time.perf_counter() - t2) * 1000
    rss2 = rss_kb(p._proc.pid) if p._proc else 0
    print(f"{label:6} {boot:>9.1f} {max(rss, rss2) / 1024:>8.1f}M {call:>12.2f}")
    p.close()
