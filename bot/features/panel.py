"""原神练度面板：``.练度 <UID> [角色名]`` —— 拉取角色展柜，渲染成练度卡片。

- 数据源：enka.network（免登录；玩家需在游戏里开「角色展柜」放上角色）
- 卡片：900 宽深蓝版式（头像/等级命座/面板属性/天赋/武器/圣遗物五件 + 圣遗物详情长段：
  全词条 + 每件双爆分/评级；有圣遗物时卡片会随词条折行变长）。
  图标从 enka CDN 拉取并本地缓存；渲染或发送失败自动回退文本摘要
- 数据文件在 genshin_data/（chars.json：角色表；loc_zhcn.json：hash→中文名）

学自「早柚核心 / GenshinUID」系的练度面板（看板字段与 enka 字段映射同源），
版式与渲染为本项目自己的 PIL 实现。
"""

import asyncio
import contextlib
import json
import logging
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

# _cardimg.py 与插件同目录（兜底 sys.path，见 gacha.py 同一段说明）
_HERE = str(Path(__file__).resolve().parent)
if _HERE not in sys.path:
    sys.path.append(_HERE)
import _cardimg  # noqa: E402

logger = logging.getLogger(__name__)

NAME = "练度面板"
DESCRIPTION = "原神练度查询：.练度 <UID> [角色名] —— 展柜角色出练度卡片"
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
FONT_CUTE = _cardimg.FONT_CUTE
FONT_CJK = _cardimg.FONT_CJK
CJK_SC_INDEX = _cardimg.CJK_SC_INDEX
KEEP_CARDS = _cardimg.KEEP_CARDS

W, H = 900, 600
ENKA_API = "https://enka.network/api/uid/{uid}"
ICON_BASE = "https://enka.network/ui/{name}.png"
UA = "KleeQQBot/0.1 (a friendly local QQ bot; contact: owner)"
TIMEOUT = 20
CACHE_TTL = 90.0            # enka 响应缓存（秒）
MIN_REQ_GAP = 1.5           # 对 enka 的最小请求间隔（秒），别把它们的限速踩爆
PANEL_CACHE_V = 1           # 面板版式版本：改设计就 +1（渲染缓存 key 的一部分）

ELEMENT_CN = {"Fire": "火", "Water": "水", "Electric": "雷", "Grass": "草",
              "Wind": "风", "Rock": "岩", "Ice": "冰"}
ELEMENT_FIGHT_ID = {"Fire": "40", "Water": "42", "Electric": "41", "Grass": "43",
                    "Wind": "44", "Rock": "45", "Ice": "46"}
ELEMENT_COLOR = {"Fire": (255, 130, 80), "Water": (86, 170, 255), "Electric": (185, 140, 245),
                 "Grass": (130, 210, 90), "Wind": (100, 215, 200), "Rock": (250, 200, 90),
                 "Ice": (140, 180, 255)}
QUALITY_STARS = {"QUALITY_ORANGE": 5, "QUALITY_PURPLE": 4, "QUALITY_BLUE": 3, "QUALITY_GREEN": 2}

PROP_CN = {
    "FIGHT_PROP_HP": "生命值", "FIGHT_PROP_ATTACK": "攻击力", "FIGHT_PROP_DEFENSE": "防御力",
    "FIGHT_PROP_HP_PERCENT": "生命值%", "FIGHT_PROP_ATTACK_PERCENT": "攻击力%",
    "FIGHT_PROP_DEFENSE_PERCENT": "防御力%", "FIGHT_PROP_ELEMENT_MASTERY": "元素精通",
    "FIGHT_PROP_CRITICAL": "暴击率", "FIGHT_PROP_CRITICAL_HURT": "暴击伤害",
    "FIGHT_PROP_CHARGE_EFFICIENCY": "元素充能效率", "FIGHT_PROP_HEAL_ADD": "治疗加成",
    "FIGHT_PROP_PHYSICAL_ADD_HURT": "物理伤害加成", "FIGHT_PROP_FIRE_ADD_HURT": "火元素伤害加成",
    "FIGHT_PROP_WATER_ADD_HURT": "水元素伤害加成", "FIGHT_PROP_ELEC_ADD_HURT": "雷元素伤害加成",
    "FIGHT_PROP_GRASS_ADD_HURT": "草元素伤害加成", "FIGHT_PROP_WIND_ADD_HURT": "风元素伤害加成",
    "FIGHT_PROP_ROCK_ADD_HURT": "岩元素伤害加成", "FIGHT_PROP_ICE_ADD_HURT": "冰元素伤害加成",
    "FIGHT_PROP_BASE_ATTACK": "基础攻击", "FIGHT_PROP_BASE_HP": "基础生命", "FIGHT_PROP_BASE_DEFENSE": "基础防御",
}
PROP_SHORT = {"生命值": "生命", "攻击力": "攻击", "防御力": "防御", "暴击率": "暴击",
              "暴击伤害": "爆伤", "元素充能效率": "充能", "元素精通": "精通",
              "物理伤害加成": "物伤", "治疗加成": "治疗"}
PCT_PROPS = {k for k in PROP_CN if k.endswith(("_PERCENT", "_ADD_HURT"))} | {
    "FIGHT_PROP_CRITICAL", "FIGHT_PROP_CRITICAL_HURT", "FIGHT_PROP_CHARGE_EFFICIENCY", "FIGHT_PROP_HEAL_ADD"}
# 部位认 equipType 枚举（权威）：图标后缀与部位顺序不同名（实测 _4=花、_1=杯），别拿后缀猜
EQUIP_SLOT_CN = {"EQUIP_BRACER": "生之花", "EQUIP_NECKLACE": "死之羽", "EQUIP_SHOES": "时之沙",
                 "EQUIP_RING": "空之杯", "EQUIP_DRESS": "理之冠"}
_SLOT_ORDER = ["生之花", "死之羽", "时之沙", "空之杯", "理之冠"]


# 双爆分评级（展示口径，阈值可调；不是硬性标准）
_GRADE_BANDS = ((42.0, "S"), (32.0, "A"), (22.0, "B"))
GRADE_COLOR = {"S": (246, 140, 190), "A": (230, 190, 90), "B": (120, 200, 150), "C": (150, 158, 190)}


def grade_of(cv) -> str:
    """圣遗物评分（双爆分）→ 评级：S ≥42 / A ≥32 / B ≥22 / C 其余（坏值算 C）。"""
    try:
        v = float(cv)
    except (TypeError, ValueError):
        return "C"
    for lo, g in _GRADE_BANDS:
        if v >= lo:
            return g
    return "C"


def wrap_subs(subs, font, limit, draw) -> list:
    """副词条按「段」折行：一段 = 一条词条（不在词条中间断）。

    返回 ``[[(name, value, is_crit), …], …]``；空词条给 ``[[]]``（占一行位）。
    """
    units = []
    for s in subs or []:
        name = str(s.get("name") or "")
        val = str(s.get("value") or "")
        if name or val:
            units.append((name, val, bool(s.get("crit"))))
    if not units:
        return [[]]
    lines, cur, w = [], [], 0.0
    for u in units:
        seg = draw.textlength(f"{u[0]} {u[1]}", font=font)
        add = seg if not cur else draw.textlength(" · ", font=font) + seg
        if cur and w + add > limit:
            lines.append(cur)
            cur, w = [u], seg
        else:
            cur.append(u)
            w += add
    if cur:
        lines.append(cur)
    return lines or [[]]


class EnkaError(Exception):
    """查得到人但拿不到数据时的友好错误（文案直接给用户看）。"""


# ---------------- 数据文件 ----------------

_CHARS_DB = None
_LOC_DB = None
_SETS_DB = None


def chars_db() -> dict:
    global _CHARS_DB
    if _CHARS_DB is None:
        try:
            _CHARS_DB = json.loads((DATA_DIR / "chars.json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            logger.warning("练度：角色表读取失败（%s）", e)
            _CHARS_DB = {}
    return _CHARS_DB


def loc_name(text_hash) -> str:
    global _LOC_DB
    if _LOC_DB is None:
        try:
            _LOC_DB = json.loads((DATA_DIR / "loc_zhcn.json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            logger.warning("练度：语言表读取失败（%s）", e)
            _LOC_DB = {}
    return _LOC_DB.get(str(text_hash)) or ""


def sets_db() -> dict:
    """setId → 中文套名（yatta 表）——文本表兜底。

    实测 enka 对个别套装发的 setNameTextMapHash 不在文本表里（set 14001 发 933076115，
    文本表只有 933076627），只靠 loc 会一直显示「套装#14001」；这里按 setId 再兜一层。
    """
    global _SETS_DB
    if _SETS_DB is None:
        try:
            _SETS_DB = json.loads((DATA_DIR / "sets.json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            logger.warning("练度：套装表读取失败（%s）", e)
            _SETS_DB = {}
    return _SETS_DB


# ---------------- enka 取数 ----------------

_CACHE: dict = {}
_LAST_REQ = [0.0]
_REQ_LOCK = threading.Lock()             # 限速器是 check-then-act，多线程同时查会双开（OCR 五轮 #3）

CACHE_DISK_TTL = 180.0                   # 磁盘缓存新鲜期（秒）：跨重启保留、少打搅 enka；过期才重新拉
DISK_DIR = BASE / "enka_cache"           # 测试请改这个模块属性到 TMP，别写生产 state
DISK_KEEP = 50                           # 缓存文件上限（按修改时间清最旧）


def _disk_path(uid: str) -> Path:
    safe = re.sub(r"[^\d]", "", str(uid))[:12] or "x"
    return DISK_DIR / f"enka_{safe}.json"


def disk_get(uid: str):
    """读磁盘缓存；过期/坏文件/形状不对 → None（当没缓存，照常拉取）。"""
    p = _disk_path(uid)
    try:
        if not p.is_file():
            return None
        if time.time() - p.stat().st_mtime > CACHE_DISK_TTL:
            return None
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and "playerInfo" in data else None


def disk_put(uid: str, data: dict) -> None:
    """原子写磁盘缓存；任何失败只记 debug，绝不打断正常流程。"""
    try:
        DISK_DIR.mkdir(parents=True, exist_ok=True)
        p = _disk_path(uid)
        tmp = p.with_name(f".{p.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, p)
        files = sorted(DISK_DIR.glob("enka_*.json"), key=lambda q: q.stat().st_mtime)
        for old in files[:-DISK_KEEP]:
            with contextlib.suppress(OSError):
                old.unlink()
    except OSError as e:
        logger.debug("练度：磁盘缓存写入失败（%s）", e)


def fetch_enka(uid: str) -> dict:
    """GET enka（同步，调用方塞 to_thread）。错误转成友好文案。"""
    req = urllib.request.Request(ENKA_API.format(uid=uid), headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:      # noqa: S310
            raw = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        if e.code == 400:
            raise EnkaError("UID 格式不对哦（9 位数字，比如 100000000）") from e
        if e.code == 404:
            raise EnkaError("没有找到这个 UID 诶，检查一下有没有输错～") from e
        if e.code == 424:
            raise EnkaError("enka 正好在维护（游戏更新后常见），晚点再来看看～") from e
        if e.code == 429:
            raise EnkaError("查得太频繁啦，等一小会儿再试哦～") from e
        raise EnkaError(f"enka 返回了 {e.code}，稍后再试～") from e
    except Exception as e:                                   # noqa: BLE001
        raise EnkaError(f"连不上 enka（{type(e).__name__}），检查下网络～") from e
    try:
        data = json.loads(raw)
    except ValueError as e:
        raise EnkaError("enka 返回的内容看不懂，稍后再试～") from e
    if not isinstance(data, dict) or "playerInfo" not in data:
        raise EnkaError("enka 返回异常，稍后再试～")
    return data


def get_enka(uid: str) -> dict:
    """带 90s 内存缓存 + 180s 磁盘缓存 + 最小请求间隔（线程安全：限速与取数在锁里串行）。"""
    now = time.time()
    hit = _CACHE.get(uid)
    if hit and now - hit[0] < CACHE_TTL:
        return hit[1]
    with _REQ_LOCK:
        hit = _CACHE.get(uid)                    # 等锁期间别人可能已经填好
        if hit and time.time() - hit[0] < CACHE_TTL:
            return hit[1]
        disk = disk_get(uid)                     # 磁盘缓存（跨重启）：新鲜就直接用，不惊动 enka
        if disk is not None:
            _CACHE[uid] = (time.time(), disk)
            return disk
        gap = time.time() - _LAST_REQ[0]
        if gap < MIN_REQ_GAP:
            time.sleep(MIN_REQ_GAP - gap)
        data = fetch_enka(uid)
        t = time.time()
        if len(_CACHE) > 64:                     # 长跑防膨胀：满了先清过期项（OCR 五轮 #4）
            for k in [k for k, (ts, _) in _CACHE.items() if t - ts > CACHE_TTL]:
                _CACHE.pop(k, None)
        _CACHE[uid] = (t, data)
        _LAST_REQ[0] = t
        disk_put(uid, data)
        return data


# ---------------- 解析 ----------------

def classify_skills(info: dict, levels: dict) -> list:
    """→ [(分类, 等级)] × 最多三个：普攻 / 战技 / 爆发。"""
    order = {"普攻": 0, "战技": 1, "爆发": 2}
    got = {}
    for sid, sname in (info.get("s") or {}).items():
        lv = levels.get(str(sid))
        if lv is None:
            continue
        if sname.startswith("Skill_A_"):
            kind = "普攻"
        elif sname.startswith("Skill_E_"):
            kind = "战技"
        elif sname.startswith("Skill_S_"):
            kind = "爆发"
        else:
            continue
        if kind not in got:
            got[kind] = lv
    return [(k, got[k]) for k in sorted(got, key=lambda x: order[x])]


def _safe_int(v, default: int = 0) -> int:
    """对外部数据的安全取整（int/float/数字字符串都认；其余给默认值）。"""
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


def fmt_main(prop: str, v) -> str:
    """圣遗物主词条值：百分比补 %、数值去多余小数。"""
    try:
        if prop in PCT_PROPS:
            return f"{('%g' % float(v))}%"
        fv = float(v)
        return str(int(fv)) if abs(fv - round(fv)) < 1e-6 else ("%g" % fv)
    except (TypeError, ValueError):
        return str(v)


def parse_char(entry: dict) -> dict:
    """enka 的 avatarInfoList[i] → 渲染/摘要共用的扁平结构。"""
    info = chars_db().get(str(entry.get("avatarId"))) or {}
    element = info.get("e") or ""
    fm = entry.get("fightPropMap") or {}

    def g(k, default=0.0):
        v = fm.get(k)
        return float(v) if isinstance(v, (int, float)) else default

    levels = {str(k): v for k, v in (entry.get("skillLevelMap") or {}).items()}
    skills = classify_skills(info, levels)

    weapon, relics = {}, []
    for item in entry.get("equipList") or []:
        if item.get("weapon"):
            weapon = item
        elif item.get("reliquary"):
            relics.append(item)
    w = {}
    if weapon:
        wf = weapon.get("flat") or {}
        ww = weapon.get("weapon") or {}
        affix = 1
        if ww.get("affixMap"):
            try:
                affix = int(list((ww.get("affixMap") or {}).values())[0]) + 1
            except (IndexError, TypeError, ValueError):
                affix = 1
        stats = []
        for s in wf.get("weaponStats") or []:
            prop = str(s.get("appendPropId") or "")
            stats.append((PROP_CN.get(prop, prop), fmt_main(prop, s.get("statValue"))))
        w = {"name": loc_name(wf.get("nameTextMapHash")) or "未知武器", "icon": wf.get("icon") or "",
             "star": int(wf.get("rankLevel") or 4), "affix": affix,
             "level": _safe_int(ww.get("level")), "stats": stats}

    relic_list, set_count = [], {}
    for r in relics:
        rf = r.get("flat") or {}
        rl = r.get("reliquary") or {}
        set_name = (loc_name(rf.get("setNameTextMapHash"))
                    or sets_db().get(str(rf.get("setId")))
                    or f"套装#{rf.get('setId')}")
        set_count[set_name] = set_count.get(set_name, 0) + 1
        icon = str(rf.get("icon") or "")
        piece = EQUIP_SLOT_CN.get(str(rf.get("equipType") or ""), "神秘部件")
        ms = rf.get("reliquaryMainstat") or {}
        mp = str(ms.get("mainPropId") or "")
        score = 0.0                      # 副词条双爆分：2×暴击率 + 暴击伤害（社区口径）
        subs = []
        for sub in rf.get("reliquarySubstats") or []:
            sp = str(sub.get("appendPropId") or "")
            try:
                sv = float(sub.get("statValue") or 0)
            except (TypeError, ValueError):
                sv = 0.0
            subs.append({"name": PROP_CN.get(sp, sp), "value": fmt_main(sp, sv),
                         "crit": sp in ("FIGHT_PROP_CRITICAL", "FIGHT_PROP_CRITICAL_HURT")})
            if sp == "FIGHT_PROP_CRITICAL":
                score += sv * 2
            elif sp == "FIGHT_PROP_CRITICAL_HURT":
                score += sv
        relic_list.append({"set": set_name, "piece": piece,
                           "level": max(0, int(rl.get("level") or 1) - 1), "icon": icon,
                           "star": _safe_int(rf.get("rankLevel"), 5),
                           "main": (PROP_CN.get(mp, mp), fmt_main(mp, ms.get("statValue"))),
                           "subs": subs,
                           "crit_score": round(score, 1)})
    relic_list.sort(key=lambda x: _SLOT_ORDER.index(x["piece"]) if x["piece"] in _SLOT_ORDER else 9)

    pm_node = (entry.get("propMap") or {}).get("4001")
    level = _safe_int(pm_node.get("val") if isinstance(pm_node, dict) else 0)

    return {
        "avatarId": entry.get("avatarId"),
        "name": info.get("n") or f"角色{entry.get('avatarId')}",
        "element": element, "quality": info.get("q") or "",
        "level": level,
        "const": len(entry.get("talentIdList") or []),
        "fetter": _safe_int((entry.get("fetterInfo") or {}).get("expLevel")),
        "skills": skills, "weapon": w, "relics": relic_list, "set_count": set_count,
        "crit_score": round(sum(float(r.get("crit_score") or 0) for r in relic_list), 1),
        "icon_name": (info.get("i") or ""),
        "stats": {"hp": g("2000"), "hp_base": g("1"), "atk": g("2001"), "atk_base": g("4"),
                  "def": g("2002"), "def_base": g("7"), "em": g("28"), "crit": g("20"),
                  "crit_dmg": g("22"), "er": g("23"),
                  "dmg_bonus": g(ELEMENT_FIGHT_ID.get(element, "0")) if element else 0.0,
                  "phys": g("30"), "heal": g("26")},
    }


def pick_avatar(avatar_list: list, query: str):
    """按名字挑展柜角色；挑不到返回友好文案（str）。"""
    q = query.strip().lower()
    for a in avatar_list:
        info = chars_db().get(str(a.get("avatarId"))) or {}
        n = str(info.get("n") or "").lower()
        if q and n and (q == n or q in n):
            return a
    names = "、".join(
        (chars_db().get(str(a.get("avatarId"))) or {}).get("n") or str(a.get("avatarId"))
        for a in avatar_list[:8])
    return f"展柜里没有「{query}」诶～有的角色：{names}"


# ---------------- 图标缓存 ----------------

def cached_icon(name: str, cache_root: Path):
    """缓存命中就返回路径，否则 None（**不联网**）。缓存键/大小阈值就这一份，别处别再写第二遍。"""
    if not name or not re.fullmatch(r"[A-Za-z0-9_]+", name):
        return None
    p = cache_root / f"{name}.png"
    try:                                                         # OCR 六轮 #5：is_file→stat 之间文件可能被删
        if p.is_file() and p.stat().st_size > 300:
            return p
    except OSError:
        pass
    return None


def icon_path(name: str, cache_root: Path):
    """取（或下载）一张 enka 图标；名字不合法或失败返回 None。"""
    if not name or not re.fullmatch(r"[A-Za-z0-9_]+", name):
        return None
    p = cache_root / f"{name}.png"
    hit = cached_icon(name, cache_root)
    if hit is not None:
        return hit
    req = urllib.request.Request(ICON_BASE.format(name=name), headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:      # noqa: S310
            blob = r.read()
        if not blob.startswith(b"\x89PNG"):
            return None
        p.parent.mkdir(parents=True, exist_ok=True)
        # OCR 六轮 #6：别直接写目标名 —— 多线程/预热脚本并发拉同一张时，
        # 读者可能读到写了一半的 PNG（≥300 字节的半截图还会被当缓存命中）。
        # 先写唯一临时名再 os.replace（同目录 rename 是原子操作）。
        tmp = p.with_name(f"{p.name}.{os.getpid()}.{threading.get_ident()}.part")
        try:
            tmp.write_bytes(blob)
            os.replace(tmp, p)
        finally:
            if tmp.exists():
                with contextlib.suppress(OSError):
                    tmp.unlink()
        return p
    except Exception as e:                                       # noqa: BLE001
        logger.warning("练度：图标 %s 拉取失败（%s）", name, e)
        return None


def level_of(entry: dict):
    """展柜条目的等级（enka 的 `propMap['4001'].val`）；字段缺失/形状意外都返回 None。

    OCR 六轮 #4：原写法 `(a.get("propMap") or {}).get("4001", {}).get("val")` 一旦
    `propMap` 不是 dict（上游改字段/返回字符串）就 AttributeError，整条命令不回消息。
    """
    pm = (entry or {}).get("propMap")
    lv = pm.get("4001") if isinstance(pm, dict) else None
    return (lv or {}).get("val") if isinstance(lv, dict) else None


def collect_icons(f: dict, cache_root=None, avatar_only: bool = False) -> dict:
    """下载这张面板需要的全部图标（头像/武器/五件圣遗物）；并行拉取，缓存命中即秒回。

    avatar_only=True 时只拉头像（花名册模式用，省 6 次请求）。
    """
    root = Path(cache_root) if cache_root else BASE / "genshin_cache"
    side = str(f.get("icon_name") or "")
    front = side.replace("_Side", "") if "_Side" in side else side
    wname = "" if avatar_only else str((f.get("weapon") or {}).get("icon") or "")
    rnames = [] if avatar_only else [str(r.get("icon") or "") for r in (f.get("relics") or [])]
    wanted = [n for n in dict.fromkeys([front, side, wname, *rnames]) if n]   # 去重保序
    got: dict = {}
    if wanted:
        with ThreadPoolExecutor(max_workers=4) as pool:
            for n, p in zip(wanted, pool.map(lambda x: icon_path(x, root), wanted)):
                got[n] = p
    return {"avatar": got.get(front) or (got.get(side) if side else None),
            "weapon": got.get(wname) if wname else None,
            "relics": [got.get(n) if n else None for n in rnames]}


def collect_avatar_icons(parsed: list, cache_root=None) -> list:
    """花名册批量头像（并行）；返回与 parsed 等长的列表（失败位为 None）。"""
    root = Path(cache_root) if cache_root else BASE / "genshin_cache"
    names = []
    for f in parsed:
        side = str(f.get("icon_name") or "")
        names.append(side.replace("_Side", "") if "_Side" in side else side)
    got: dict = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        for n, p in zip(names, pool.map(lambda x: icon_path(x, root) if x else None, names)):
            got[n] = p
    return [got.get(n) for n in names]


# ---------------- 渲染 ----------------

def _fit(img, box: int):
    """等比缩放进 box×box（公共件 `_cardimg.fit_box`）。"""
    return _cardimg.fit_box(img, box)


def _paste_round(im, path, cx, top, box, radius=14):
    """把一张图按圆角贴到（cx 居中, top）处，最大边缩到 box；失败静默。

    实际逻辑在公共件 `_cardimg.paste_icon`（圆角 mask ∩ 原图 alpha；练度是第一个用它的插件，
    卡片插件再贴头像/图标都走同一条路）。返回 (贴上的宽, 高)；失败 / 无图返回 None。
    """
    return _cardimg.paste_icon(im, path, cx, top, box, radius=radius, log=logger)


def render_panel(f: dict, meta: dict, icons: dict, out_path) -> bool:
    """练度卡片（900×600）；任何环节失败返回 False（调用方回退文本）。"""
    try:
        Image, ImageDraw, ImageFont = _cardimg.load_pil()
        f_name = _cardimg.font_cute(42)
        f_lv = _cardimg.font_cjk(19)
        f_label = _cardimg.font_cjk(15)
        f_val = _cardimg.font_cjk(27)
        f_sub = _cardimg.font_cjk(15)
        f_sec = _cardimg.font_cute(20)
        f_chip = _cardimg.font_cjk(18)
        f_star = _cardimg.font_cjk(21)
        f_small = _cardimg.font_cjk(14)
        f_body = _cardimg.font_cjk(16)
    except Exception as e:                                # noqa: BLE001
        logger.warning("练度：卡片字体加载失败（%s: %s）→ 回退文本", type(e).__name__, e)
        return False
    try:
        light, muted, gold = (232, 236, 248), (150, 158, 190), (222, 184, 96)
        green = (120, 205, 135)
        block_fill, block_line = (46, 52, 78), (88, 96, 134)
        top, bottom = (24, 28, 46), (40, 46, 72)

        # —— 圣遗物详情段：先量高度再画（卡片随词条折行变长；用户明说「可以增加图片长度」）——
        rows = (f.get("relics") or [])[:5]
        probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))
        row_lines = [wrap_subs(r.get("subs") or [], f_small, 646, probe) for r in rows]
        row_heights = [108 + max(0, len(ls) - 1) * 21 for ls in row_lines]
        sec_h = (48 + sum(row_heights) + 16) if rows else 0
        Hh = (588 + sec_h + 40) if rows else H
        im = Image.new("RGB", (W, Hh), top)
        d = ImageDraw.Draw(im)
        for y in range(Hh):
            t = y / (Hh - 1)
            d.line([(0, y), (W, y)],
                   fill=tuple(int(a + (b - a) * t) for a, b in zip(top, bottom)))
        # 头部
        d.text((30, 34), f"UID {meta.get('uid', '?')} · {meta.get('nickname', '旅行者')}",
               font=f_body, fill=muted, anchor="lm")
        d.text((W - 30, 34), "Enka 展柜快照 · 可莉面板", font=f_body, fill=muted, anchor="rm")

        # 三块底板
        for x0, x1 in ((24, 300), (316, 600), (616, 876)):
            d.rounded_rectangle([x0, 62, x1, 560], radius=18, fill=block_fill,
                                outline=block_line, width=2)

        # —— 左：头像 / 名字 / 等级 / 元素 / 星级
        av = icons.get("avatar")
        if av is not None:
            # 圆角 + 底部渐隐（fade 让头像底边融进左栏底色，别像硬贴的方块）
            res = _cardimg.paste_icon(im, av, 162, 90, 232, radius=16,
                                      fade=64, fade_color=block_fill, log=logger)
            done = res is not None
        else:
            done = False
        if not done:
            d.rounded_rectangle([46, 90, 278, 322], radius=16, outline=block_line, width=2)
            d.text((162, 206), "（头像未取到）", font=f_label, fill=muted, anchor="mm")
        d.text((162, 350), f["name"], font=f_name, fill=light, anchor="mm")
        d.text((162, 386), f"Lv.{f['level']} · C{f['const']} · 好感 {f['fetter']}",
               font=f_lv, fill=muted, anchor="mm")
        elem = f.get("element") or ""
        if elem:
            chip = f"{ELEMENT_CN.get(elem, elem)}元素"
            col = ELEMENT_COLOR.get(elem, muted)
            tw = d.textlength(chip, font=f_chip)
            d.rounded_rectangle([162 - tw / 2 - 16, 404, 162 + tw / 2 + 16, 438],
                                radius=17, outline=col, width=2)
            d.text((162, 421), chip, font=f_chip, fill=col, anchor="mm")
        stars = QUALITY_STARS.get(str(f.get("quality")), 5)
        d.text((162, 462), "★" * stars, font=f_star, fill=gold, anchor="mm")
        # 命座点：6 颗，点亮的 = 已解锁的那几层（顺手填住左下留白）
        for i in range(6):
            x = 162 - 100 + i * 40
            if i < f["const"]:
                d.ellipse([x - 7, 492, x + 7, 506], fill=gold)
            else:
                d.ellipse([x - 7, 492, x + 7, 506], outline=block_line, width=2)

        # —— 中：面板属性（2×4）+ 天赋
        d.text((326, 84), "面板属性", font=f_sec, fill=gold, anchor="lm")
        st = f["stats"]
        if st["dmg_bonus"]:
            dmg_cell = ("元素伤害", f"{st['dmg_bonus'] * 100:.1f}%",
                        f"物伤 {st['phys'] * 100:.1f}%" if st["phys"] else "")
        elif st["phys"]:
            dmg_cell = ("物伤", f"{st['phys'] * 100:.1f}%", "")
        else:
            dmg_cell = ("元素伤害", "无", "")
        cells = [
            ("生命值", f"{st['hp']:,.0f}", f"加成 {st['hp'] - st['hp_base']:,.0f}"),
            ("攻击力", f"{st['atk']:,.0f}", f"加成 {st['atk'] - st['atk_base']:,.0f}"),
            ("防御力", f"{st['def']:,.0f}", f"加成 {st['def'] - st['def_base']:,.0f}"),
            ("元素精通", f"{st['em']:.0f}", ""),
            ("暴击率", f"{st['crit'] * 100:.1f}%", ""),
            ("暴击伤害", f"{st['crit_dmg'] * 100:.1f}%", ""),
            ("元素充能", f"{st['er'] * 100:.1f}%", ""),
            dmg_cell,
        ]
        for i, (label, val, sub) in enumerate(cells):
            x = 326 + (i % 2) * 138
            y = 116 + (i // 2) * 96
            d.text((x, y), label, font=f_label, fill=muted, anchor="lm")
            d.text((x, y + 30), val, font=f_val, fill=(muted if val == "无" else light), anchor="lm")
            if sub:
                d.text((x, y + 58), sub, font=f_sub, fill=green, anchor="lm")
        for i, (kind, lv) in enumerate((f.get("skills") or [])[:3]):
            x0 = 326 + i * 92
            d.rounded_rectangle([x0, 492, x0 + 80, 528], radius=16, outline=block_line, width=2)
            d.text((x0 + 40, 510), f"{kind} {lv}", font=f_chip, fill=light, anchor="mm")

        # —— 右：武器
        d.text((628, 84), "武器", font=f_sec, fill=gold, anchor="lm")
        w = f.get("weapon") or {}
        wi = icons.get("weapon")
        if wi is not None:
            try:
                with Image.open(wi) as wi2:
                    wi2 = wi2.convert("RGBA")
                    fitted = _fit(wi2, 88)
                    if fitted:
                        im.paste(fitted, (628 + 44 - fitted.size[0] // 2, 100 + 44 - fitted.size[1] // 2), fitted)
            except Exception as e:                            # noqa: BLE001
                logger.debug("练度：武器图标粘贴跳过（%s）", e)
        d.text((734, 112), str(w.get("name") or "未装备")[:9], font=f_chip, fill=light, anchor="lm")
        if w:
            d.text((734, 140), f"{'★' * int(w.get('star', 4))} R{w.get('affix', 1)}", font=f_body,
                   fill=gold, anchor="lm")
            stats = w.get("stats") or []
            if stats:
                d.text((734, 166), f"{stats[0][0]} {stats[0][1]}", font=f_body, fill=muted, anchor="lm")
            if len(stats) > 1:
                d.text((734, 190), f"{stats[1][0]} {stats[1][1]}", font=f_body, fill=muted, anchor="lm")
        else:
            d.text((734, 140), "（展柜没带武器数据）", font=f_sub, fill=muted, anchor="lm")

        # —— 右：圣遗物
        d.text((628, 232), "圣遗物", font=f_sec, fill=gold, anchor="lm")
        sets = " · ".join(f"{k}×{v}" for k, v in (f.get("set_count") or {}).items())
        if not sets:
            sets = "（没有带圣遗物数据）"
        line, lines = "", []
        for ch in sets:
            if d.textlength(line + ch, font=f_body) > 238:
                lines.append(line)
                line = ch
                if len(lines) == 2:
                    break
            else:
                line += ch
        if line and len(lines) < 2:
            lines.append(line)
        for i, ln in enumerate(lines[:2]):
            d.text((628, 258 + i * 24), ln, font=f_body, fill=muted, anchor="lm")

        y0 = 316
        for i, r in enumerate((f.get("relics") or [])[:5]):
            yi = y0 + i * 44
            ri = (icons.get("relics") or [None] * 5)[i] if i < len(icons.get("relics") or []) else None
            if ri is not None:
                try:
                    with Image.open(ri) as r2:
                        r2 = r2.convert("RGBA")
                        fitted = _fit(r2, 30)
                        if fitted:
                            im.paste(fitted, (628 + 15 - fitted.size[0] // 2, yi + 15 - fitted.size[1] // 2), fitted)
                except Exception as e:                         # noqa: BLE001
                    logger.debug("练度：圣遗物图标粘贴跳过（%s）", e)
            main_name = PROP_SHORT.get(r["main"][0], r["main"][0])
            if main_name.endswith("%"):
                base = main_name[:-1]
                main_name = (PROP_SHORT.get(base) or base.replace("力", "").replace("值", "")) + "%"
            else:
                main_name = main_name.replace("力", "").replace("值", "")
                main_name = main_name.replace("元素伤害加成", "元素伤").replace("伤害加成", "伤")
            tx = 632 + 30 + 8
            d.text((tx, yi + 15), f"{r['piece']} +{r['level']}", font=f_body, fill=muted, anchor="lm")
            d.text((866, yi + 15), f"{main_name} {r['main'][1]}", font=f_body, fill=light, anchor="rm")

        if f.get("relics"):
            total_txt = f"{float(f.get('crit_score') or 0):.1f}（2×暴击+爆伤）"
            label = "副词条双爆分"
            if 628 + d.textlength(label, font=f_sub) + 10 > 866 - d.textlength(total_txt, font=f_sub):
                label = "双爆分"          # 三位数总分时收紧标签，别压住首位数字（2026-10-16 实测）
            d.text((628, 535), label, font=f_sub, fill=muted, anchor="lm")
            d.text((866, 535), total_txt, font=f_sub, fill=gold, anchor="rm")

        # —— 圣遗物详情：五件全信息 + 每件双爆分/评级 ——
        if rows:
            y0 = 588
            d.rounded_rectangle([24, y0, 876, y0 + sec_h], radius=18, fill=block_fill,
                                outline=block_line, width=2)
            d.text((40, y0 + 26), "圣遗物详情", font=f_sec, fill=gold, anchor="lm")
            d.text((858, y0 + 26), "评分 = 双爆分（2×暴击率 + 暴击伤害）", font=f_small,
                   fill=muted, anchor="rm")
            relic_icons = icons.get("relics") or []
            ry = y0 + 48
            for i, r in enumerate(rows):
                rh = row_heights[i]
                ri = relic_icons[i] if i < len(relic_icons) else None
                if ri is not None:
                    try:
                        with Image.open(ri) as r2:
                            r2 = r2.convert("RGBA")
                            fitted = _fit(r2, 52)
                            if fitted:
                                im.paste(fitted, (74 - fitted.size[0] // 2,
                                                  ry + 34 - fitted.size[1] // 2), fitted)
                    except Exception as e:                # noqa: BLE001
                        logger.debug("练度：详情图标跳过（%s）", e)
                else:
                    d.rounded_rectangle([48, ry + 8, 100, ry + 60], radius=10,
                                        outline=block_line, width=1)
                # 行 1：部位 · 套装 +20 ★5
                xx, yy1 = 116, ry + 18
                for text, color, fnt in ((str(r.get("piece") or ""), gold, f_body),
                                         (" · ", muted, f_body),
                                         (str(r.get("set") or ""), light, f_body),
                                         (f" +{r.get('level', 0)}", muted, f_body),
                                         (f" ★{r.get('star', 5)}", muted, f_small)):
                    d.text((xx, yy1), text, font=fnt, fill=color, anchor="lm")
                    xx += d.textlength(text, font=fnt)
                # 行 2：主词条
                d.text((116, ry + 44), "主词条 ", font=f_small, fill=muted, anchor="lm")
                m_x = 116 + d.textlength("主词条 ", font=f_small)
                d.text((m_x, ry + 44), f"{r['main'][0]} {r['main'][1]}", font=f_small,
                       fill=light, anchor="lm")
                # 行 3：副词条（按段折行；双爆金色高亮）
                ly = ry + 66
                for line in row_lines[i]:
                    lx = 116
                    for u_i, (un, uv, uc) in enumerate(line):
                        if u_i:
                            d.text((lx, ly), " · ", font=f_small, fill=block_line, anchor="lm")
                            lx += d.textlength(" · ", font=f_small)
                        d.text((lx, ly), un + " ", font=f_small,
                               fill=(222, 184, 96) if uc else muted, anchor="lm")
                        lx += d.textlength(un + " ", font=f_small)
                        d.text((lx, ly), uv, font=f_small, fill=gold if uc else light, anchor="lm")
                        lx += d.textlength(uv, font=f_small)
                    ly += 21
                # 评分块：评级章 + 双爆分
                cv = float(r.get("crit_score") or 0)
                g = grade_of(cv)
                d.rounded_rectangle([826, ry + 8, 858, ry + 34], radius=8,
                                    fill=GRADE_COLOR.get(g, muted))
                d.text((842, ry + 22), g, font=f_body, fill=(30, 30, 52), anchor="mm")
                d.text((858, ry + 64), f"{cv:.1f}", font=f_val, fill=gold, anchor="rm")
                d.text((858, ry + 88), "双爆分", font=f_small, fill=muted, anchor="rm")
                if i < len(rows) - 1:
                    d.line([40, ry + rh - 8, 860, ry + rh - 8], fill=block_line, width=1)
                ry += rh

        # 底部
        d.text((W // 2, (Hh - 20) if rows else 578), "数据来自 enka.network 展柜快照 · 仅供参考",
               font=f_small, fill=muted, anchor="mm")
        _cardimg.save_img(im, out_path)
        return True
    except Exception as e:                               # noqa: BLE001
        logger.warning("练度：卡片渲染失败（%s: %s）→ 回退文本", type(e).__name__, e)
        return False


def render_roster(parsed: list, meta: dict, icons_list: list, out_path) -> bool:
    """展柜一览花名册卡（最多 8 位，4×2 格）；任何环节失败返回 False（调用方回退文本）。"""
    try:
        Image, ImageDraw, ImageFont = _cardimg.load_pil()
        f_head = _cardimg.font_cjk(16)
        f_name = _cardimg.font_cute(26)
        f_sub = _cardimg.font_cjk(16)
        f_el = _cardimg.font_cjk(15)
        f_foot = _cardimg.font_cjk(14)
    except Exception as e:                                # noqa: BLE001
        logger.warning("练度：花名册字体加载失败（%s: %s）→ 回退文本", type(e).__name__, e)
        return False
    try:
        light, muted = (232, 236, 248), (150, 158, 190)
        block_fill, block_line = (46, 52, 78), (88, 96, 134)
        top, bottom = (24, 28, 46), (40, 46, 72)
        im = Image.new("RGB", (W, H), top)
        d = ImageDraw.Draw(im)
        for y in range(H):
            t = y / (H - 1)
            d.line([(0, y), (W, y)],
                   fill=tuple(int(a + (b - a) * t) for a, b in zip(top, bottom)))
        d.text((30, 34), f"UID {meta.get('uid', '?')} · {meta.get('nickname', '旅行者')}",
               font=f_head, fill=muted, anchor="lm")
        d.text((W - 30, 34), f"展柜一览 · 共 {min(len(parsed), 8)} 位", font=f_head, fill=muted,
               anchor="rm")
        cols, cw, gx, gy, chh, y0 = 4, 207, 12, 8, 242, 64
        rows_used = max(1, (min(len(parsed), 8) + cols - 1) // cols)
        y0 += ((2 - rows_used) * (chh + gy)) // 2          # 行数不够时垂直居中，别留半张死白
        x0 = (W - (cols * cw + (cols - 1) * gx)) // 2
        for i, f in enumerate(parsed[:8]):
            r, c = divmod(i, cols)
            cx0 = x0 + c * (cw + gx)
            cy0 = y0 + r * (chh + gy)
            d.rounded_rectangle([cx0, cy0, cx0 + cw, cy0 + chh], radius=16, fill=block_fill,
                                outline=block_line, width=2)
            ccx = cx0 + cw // 2
            ic = icons_list[i] if i < len(icons_list) else None
            if _paste_round(im, ic, ccx, cy0 + 12, 132, radius=14) is None:
                d.rounded_rectangle([ccx - 66, cy0 + 12, ccx + 66, cy0 + 144], radius=14,
                                    outline=block_line, width=2)
            d.text((ccx, cy0 + 168), str(f["name"])[:7], font=f_name, fill=light, anchor="mm")
            d.text((ccx, cy0 + 196), f"Lv.{f['level']} · C{f['const']}", font=f_sub, fill=muted,
                   anchor="mm")
            elem = f.get("element") or ""
            if elem:
                d.text((ccx, cy0 + 222), f"{ELEMENT_CN.get(elem, elem)}元素", font=f_el,
                       fill=ELEMENT_COLOR.get(elem, muted), anchor="mm")
        d.text((W // 2, 578), "数据来自 enka.network 展柜快照 · 可莉面板", font=f_foot,
               fill=muted, anchor="mm")
        _cardimg.save_img(im, out_path)
        return True
    except Exception as e:                               # noqa: BLE001
        logger.warning("练度：花名册渲染失败（%s: %s）→ 回退文本", type(e).__name__, e)
        return False


# ---------------- 命令 ----------------

def register(api):
    settings = dict(getattr(api, "settings", None) or {})

    async def cmd(ctx):
        parts = (ctx.arg_text or "").split()
        if not parts or not parts[0].isdigit():
            await ctx.reply("用法：.练度 <UID> [角色名]\n比如：.练度 100000000 可莉\n"
                            "（展柜里要放上想看的角色，没填角色名可莉会列出展柜名单）")
            return
        uid = parts[0]
        if not 9 <= len(uid) <= 10:
            await ctx.reply("UID 是 9 位数字哦，再检查一下～")
            return
        query = " ".join(parts[1:]).strip()
        try:
            data = await asyncio.to_thread(get_enka, uid)
        except EnkaError as e:
            await ctx.reply(f"呜呜，没查到：{e}")
            return
        avatar_list = data.get("avatarInfoList") or []
        if not avatar_list:
            await ctx.reply(f"UID {uid} 的展柜空空如也～在游戏「角色展柜」里放几个角色，可莉就能看啦！")
            return
        nickname = str((data.get("playerInfo") or {}).get("nickname") or "旅行者")
        if query in ("全部", "所有", "名单", "全", "all"):
            try:                                    # OCR 六轮 #4：解析失败要给一句人话，不能整条命令哑掉
                parsed = await asyncio.to_thread(lambda: [parse_char(a) for a in avatar_list[:8]])
            except Exception as e:                                   # noqa: BLE001
                logger.warning("练度：花名册解析失败（%s: %s）", type(e).__name__, e)
                await ctx.reply("诶……这份展柜数据看得可莉有点懵，换个角色或稍后再试试？")
                return
            icons_list = await asyncio.to_thread(collect_avatar_icons, parsed)
            try:
                out = _cardimg.card_path(_cardimg.cards_dir(settings, BASE), "panel-roster",
                                         ctx.user_id)
            except OSError as e:
                logger.warning("练度：卡片目录不可用（%s）→ 回退文本", e)
                out = None
            if out is not None and await asyncio.to_thread(
                    render_roster, parsed, {"nickname": nickname, "uid": uid}, icons_list, out):
                caption = f"🏵️ {nickname} 的展柜一览（{len(parsed)} 位）"
                if await _cardimg.send_card(ctx, out, caption, pattern="panel-roster-*.png",
                                            keep=KEEP_CARDS):
                    return
            lines = [f"· {f['name']}（Lv.{f['level']} · C{f['const']}）" for f in parsed]
            await ctx.reply(f"🏵️ {nickname} 的展柜一览：\n" + "\n".join(lines))
            return
        if not query:
            lines = []
            for a in avatar_list[:8]:
                info = chars_db().get(str(a.get("avatarId"))) or {}
                lv = level_of(a) or "?"
                lines.append(f"· {info.get('n') or a.get('avatarId')}（Lv.{lv}）")
            await ctx.reply(f"🏵️ {nickname} 的展柜里有：\n" + "\n".join(lines)
                            + f"\n\n想看谁？发：.练度 {uid} 角色名（或 .练度 {uid} 全部 看花名册卡）")
            return
        target = pick_avatar(avatar_list, query)
        if isinstance(target, str):
            await ctx.reply(target)
            return
        try:
            f = await asyncio.to_thread(parse_char, target)
        except Exception as e:                                   # noqa: BLE001
            logger.warning("练度：解析失败（%s: %s）", type(e).__name__, e)
            await ctx.reply("诶……这份展柜数据看得可莉有点懵，换个角色或稍后再试试？")
            return
        try:
            out = _cardimg.card_path(_cardimg.cards_dir(settings, BASE), "panel", ctx.user_id)
        except OSError as e:
            logger.warning("练度：卡片目录不可用（%s）→ 回退文本", e)
            out = None
        if out is not None:
            key = _cardimg.rcache_key("panel", PANEL_CACHE_V, uid,
                                      json.dumps(f, sort_keys=True, ensure_ascii=False))

            def _render():                       # 图标收集也裹进来：缓存命中时连下载都省
                icons = collect_icons(f)
                return render_panel(f, {"nickname": nickname, "uid": uid}, icons, out)

            if await asyncio.to_thread(_cardimg.rcache_render, settings, BASE, "panel", key, out,
                                       _render):
                caption = f"🏵️ {nickname} 的「{f['name']}」练度面板（UID {uid}）"
                if await _cardimg.send_card(ctx, out, caption, pattern="panel-*.png", keep=KEEP_CARDS):
                    return
        w = f.get("weapon") or {}
        sets = "、".join(f"{k}×{v}" for k, v in (f.get("set_count") or {}).items()) or "无"
        st = f["stats"]
        await ctx.reply(
            f"🏵️ {f['name']} Lv.{f['level']} C{f['const']}｜{ELEMENT_CN.get(f.get('element'), '?')}元素\n"
            f"暴击 {st['crit'] * 100:.1f}% · 爆伤 {st['crit_dmg'] * 100:.1f}% · 充能 {st['er'] * 100:.1f}%\n"
            f"武器：{w.get('name', '未装备')} R{w.get('affix', 1)}｜圣遗物：{sets}")

    api.on_command("练度", cmd, aliases=("面板", "练度查询", "panel"), cooldown=20,
                   help=".练度 <UID> [角色名|全部] —— 查原神角色练度（发面板卡/花名册）")
