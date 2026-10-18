"""角色立绘出多档尺寸：手机只下 480w 的小图。

原来是单一 900×1200 WebP（59–153 KB）。在 390px 宽的手机上，
头像/小卡只用得到 80–300 CSS px，下 900w 是纯浪费 —— 聊天页首屏那张
（honghong 150KB）在 1.6Mbps 下要 750ms，直接拖慢 LCP。

产出：
  <id>_s.webp   480×640 q=70   头像 / 侧栏 / 看板小卡
  <id>_m.webp   720×960 q=72   角色卡（2 倍屏 360 CSS px 内够用）
  <id>.webp     900×1200 q=72  聊天页大图 / 桌面
优先从同名 .jpg 原图重新编码（比 webp→webp 二压干净）。
"""
from __future__ import annotations

import glob
import sys
from pathlib import Path

for sp in glob.glob("/root/.hermes/installs/*/environments/*/venv/lib/python3*/site-packages"):
    if sp not in sys.path:
        sys.path.append(sp)
from PIL import Image  # noqa: E402

ROOT = Path("/root/.hermes/workspace/klee-studio")
CHARS = ROOT / "static/img/chars"

VARIANTS = [("_s", 480, 70), ("_m", 720, 72), ("", 900, 72)]

total_before = total_after = 0
rows = []
for webp in sorted(CHARS.glob("*.webp")):
    cid = webp.stem
    if cid.endswith(("_s", "_m")):
        continue
    jpg = CHARS / f"{cid}.jpg"
    src = jpg if jpg.exists() else webp
    before = webp.stat().st_size
    total_before += before
    im = Image.open(src).convert("RGB")
    # 统一按 3:4 裁到目标宽度，保持主体
    for suffix, width, q in VARIANTS:
        h = round(width * 4 / 3)
        out = CHARS / f"{cid}{suffix}.webp"
        im.resize((width, h), Image.LANCZOS).save(out, "WEBP", quality=q, method=6)
    main = CHARS / f"{cid}.webp"
    after = main.stat().st_size
    total_after += after
    small = (CHARS / f"{cid}_s.webp").stat().st_size
    rows.append((cid, before, after, small))

print(f"{'角色':10s} {'900w 前':>9s} {'900w 后':>9s} {'480w':>8s}")
for cid, b, a, s in rows:
    print(f"{cid:10s} {b/1024:8.0f}K {a/1024:8.0f}K {s/1024:7.0f}K")
print(f"\n首屏（聊天页大图）900w：{total_before/1024:.0f}K → {total_after/1024:.0f}K"
      f"（−{(1-total_after/total_before)*100:.0f}%）")
print(f"角色页 6 张 480w 合计：{sum(r[3] for r in rows)/1024:.0f}K"
      f"（原 6 张 900w {total_before/1024:.0f}K）")
