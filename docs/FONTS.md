# 字体

面板只用一款装饰字体：**ZCOOL KuaiLe**（SIL Open Font License 1.1）。

| 文件 | 大小 | 用途 |
|---|---:|---|
| `static/fonts/ZCOOLKuaiLe-Regular.ttf` | 1.5 MB | 原始字体，**页面不引用**，只作为子集化输入 |
| `static/fonts/zcool-ui.woff2` | 197 KB | 生产用（优先） |
| `static/fonts/zcool-ui.ttf` | 331 KB | 不支持 WOFF2 时的兜底 |

## 为什么要子集化

原始字体 1.5 MB，gzip 之后仍有 1.1 MB —— 在 1.6 Mbps 的网速下要下 8 秒，
而且下完会触发一次全页重排（LCP 直接推到 2.8 s）。

`tools/subset_font.py` 只保留面板真正会渲染的字符：

- `static/index.html` 全文
- `bot/features/*.py` 的 `NAME` / `TITLE` / `DESCRIPTION` / `USAGE`（插件标题会显示）
- `bot/characters.py` 与 `bot/character_seeds/*.md` 开头（角色名/称号）
- ASCII 可见字符 + 常用中文标点

结果：1996 个字符（1743 个汉字）→ WOFF2 197 KB。

## 缺字怎么办

`@font-face` 的回退链是 `"ZCOOL KuaiLe" → PingFang SC → HarmonyOS Sans SC →
Noto Sans CJK SC → Microsoft YaHei → sans-serif`，缺字由系统字体补，不会出现豆腐块。

## 重新生成

改了界面文案以后，如果新文案里有子集里没有的字，会退回系统字体（能用但不统一）。
重新跑一次即可：

```bash
python3 tools/subset_font.py        # 需要 fontTools + brotli
```

> 注意：`font-display:swap` + 不做 `preload`。preload 会让 197 KB 的字体
> 和首屏大图抢 3G 带宽，实测反而更慢。
