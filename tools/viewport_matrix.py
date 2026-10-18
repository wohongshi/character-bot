"""设备分辨率矩阵检测：可见性 / 溢出 / 字号 / 触控目标。

需要：pip install websocket-client；面板已在 :8796 跑着；本机有 Chromium。
用法：python3 tools/viewport_matrix.py <标签> [调试端口]
     结果写 /root/.hermes/cache/scratch/matrix_<标签>.json（目录可改）


跑 11 种真实视口，每页每档收集：
  - 横向溢出（文档比视口宽）
  - 越界元素（右侧被裁 / 左侧负值）
  - 文字小于 11px 的数量（低分辨率屏上读不清）
  - 触控目标小于 32px 的数量（手指按不准）
  - #app 高度与视口差（100vh 在移动端会被地址栏吃掉）
  - 底部 dock 是否压住内容
"""
from __future__ import annotations

import json
import statistics
import subprocess
import sys
import time
import urllib.request

import websocket

import os
CHROME = os.environ.get("CHROME_BIN", "/root/.hermes/tools/chromium-1208/chrome-linux/chrome")
BASE = os.environ.get("PANEL_URL", "http://127.0.0.1:8796")
OUTDIR = os.environ.get("MATRIX_OUT", "/root/.hermes/cache/scratch")
PAGES = ["chat", "chars", "persona", "plugins", "reminders", "board", "ops", "custom"]

# (名字, 宽, 高, DPR, mobile)
DEVICES = [
    ("se1",       320, 568, 2, True),
    ("android-s", 360, 640, 3, True),
    ("iphone12",  390, 844, 3, True),
    ("iphone-max", 414, 896, 3, True),
    ("phone-land", 844, 390, 3, True),
    ("ipad-p",    768, 1024, 2, True),
    ("ipad-l",   1024, 768, 2, True),
    ("laptop",   1280, 800, 2, False),
    ("desktop",  1440, 900, 2, False),
    ("fhd",      1920, 1080, 1, False),
    ("qhd",      2560, 1440, 1, False),
]

PROBE = r"""(() => {
  const vw = window.innerWidth, vh = window.innerHeight;
  const out = {vw, vh, docW: document.documentElement.scrollWidth,
               docH: document.documentElement.scrollHeight,
               appH: (document.getElementById('app')||{getBoundingClientRect:()=>({height:0})}).getBoundingClientRect().height,
               bp: document.documentElement.dataset.bp || '',
               overflowX: document.documentElement.scrollWidth > vw + 2,
               over: [], tiny: 0, tinyList: [], smallTap: 0, tapList: [],
               dock: getComputedStyle(document.querySelector('.dock')||document.body).display,
               asideW: Math.round((document.querySelector('aside')||{getBoundingClientRect:()=>({width:0})}).getBoundingClientRect().width)};
  const inView = e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  document.querySelectorAll('body *').forEach(e => {
    if (!inView(e)) return;
    const r = e.getBoundingClientRect();
    const cs = getComputedStyle(e);
    if (cs.position === 'fixed' || cs.display === 'none') return;
    // 祖先里有横向滚动容器的，溢出是设计如此，不算问题
    let sc = e.parentElement, scrolls = false;
    while (sc && sc !== document.body) {
      const ss = getComputedStyle(sc);
      if ((ss.overflowX === 'auto' || ss.overflowX === 'scroll') && sc.scrollWidth > sc.clientWidth) { scrolls = true; break; }
      sc = sc.parentElement;
    }
    if (!scrolls && (r.right > vw + 2 || r.left < -2)) {
      const t = (e.innerText || '').trim().slice(0, 24);
      if (t) out.over.push((e.className || e.tagName) + ':' + Math.round(r.left) + '~' + Math.round(r.right) + ' ' + t);
    }
    const fs = parseFloat(cs.fontSize);
    if (e.childElementCount === 0 && (e.textContent || '').trim() && fs && fs < 11) {
      out.tiny++; if (out.tinyList.length < 5) out.tinyList.push(fs.toFixed(1) + 'px ' + (e.textContent||'').trim().slice(0, 14));
    }
    if (e.matches('button, .btn, a[href^="javascript"]')) {
      if (r.height < 30 || r.width < 28) {
        out.smallTap++; if (out.tapList.length < 5) out.tapList.push((e.innerText||'').trim().slice(0, 12) + ':' + Math.round(r.width) + 'x' + Math.round(r.height));
      }
    }
  });
  out.over = [...new Set(out.over)].slice(0, 6);
  // 底部 dock 是否压住最后一个卡片
  const v0 = document.getElementById('view');
  if (v0) { const keep = v0.scrollTop; v0.scrollTop = v0.scrollHeight; }
  const dock = document.querySelector('.dock');
  if (dock && getComputedStyle(dock).display !== 'none') {
    const dr = dock.getBoundingClientRect();
    const v = document.getElementById('view');
    const last = v && v.lastElementChild;
    if (last) {
      const lr = last.getBoundingClientRect();
      out.dockCovers = lr.bottom > dr.top + 2 && lr.top < dr.bottom;
    }
  }
  return out;
})()"""


class CDP:
    def __init__(self, port):
        self.proc = subprocess.Popen(
            [CHROME, "--headless=new", f"--remote-debugging-port={port}",
             f"--user-data-dir=/tmp/vm_{port}", "--no-sandbox", "--disable-gpu",
             "--remote-allow-origins=*", "--disable-dev-shm-usage", "about:blank"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.ws = None
        for _ in range(60):
            try:
                tabs = [t for t in json.load(urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/json", timeout=1)) if t.get("type") == "page"]
                if tabs:
                    self.ws = websocket.create_connection(tabs[0]["webSocketDebuggerUrl"], timeout=60)
                    break
            except Exception:
                pass
            time.sleep(0.4)
        if not self.ws:
            raise RuntimeError("chrome 起不来")
        self.i = 0

    def send(self, m, **kw):
        self.i += 1
        mid = self.i
        self.ws.send(json.dumps({"id": mid, "method": m, "params": kw}))
        while True:
            r = json.loads(self.ws.recv())
            if r.get("id") == mid:
                return r

    def ev(self, expr, await_promise=False):
        r = self.send("Runtime.evaluate", expression=expr, awaitPromise=await_promise,
                      returnByValue=True, timeout=60000)
        res = r.get("result", {})
        if "exceptionDetails" in res:
            d = res["exceptionDetails"]
            return {"__error": str((d.get("exception") or {}).get("description") or d.get("text"))[:200]}
        return res.get("result", {}).get("value")

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass
        self.proc.terminate()


def main():
    label = sys.argv[1] if len(sys.argv) > 1 else "base"
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 9911
    c = CDP(port)
    rows = []
    try:
        c.send("Runtime.enable")
        c.send("Page.enable")
        for name, w, h, dpr, mobile in DEVICES:
            c.send("Emulation.setDeviceMetricsOverride", width=w, height=h,
                   deviceScaleFactor=dpr, mobile=mobile)
            # 真机有触摸点；headless 默认 0，不模拟的话 pointer:coarse 永远是 false
            c.send("Emulation.setTouchEmulationEnabled", enabled=mobile,
                   maxTouchPoints=5 if mobile else 1)
            c.send("Page.navigate", url=BASE + "/")
            time.sleep(4.5)
            bad = []
            first = None
            for pg in PAGES:
                c.ev(f"go('{pg}')", await_promise=True)
                time.sleep(1.6)
                d = c.ev(PROBE)
                if not isinstance(d, dict) or d.get("__error"):
                    bad.append(f"{pg}:probe-fail")
                    continue
                first = first or d
                issues = []
                if d["overflowX"]:
                    issues.append("横向溢出")
                if d["over"]:
                    issues.append(f"越界×{len(d['over'])}")
                if d["tiny"]:
                    issues.append(f"小字×{d['tiny']}")
                if mobile and d["smallTap"]:
                    issues.append(f"小触控×{d['smallTap']}")
                if d.get("dockCovers"):
                    issues.append("dock压内容")
                if abs(d["appH"] - d["vh"]) > 2:
                    issues.append(f"app高{d['appH']:.0f}≠视口{d['vh']}")
                if issues:
                    bad.append(f"{pg}:{'/'.join(issues)}")
            r = {"device": name, "w": w, "h": h, "dpr": dpr, "mobile": mobile,
                 "bp": (first or {}).get("bp", ""),
                 "asideW": (first or {}).get("asideW"),
                 "dock": (first or {}).get("dock"),
                 "problems": bad}
            rows.append(r)
            flag = "✓" if not bad else "✗"
            print(f"  {flag} {name:11s} {w:4d}×{h:<5d}@{dpr}x bp={r['bp'] or '—':3s} "
                  f"侧栏{r['asideW']:4d} dock={r['dock']:5s} {'  '.join(bad) if bad else ''}")
    finally:
        c.close()

    json.dump(rows, open(f"{OUTDIR}/matrix_{label}.json", "w"),
              ensure_ascii=False, indent=1)
    nbad = sum(1 for r in rows if r["problems"])
    print(f"\n{len(rows)} 档设备，有问题 {nbad} 档")
    if nbad:
        print("（详见 matrix_%s.json）" % label)
    return 0 if nbad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
