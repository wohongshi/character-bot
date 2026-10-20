"""新词候选：从群聊里捞「最近反复出现的词」，给面板一键收进知识库。

为什么需要
----------
角色越用越像群里的人，靠的不是 SOUL 写得多好，而是**她认得出群里的梗**。
人工想「最近有什么新词」很难；消息流里反复出现的 2–4 字组合就是候选。

怎么挑（纯规则，不调模型；新词发现的经典三件套）
------------------------------------------------
只看「出现次数」会把「今天又」「了哈哈」这种碎片排到最前面 —— 次数很高，
但没有词的形状。所以同时要求：

1. **频次**：出现 ≥ N 次、说话人 ≥ M 个（一个人刷一百遍不算梗）；
2. **内部凝聚度**（PMI）：`p(w) / (p(左半) × p(右半))` 足够大，即它不像
   「两个常见字碰巧挨在一起」；
3. **左右自由度**（邻接熵）：左右两边出现过足够多样的字，说明它是个能独立
   成词的片段，而不是某个固定搭配的一半；
4. 长词优先：3 字词和它包含的 2 字词次数接近时只留长的；
5. 已经在这个角色知识库里的词标 `known`（面板默认折叠）。

候选本身可能很蠢，所以面板上是**人点一下**才入库，不做任何自动写入。
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
import threading
from collections import Counter, defaultdict
from pathlib import Path

log = logging.getLogger("klee.bot.newwords")

FILE = "newwords.json"
MIN_COUNT = 3
MIN_USERS = 2
MAX_WORD = 4
MIN_WORD = 2
MAX_CANDIDATES = 60
MIN_PMI = 2.0             # 内部凝聚度下限（越大越像词）
MIN_ENTROPY = 0.8         # 左右邻接熵下限（越大越像「能自由出现在别处」）

_CJK = re.compile(r"[\u4e00-\u9fff]+")
_LOCK = threading.RLock()
_LEFT, _RIGHT = "\x02", "\x03"        # 片段边界哨兵

STOP = set("""
我们 你们 他们 她们 它们 自己 什么 怎么 为什么 这个 那个 这些 那些 这样 那样 这里 那里
一个 一下 一样 一点 一些 一直 一起 一定 一般 一切 已经 可以 可能 应该 需要 觉得 认为
现在 今天 明天 昨天 早上 中午 晚上 时候 时间 地方 东西 事情 问题 意思 感觉 知道 不知
因为 所以 但是 而且 如果 虽然 然后 还是 或者 不过 只是 就是 也是 都是 不是 没有 不会
有点 有些 真的 好像 其实 反正 干脆 直接 完全 特别 非常 比较 稍微
哈哈 哈哈哈 呵呵 嘿嘿 再见 谢谢 好的 收到 明白 没事 加油 辛苦 厉害 舒服 起来
请问 大家 各位 朋友 老师 同学 老板 兄弟 姐妹 老公 老婆 宝贝 亲爱的 晚安 早安 午安
""".split())

# 单字虚词：2 字词里含它们基本是碎片
_WEAK = set("的了是我你他她它们这那就都很也还在有不没吧呢啊呀哦嗯个上与和跟把被让给对从"
            "吗嘛呗哈呵嘿哦呃")


def _clean(text) -> str:
    return str(text or "")


def _entropy(counter: Counter) -> float:
    n = sum(counter.values())
    if n <= 1:
        return 0.0
    h = 0.0
    for v in counter.values():
        p = v / n
        h -= p * math.log(p)
    return h


def collect(rows):
    """统计 n-gram / 左右邻接 / 总字数。返回 (cnt, left, right, total, users)。"""
    cnt: Counter = Counter()
    left: dict[str, Counter] = defaultdict(Counter)
    right: dict[str, Counter] = defaultdict(Counter)
    users: dict[str, set] = defaultdict(set)
    total = 0
    for r in rows:
        if not isinstance(r, dict) or r.get("role") != "user":
            continue
        who = str(r.get("user_id") or r.get("name") or "")
        text = _clean(r.get("text"))
        if not text:
            continue
        for seg in _CJK.findall(text):
            if not seg:
                continue
            total += len(seg)
            s = _LEFT + seg + _RIGHT      # 边界也当字符：词首/词尾的邻接熵才有意义
            # 1-gram 也数：PMI 要拿「子串的计数」做分母，没有 1-gram 就永远是 0
            for n in range(1, MAX_WORD + 1):
                for i in range(1, len(s) - n):
                    w = s[i:i + n]
                    if _LEFT in w or _RIGHT in w:
                        continue
                    cnt[w] += 1
                    if n == 1:
                        continue
                    users[w].add(who)
                    left[w][s[i - 1]] += 1
                    right[w][s[i + n]] += 1
    return cnt, left, right, total, users


def _pmi(w: str, cnt: Counter, total: int) -> float:
    """内部凝聚度：对所有切法取最小（最保守）。"""
    if len(w) < 2 or total <= 0:
        return 0.0
    cw = cnt.get(w, 0)
    if cw <= 0:
        return 0.0
    worst = float("inf")
    for i in range(1, len(w)):
        a, b = w[:i], w[i:]
        ca, cb = cnt.get(a, 0), cnt.get(b, 0)
        if ca <= 0 or cb <= 0:
            return 0.0
        # log( p(w) / (p(a)p(b)) )，用计数与总字数展开
        worst = min(worst, math.log((cw * total) / (ca * cb) + 1e-9))
    return 0.0 if worst == float("inf") else worst


def scan(rows, *, ignore=(), known=(), min_count: int = MIN_COUNT,
         min_users: int = MIN_USERS, top: int = MAX_CANDIDATES,
         min_pmi: float = MIN_PMI, min_entropy: float = MIN_ENTROPY) -> list[dict]:
    """扫一遍消息行 → 候选词列表（按热度降序）。纯函数，测试友好。"""
    ignore = {str(x) for x in (ignore or ()) if str(x)}
    known = {str(x) for x in (known or ()) if str(x)}
    cnt, left, right, total, users = collect(rows)
    cand: list[dict] = []
    for w, c in cnt.items():
        if c < min_count:
            continue
        if len(w) < MIN_WORD or len(w) > MAX_WORD:
            continue
        if w in STOP or w in ignore:
            continue
        if len(w) == 2 and (w[0] in _WEAK or w[1] in _WEAK):
            continue
        if len(users[w]) < min_users:
            continue
        pmi = _pmi(w, cnt, total)
        if pmi < min_pmi:
            continue
        lb, rb = _entropy(left[w]), _entropy(right[w])
        if min(lb, rb) < min_entropy:
            continue
        cand.append({"word": w, "count": c, "users": len(users[w]),
                     "pmi": round(pmi, 2), "left": round(lb, 2), "right": round(rb, 2),
                     "known": w in known})
    # 先按热度截一批再做包含扫描：候选可能上千，两两比较是 O(n²)
    cand.sort(key=lambda x: (-x["count"], -x["users"], x["word"]))
    cand = cand[: max(60, int(top) * 8)]
    # 长词优先：包含关系里次数接近的短词是碎片
    cand.sort(key=lambda x: (-len(x["word"]), -x["count"]))
    kept: list[dict] = []
    for c in cand:
        w = c["word"]
        if any(len(k["word"]) > len(w) and w in k["word"]
               and k["count"] >= c["count"] * 0.8 for k in kept):
            continue
        kept.append(c)
    kept.sort(key=lambda c: (-c["count"], -c["users"], c["word"]))
    return kept[: max(1, min(int(top), 200))]


def path_for(state_dir: Path | str) -> Path:
    return Path(state_dir) / FILE


def load_ignored(state_dir: Path | str) -> list:
    try:
        d = json.loads(path_for(state_dir).read_text(encoding="utf-8") or "{}")
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as e:
        log.info("忽略名单读取失败（按空处理）：%s", e)
        return []
    got = d.get("ignored") if isinstance(d, dict) else None
    if not isinstance(got, list):
        return []
    # **保序**去重（不能 sorted）：写入端按插入顺序丢「最早」的，
    # 这里排一次字母序，那个语义就变成「按拼音丢」了。
    return list(dict.fromkeys(str(x) for x in got if str(x)))


MAX_IGNORED = 2000


def set_ignored(state_dir: Path | str, words) -> list:
    """整份替换忽略名单。

    - **保持插入顺序**（不是字母序）：满了要丢的时候丢的是最早的那个，不是「按拼音最后」的；
    - 去重后超过 2000 条就截断，并在日志里说一声（静默丢用户数据很糟）。
    """
    seen, clean = set(), []
    for w in (words or ()):
        w = str(w).strip()
        if w and w not in seen:
            seen.add(w)
            clean.append(w)
    if len(clean) > MAX_IGNORED:
        log.warning("忽略名单超过 %d 条，丢弃最早的 %d 条", MAX_IGNORED,
                    len(clean) - MAX_IGNORED)
        clean = clean[-MAX_IGNORED:]
    p = path_for(state_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        tmp = p.with_name(f"{p.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        try:
            tmp.write_text(json.dumps({"ignored": clean}, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
            os.replace(tmp, p)
        finally:
            try:
                tmp.unlink()
            except OSError:
                pass
    return clean


def ignore_word(state_dir: Path | str, word: str) -> list:
    w = str(word or "").strip()
    if not w:
        return load_ignored(state_dir)
    # 读-改-写整段持锁（RLock，嵌套 set_ignored 不会自锁）：面板连点两下，
    # 或面板与运行时同时忽略两个词，后写的会把先写的丢掉。
    with _LOCK:
        return set_ignored(state_dir, load_ignored(state_dir) + [w])


def unignore_word(state_dir: Path | str, word: str) -> list:
    w = str(word or "").strip()
    with _LOCK:
        return set_ignored(state_dir, [x for x in load_ignored(state_dir) if x != w])
