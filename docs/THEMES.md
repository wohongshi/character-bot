# 界面主题插件规范（Character Bot）

主题是**目录插件**：`bot/ui_themes/<id>/theme.json`（必需）+ `theme.css`（可选）。
丢一个目录进去就被发现，不用改前端、不用重启（刷新页面即可）。

## 1. 目录结构

```
bot/ui_themes/<id>/
  theme.json    # id / name / version / tokens
  theme.css     # 可选：复杂规则（字体、气泡形状、背景纹理、卡片边框）
```

`<id>` 必须与 `theme.json` 里的 `id` 一致，且只允许小写字母/数字/`-`/`_`。

## 2. theme.json

```json
{
  "id": "hutao",
  "name": "往生堂 · 朱墨",
  "version": "1.0",
  "tokens": {
    "--bg": "#f5ebdd",
    "--paper": "#fffaf1",
    "--ink": "#2a1c1a",
    "--dim": "#8a6a5e",
    "--line": "#e3cdb6",
    "--red": "#a3232f",
    "--red-deep": "#7c1822",
    "--red-soft": "#f7e3dd",
    "--font-cute": "\"Noto Serif CJK SC\", serif"
  }
}
```

### 允许的变量（白名单）

| 变量 | 用途 |
| --- | --- |
| `--bg` | 页面底色 |
| `--paper` | 卡片/面板底色 |
| `--ink` | 正文颜色 |
| `--dim` | 次要文字 |
| `--line` | 描边/分隔线 |
| `--red` / `--red-deep` / `--red-soft` | 主色 / 深主色 / 主色浅底 |
| `--gold` / `--gold-soft` | 强调色 |
| `--sky` / `--sky-soft` | 辅助蓝 |
| `--green` / `--green-soft` | 成功态 |
| `--shadow` | 阴影（完整 box-shadow 值） |
| `--font-cute` | 标题/按钮字体栈 |
| `--font-body` | 正文字体栈 |

值里出现 `url(`、`;`、`{`、`}`、`<`、`>` 会被丢弃（防注入）；长度 >300 截断。
声明了白名单外的变量会被忽略并写日志。

## 3. theme.css

只写「变量表达不了」的部分，选择器必须以 `body[data-theme="<id>"]` 开头，避免串味：

```css
body[data-theme="hutao"] aside{
  background:linear-gradient(180deg,#fdf6ea,#f3e5d2);
  border-right:1px solid #d8bda3;
}
body[data-theme="hutao"] .bub{
  border-width:1px; border-radius:8px; border-top-left-radius:1px;
}
body[data-theme="hutao"] h2.page{letter-spacing:2px}
```

- 只能写 CSS，不要写 `@import` 外部地址；服务端只当文本下发，不执行 JS。
- 单文件上限 24KB。
- 不要用 `!important` 大面积覆盖，优先调变量。

## 4. 与角色绑定（一对一，锁死）

一个角色只认一个主题目录（目录名 = 角色 id），**面板不提供切换**。主题随角色包 ZIP 一起导入；换主题 = 换角色包/替换目录。

- 主题目录名必须等于角色 id（`bot/ui_themes/atri/` 只给 atri 用）；
- 前端切换角色时替换 `<link id="charThemeCss">`；
- 不再提供主题绑定接口给面板使用（内部 API 仍在，但 UI 已移除）。

## 5. 加载与回退

- `GET /api/themes`：列出全部主题（含 `used_by` 角色名、`tokens`、路径）；
- `GET /api/theme.css?character=<角色id>`：返回该角色主题的 CSS（`text/css`，`no-cache`）；
- 主题不存在 / 角色没绑 → 回退 `klee`；
- 页面声明 `color-scheme: light`，避免手机浏览器「强制深色」把浅色主题压黑。

## 6. 新增主题步骤

1. `mkdir -p bot/ui_themes/<id>`；
2. 写 `theme.json`（至少 `id/name/tokens`）；
3. 需要的话写 `theme.css`（选择器带 `body[data-theme="<id>"]`）；
4. 把主题目录放进角色包（`theme/`）并导入；
5. 切换角色看效果（背景、字体、气泡、卡片）。

## 7. 检查清单

- [ ] 目录名 = `theme.json.id`，且合法
- [ ] 至少覆盖 `--bg/--paper/--ink/--red/--font-cute`，对比度可读
- [ ] `theme.css` 选择器都带 `body[data-theme="<id>"]`
- [ ] 没有 `url()`/`@import`/脚本
- [ ] 手机宽度（≤430px）下不溢出、不重叠
- [ ] 目录名 = 角色 id，切换后 `document.body.dataset.theme` 正确
- [ ] `testkit/test_themes.py` 与 `test_theme_http.py` 通过
