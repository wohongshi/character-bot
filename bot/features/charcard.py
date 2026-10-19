"""角色卡 & 生日提醒 & 天赋 —— `.角色卡 <名字>` / `.生日 [N|名字]` / `.天赋 <名字>`。

角色卡（`.角色卡 可莉`）：头像 + 名字/称号 + 元素/武器/星级 + 生日/地区/所属/CV +
命之座（座名 + C1~C6） + 一句简介。数据：gi.yatta.moe 角色详情接口（按角色缓存 30 天）。
生日（`.生日 [N]` default 7）：接下来 N 天过生日的角色（含今天）；`.生日 可莉` 查单个角色。
天赋（`.天赋 可莉`，v1.2）：战斗天赋三件套（普攻/战技/爆发的名称 + 简述 + 冷却/能量）+ 固有天赋。

数据文件：`genshin_data/avatars.json`（名字/生日/地区/图标，由
`~/.hermes/scripts/klee-genshin-avatars-update.py` 维护；缺了就临时拉一份进 state 兜底缓存）。
卡片：560 宽自适应星空卡；渲染/发送失败回退文本；`settings.card=false` 只发文本。

命令：`.角色卡 <名字|id>`（别名 .人物卡 / .角色资料） / `.命座 <名字|id>`（别名 .命之座，v1.1 新增） /
`.生日 [N|名字]`（别名 .本周生日 / .生日提醒 / .谁过生日） / `.天赋 <名字|id>`（别名 .技能 / .天赋技能，v1.2）
"""
from __future__ import annotations

import asyncio
import datetime as _dt
import json
import logging
import re
import sys
import time
import urllib.request
from pathlib import Path

_HERE = str(Path(__file__).resolve().parent)
if _HERE not in sys.path:
    sys.path.append(_HERE)
import _cardimg  # noqa: E402
import os

logger = logging.getLogger(__name__)

NAME = "角色卡"
DESCRIPTION = "角色图鉴卡与生日提醒：.角色卡 <名字> / .命座 <名字> / .生日 [N|名字] / .天赋 <名字>"
VERSION = "1.2"

# 角色限定：只有这些角色启用；空 = 通用插件（可在面板关闭）
CHARACTERS = ('klee', 'hutao', 'citlali')

def _state_root() -> "Path":
    """状态目录：`KLEE_STATE_DIR` 可覆盖（干跑测试台/冒烟靠它把状态写到临时目录，
    别让「跑一遍测试」改到真实战绩和抽卡记录）。"""
    base = os.environ.get("KLEE_STATE_DIR")
    return Path(base) if base else Path(__file__).resolve().parent / "state"


BASE = _state_root()
DATA_DIR = Path(__file__).resolve().parent / "genshin_data"
CARDS_DIR = BASE / "char_cards"
CACHE_TTL_DAYS = 30
LIST_URL = "https://gi.yatta.moe/api/v2/chs/avatar"
DETAIL_URL = "https://gi.yatta.moe/api/v2/chs/avatar/{id}"
UA = "KleeQQBot/0.1 (a friendly local QQ bot; charcard tool)"
TIMEOUT = 30

W = 560
SKY_TOP, SKY_BOTTOM = (26, 28, 58), (48, 44, 96)
GOLD, GOLD_DIM = (232, 196, 106), (150, 124, 66)
LIGHT, MUTED = (232, 228, 246), (152, 144, 190)
PANEL = (34, 34, 66)
CHIP_TXT = (250, 246, 235)

ELEMENT_CN = {"Fire": "火", "Water": "水", "Electric": "雷", "Grass": "草",
              "Wind": "风", "Rock": "岩", "Ice": "冰"}
ELEMENT_COLOR = {"Fire": (255, 130, 80), "Water": (86, 170, 255), "Electric": (185, 140, 245),
                 "Grass": (130, 210, 90), "Wind": (100, 215, 200), "Rock": (250, 200, 90),
                 "Ice": (140, 200, 240)}
WEAPON_CN = {"WEAPON_CATALYST": "法器", "WEAPON_SWORD_ONE_HAND": "单手剑",
             "WEAPON_CLAYMORE": "双手剑", "WEAPON_POLE": "长柄武器", "WEAPON_BOW": "弓"}
REGION_CN = {"MONDSTADT": "蒙德", "LIYUE": "璃月", "INAZUMA": "稻妻", "SUMERU": "须弥",
             "FONTAINE": "枫丹", "NATLAN": "纳塔", "SNEZHNAYA": "至冬", "NODKRAI": "挪德卡莱",
             "FATUI": "至冬", "RANGER": "纳塔", "OMNI_SCOURGE": "纳塔", "HVISION": "挪德卡莱"}


def region_cn(g: str) -> str:
    if not g:
        return "—"
    return REGION_CN.get(g.split("_")[0], "—" if g.startswith("MAINACTOR") else g)


def _save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{time.time_ns() % 1_000_000}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def _load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def trim_items(items: dict) -> dict:
    """上游 items → {id: {n,e,r,b,g,i}}（与更新脚本同款裁剪；非数字 id / 空名丢弃）。"""
    out = {}
    for key, v in (items or {}).items():
        if not isinstance(v, dict):
            continue
        try:
            iid = str(v.get("id") or key)
            if not iid.isdigit():
                continue
            name = v.get("name")
            if not isinstance(name, str) or not name.strip():
                continue
            b = v.get("birthday")
            b = [int(b[0]), int(b[1])] if isinstance(b, list) and len(b) == 2 else None
            if b is not None and not (1 <= b[0] <= 12 and 1 <= b[1] <= 31):
                b = None
            out[iid] = {"n": name.strip(), "e": str(v.get("element") or ""),
                        "r": int(v.get("rank") or 4), "b": b,
                        "g": str(v.get("region") or ""), "i": str(v.get("icon") or ""),
                        "w": str(v.get("weaponType") or "")}
        except (TypeError, ValueError):
            continue
    return out


def _filter_roster(rows: dict) -> dict:
    """展示层过滤：旅行者 / 奇偶占位（MAINACTOR）不要。"""
    return {k: v for k, v in rows.items()
            if isinstance(v, dict) and v.get("n") not in ("旅行者",)
            and not str(v.get("g") or "").startswith("MAINACTOR")}


def _valid_roster(rows) -> bool:
    return (isinstance(rows, dict) and len(rows) >= 50
            and all(isinstance(v, dict) and v.get("n") for v in rows.values()))


_roster_memo: dict = {}


def roster() -> dict:
    """角色名单：genshin_data（脚本维护）优先 → state 兜底缓存 → 现场拉一份。"""
    for path in (DATA_DIR / "avatars.json", CARDS_DIR / "avatars_cache.json"):
        try:
            mtime = path.stat().st_mtime_ns
        except OSError:
            continue
        memo = _roster_memo.get(str(path))
        if memo and memo[0] == mtime:
            return memo[1]
        raw = _load_json(path)
        if _valid_roster(raw):
            data = _filter_roster(raw)
            _roster_memo[str(path)] = (mtime, data)
            return data
    try:
        req = urllib.request.Request(LIST_URL, headers={"User-Agent": UA,
                                                        "Referer": "https://gi.yatta.moe/"})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:      # noqa: S310
            items = (((json.loads(r.read().decode()) or {}).get("data") or {}).get("items")) or {}
        data = _filter_roster(trim_items(items))
        if _valid_roster(data):
            try:
                _save_json(CARDS_DIR / "avatars_cache.json", data)
            except OSError:
                pass
            return data
    except Exception as e:                                            # noqa: BLE001
        logger.warning("角色卡：名单拉取失败（%s: %s）", type(e).__name__, e)
    return {}


TALENT_KIND = {"normal": "普通攻击", "skill": "元素战技", "burst": "元素爆发",
               "passive": "固有天赋", "other": "天赋"}
TALENT_COLOR = {"normal": (120, 150, 205), "skill": (118, 198, 168),
                "burst": (232, 176, 90), "passive": (172, 148, 236), "other": (150, 154, 190)}
TALENT_ORDER = ("normal", "skill", "burst")


def _talent_kind(icon, ttype) -> str:
    """判天赋类别：**按图标前缀**（yatta 的 type 字段不够用 —— 普攻与战技都是 type=0）。

    Skill_A* → 普攻；Skill_S* → 战技；Skill_E* → 爆发；UI_Talent* → 固有天赋；
    认不出图标才退回 type（1=爆发 / 2=固有天赋），其余记 "other"（照常展示，标签「天赋」）。
    """
    ic = str(icon or "")
    if ic.startswith("Skill_A"):
        return "normal"
    if ic.startswith("Skill_S"):
        return "skill"
    if ic.startswith("Skill_E"):
        return "burst"
    if ic.startswith("UI_Talent"):
        return "passive"
    if ttype == 1:
        return "burst"
    if ttype == 2:
        return "passive"
    return "other"


def _int_or_none(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return int(round(f)) if f > 0 else None


def _strip_label(kind: str, desc: str) -> str:
    """普攻描述首行常常就是「普通攻击」——与芯片标签重复，去掉。"""
    lines = str(desc or "").split("\n")
    lab = TALENT_KIND.get(kind, "")
    if lines and lab and lines[0].strip() == lab:
        lines = lines[1:]
    return "\n".join(lines).strip()


def _clean_desc(text) -> str:
    """命座/天赋描述清洗：去 <color> 等标签、还原转义换行（\\n / 双反斜杠）、压空白。"""
    t = str(text or "")
    t = re.sub(r"</?[a-zA-Z][^>]{0,40}>", "", t)
    t = t.replace("\\\\n", "\n").replace("\\n", "\n")
    return re.sub(r"[ \t]+", " ", t).strip()


def _trim_detail(raw: dict) -> dict:
    """裁剪 yatta 详情。v3 起带 talents（天赋）；v<3 旧缓存会被重拉。"""
    fet = raw.get("fetter") or {}
    cons = raw.get("constellation") or {}
    tal = raw.get("talent") or {}
    names, cons_desc, talents = [], [], []
    for k in sorted((str(kk) for kk in cons), key=lambda x: int(x) if x.isdigit() else 99):
        c = cons.get(k) or cons.get(int(k)) or {}
        nm = c.get("name")
        if isinstance(nm, str) and nm:
            names.append(nm)
            desc = _clean_desc(c.get("description") or c.get("descriptionBuff") or "")
            cons_desc.append({"n": nm, "d": desc[:300]})
    for k in sorted((str(kk) for kk in tal), key=lambda x: int(x) if x.isdigit() else 99):
        t = tal.get(k) or tal.get(int(k)) or {}
        nm = t.get("name")
        if not isinstance(nm, str) or not nm:
            continue
        kind = _talent_kind(t.get("icon"), t.get("type"))
        desc = _clean_desc(t.get("description") or t.get("descriptionBuff") or "")
        talents.append({"k": kind, "n": nm, "d": _strip_label(kind, desc)[:320],
                        "cd": _int_or_none(t.get("cooldown")),
                        "en": _int_or_none(t.get("cost"))})
    cv = None
    if isinstance(fet.get("cv"), dict):
        cv = fet["cv"].get("CHS")
    return {"title": str(fet.get("title") or ""), "intro": str(fet.get("detail") or ""),
            "native": str(fet.get("native") or ""), "cv": str(cv or ""),
            "constellation": str(fet.get("constellation") or ""), "cons": names[:6],
            "cons_desc": cons_desc[:6], "talents": talents[:10], "v": 3,
            "weapon": str(raw.get("weaponType") or "")}


def _fetch_detail(iid: str):
    """拉单个角色详情（网络）。测试猴补丁这个函数。"""
    req = urllib.request.Request(DETAIL_URL.format(id=iid),
                                 headers={"User-Agent": UA, "Referer": "https://gi.yatta.moe/"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:          # noqa: S310
        raw = ((json.loads(r.read().decode()) or {}).get("data")) or {}
    if isinstance(raw, dict) and raw:
        return _trim_detail(raw)
    return None


def card_data(iid: str):
    """角色详情（缓存 30 天）；拉了新的失败但本地有旧的就用旧的（图鉴数据不追时效）。"""
    path = CARDS_DIR / f"card_{iid}.json"
    fresh = False
    if path.exists():
        try:
            fresh = (time.time() - path.stat().st_mtime) < CACHE_TTL_DAYS * 86400
        except OSError:
            fresh = False
    if fresh:
        cached = _load_json(path)
        if isinstance(cached, dict) and cached.get("v", 1) >= 3:
            return cached
    fetched = None
    try:
        fetched = _fetch_detail(iid)
    except Exception as e:                                            # noqa: BLE001
        logger.warning("角色卡：详情拉取失败（%s: %s）", type(e).__name__, e)
    if fetched is not None:
        try:
            _save_json(path, fetched)
        except OSError:
            pass
        return fetched
    stale = _load_json(path)
    return stale if isinstance(stale, dict) else None


def find_char(rows: dict, query: str):
    """→ (iid, entry, 候选名列表)。精确 → 前缀 → 包含。"""
    q = (query or "").strip()
    if not q:
        return None, None, []
    if q in rows:
        return q, rows[q], []
    exact = [k for k, v in rows.items() if v["n"] == q]
    if len(exact) == 1:
        return exact[0], rows[exact[0]], []
    pref = [k for k, v in rows.items() if v["n"].startswith(q)]
    if len(pref) == 1:
        return pref[0], rows[pref[0]], []
    cont = [k for k, v in rows.items() if q in v["n"]]
    if len(cont) == 1:
        return cont[0], rows[cont[0]], []
    cands = pref or cont
    return None, None, [rows[k]["n"] for k in cands[:8]]


def days_until(b: list, today: _dt.date | None = None) -> int:
    """距离下一次生日的天数（今天 = 0）。"""
    today = today or _dt.date.today()
    m, d = int(b[0]), int(b[1])
    for year in (today.year, today.year + 1):
        try:
            nb = _dt.date(year, m, d)
        except ValueError:
            nb = _dt.date(year, m, 28)              # 2/29 之类兜底
        if nb >= today:
            return (nb - today).days
    return 366


def birthday_rows(rows: dict, n: int, today: _dt.date | None = None) -> list:
    """接下来 n 天内过生日的 [(天数, 名字, [月,日])]（含今天），按天数/名字排序。"""
    out = []
    for v in rows.values():
        b = v.get("b")
        if not b:
            continue
        du = days_until(b, today)
        if du <= n:
            out.append((du, v["n"], b))
    out.sort(key=lambda r: (r[0], r[1]))
    return out


def fmt_birthday(rows_list: list, n: int) -> str:
    if not rows_list:
        return f"🎂 接下来 {n} 天里没有角色过生日～（可莉的生日是 7 月 27 日哦）"
    lines = [f"🎂 接下来 {n} 天过生日的角色："]
    for du, name, b in rows_list:
        when = "就是今天！🎉" if du == 0 else f"{du} 天后"
        lines.append(f"· {b[0]}月{b[1]}日（{when}）—— {name}")
    lines.append("别忘了给 TA 准备蛋糕呀～（可莉是 7 月 27 日）")
    return "\n".join(lines)


def fmt_birthday_one(name: str, b: list, today: _dt.date | None = None) -> str:
    du = days_until(b, today)
    when = "就是今天！🎉" if du == 0 else f"还有 {du} 天"
    return f"🎂 「{name}」的生日：{b[0]} 月 {b[1]} 日（{when}）"


# ---------- 渲染 ----------

def _bg(H):
    Image, ImageDraw, _ = _cardimg.load_pil()
    im = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(im)
    for y in range(H):
        t = y / max(1, H - 1)
        d.line([(0, y), (W, y)],
               fill=tuple(int(a + (b - a) * t) for a, b in zip(SKY_TOP, SKY_BOTTOM)))
    return im, d


def _frame(d, H):
    d.rounded_rectangle([14, 14, W - 14, H - 14], radius=26, outline=GOLD, width=3)
    d.rounded_rectangle([25, 25, W - 25, H - 25], radius=22, outline=GOLD_DIM, width=1)


def _chip(d, f, cx, top, text, color):
    tw = d.textlength(text, font=f) + 26
    x0 = int(cx - tw / 2)
    d.rounded_rectangle([x0, top, x0 + tw, top + 30], radius=15, fill=color)
    d.text((cx, top + 15), text, font=f, fill=CHIP_TXT, anchor="mm")


def _soft(text, limit: int) -> str:
    """长描述用软清洗：只去方括号/实体（保留中文逗号句号），再截断。"""
    return _cardimg.safe_name(str(text or ""), limit)


def _wrap_para(text: str, font, limit: float, slack: float = 24) -> list:
    """单段折行：ASCII 串（数字/%/英文）整体不拆；CJK 逐字。
    slack = 孤字行合并时允许超出的像素（别越过容器边线）。"""
    tokens, cur = [], ""
    for ch in text:
        if ch.isascii() and not ch.isspace():
            cur += ch
        else:
            if cur:
                tokens.append(cur)
                cur = ""
            tokens.append(ch)
    if cur:
        tokens.append(cur)
    lines, line = [], ""
    for tk in tokens:
        if tk and font.getlength(tk) > limit:                    # 超长 ASCII 串兜底：逐字拆
            for ch in tk:
                if line and font.getlength(line + ch) > limit:
                    lines.append(line)
                    line = ch
                else:
                    line += ch
            continue
        if line and font.getlength(line + tk) > limit:
            lines.append(line)
            line = tk
        else:
            line += tk
    if line:
        lines.append(line)
    lines = lines or [""]
    # 孤字行（标点被挤到行首剩一两个字）→ 并回上一行（允许轻微超限 slack 像素，别越边线）
    if len(lines) >= 2 and font.getlength(lines[-1]) <= limit * 0.2:
        merged = lines[-2] + lines[-1]
        if font.getlength(merged) <= limit + slack:
            lines = lines[:-2] + [merged]
    return lines


def _wrap(text: str, font, limit: float, slack: float = 24) -> list:
    out = []
    for para in str(text or "").split("\n"):                     # 硬换行（数据自带）先分段
        out.extend(_wrap_para(para, font, limit, slack))
    return out or [""]


def render_card(ent: dict, detail: dict | None, icon, out_path) -> bool:
    """角色卡（560 宽自适应）。任何异常 → False。"""
    try:
        _, _, _ = _cardimg.load_pil()
        f_head = _cardimg.font_cute(30)
        f_name = _cardimg.font_cute(44)
        f_title = _cardimg.font_cjk(18)
        f_chip = _cardimg.font_cjk(16)
        f_lbl = _cardimg.font_cjk(17)
        f_con = _cardimg.font_cjk(17)
        f_intro = _cardimg.font_cjk(16)
    except Exception as e:                                            # noqa: BLE001
        logger.warning("角色卡：字体加载失败（%s: %s）→ 回退文本", type(e).__name__, e)
        return False
    try:
        detail = detail or {}
        intro = _soft(detail.get("intro") or "", 120)
        cons = [c for c in (detail.get("cons") or []) if isinstance(c, str)][:6]
        intro_lines = _wrap(intro, f_intro, W - 96) if intro else []
        intro_lines = intro_lines[:5]
        H = 700 + (len(intro_lines) * 28 if intro_lines else 0) + 80
        im, d = _bg(H)
        _frame(d, H)

        d.text((W // 2, 52), "角 色 卡", font=f_head, fill=GOLD, anchor="mm")

        r = 84
        cx, cy = W // 2, 180
        if icon is not None:
            _cardimg.paste_icon(im, icon, cx, cy - r, r * 2, radius=r, log=logger)
        d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=GOLD_DIM, width=3)

        d.text((W // 2, 292), f"「{_soft(ent.get('n') or '?', 9)}」", font=f_name,
               fill=(240, 226, 190), anchor="mm")
        title = _soft(detail.get("title") or "", 16)
        if title:
            d.text((W // 2, 326), f"—— {title} ——", font=f_title, fill=GOLD_DIM, anchor="mm")

        el = ELEMENT_CN.get(ent.get("e"), "?")
        ec = ELEMENT_COLOR.get(ent.get("e"), (150, 154, 190))
        chips = [(f"{el}元素", ec)]
        wt = WEAPON_CN.get((detail.get("weapon") or ent.get("w") or "").strip(), "")
        if wt:
            chips.append((wt, (120, 150, 205)))
        r_star = ent.get("r", 4)
        chips.append((f"{r_star}★", (232, 176, 90) if r_star == 5 else (168, 132, 200)))
        for i, (txt, col) in enumerate(chips):
            _chip(d, f_chip, 280 + (i - (len(chips) - 1) / 2) * 130, 348, txt, col)

        y = 398
        d.rounded_rectangle([36, y, W - 36, y + 66], radius=16, fill=PANEL,
                            outline=(70, 66, 110), width=1)
        b = ent.get("b")
        rec1 = f"生日 {b[0]}月{b[1]}日 · 地区 {region_cn(ent.get('g'))}" if b else f"地区 {region_cn(ent.get('g'))}"
        rec2_parts = []
        if detail.get("native"):
            rec2_parts.append(f"所属 {detail['native']}")
        if detail.get("cv"):
            rec2_parts.append(f"CV {detail['cv']}")
        rec2 = " · ".join(rec2_parts) or "——"
        d.text((W // 2, y + 20), _soft(rec1, 30), font=f_lbl, fill=LIGHT, anchor="mm")
        d.text((W // 2, y + 46), _soft(rec2, 32), font=f_lbl, fill=MUTED, anchor="mm")

        y += 92
        con_head = detail.get("constellation") or ""
        head = f"命之座 · {con_head}" if con_head else "命之座"
        d.text((48, y), head, font=f_con, fill=GOLD, anchor="lm")
        y += 26
        if cons:
            for i, nm in enumerate(cons):
                col = i % 2
                row = i // 2
                x = 64 + col * 226
                d.text((x, y + row * 30), f"C{i + 1} {_cardimg.short(nm, 8)}", font=f_con,
                       fill=LIGHT, anchor="lm")
            y += ((len(cons) + 1) // 2) * 30 + 8
        else:
            d.text((64, y + 10), "（暂无命座资料）", font=f_con, fill=MUTED, anchor="lm")
            y += 48

        if intro_lines:
            d.rounded_rectangle([36, y, W - 36, y + 16 + len(intro_lines) * 28], radius=14,
                                fill=(28, 28, 56), outline=(70, 66, 110), width=1)
            for i, line in enumerate(intro_lines):
                d.text((52, y + 22 + i * 28), line, font=f_intro, fill=MUTED, anchor="lm")
            y += 16 + len(intro_lines) * 28

        d.text((W // 2, H - 44), "资料：gi.yatta.moe · 图鉴卡", font=f_intro, fill=(120, 114, 158),
               anchor="mm")
        _cardimg.save_img(im, out_path)
        return True
    except Exception as e:                                            # noqa: BLE001
        logger.warning("角色卡：渲染失败（%s: %s）→ 回退文本", type(e).__name__, e)
        return False


def render_cons(ent: dict, detail: dict | None, icon, out_path) -> bool:
    """命之座一览卡（560 宽自适应）：C1~C6 名称 + 效果描述。任何异常 → False。"""
    try:
        _, _, _ = _cardimg.load_pil()
        f_head = _cardimg.font_cute(30)
        f_name = _cardimg.font_cute(40)
        f_sub = _cardimg.font_cjk(19)
        f_chip = _cardimg.font_cjk(17)
        f_cn = _cardimg.font_cjk(20)
        f_desc = _cardimg.font_cjk(17)
        f_small = _cardimg.font_cjk(15)
    except Exception as e:                                            # noqa: BLE001
        logger.warning("命座卡：字体加载失败（%s: %s）→ 回退文本", type(e).__name__, e)
        return False
    try:
        detail = detail or {}
        items = [it for it in (detail.get("cons_desc") or []) if isinstance(it, dict)][:6]
        if not items:
            items = [{"n": nm, "d": ""} for nm in (detail.get("cons") or [])
                     if isinstance(nm, str)][:6]
        blocks = []
        for it in items:
            desc = _soft(it.get("d") or "", 300)
            lines = _wrap(desc, f_desc, W - 152, slack=48)[:4] if desc else []
            blocks.append((_cardimg.short(it.get("n") or "", 10), lines))
        y0 = 312
        H = y0 + sum(74 + len(ln) * 30 + 12 for _, ln in blocks) + 56
        im, d = _bg(H)
        _frame(d, H)

        d.text((W // 2, 46), "命 之 座", font=f_head, fill=GOLD, anchor="mm")
        r = 58
        cx, cy = W // 2, 142
        if icon is not None:
            _cardimg.paste_icon(im, icon, cx, cy - r, r * 2, radius=r, log=logger)
        d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=GOLD_DIM, width=3)
        d.text((W // 2, 238), f"「{_cardimg.short(ent.get('n') or '?', 9)}」", font=f_name,
               fill=(240, 226, 190), anchor="mm")
        sub = _soft(detail.get("constellation") or "", 14)
        if sub:
            d.text((W // 2, 272), f"「{sub}」", font=f_sub, fill=GOLD_DIM, anchor="mm")

        y = y0
        for i, (nm, lines) in enumerate(blocks):
            bh = 74 + len(lines) * 30
            d.rounded_rectangle([36, y, W - 36, y + bh], radius=16, fill=PANEL,
                                outline=(70, 66, 110), width=1)
            d.rounded_rectangle([52, y + 16, 104, y + 46], radius=15, fill=GOLD_DIM)
            d.text((78, y + 31), f"C{i + 1}", font=f_chip, fill=CHIP_TXT, anchor="mm")
            d.text((120, y + 31), nm, font=f_cn, fill=GOLD, anchor="lm")
            for j, line in enumerate(lines):
                d.text((64, y + 72 + j * 30), line, font=f_desc, fill=LIGHT, anchor="lm")
            y += bh + 12

        d.text((W // 2, H - 40), "资料：gi.yatta.moe · 命座一览", font=f_small,
               fill=(120, 114, 158), anchor="mm")
        _cardimg.save_img(im, out_path)
        return True
    except Exception as e:                                            # noqa: BLE001
        logger.warning("命座卡：渲染失败（%s: %s）→ 回退文本", type(e).__name__, e)
        return False


def fmt_card(ent: dict, detail: dict | None) -> str:
    """文本回退。"""
    detail = detail or {}
    parts = [f"🎴 「{ent.get('n')}」" + (f" —— {detail['title']}" if detail.get("title") else "")]
    b = ent.get("b")
    line2 = [f"{ELEMENT_CN.get(ent.get('e'), '?')}元素"]
    wt = WEAPON_CN.get((detail.get("weapon") or ent.get("w") or "").strip(), "")
    if wt:
        line2.append(wt)
    line2 += [f"{ent.get('r', 4)}★", f"地区 {region_cn(ent.get('g'))}"]
    if b:
        line2.append(f"生日 {b[0]}月{b[1]}日")
    parts.append(" · ".join(line2))
    if detail.get("native") or detail.get("cv"):
        parts.append(" · ".join(x for x in (f"所属 {detail.get('native')}" if detail.get("native") else "",
                                            f"CV {detail.get('cv')}" if detail.get("cv") else "") if x))
    if detail.get("constellation"):
        parts.append(f"命之座 · {detail['constellation']}")
    if detail.get("intro"):
        parts.append(_soft(detail["intro"], 100))
    return "\n".join(parts)


def fmt_cons(ent: dict, detail: dict | None) -> str:
    """命座文本回退：C1~C6 名称 + 一句效果。"""
    detail = detail or {}
    items = [it for it in (detail.get("cons_desc") or []) if isinstance(it, dict)][:6]
    if not items:
        items = [{"n": nm, "d": ""} for nm in (detail.get("cons") or [])
                 if isinstance(nm, str)][:6]
    head = f"🔯 「{ent.get('n')}」的命之座"
    if detail.get("constellation"):
        head += f" · {detail['constellation']}"
    if not items:
        return head + "\n（命座资料还没收录诶……）"
    lines = [head]
    for i, it in enumerate(items):
        dsc = _soft(it.get("d") or "", 120)
        lines.append(f"C{i + 1} {it.get('n')}" + (f" —— {dsc}" if dsc else ""))
    return "\n".join(lines)


def _talent_items(detail: dict) -> tuple[list, list]:
    """→ (主动天赋块, 固有天赋块)。每块 = (kind, name, desc, cd, energy)。

    顺序：普攻 → 战技 → 爆发（`TALENT_ORDER`）；同 kind 保持数据原序（副战技/冲刺变体跟在后）。
    """
    raw = [t for t in ((detail or {}).get("talents") or []) if isinstance(t, dict)]
    acts, pas = [], []
    for t in raw:
        kind = str(t.get("k") or "other")
        nm = str(t.get("n") or "").strip()
        if not nm:
            continue
        item = (kind, nm, _strip_label(kind, str(t.get("d") or "")),
                t.get("cd"), t.get("en"))
        (pas if kind == "passive" else acts).append(item)
    rank = {k: i for i, k in enumerate(TALENT_ORDER)}
    acts.sort(key=lambda x: rank.get(x[0], 9))                    # stable：同 kind 保持原序
    return acts, pas


def _talent_foot(cd, energy) -> str:
    bits = []
    if _int_or_none(cd):
        bits.append(f"冷却 {_int_or_none(cd)} 秒")
    if _int_or_none(energy):
        bits.append(f"元素能量 {_int_or_none(energy)}")
    return " · ".join(bits)


def render_talent(ent: dict, detail: dict | None, icon, out_path) -> bool:
    """天赋一览卡（560 宽自适应）：普攻 / 战技 / 爆发（名称 + 简述 + 冷却能量）+ 固有天赋。"""
    try:
        _, _, _ = _cardimg.load_pil()
        f_head = _cardimg.font_cute(30)
        f_name = _cardimg.font_cute(40)
        f_sub = _cardimg.font_cjk(19)
        f_chip = _cardimg.font_cjk(16)
        f_cn = _cardimg.font_cjk(20)
        f_desc = _cardimg.font_cjk(17)
        f_small = _cardimg.font_cjk(15)
        f_foot = _cardimg.font_cjk(15)
    except Exception as e:                                            # noqa: BLE001
        logger.warning("天赋卡：字体加载失败（%s: %s）→ 回退文本", type(e).__name__, e)
        return False
    try:
        detail = detail or {}
        acts, pas = _talent_items(detail)
        blocks = []
        for kind, nm, desc, cd, en in acts:
            lines = [x for x in _wrap(_soft(desc, 320), f_desc, W - 152, slack=48) if x.strip()][:5]
            blocks.append((kind, _cardimg.short(nm, 12), lines, _talent_foot(cd, en)))
        for kind, nm, desc, cd, en in pas[:4]:
            lines = [x for x in _wrap(_soft(desc, 220), f_desc, W - 152, slack=48) if x.strip()][:3]
            blocks.append((kind, _cardimg.short(nm, 12), lines, _talent_foot(cd, en)))

        y0 = 312
        heights = [66 + len(ln) * 30 + (28 if foot else 0) for _k, _n, ln, foot in blocks]
        H = y0 + sum(h + 12 for h in heights) + 56
        im, d = _bg(H)
        _frame(d, H)

        d.text((W // 2, 46), "天 赋", font=f_head, fill=GOLD, anchor="mm")
        r = 58
        cx, cy = W // 2, 142
        if icon is not None:
            _cardimg.paste_icon(im, icon, cx, cy - r, r * 2, radius=r, log=logger)
        d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=GOLD_DIM, width=3)
        d.text((W // 2, 238), f"「{_cardimg.short(ent.get('n') or '?', 9)}」", font=f_name,
               fill=(240, 226, 190), anchor="mm")
        sub = _soft(detail.get("title") or detail.get("constellation") or "", 14)
        if sub:
            d.text((W // 2, 272), f"「{sub}」", font=f_sub, fill=GOLD_DIM, anchor="mm")

        y = y0
        for (kind, nm, lines, foot), bh in zip(blocks, heights):
            d.rounded_rectangle([36, y, W - 36, y + bh], radius=16, fill=PANEL,
                                outline=(70, 66, 110), width=1)
            label = TALENT_KIND.get(kind, "天赋")
            lw = int(d.textlength(label, font=f_chip)) + 26
            col = TALENT_COLOR.get(kind, (150, 154, 190))
            d.rounded_rectangle([52, y + 16, 52 + lw, y + 46], radius=15, fill=col)
            d.text((52 + lw / 2, y + 31), label, font=f_chip, fill=CHIP_TXT, anchor="mm")
            d.text((52 + lw + 16, y + 31), nm, font=f_cn, fill=GOLD, anchor="lm")
            for j, line in enumerate(lines):
                d.text((64, y + 72 + j * 30), line, font=f_desc, fill=LIGHT, anchor="lm")
            if foot:
                d.text((64, y + 72 + len(lines) * 30), foot, font=f_foot,
                       fill=(196, 188, 226), anchor="lm")
            y += bh + 12

        d.text((W // 2, H - 40), "资料：gi.yatta.moe · 天赋一览", font=f_small,
               fill=(120, 114, 158), anchor="mm")
        _cardimg.save_img(im, out_path)
        return True
    except Exception as e:                                            # noqa: BLE001
        logger.warning("天赋卡：渲染失败（%s: %s）→ 回退文本", type(e).__name__, e)
        return False


def fmt_talent(ent: dict, detail: dict | None) -> str:
    """天赋文本回退：普攻/战技/爆发的名称 + 一句简述 + 冷却能量，末行固有天赋名单。"""
    acts, pas = _talent_items(detail or {})
    head = f"📖 「{ent.get('n')}」的天赋"
    if not acts and not pas:
        return head + "\n（天赋资料还没收录诶……）"
    lines = [head]
    for kind, nm, desc, cd, en in acts:
        foot = _talent_foot(cd, en)
        dsc = _soft(desc.replace("\n", " "), 90)
        lines.append(f"{TALENT_KIND.get(kind, '天赋')} · {nm}" + (f"（{foot}）" if foot else "")
                     + (f" —— {dsc}" if dsc else ""))
    if pas:
        lines.append("固有天赋：" + " / ".join(n for _k, n, *_ in pas[:5]))
    return "\n".join(lines)


def _opt_bool(v, default: bool = True) -> bool:
    if v is None:
        return default
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    if isinstance(v, str):
        return v.strip().lower() not in ("false", "0", "no", "off", "关", "关闭", "不")
    return default


def _num(v, default: int = 0) -> int:
    if isinstance(v, bool):
        return default
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _icon_for(ent: dict, cache_root=None):
    try:
        _cardimg.ensure_sibling_path(__file__)               # 同上：运行期自愈
        import randchar  # noqa: PLC0415 —— 复用同目录的图标获取（含 Side→正脸转换）
        return randchar.icon_for({"icon": ent.get("i")}, cache_root)
    except Exception as e:                                            # noqa: BLE001
        logger.debug("角色卡：头像获取失败（%s: %s）", type(e).__name__, e)
        return None


def register(api):
    settings = dict(getattr(api, "settings", None) or {})
    card_on = _opt_bool(settings.get("card"), True)

    async def card_cmd(ctx):
        arg = (ctx.arg_text or "").strip()
        rows = roster()
        if not rows:
            await ctx.reply("角色名单还没准备好呀……让主人跑一下 klee-genshin-avatars-update.py 好不好～")
            return
        if not arg:
            await ctx.reply(f"用法：.角色卡 <名字>（比如 .角色卡 可莉）；名单里现在有 {len(rows)} 个角色～")
            return
        iid, ent, cands = find_char(rows, arg)
        if ent is None:
            if cands:
                await ctx.reply("诶，说的是这几位里的哪个呀：" + "、".join(cands) + "？")
            else:
                await ctx.reply(f"没找到「{_cardimg.safe_name(arg, 12)}」诶……检查一下名字？")
            return
        detail = await asyncio.to_thread(card_data, iid)
        icon = await asyncio.to_thread(_icon_for, ent)
        name = _cardimg.safe_name(ctx.user_name or "你", 12)
        if card_on:
            try:
                out = _cardimg.card_path(_cardimg.cards_dir(settings, BASE), "char", ctx.user_id)
            except OSError as e:
                logger.warning("角色卡：卡片目录不可用（%s）→ 回退文本", e)
                out = None
            if out is not None and await asyncio.to_thread(render_card, ent, detail, icon, out):
                title = f"「{detail['title']}」" if detail and detail.get("title") else ""
                caption = f"🎴 {name} 要的角色卡 —— 「{ent['n']}」{title}"
                if await _cardimg.send_card(ctx, out, caption, pattern="char-*.png"):
                    return
        await ctx.reply(fmt_card(ent, detail))

    async def cons_cmd(ctx):
        arg = (ctx.arg_text or "").strip()
        rows = roster()
        if not rows:
            await ctx.reply("角色名单还没准备好呀……让主人跑一下 klee-genshin-avatars-update.py 好不好～")
            return
        if not arg:
            await ctx.reply("用法：.命座 <名字>（比如 .命座 可莉）～")
            return
        if arg in ("我", "我的", "自己", "我自己"):
            await ctx.reply("诶……可莉不知道你的命座呀（只有游戏角色才有命之座哦）～想查谁？")
            return
        iid, ent, cands = find_char(rows, arg)
        if ent is None:
            if cands:
                await ctx.reply("诶，说的是这几位里的哪个呀：" + "、".join(cands) + "？")
            else:
                await ctx.reply(f"没找到「{_cardimg.safe_name(arg, 12)}」诶……检查一下名字？")
            return
        detail = await asyncio.to_thread(card_data, iid)
        if not (isinstance(detail, dict) and (detail.get("cons_desc") or detail.get("cons"))):
            await ctx.reply(f"「{ent['n']}」的命座资料还没收录诶……等主人更新下资料好不好～")
            return
        icon = await asyncio.to_thread(_icon_for, ent)
        name = _cardimg.safe_name(ctx.user_name or "你", 12)
        if card_on:
            try:
                out = _cardimg.card_path(_cardimg.cards_dir(settings, BASE), "cons", ctx.user_id)
            except OSError as e:
                logger.warning("命座卡：卡片目录不可用（%s）→ 回退文本", e)
                out = None
            if out is not None and await asyncio.to_thread(render_cons, ent, detail, icon, out):
                sub = f"「{detail['constellation']}」" if detail.get("constellation") else ""
                caption = f"🔯 {name} 要的命之座 —— 「{ent['n']}」{sub}"
                if await _cardimg.send_card(ctx, out, caption, pattern="cons-*.png"):
                    return
        await ctx.reply(fmt_cons(ent, detail))

    async def talent_cmd(ctx):
        arg = (ctx.arg_text or "").strip()
        rows = roster()
        if not rows:
            await ctx.reply("角色名单还没准备好呀……让主人跑一下 klee-genshin-avatars-update.py 好不好～")
            return
        if not arg:
            await ctx.reply("用法：.天赋 <名字>（比如 .天赋 可莉）～")
            return
        if arg in ("我", "我的", "自己", "我自己"):
            await ctx.reply("诶……可莉不知道你的天赋呀（只有游戏角色才有天赋哦）～想查谁？")
            return
        iid, ent, cands = find_char(rows, arg)
        if ent is None:
            if cands:
                await ctx.reply("诶，说的是这几位里的哪个呀：" + "、".join(cands) + "？")
            else:
                await ctx.reply(f"没找到「{_cardimg.safe_name(arg, 12)}」诶……检查一下名字？")
            return
        detail = await asyncio.to_thread(card_data, iid)
        if not (isinstance(detail, dict) and detail.get("talents")):
            await ctx.reply(f"「{ent['n']}」的天赋资料还没收录诶……等主人更新下资料好不好～")
            return
        icon = await asyncio.to_thread(_icon_for, ent)
        name = _cardimg.safe_name(ctx.user_name or "你", 12)
        if card_on:
            try:
                out = _cardimg.card_path(_cardimg.cards_dir(settings, BASE), "talent", ctx.user_id)
            except OSError as e:
                logger.warning("天赋卡：卡片目录不可用（%s）→ 回退文本", e)
                out = None
            if out is not None and await asyncio.to_thread(render_talent, ent, detail, icon, out):
                caption = f"📖 {name} 要的天赋 —— 「{ent['n']}」"
                if await _cardimg.send_card(ctx, out, caption, pattern="talent-*.png"):
                    return
        await ctx.reply(fmt_talent(ent, detail))

    async def bday_cmd(ctx):
        arg = (ctx.arg_text or "").strip()
        rows = roster()
        if not rows:
            await ctx.reply("角色名单还没准备好呀……让主人跑一下 klee-genshin-avatars-update.py 好不好～")
            return
        if arg and not arg.isdecimal():
            if arg in ("我", "我的", "自己", "我自己", "自己我"):
                await ctx.reply("诶……可莉不知道你的生日呀（只有游戏角色的才查得到哦）～想查谁？")
                return
            iid, ent, cands = find_char(rows, arg)
            if ent is not None:
                if ent.get("b"):
                    await ctx.reply(fmt_birthday_one(ent["n"], ent["b"]))
                else:
                    await ctx.reply(f"「{ent['n']}」的生日资料还没收录诶……")
                return
            if cands:
                await ctx.reply("说的是这几位里的哪个呀：" + "、".join(cands) + "？")
                return
            await ctx.reply(f"没找到「{_cardimg.safe_name(arg, 12)}」诶……检查一下名字？")
            return
        n = 7 if not arg else max(1, min(60, _num(arg, 7) or 7))
        await ctx.reply(fmt_birthday(birthday_rows(rows, n), n))

    api.on_command("角色卡", card_cmd, aliases=("人物卡", "角色资料"), cooldown=8,
                   help=".角色卡 <名字> —— 角色图鉴卡（头像/称号/命座/生日）")
    api.on_command("命座", cons_cmd, aliases=("命之座",), cooldown=8,
                   help=".命座 <名字> —— 命之座一览（C1~C6 名称 + 效果）")
    api.on_command("天赋", talent_cmd, aliases=("技能", "天赋技能"), cooldown=8,
                   help=".天赋 <名字|id> —— 天赋一览（普攻/战技/爆发 + 固有天赋）")
    api.on_command("生日", bday_cmd, aliases=("本周生日", "生日提醒", "谁过生日"), cooldown=8,
                   help=".生日 [N|名字] —— 接下来 N 天谁过生日（默认 7）；给名字查单人")
