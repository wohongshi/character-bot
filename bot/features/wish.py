"""可莉祈愿：原神风格的抽卡模拟器 —— ``.祈愿`` / ``.祈愿武器`` / ``.祈愿常驻`` / ``.祈愿统计``。

模拟真游戏的祈愿机制（口径按官方公开数据，纯娱乐）。三个池（2026-10-17 起）：
- **角色池**（``.祈愿``）：5★ 基础 0.6%；第 74 抽起每抽 +6%；90 抽必得；
  50% 为本期限定「可莉」，歪了下次必中（大保底）
- **武器池**（``.祈愿武器``）：5★ 基础 0.7%；第 63 抽起每抽 +7%；80 抽必得；
  UP 75%（四风原典 / 尘世之锁 两把均分，虚构当期）；**定轨**：设一把，歪 1 次后下个金必中
  （``.定轨 1|2|名字`` 设置/查看）
- **常驻池**（``.祈愿常驻``）：5★ 基础 0.6%；第 74 抽起提升；90 抽必得；角色+武器大混池、无 UP
- 4★：角色池 5.1% / 武器池 6%；第 9 抽起提升；10 抽内必得 4★ 或以上

命令：
- ``.祈愿`` 单抽卡片；``.祈愿 10`` / ``.祈愿十连`` 十连卡片；``.祈愿 N``（2~300）大样本模拟
- ``.祈愿武器 [1|10|N]`` / ``.祈愿常驻 [1|10|N]`` 武器池 / 常驻池
- ``.祈愿统计 [武器|常驻]`` 战绩卡；``.祈愿概率 [武器|常驻]`` 概率公示（纯文本）
- ``.祈愿记录 [武器|常驻] [N]`` 最近 N 抽流水（默认 20 / 上限 50；日志滚动 200 条）

记账分池存 ``state/wish.json``（``users.<uid>.pools.{char,weapon,standard}``；老结构自动迁移）。
卡片渲染失败 / 发送失败一律回退纯文本（fail-soft）；``settings.card=false`` 只发文本。
"""

import asyncio
import json
import logging
import os
import random
import sys
import time
from pathlib import Path

# _cardimg.py 与插件同目录（兜底 sys.path，见 gacha.py 同一段说明）
_HERE = str(Path(__file__).resolve().parent)
if _HERE not in sys.path:
    sys.path.append(_HERE)
import _cardimg  # noqa: E402

logger = logging.getLogger(__name__)

NAME = "祈愿"
DESCRIPTION = "原神风祈愿模拟器：.祈愿 / .祈愿武器 / .祈愿常驻 / .定轨（三池保底记账，发卡片）"
VERSION = "1.3"

# 角色限定：只有这些角色启用；空 = 通用插件（可在面板关闭）
CHARACTERS = ('klee', 'hutao', 'citlali')


def _state_root() -> Path:
    """状态目录：`KLEE_STATE_DIR` 可覆盖（干跑测试台靠它把状态写到临时目录）。"""
    base = os.environ.get("KLEE_STATE_DIR")
    return Path(base) if base else Path(__file__).resolve().parent / "state"


BASE = _state_root()
STATE_FILE = BASE / "wish.json"

# ---------- 概率模型（真游戏口径） ----------

P5_BASE, P5_SOFT_START, P5_STEP, P5_HARD = 0.006, 74, 0.06, 90
P4_BASE, P4_SOFT_START, P4_STEP, P4_HARD = 0.051, 9, 0.51, 10

UP5 = ("可莉",)
STD5 = ("七七", "刻晴", "莫娜", "迪卢克", "琴", "提纳里")
UP4 = ("诺艾尔", "菲谢尔", "砂糖")
STD4 = ("芭芭拉", "班尼特", "香菱", "行秋", "凝光", "重云", "雷泽", "北斗")
W3 = ("冷刃", "暗铁剑", "飞天御剑", "黑缨枪", "鸦羽弓", "弹弓", "神射手之誓",
      "讨龙英杰谭", "魔导绪论", "以理服人", "沐浴龙血的剑", "铁影阔剑")

# —— 武器池 / 常驻池内容（2026-10-17 新增；娱乐向模拟池，非游戏现行卡池）——
W5_UP = ("四风原典", "尘世之锁")                   # 可莉主题法器双 UP（各 37.5%）
W5_STD = ("阿莫斯之弓", "天空之翼", "天空之卷", "天空之脊", "天空之刃",
          "狼的末路", "风鹰剑", "和璞鸢")           # 常驻五★武器
W4_UP = ("流浪乐章", "匣里日月", "万国诸海图谱")
W4_STD = ("祭礼剑", "西风剑", "笛剑", "西风大剑", "雨裁", "钟剑",
          "西风长枪", "匣里灭辰", "西风猎弓", "弓藏")

POOLS = {
    "char": {
        "label": "角色池",
        "up5": UP5, "std5": STD5, "up4": UP4, "std4": STD4, "w3": W3,
        "p5": (P5_BASE, P5_SOFT_START, P5_STEP, P5_HARD),
        "p4": (P4_BASE, P4_SOFT_START, P4_STEP, P4_HARD),
        "up5_rate": 0.5, "up4_rate": 0.5,
        "guarantee5": True,               # 大保底：歪了下次必中
        "epi": False,                     # 定轨（仅武器池）
    },
    "weapon": {
        "label": "武器池",
        "up5": W5_UP, "std5": W5_STD, "up4": W4_UP, "std4": W4_STD, "w3": W3,
        "p5": (0.007, 63, 0.07, 80),      # 0.7% / 63 起 +7% / 80 必得
        "p4": (0.06, 9, 0.6, 10),
        "up5_rate": 0.75, "up4_rate": 0.5,
        "guarantee5": False,
        "epi": True,
    },
    "standard": {
        "label": "常驻池",
        "up5": None, "std5": STD5 + ("迪希雅",) + W5_STD,
        "up4": None, "std4": STD4 + W4_STD,
        "w3": W3,
        "p5": (0.006, 74, 0.06, 90),
        "p4": (0.051, 9, 0.51, 10),
        "up5_rate": 0.0, "up4_rate": 0.0,
        "guarantee5": False,
        "epi": False,
    },
}

POOL_ALIASES = {"角色": "char", "角色池": "char", "char": "char",
                "武器": "weapon", "武器池": "weapon", "weapon": "weapon",
                "常驻": "standard", "常驻池": "standard", "standard": "standard"}

MAX_SIM = 300                     # `.祈愿 N` 单次模拟上限（再大就按 300 算）


def rate5(count: int, pool: str = "char") -> float:
    """已垫 count 抽后，本抽出 5★ 的概率（按池概率表）。角色池 74 起 +6%/90 必得；
    武器池 63 起 +7%/80 必得；常驻池同角色池。"""
    base, soft, step, hard = POOLS.get(pool, POOLS["char"])["p5"]
    i = count + 1
    if i >= hard:
        return 1.0
    if i < soft:
        return base
    return min(1.0, base + step * (i - soft + 1))


def rate4(count: int, pool: str = "char") -> float:
    """已垫 count 抽后，本抽出 4★（或以上）的概率（按池概率表；9 抽起提升，10 抽必得）。"""
    base, soft, step, hard = POOLS.get(pool, POOLS["char"])["p4"]
    i = count + 1
    if i >= hard:
        return 1.0
    if i < soft:
        return base
    return min(1.0, base + step * (i - soft + 1))


# ---------- 状态读写（模式同 affinity.py：损坏备份 / 原子写） ----------

def _num(v, default: int = 0) -> int:
    """安全取整数（手改/半损状态不炸整条命令；bool 不当数字用）。"""
    if isinstance(v, bool):
        return default
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _flag(v) -> bool:
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def _load() -> dict:
    try:
        d = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as e:
        logger.warning("祈愿：状态读取失败（%s: %s）→ 备份后重来", type(e).__name__, e)
        try:
            STATE_FILE.replace(STATE_FILE.with_name(f"wish.corrupt-{int(time.time())}.json"))
        except OSError:
            pass
        return {}


def _save(d: dict) -> None:
    """原子写（pid+微秒后缀 tmp）；失败只记日志 —— 抽卡结果不能因为写盘失败就不回。"""
    try:
        BASE.mkdir(parents=True, exist_ok=True)
        tmp = STATE_FILE.with_name(f"{STATE_FILE.name}.{os.getpid()}-{time.time_ns() % 1_000_000}.tmp")
        tmp.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        tmp.replace(STATE_FILE)
    except OSError as e:
        logger.warning("祈愿：状态保存失败（%s: %s）", type(e).__name__, e)


_LEGACY_KEYS = ("total", "c5", "c4", "off5", "count5", "count4", "guaranteed5",
                "guaranteed4", "sum_pity5", "last5", "log")


def _new_pool_state() -> dict:
    """单池的空白记账。"""
    return {"total": 0, "c5": 0, "c4": 0, "off5": 0, "count5": 0, "count4": 0,
            "guaranteed5": False, "guaranteed4": False, "sum_pity5": 0,
            "last5": [], "log": [], "epi_choice": 1, "epi_misses": 0}


def _pool_state(u: dict, pool: str = "char") -> dict:
    """取用户节点里某池的记账；**老结构（顶层字段就是角色池）自动迁移**。

    迁移是一次性的：老 `{total, c5, …}` 顶层字段收进 ``pools.char`` 后删除，
    之后所有读写都走 ``pools.<pool>``。坏节点（被手改坏）当空白重建。
    """
    pools = u.get("pools")
    if not isinstance(pools, dict):
        ps = _new_pool_state()
        for k in _LEGACY_KEYS:                      # 老结构：顶层字段 = 角色池数据
            if k in u:
                ps[k] = u[k]
                u.pop(k, None)
        u["pools"] = {"char": ps}
        pools = u["pools"]
    p = pools.get(pool)
    if not isinstance(p, dict):
        p = pools[pool] = _new_pool_state()
    return p


def _entry(d: dict, uid, pool: str = "char") -> dict:
    """取某人某池的记录；坏条目就地重置（手改/半损容错）。默认角色池（向后兼容）。"""
    users = d.get("users")
    if not isinstance(users, dict):
        users = d["users"] = {}
    u = users.get(str(uid))
    if not isinstance(u, dict):
        u = users[str(uid)] = {}
    return _pool_state(u, pool)


def _log_push(u: dict, star: int, name: str, pity: int = 0) -> None:
    """滚动流水：最近 200 抽（``[抽数, 星级, 名字, 出金垫抽(仅5★)]``）。"""
    log = u.get("log")
    log = log if isinstance(log, list) else []
    u["log"] = log[-199:] + [[_num(u.get("total")), int(star), str(name), int(pity)]]


# ---------- 抽卡核心 ----------

def _pick(pool, rng):
    """从池里挑一个；rng 理论值域 [0,1)，1.0 也夹在界内。"""
    return pool[min(int(rng() * len(pool)), len(pool) - 1)]


def pull_one(u: dict, rng, pool: str = "char") -> dict:
    """抽一抽：就地更新 u 的垫抽/保底/统计，返回 ``{"star","name","up"[, "guar","epi","pity"]}``。

    `pool`：``char``（默认，角色池）/ ``weapon``（武器池，定轨制）/ ``standard``（常驻池）。

    对 rng 的调用顺序（测试用固定序列时按这个来；三池结构一致）：
    - 出 5★：roll5（未到硬保底才调）→ up 判定（恒调一次）→ 池内挑名
    - 出 4★：roll5 → roll4（未到硬保底才调）→ up4 → 池内挑名
    - 出 3★：roll5 → roll4 → 池内挑名
    保底命中（硬保底那一抽）不消耗对应的 roll 调用；「定轨已就绪」时第 5★ 名是指定武器、
    不消耗「挑名」那次 rng。up 判定恒调一次。
    """
    cfg = POOLS.get(pool, POOLS["char"])
    p5_hard = cfg["p5"][3]
    p4_hard = cfg["p4"][3]
    u["total"] = _num(u.get("total")) + 1

    c5 = _num(u.get("count5")) + 1
    if c5 >= p5_hard or rng() < rate5(c5 - 1, pool):
        epi_hit = None
        if pool == "weapon":
            up_roll = rng() < cfg["up5_rate"]               # 恒调：序列 rng 的调用次数才可预测
            choices = cfg["up5"]
            choice = _num(u.get("epi_choice"), 1)
            choice = choice if 1 <= choice <= len(choices) else 1
            epi_ready = _num(u.get("epi_misses")) >= 1
            if epi_ready:                                   # 命定就绪：直接命中定轨，不挑名
                name, up, epi_hit, was_guar = choices[choice - 1], True, True, True
                u["epi_misses"] = 0
            else:
                was_guar = False
                if up_roll:
                    name = _pick(choices, rng)
                    up, epi_hit = True, (name == choices[choice - 1])
                else:
                    name = _pick(cfg["std5"], rng)
                    up, epi_hit = False, False
                u["epi_misses"] = 0 if epi_hit else 1       # 非定轨 → 命定 +1（封顶 1）
            u["guaranteed5"] = _num(u.get("epi_misses")) >= 1
        elif pool == "standard":
            rng()                                           # 恒调一次，与其它池结构一致（结果弃用）
            name, up, was_guar = _pick(cfg["std5"], rng), False, False
        else:
            was_guar = _flag(u.get("guaranteed5"))
            up_roll = rng() < 0.5                           # 恒调：序列 rng 的调用次数才可预测
            up = was_guar or up_roll
            name = _pick(cfg["up5"] if up else cfg["std5"], rng)
            u["guaranteed5"] = not up
        u["count5"] = 0
        u["count4"] = 0                                     # 5★ 也算「4★ 或以上」保底命中
        u["c5"] = _num(u.get("c5")) + 1
        u["sum_pity5"] = _num(u.get("sum_pity5")) + c5
        if pool == "weapon":
            if not epi_hit:
                u["off5"] = _num(u.get("off5")) + 1         # 武器池「歪」= 没中定轨
        elif pool == "char" and not up:
            u["off5"] = _num(u.get("off5")) + 1
        rec = {"name": name, "up": bool(up), "pity": c5, "total": u["total"],
               "ts": int(time.time())}
        if epi_hit is not None:
            rec["epi"] = bool(epi_hit)
        last = u.get("last5")
        last = last if isinstance(last, list) else []
        u["last5"] = last[-9:] + [rec]
        _log_push(u, 5, name, c5)
        out = {"star": 5, "name": name, "up": bool(up), "guar": was_guar, "pity": c5}
        if epi_hit is not None:
            out["epi"] = bool(epi_hit)
        return out

    c4 = _num(u.get("count4")) + 1
    if c4 >= p4_hard or rng() < rate4(c4 - 1, pool):
        if pool == "standard":
            rng()                                           # 恒调：同上
            name, up = _pick(cfg["std4"], rng), False
        else:
            was_guar = _flag(u.get("guaranteed4"))
            up_roll = rng() < 0.5                           # 恒调：同上
            up = was_guar or up_roll
            name = _pick(cfg["up4"] if up else cfg["std4"], rng)
            u["guaranteed4"] = not up
        u["count5"] = c5
        u["count4"] = 0
        u["c4"] = _num(u.get("c4")) + 1
        _log_push(u, 4, name)
        return {"star": 4, "name": name, "up": bool(up)}

    u["count5"] = c5
    u["count4"] = c4
    name = _pick(cfg["w3"], rng)
    _log_push(u, 3, name)
    return {"star": 3, "name": name}


def pull_many(u: dict, n: int, rng=None, pool: str = "char") -> list:
    """连抽 n 次（n<=0 返回空列表、不动状态）。rng / pool 可注入（测试用）。"""
    n = _num(n)
    if n <= 0:
        return []
    rng = rng or random.random
    return [pull_one(u, rng, pool) for _ in range(n)]


# ---------- 战绩统计 ----------

def luck_title(avg, c5: int) -> str:
    """欧非鉴定（卡片上也用，不放 emoji —— 字体覆盖不了会变豆腐块）。"""
    if not c5 or avg is None:
        return "初来报到的旅行者"
    if avg <= 45:
        return "欧皇"
    if avg <= 60:
        return "小欧"
    if avg <= 75:
        return "不欧不非"
    if avg <= 85:
        return "非酋预备"
    return "非酋之王"


def stats_of(u: dict) -> dict:
    """给渲染/文案用的纯数据视图（坏值全部安全兜底）。"""
    total, c5, c4 = _num(u.get("total")), _num(u.get("c5")), _num(u.get("c4"))
    off5 = _num(u.get("off5"))
    avg = (_num(u.get("sum_pity5")) / c5) if c5 else None
    last = u.get("last5")
    last = [r for r in last if isinstance(r, dict) and isinstance(r.get("name"), str)] \
        if isinstance(last, list) else []
    return {"total": total, "c5": c5, "c4": c4, "off5": off5, "avg": avg,
            "count5": _num(u.get("count5")), "count4": _num(u.get("count4")),
            "guaranteed5": _flag(u.get("guaranteed5")), "title": luck_title(avg, c5),
            "last5": last, "epi_choice": _num(u.get("epi_choice"), 1),
            "epi_misses": _num(u.get("epi_misses"))}


def rank_rows(d: dict, pool: str = "char") -> list:
    """欧非榜数据：按「平均出金抽数」升序（只含出过金的号；**分池口径**，默认角色池）。

    老结构（顶层字段）通过 `_pool_state` 迁移后再读。返回 [(avg, c5, total, uid), ...] —— 纯函数，好测。
    """
    if pool not in POOLS:
        pool = "char"
    rows = []
    users = d.get("users")
    if isinstance(users, dict):
        for uid, u in users.items():
            if not isinstance(u, dict):
                continue
            st = stats_of(_pool_state(u, pool))
            if st["c5"] >= 1:
                rows.append((st["avg"], st["c5"], st["total"], str(uid)))
    rows.sort(key=lambda r: (r[0], -r[1], r[2]))
    return rows


def _epi_choice_of(st: dict, pool: str = "weapon") -> int:
    """定轨序号（1 起，越界回 1）。"""
    choices = POOLS[pool]["up5"]
    c = _num(st.get("epi_choice"), 1)
    return c if 1 <= c <= len(choices) else 1


def _pity_line(st: dict, short: bool = False, pool: str = "char") -> str:
    c = st["count5"]
    cfg = POOLS.get(pool, POOLS["char"])
    hard = cfg["p5"][3]
    if pool == "weapon":
        nm = cfg["up5"][_epi_choice_of(st) - 1]
        if _num(st.get("epi_misses")) >= 1:
            return (f"垫抽 {c}/{hard} · 定轨已就绪！下个金必是「{nm}」" if short else
                    f"保底进度 {c}/{hard} · 定轨已就绪 —— 下一个 5★ 必是「{nm}」！")
        return (f"垫抽 {c}/{hard} · 定轨「{nm}」（歪 1 次必中）" if short else
                f"保底进度 {c}/{hard} · 定轨「{nm}」：歪 1 次后，下个 5★ 必中")
    if pool == "standard":
        return (f"垫抽 {c}/{hard} · 常驻大混池" if short else
                f"保底进度 {c}/{hard} · 常驻大混池（无 UP，角色 / 武器都可能）")
    if st["guaranteed5"]:
        return (f"垫抽 {c}/90 · 大保底已就绪！下个金必是可莉" if short else
                f"保底进度 {c}/90 · 大保底已就绪 —— 下一个 5★ 必是「可莉」！")
    return (f"垫抽 {c}/90 · 下个金 50% 是可莉" if short else
            f"保底进度 {c}/90 · 下一个 5★ 有 50% 是「可莉」")


def _five_note(item: dict, pool: str = "char") -> str:
    """出金时的一句评语（文案；按池说人话）。"""
    if pool == "weapon":
        if item.get("epi") and item.get("guar"):
            return "命定值兑现！定轨到手"
        if item.get("epi"):
            return "定轨命中！运气真好"
        if item.get("up"):
            return "捞到 UP，可惜没定上……命定值 +1"
        return "歪了常驻……命定值 +1，下个金必中定轨"
    if pool == "standard":
        return "常驻池出金啦，不亏不亏"
    if item.get("up"):
        return "大保底命中！可莉来啦" if item.get("guar") else "50/50 命中！可莉来啦"
    return "歪了……下个金必中"


# ---------- 文案（卡片发不出去时用的纯文本） ----------

def fmt_single(name: str, item: dict, st: dict, pool: str = "char") -> str:
    title = {"char": "祈愿", "weapon": "武器祈愿", "standard": "常驻祈愿"}.get(pool, "祈愿")
    star = item["star"]
    if star == 5:
        line = f"✨✨ 出金！5★「{item['name']}」—— {_five_note(item, pool)}！"
    elif star == 4:
        tail = "（UP！）" if item.get("up") else ""
        line = f"💜 4★「{item['name']}」{tail}不错不错～"
    else:
        line = f"⚪ 3★「{item['name']}」……聊胜于无，再来一抽？"
    return "\n".join([f"🔮 {name} 的{title} · 第 {st['total']} 抽",
                      line, f"📏 {_pity_line(st, pool=pool)}"])


def fmt_ten(name: str, items: list, st: dict, pool: str = "char") -> str:
    title = {"char": "十连祈愿", "weapon": "武器十连", "standard": "常驻十连"}.get(pool, "十连祈愿")
    strip = "".join({5: "✨", 4: "💜", 3: "⚪"}[i["star"]] for i in items)
    five = [i for i in items if i["star"] == 5]
    four = [i for i in items if i["star"] == 4]
    lines = [f"🔮 {name} 的{title}！", strip,
             " ".join(f"「{i['name']}」" for i in items[:5]),
             " ".join(f"「{i['name']}」" for i in items[5:])]
    if five:
        f0 = five[0]
        lines.append(f"✨ 出金！「{f0['name']}」（第 {f0['pity']} 抽）—— {_five_note(f0, pool)}！")
    elif four:
        lines.append(f"💜 最好的是「{four[0]['name']}」，也还行啦～")
    else:
        lines.append("……全是 3★，下次一定！")
    lines.append(f"📏 {_pity_line(st, pool=pool)}")
    return "\n".join(lines)


def fmt_sim(name: str, n: int, items: list, st: dict, pool: str = "char") -> str:
    title = {"char": "模拟", "weapon": "武器模拟", "standard": "常驻模拟"}.get(pool, "模拟")
    five = [i for i in items if i["star"] == 5]
    four = [i for i in items if i["star"] == 4]
    lines = [f"🔮 {name} 一口气{title}了 {n} 抽！",
             f"✨ 出金 ×{len(five)} · 💜 4★ ×{len(four)} · ⚪ 3★ ×{n - len(five) - len(four)}"]
    for f0 in five[:8]:
        tag = "命中" if f0["up"] else "歪…"
        if pool == "weapon":
            tag = "定轨" if f0.get("epi") else ("UP" if f0["up"] else "歪…")
        if pool == "standard":
            tag = "常驻"
        lines.append(f"  · 第 {f0['pity']} 抽「{f0['name']}」（{tag}）")
    if len(five) > 8:
        lines.append(f"  · …还有 {len(five) - 8} 个")
    if not five:
        hard = POOLS.get(pool, POOLS["char"])["p5"][3]
        lines.append(f"  这 {n} 抽没出金……保底再 {max(0, hard - st['count5'])} 抽必得！")
    lines.append(f"📏 {_pity_line(st, pool=pool)}")
    return "\n".join(lines)


def fmt_stats(name: str, st: dict, pool: str = "char") -> str:
    label = POOLS.get(pool, POOLS["char"])["label"]
    head = f"🎴 {name} 的祈愿战绩" if pool == "char" else f"🎴 {name} 的{label}战绩"
    if st["c5"]:
        five_txt = f"×{st['c5']}（平均 {st['avg']:.1f} 抽/个）"
        off_txt = f"{st['off5']} 次（{st['off5'] / st['c5']:.0%}）"
    else:
        five_txt, off_txt = "×0（还没出过金）", "—"
    off_label = "🚩 定轨歪率" if pool == "weapon" else "🎯 歪率"
    lines = [head,
             f"总抽数：{st['total']}",
             f"✨ 5★：{five_txt}",
             f"💜 4★：×{st['c4']}",
             f"{off_label}：{off_txt}"]
    if pool == "weapon":
        nm = POOLS["weapon"]["up5"][_epi_choice_of(st) - 1]
        ready = "（已就绪！）" if st["epi_misses"] >= 1 else ""
        lines.append(f"🚩 定轨：{nm}{ready}")
    lines += [f"📏 {_pity_line(st, pool=pool)}",
              f"🏅 欧非鉴定：{st['title']}"]
    rec = st["last5"][-3:]
    if rec:
        def _tag(r):
            if pool == "weapon":
                return "定轨" if r.get("epi") else ("UP" if r.get("up") else "歪")
            return "命中" if r.get("up") else "歪"
        lines.append("最近出金：" + "；".join(
            f"{r['name']}（第 {_num(r.get('pity'))} 抽 · {_tag(r)}）" for r in rec))
    else:
        lines.append("最近出金：还没有呢～快去抽两发！")
    return "\n".join(lines)


def fmt_records(name: str, u: dict, shown: int, pool: str = "char") -> str:
    """祈愿记录文案（最近 shown 条，最新在前）；坏流水行过滤不炸。"""
    label = "" if pool == "char" else POOLS.get(pool, POOLS["char"])["label"]
    log = u.get("log") if isinstance(u.get("log"), list) else []
    log = [e for e in log if isinstance(e, list) and len(e) >= 3 and _num(e[1]) in (3, 4, 5)]
    if not log:
        hint = ".祈愿武器" if pool == "weapon" else (".祈愿常驻" if pool == "standard" else ".祈愿")
        return f"🔮 {name}还没有{label}祈愿过呀～发「{hint}」开抽吧！"
    show = log[-shown:][::-1]
    lines = [f"🔮 {name} 的{label}祈愿记录（最近 {len(show)} / 日志 {len(log)} 条）", ""]
    for e in show:
        total, star, nm = _num(e[0]), _num(e[1]), str(e[2])
        pity = _num(e[3]) if len(e) > 3 else 0
        seg = {5: "✨5★", 4: "💜4★"}.get(star, "⚪3★")
        extra = f"（第 {pity} 抽出金）" if star == 5 and pity else ""
        lines.append(f"第 {total} 抽 · {seg}「{nm}」{extra}")
    st = stats_of(u)
    lines.append("")
    lines.append(f"合计 {st['total']} 抽 · 5★×{st['c5']} · 4★×{st['c4']}")
    if len(log) > len(show):
        lines.append("（「.祈愿记录 N」最多看 50 条）")
    return "\n".join(lines)


def fmt_rates(pool: str = "char") -> str:
    if pool == "weapon":
        return "\n".join([
            "🌠 可莉祈愿 · 武器池概率公示",
            "・5★ 基础 0.7%｜第 63 抽起概率提升｜80 抽必得",
            "・5★ 有 75% 是 UP：四风原典 / 尘世之锁（各 37.5%）",
            "・其余 25% 为常驻五★武器（阿莫斯之弓 / 天空系列 / 狼的末路 / 风鹰剑 / 和璞鸢）",
            "・定轨：选一把 UP，歪 1 次后下一个 5★ 必中它（中定轨或换定轨时命定值清零）",
            "・4★ 基础 6%｜第 9 抽起提升｜10 抽内必得 4★ 或以上｜50% UP",
            "（娱乐向模拟，概率口径照着真游戏来，但和钱包无关～）",
        ])
    if pool == "standard":
        return "\n".join([
            "🌠 可莉祈愿 · 常驻池概率公示",
            "・5★ 基础 0.6%｜第 74 抽起概率提升｜90 抽必得",
            "・无 UP：常驻五★角色（七七 / 刻晴 / 莫娜 / 迪卢克 / 琴 / 提纳里 / 迪希雅）",
            "  与常驻五★武器（阿莫斯之弓 / 天空系列 / 狼的末路 / 风鹰剑 / 和璞鸢）大混池",
            "・4★ 基础 5.1%｜第 9 抽起提升｜10 抽内必得 4★ 或以上",
            "（娱乐向模拟，概率口径照着真游戏来，但和钱包无关～）",
        ])
    return "\n".join([
        "🌠 可莉祈愿 · 概率公示",
        "・5★ 基础 0.6%｜第 74 抽起概率提升｜90 抽必得",
        "・4★ 基础 5.1%｜第 9 抽起提升｜10 抽内必得 4★ 或以上",
        "・5★ 有 50% 是限定「可莉」，歪了下一次必得（大保底）",
        "・4★ 有 50% 是 UP：诺艾尔 / 菲谢尔 / 砂糖",
        "・常驻 5★：七七 / 刻晴 / 莫娜 / 迪卢克 / 琴 / 提纳里",
        "（娱乐向模拟，概率口径照着真游戏来，但和钱包无关～）",
    ])


# ---------- 卡片渲染（星空深蓝紫 + 金边，与塔罗同族） ----------

W = 560
SKY_TOP, SKY_BOTTOM = (26, 28, 58), (48, 44, 96)
GOLD, GOLD_DIM = (232, 196, 106), (150, 124, 66)
LIGHT, MUTED = (232, 228, 246), (152, 144, 190)
TILE = {5: (255, 246, 216), 4: (247, 242, 255), 3: (240, 238, 248)}
EDGE = {5: (240, 180, 60), 4: (178, 140, 230), 3: (128, 126, 156)}
STAR_COL = {5: (214, 140, 10), 4: (138, 96, 180), 3: (110, 108, 140)}
NAME_COL = {5: (186, 116, 8), 4: (120, 84, 168), 3: (72, 64, 100)}
NOTE_COL = {5: (140, 96, 10), 4: (120, 84, 168), 3: (100, 98, 130)}   # 浅底上的小字要够深
PANEL = (34, 34, 66)
FONT_CUTE = _cardimg.FONT_CUTE
FONT_CJK = _cardimg.FONT_CJK
CJK_SC_INDEX = _cardimg.CJK_SC_INDEX

_ART_MAP = None


def _char_icon(name: str):
    """角色名 → 头像缓存路径（缓存优先，缺了才下载）。非角色（武器）/任何失败 → None。

    复用练度 panel 的解析与下载（同一份缓存键 / URL 规则 / 原子落盘），不重复实现。
    头像只影响观感 —— 拉不到就只是「没头像」，卡片照常出。
    """
    global _ART_MAP
    try:
        _cardimg.ensure_sibling_path(__file__)               # 装载后目录会被移出 sys.path → 自愈
        import panel  # noqa: PLC0415 —— 与练度同目录；只借图标助手
        if _ART_MAP is None:
            m = {}
            for ent in panel.chars_db().values():
                if isinstance(ent, dict) and isinstance(ent.get("n"), str):
                    m.setdefault(ent["n"], str(ent.get("i") or ""))
            _ART_MAP = m
        i = _ART_MAP.get(name, "")
        if i.startswith("UI_AvatarIcon_Side_"):
            i = "UI_AvatarIcon_" + i[len("UI_AvatarIcon_Side_"):]
        if not i.startswith("UI_AvatarIcon_") or "Side" in i:
            return None
        return panel.icon_path(i, BASE / "genshin_cache")
    except Exception as e:                                # noqa: BLE001
        logger.debug("祈愿：头像解析失败（%s: %s）", type(e).__name__, e)
        return None


def _sky(H):
    """星空底：竖渐变 + 固定种子星点（每张卡一致，不闪；避开标题带，别压字）。"""
    Image, ImageDraw, _ = _cardimg.load_pil()
    im = Image.new("RGB", (W, H), SKY_TOP)
    d = ImageDraw.Draw(im)
    for y in range(H):
        t = y / max(1, H - 1)
        d.line([(0, y), (W, y)],
               fill=tuple(int(a + (b - a) * t) for a, b in zip(SKY_TOP, SKY_BOTTOM)))
    srng = random.Random(20261014)
    for _ in range(int(H * 0.045)):
        x = srng.randint(30, W - 30)
        y = srng.randint(24, int(H * 0.72))
        if 30 <= y <= 112 and 130 <= x <= W - 130:      # 标题带：跳过，别在字上撒点
            continue
        r = srng.choice((1, 1, 2))
        v = srng.randint(110, 220)
        d.ellipse([x - r, y - r, x + r, y + r], fill=(v, v, min(255, v + 30)))
    for cx, cy, s in ((96, 150, 8), (466, 118, 7)):
        d.line([cx - s, cy, cx + s, cy], fill=GOLD_DIM, width=2)
        d.line([cx, cy - s, cx, cy + s], fill=GOLD_DIM, width=2)
    return im, d


def _frame(d, H):
    d.rounded_rectangle([14, 14, W - 14, H - 14], radius=26, outline=GOLD, width=3)
    d.rounded_rectangle([25, 25, W - 25, H - 25], radius=22, outline=GOLD_DIM, width=1)


def _tile(d, fonts, x, y, w, h, item, name_limit):
    """一张小物件牌（浅色底 + 稀有度描边）。"""
    f_star, f_cap, f_name = fonts
    star = item["star"]
    d.rounded_rectangle([x, y, x + w, y + h], radius=14, fill=TILE[star],
                        outline=EDGE[star], width=3)
    d.text((x + 12, y + 8), "★" * star, font=f_star, fill=STAR_COL[star])
    d.rounded_rectangle([x + w - 52, y + 8, x + w - 10, y + 30], radius=11, fill=EDGE[star])
    d.text((x + w - 31, y + 19), f"{star}★", font=f_cap, fill=(255, 255, 255), anchor="mm")
    d.text((x + 12, y + 40), f"「{_cardimg.short(item['name'], name_limit)}」", font=f_name, fill=NAME_COL[star])


def _pity_bar(d, st, x0, y0, x1, y1, hard: int = 90):
    """保底进度条（轨道可见 + 金色填充；0 抽也画个起点圆头，免得像坏了）。"""
    h = y1 - y0
    d.rounded_rectangle([x0, y0, x1, y1], radius=h // 2, fill=(40, 40, 80),
                        outline=(100, 94, 152), width=1)
    frac = min(1.0, max(0.0, st["count5"] / hard))
    if frac <= 0:
        rr = h - 2                                       # 圆头与轨道同心
        d.ellipse([x0 + 1, y0 + 1, x0 + 1 + rr, y0 + 1 + rr], fill=GOLD_DIM)
        return
    w = max(int((x1 - x0) * frac), h)
    d.rounded_rectangle([x0, y0, x0 + min(w, x1 - x0), y1], radius=h // 2,
                        fill=(248, 210, 110) if st["guaranteed5"] else (212, 170, 96))


def _pity_footer(d, st, H, f_pity, pool: str = "char"):
    hard = POOLS.get(pool, POOLS["char"])["p5"][3]
    pt = H - 88
    d.rounded_rectangle([18, pt, W - 18, pt + 58], radius=20, fill=PANEL, outline=GOLD_DIM, width=2)
    d.text((W // 2, pt + 19), _pity_line(st, short=True, pool=pool), font=f_pity, fill=LIGHT, anchor="mm")
    _pity_bar(d, st, 104, pt + 34, W - 104, pt + 46, hard=hard)


def render_single(name, item, st, out_path, art=None, pool="char") -> bool:
    """art=预解析好的头像路径（调用方只给 4★+ 角色；None / 贴失败 → 无头像版式）。"""
    try:
        _, _, ImageFont = _cardimg.load_pil()
        f_head = _cardimg.font_cute(40)
        f_sub = _cardimg.font_cjk(18)
        f_stars = _cardimg.font_cjk(34)
        f_name = _cardimg.font_cute(52)
        f_name_av = _cardimg.font_cute(44)     # 有头像时给头像让位
        f_note = _cardimg.font_cjk(20)
        f_pity = _cardimg.font_cjk(17)
    except Exception as e:                                # noqa: BLE001
        logger.warning("祈愿：单抽卡字体加载失败（%s: %s）→ 回退文本", type(e).__name__, e)
        return False
    try:
        H = 420
        star = item["star"]
        im, d = _sky(H)
        _frame(d, H)
        _single_title = {"char": "祈愿 · 单抽", "weapon": "武器祈愿 · 单抽",
                         "standard": "常驻祈愿 · 单抽"}.get(pool, "祈愿 · 单抽")
        d.text((W // 2, 58), _single_title, font=f_head, fill=GOLD, anchor="mm")
        d.text((W // 2, 90), f"—— {name} 的第 {st['total']} 抽 ——", font=f_sub,
               fill=MUTED, anchor="mm")
        if star == 5:
            d.rounded_rectangle([84, 116, W - 84, 308], radius=30,
                                outline=(240, 180, 60), width=2)        # 外发光
        d.rounded_rectangle([96, 128, W - 96, 296], radius=26, fill=TILE[star],
                            outline=EDGE[star], width=5)
        text_cx, f_name_use = W // 2, f_name
        if art is not None and star >= 4:                 # 头像贴牌面左侧（圆形），文字块右移
            if _cardimg.paste_icon(im, art, 166, 158, 108, radius=54, log=logger) is not None:
                d.ellipse([112, 158, 220, 266], outline=EDGE[star], width=3)   # 稀有度色描环
                text_cx, f_name_use = 350, f_name_av
        d.text((text_cx, 168), "★" * star, font=f_stars, fill=STAR_COL[star], anchor="mm")
        d.text((text_cx, 226), f"「{_cardimg.short(item['name'], 10)}」", font=f_name_use,
               fill=NAME_COL[star], anchor="mm")
        note = "……再来一抽？"
        if star == 5:
            note = _five_note(item, pool)
        elif star == 4:
            note = "UP 到手！" if item.get("up") else "不错不错～"
        d.text((text_cx, 268), note, font=f_note, fill=NOTE_COL[star], anchor="mm")
        d.rounded_rectangle([28, 308, W - 28, 390], radius=18, fill=PANEL,
                            outline=GOLD_DIM, width=2)
        _hard = POOLS.get(pool, POOLS["char"])["p5"][3]
        d.text((W // 2, 330), _pity_line(st, short=True, pool=pool), font=f_pity, fill=LIGHT, anchor="mm")
        _pity_bar(d, st, 96, 350, W - 96, 364, hard=_hard)
        _cardimg.save_img(im, out_path)
        return True
    except Exception as e:                                # noqa: BLE001
        logger.warning("祈愿：单抽卡渲染失败（%s: %s）→ 回退文本", type(e).__name__, e)
        return False


def render_ten(name, items, st, out_path, art=None, pool="char") -> bool:
    """art=预解析好的头像路径（只用于金区英雄位；None / 贴失败 → 无头像版式）。"""
    try:
        _, _, ImageFont = _cardimg.load_pil()
        f_head = _cardimg.font_cute(44)
        f_sub = _cardimg.font_cjk(18)
        f_star = _cardimg.font_cjk(18)
        f_star_big = _cardimg.font_cjk(30)
        f_cap = _cardimg.font_cjk(16)
        f_item = _cardimg.font_cjk(18)
        f_item3 = _cardimg.font_cjk(15)
        f_name = _cardimg.font_cute(44)
        f_note = _cardimg.font_cjk(20)
        f_pity = _cardimg.font_cjk(18)
    except Exception as e:                                # noqa: BLE001
        logger.warning("祈愿：十连卡字体加载失败（%s: %s）→ 回退文本", type(e).__name__, e)
        return False
    try:
        sorted_items = sorted(items, key=lambda t: -t["star"])
        hero = sorted_items[0] if sorted_items and sorted_items[0]["star"] == 5 else None
        rest = sorted_items[1:] if hero else sorted_items
        cols = 3 if hero else 2
        cw, ch, gx = (161, 96, 10) if hero else (240, 106, 24)
        gy, x0 = 10, 28
        y0 = 286 if hero else 124
        rows = 3 if hero else 5
        rest = rest[:rows * cols]                         # 防呆：多出来的不画（别压页脚）
        H = 712 if hero else 816
        im, d = _sky(H)
        _frame(d, H)
        d.text((W // 2, 62), {"char": "祈愿 · 十连", "weapon": "武器祈愿 · 十连",
                              "standard": "常驻祈愿 · 十连"}.get(pool, "祈愿 · 十连"),
               font=f_head, fill=GOLD, anchor="mm")
        _ten_sub = {"char": "十连祈愿", "weapon": "武器十连",
                    "standard": "常驻十连"}.get(pool, "十连祈愿")
        d.text((W // 2, 96), f"—— {name} 的{_ten_sub} ——", font=f_sub, fill=MUTED, anchor="mm")
        if hero:
            d.rounded_rectangle([32, 124, W - 32, 254], radius=22, fill=(255, 247, 221),
                                outline=(240, 180, 60), width=4)
            hx = W // 2
            if art is not None:                           # 金区头像（圆形），文字块右移
                if _cardimg.paste_icon(im, art, 96, 141, 96, radius=48, log=logger) is not None:
                    d.ellipse([48, 141, 144, 237], outline=(200, 132, 22), width=3)   # 描环
                    hx = 332
            d.text((hx, 152), "★★★★★", font=f_star_big, fill=(214, 140, 10), anchor="mm")
            d.text((hx, 196), f"「{_cardimg.short(hero['name'], 12)}」", font=f_name,
                   fill=(186, 116, 8), anchor="mm")
            d.text((hx, 231), _five_note(hero, pool), font=f_note, fill=(170, 105, 16),
                   anchor="mm")
        for i, it in enumerate(rest):
            cx = x0 + (i % cols) * (cw + gx)
            cy = y0 + (i // cols) * (ch + gy)
            _tile(d, (f_star, f_cap, f_item3 if hero else f_item), cx, cy, cw, ch, it,
                  6 if hero else 12)
        rows_used = (len(rest) + cols - 1) // cols if rest else 0
        bottom = y0 + (rows_used - 1) * (ch + gy) + ch if rows_used else y0 - 22
        five = [i for i in items if i["star"] == 5]
        four = [i for i in items if i["star"] == 4]
        d.text((W // 2, bottom + 18),
               f"本次：5★×{len(five)} · 4★×{len(four)} · 3★×{10 - len(five) - len(four)}",
               font=f_sub, fill=MUTED, anchor="mm")
        _pity_footer(d, st, H, f_pity, pool=pool)
        _cardimg.save_img(im, out_path)
        return True
    except Exception as e:                                # noqa: BLE001
        logger.warning("祈愿：十连卡渲染失败（%s: %s）→ 回退文本", type(e).__name__, e)
        return False


def render_sim(name, n, items, st, out_path, pool="char") -> bool:
    try:
        _, _, ImageFont = _cardimg.load_pil()
        f_head = _cardimg.font_cute(42)
        f_sub = _cardimg.font_cjk(18)
        f_chip = _cardimg.font_cjk(23)
        f_line = _cardimg.font_cjk(20)
        f_tag = _cardimg.font_cjk(18)
        f_big = _cardimg.font_cjk(24)
        f_pity = _cardimg.font_cjk(18)
    except Exception as e:                                # noqa: BLE001
        logger.warning("祈愿：模拟卡字体加载失败（%s: %s）→ 回退文本", type(e).__name__, e)
        return False
    try:
        five = [i for i in items if i["star"] == 5]
        four = [i for i in items if i["star"] == 4]
        if five:
            shown = five[:8]
            lines = len(shown) + (1 if len(five) > 8 else 0)
            H = 372 + lines * 40                          # 高度贴着列表走，别留空段
        else:
            shown, H = [], 520
        im, d = _sky(H)
        _frame(d, H)
        d.text((W // 2, 62), f"{'祈愿模拟' if pool == 'char' else ('武器模拟' if pool == 'weapon' else '常驻模拟')} · {n} 连",
               font=f_head, fill=GOLD, anchor="mm")
        d.text((W // 2, 96), f"—— {name} 的一口气模拟 ——", font=f_sub, fill=MUTED, anchor="mm")
        chips = ((f"出金 ×{len(five)}", (248, 210, 110)),
                 (f"4★ ×{len(four)}", (196, 158, 240)),
                 (f"3★ ×{n - len(five) - len(four)}", (168, 164, 200)))
        for i, (label, col) in enumerate(chips):
            bx0 = 28 + i * 172
            d.rounded_rectangle([bx0, 120, bx0 + 160, 176], radius=18, fill=PANEL,
                                outline=GOLD_DIM, width=2)
            d.text((bx0 + 80, 148), label, font=f_chip, fill=col, anchor="mm")
        d.text((48, 216), "出金记录", font=f_sub, fill=MUTED, anchor="lm")
        y = 252
        if shown:
            for it in shown:
                d.text((48, y), f"第 {it['pity']} 抽 · 「{_cardimg.short(it['name'], 8)}」",
                       font=f_line, fill=LIGHT, anchor="lm")
                tag, tcol = ("命中", (248, 210, 110)) if it.get("up") else ("歪了", (188, 178, 218))
                d.text((W - 48, y), tag, font=f_tag, fill=tcol, anchor="rm")
                y += 40
            if len(five) > 8:
                d.text((48, y), f"……还有 {len(five) - 8} 个", font=f_line, fill=MUTED,
                       anchor="lm")
        else:
            d.text((W // 2, 330), f"这 {n} 抽里没有出金……", font=f_big, fill=MUTED, anchor="mm")
            _hard = POOLS.get(pool, POOLS["char"])["p5"][3]
            d.text((W // 2, 374), f"保底再 {max(0, _hard - st['count5'])} 抽就轮到你了！",
                   font=f_sub, fill=(178, 170, 210), anchor="mm")
        _pity_footer(d, st, H, f_pity, pool=pool)
        _cardimg.save_img(im, out_path)
        return True
    except Exception as e:                                # noqa: BLE001
        logger.warning("祈愿：模拟卡渲染失败（%s: %s）→ 回退文本", type(e).__name__, e)
        return False


def render_stats(name, st, out_path, pool="char") -> bool:
    try:
        _, _, ImageFont = _cardimg.load_pil()
        f_head = _cardimg.font_cute(42)
        f_sub = _cardimg.font_cjk(18)
        f_lbl = _cardimg.font_cjk(19)
        f_val = _cardimg.font_cjk(22)
        f_pity = _cardimg.font_cjk(18)
        f_title = _cardimg.font_cute(30)
        f_rec = _cardimg.font_cjk(18)
    except Exception as e:                                # noqa: BLE001
        logger.warning("祈愿：战绩卡字体加载失败（%s: %s）→ 回退文本", type(e).__name__, e)
        return False
    try:
        H = 640 + (54 if pool == "weapon" else 0)         # 武器池多一行「定轨」
        im, d = _sky(H)
        _frame(d, H)
        _stats_title = {"char": "祈愿战绩", "weapon": "武器战绩", "standard": "常驻战绩"}.get(pool, "祈愿战绩")
        d.text((W // 2, 60), _stats_title, font=f_head, fill=GOLD, anchor="mm")
        d.text((W // 2, 92), f"—— {name} 的{'祈愿手账' if pool == 'char' else POOLS.get(pool, POOLS['char'])['label'] + '手账'} ——",
               font=f_sub, fill=MUTED, anchor="mm")
        rows = [
            ("总抽数", f"{st['total']}"),
            ("5★ 出金", f"×{st['c5']}" + (f" · 平均 {st['avg']:.1f} 抽/个" if st["c5"] else " · 还没出过金")),
            ("4★ 收获", f"×{st['c4']}"),
            ("定轨歪率" if pool == "weapon" else "歪 率",
             f"{st['off5']}/{st['c5']} = {st['off5'] / st['c5']:.0%}" if st["c5"] else "—"),
        ]
        if pool == "weapon":
            _nm = POOLS["weapon"]["up5"][_epi_choice_of(st) - 1]
            rows.append(("定轨", _nm + ("（已就绪！）" if st["epi_misses"] >= 1 else "（歪 1 次必中）")))
        y = 112
        for lbl, val in rows:
            d.rounded_rectangle([28, y, W - 28, y + 46], radius=15, fill=PANEL,
                                outline=(70, 66, 110), width=1)
            d.text((48, y + 23), lbl, font=f_lbl, fill=MUTED, anchor="lm")
            d.text((W - 48, y + 23), val, font=f_val, fill=LIGHT, anchor="rm")
            y += 54
        d.rounded_rectangle([28, y + 4, W - 28, y + 80], radius=18, fill=PANEL,
                            outline=GOLD_DIM, width=2)
        d.text((W // 2, y + 28), _pity_line(st, short=True, pool=pool), font=f_pity, fill=LIGHT, anchor="mm")
        _pity_bar(d, st, 64, y + 52, W - 64, y + 68, hard=POOLS.get(pool, POOLS["char"])["p5"][3])
        d.rounded_rectangle([28, y + 94, W - 28, y + 158], radius=18, fill=PANEL,
                            outline=GOLD_DIM, width=2)
        d.text((W // 2, y + 126), f"称号 · {st['title']}", font=f_title, fill=GOLD, anchor="mm")
        d.text((48, y + 188), "最近出金", font=f_sub, fill=MUTED, anchor="lm")
        rec = st["last5"][-3:]
        if rec:
            for i, r in enumerate(rec):
                d.text((48, y + 214 + i * 26),
                       f"· {_cardimg.short(str(r.get('name')), 8)}"
                       f"（第 {_num(r.get('pity'))} 抽 · {'命中' if r.get('up') else '歪'}）",
                       font=f_rec, fill=LIGHT, anchor="lm")
        else:
            d.text((48, y + 214), "· 还没有出过金，去 .祈愿 试试运气吧", font=f_rec, fill=MUTED,
                   anchor="lm")
        _cardimg.save_img(im, out_path)
        return True
    except Exception as e:                                # noqa: BLE001
        logger.warning("祈愿：战绩卡渲染失败（%s: %s）→ 回退文本", type(e).__name__, e)
        return False


# ---------- 命令 ----------

def _parse_count(arg: str):
    """→ (n, err)。'' → 1；'10'/'十'/'十连'/'x10' → 10；'1'~'300' → n；其余 → err 文案。"""
    s = (arg or "").strip().lower().replace("x", "").replace("×", "").rstrip("连抽单")
    if not s:
        return 1, None
    if s == "十":
        return 10, None
    if s.isdecimal():                                     # OCR 八轮：isdigit() 会放过 ² 这类「假数字」，
        n = int(s)                                        # isdecimal() 才和 int() 的接受集对齐
        if 1 <= n <= MAX_SIM:
            return n, None
        if n > MAX_SIM:
            return MAX_SIM, None
    return None, "诶……这个数字可莉看不懂。试试「.祈愿」「.祈愿 10」「.祈愿 90」？"


def _opt_bool(v, default: bool = True) -> bool:
    """settings 里读开关：缺省/坏值走 default，只有明确「假」才关。"""
    if v is None:
        return default
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    if isinstance(v, str):
        return v.strip().lower() not in ("false", "0", "no", "off", "关", "关闭", "不")
    return default


def _parse_pool_arg(arg: str):
    """『武器 [N]』『常驻 [N]』→ (pool, 剩余参数)；默认 char（向后兼容）。"""
    parts = (arg or "").strip().split(maxsplit=1)
    if parts:
        pool = POOL_ALIASES.get(parts[0])
        if pool:
            return pool, (parts[1] if len(parts) > 1 else "")
    return "char", (arg or "").strip()


def register(api):
    settings = dict(getattr(api, "settings", None) or {})
    card_on = _opt_bool(settings.get("card"), True)      # settings.card=false → 只发纯文本
    avatar_on = _opt_bool(settings.get("avatar"), True)  # settings.avatar=false → 卡片不贴角色头像

    async def _send_card_or_text(ctx, prefix, render_fn, render_args, caption, fallback, extra=None):
        if not card_on:                                  # settings.card=false → 只发文本
            await ctx.reply(fallback)
            return
        try:
            out = _cardimg.card_path(_cardimg.cards_dir(settings, BASE), prefix, ctx.user_id)
        except OSError as e:
            logger.warning("祈愿：卡片目录不可用（%s）→ 回退文本", e)
            out = None
        if out is not None and await asyncio.to_thread(render_fn, *render_args, out, **(extra or {})):
            if await _cardimg.send_card(ctx, out, caption, pattern=f"{prefix}-*.png"):
                return
        await ctx.reply(fallback)

    _POOL_PREFIX = {"char": "wish", "weapon": "wishw", "standard": "wishs"}

    async def _run_pull(ctx, n: int, pool: str = "char"):
        d = _load()
        u = _entry(d, ctx.user_id, pool)
        items = pull_many(u, n, pool=pool)
        _save(d)
        st = stats_of(u)
        name = _cardimg.safe_name(ctx.user_name or "你", 12)
        five = [i for i in items if i["star"] == 5]
        four = [i for i in items if i["star"] == 4]
        prefix = _POOL_PREFIX.get(pool, "wish")
        lb = POOLS[pool]["label"] if pool != "char" else ""
        tag = f"（{lb}）" if lb else ""
        if n == 1:
            item = items[0]
            art = None
            if card_on and avatar_on and pool == "char" and item["star"] >= 4:  # 头像只给角色池
                art = await asyncio.to_thread(_char_icon, item["name"])
            lead = (f"✨ 出金「{item['name']}」！" if item["star"] == 5
                    else f"{item['star']}★「{item['name']}」")
            caption = f"🔮 {name} 的第 {st['total']} 抽{tag} —— {lead}"
            await _send_card_or_text(ctx, prefix, render_single, (name, item, st),
                                     caption, fmt_single(name, item, st, pool),
                                     extra={"art": art, "pool": pool})
        elif n == 10:
            art = None
            if card_on and avatar_on and pool == "char" and five:
                art = await asyncio.to_thread(_char_icon, five[0]["name"])
            short = {"weapon": "武器", "standard": "常驻"}.get(pool, "")
            caption = (f"🔮 {name} 的{short}十连{'祈愿' if pool == 'char' else ''} —— "
                       f"✨×{len(five)} · 💜×{len(four)}" + ("（出金啦！）" if five else ""))
            await _send_card_or_text(ctx, prefix, render_ten, (name, items, st),
                                     caption, fmt_ten(name, items, st, pool),
                                     extra={"art": art, "pool": pool})
        else:
            caption = (f"🔮 {name} 模拟 {n} 抽{tag} —— ✨×{len(five)} · 💜×{len(four)}"
                       + ("（出金啦！）" if five else "（没出金…）"))
            await _send_card_or_text(ctx, prefix, render_sim, (name, n, items, st),
                                     caption, fmt_sim(name, n, items, st, pool),
                                     extra={"pool": pool})

    async def wish_cmd(ctx):
        n, err = _parse_count(ctx.arg_text)
        if err:
            await ctx.reply(err)
            return
        await _run_pull(ctx, n, "char")

    async def ten_cmd(ctx):
        await _run_pull(ctx, 10, "char")

    async def wwish_cmd(ctx):
        n, err = _parse_count(ctx.arg_text)
        if err:
            await ctx.reply(err)
            return
        await _run_pull(ctx, n, "weapon")

    async def swish_cmd(ctx):
        n, err = _parse_count(ctx.arg_text)
        if err:
            await ctx.reply(err)
            return
        await _run_pull(ctx, n, "standard")

    async def epi_cmd(ctx):
        d = _load()
        ps = _entry(d, ctx.user_id, "weapon")
        choices = POOLS["weapon"]["up5"]
        arg = (ctx.arg_text or "").strip()
        cur = _epi_choice_of(ps)
        if not arg:
            ready = "（已就绪！下个金必中）" if _num(ps.get("epi_misses")) >= 1 else "（歪 1 次必中）"
            await ctx.reply(f"🚩 你的武器定轨：「{choices[cur - 1]}」{ready}\n"
                            f"切换：.定轨 1 / .定轨 2 / .定轨 {choices[0]}。")
            return
        idx = None
        first = arg.split()[0]
        if first in ("1", "一"):
            idx = 1
        elif first in ("2", "二", "两"):
            idx = 2
        else:
            for i, nm in enumerate(choices, 1):
                if nm in arg:
                    idx = i
        if idx is None:
            await ctx.reply(f"定轨要选 1 或 2 呀：1 = 「{choices[0]}」，2 = 「{choices[1]}」")
            return
        old = cur
        misses_was = _num(ps.get("epi_misses"))
        ps["epi_choice"] = idx
        if idx != old and misses_was >= 1:                  # 换定轨 → 命定值清零（官方规则）
            ps["epi_misses"] = 0
            ps["guaranteed5"] = False
        _save(d)
        tail = "（换定轨，命定值已清零）" if (idx != old and misses_was >= 1) else ""
        await ctx.reply(f"🚩 定轨已设置：「{choices[idx - 1]}」{tail}\n歪 1 次后，下一个 5★ 必是它！")

    async def stats_cmd(ctx):
        pool, _rest = _parse_pool_arg(ctx.arg_text)
        d = _load()
        u = _entry(d, ctx.user_id, pool)
        st = stats_of(u)
        name = _cardimg.safe_name(ctx.user_name or "你", 12)
        lb = "祈愿" if pool == "char" else POOLS[pool]["label"]
        caption = f"🎴 {name} 的{lb}战绩 —— 总共 {st['total']} 抽 · 5★ ×{st['c5']}"
        await _send_card_or_text(ctx, "wish", render_stats, (name, st), caption,
                                 fmt_stats(name, st, pool), extra={"pool": pool})

    async def rates_cmd(ctx):
        pool, _rest = _parse_pool_arg(ctx.arg_text)
        d = _load()
        st = stats_of(_entry(d, ctx.user_id, pool))
        await ctx.reply(fmt_rates(pool) + "\n\n📏 " + _pity_line(st, pool=pool))

    async def rank_cmd(ctx):
        pool, _rest = _parse_pool_arg(ctx.arg_text)
        lb = POOLS[pool]["label"] if pool != "char" else "祈愿"
        rows = rank_rows(_load(), pool=pool)
        if not rows:
            hint = {"char": "`.祈愿`", "weapon": "`.祈愿武器`", "standard": "`.祈愿常驻`"}.get(pool, "`.祈愿`")
            await ctx.reply(f"{lb}欧非榜还没开张呢～先去 {hint} 抽两下吧！")
            return
        names: dict = {}
        if getattr(ctx, "is_group", False) and getattr(ctx, "group_id", ""):
            fn = getattr(ctx.adapter, "_member_names", None)
            if fn:
                try:
                    names = await fn(ctx.group_id)
                except Exception:                        # noqa: BLE001
                    names = {}

        def disp(uid: str) -> str:
            nm = names.get(str(uid))
            return str(nm)[:12] if nm else f"旅行者·{uid[-4:]}"

        medals = ("🥇", "🥈", "🥉")
        lines = []
        for i, (avg, c5, total, uid) in enumerate(rows[:10]):
            mark = medals[i] if i < 3 else f"{i + 1}."
            me = "（你）" if uid == str(ctx.user_id) else ""
            lines.append(f"{mark} {disp(uid)}{me} —— 平均 {avg:.1f} 抽/金"
                         f"（金 ×{c5} · 共 {total} 抽）")
        mine = next(((i + 1, r) for i, r in enumerate(rows)
                     if r[3] == str(ctx.user_id)), None)
        tail = ""
        if mine and mine[0] > 10:
            tail = f"\n……你的名次：第 {mine[0]} 名（平均 {mine[1][0]:.1f} 抽/金）"
        if len(rows) >= 2:
            avg, c5, total, uid = rows[-1]
            tail += f"\n🌚 当前最非：{disp(uid)}（平均 {avg:.1f} 抽/金）"
        await ctx.reply(f"🎴 可莉的{lb}欧非榜（比谁的金来得早）：\n"
                        + "\n".join(lines) + tail)

    api.on_command("祈愿", wish_cmd, aliases=("单抽", "wish"), cooldown=20,
                   help=".祈愿 [1|10|N] —— 角色池祈愿（保底/大保底记账；N ≤ 300）")
    api.on_command("祈愿十连", ten_cmd, aliases=("十连祈愿", "祈愿10"), cooldown=45,
                   help=".祈愿十连 —— 角色池十连（发卡片，带保底进度）")
    api.on_command("祈愿武器", wwish_cmd, aliases=("武器祈愿", "武器池"), cooldown=20,
                   help=".祈愿武器 [1|10|N] —— 武器池祈愿（0.7% / 80 保底 / 定轨）")
    api.on_command("祈愿常驻", swish_cmd, aliases=("常驻祈愿", "常驻池"), cooldown=20,
                   help=".祈愿常驻 [1|10|N] —— 常驻池祈愿（角色+武器大混池 / 90 保底）")
    api.on_command("定轨", epi_cmd, aliases=("命定",), cooldown=5,
                   help=".定轨 [1|2|名字] —— 武器池定轨（歪 1 次后下个金必中，换定轨清零）")
    api.on_command("祈愿统计", stats_cmd, aliases=("欧非报告", "祈愿战绩"), cooldown=10,
                   help=".祈愿统计 [武器|常驻] —— 出金记录 / 垫抽 / 欧非鉴定")
    api.on_command("祈愿概率", rates_cmd, aliases=("卡池概率",), cooldown=30,
                   help=".祈愿概率 [武器|常驻] —— 卡池概率与 UP 公示")
    async def records_cmd(ctx):
        pool, rest = _parse_pool_arg(ctx.arg_text)
        n = 20
        rest = rest.strip()
        if rest:
            n = max(1, min(50, _num(rest, 20) or 20))
        d = _load()
        u = _entry(d, ctx.user_id, pool)
        name = _cardimg.safe_name(ctx.user_name or "你", 12)
        await ctx.reply(fmt_records(name, u, n, pool))

    api.on_command("祈愿记录", records_cmd, aliases=("祈愿历史",), cooldown=10,
                   help=".祈愿记录 [武器|常驻] [N] —— 最近 N 抽流水（默认 20，最多 50）")
    api.on_command("祈愿排行", rank_cmd, aliases=("欧非榜", "欧皇榜"), cooldown=15,
                   help=".祈愿排行 [武器|常驻] —— 分池欧非榜（平均出金抽数，谁最欧）")
