"""消息索引：SQLite（stdlib）+ FTS5。

**JSONL 仍然是唯一真相**，这个库只是它的索引：删掉 `.db` 不会丢任何数据，
下次启动会自己重建。这么设计是因为 append-only 的 JSONL 已经跑了很久、
格式稳定，把它换成数据库是一次不可逆的迁移；而「加一个索引」随时能退回。

为什么值得多这一层（20 万条 / 42MB 实测，见 tools/bench_sql.py）：

    关键词搜索        362.8 ms → 12.7 ms   (29×)
    某会话最近 50 条   331.3 ms → 1.2 ms    (276×)
    按天 × 角色聚合    ~1.4 s   → 12 ms

也就是说它换来的是「能搜历史」，而不只是快一点 —— 以前面板只能看最新 N 条。

同步是增量的：记住每个文件读到哪个字节，只追加新内容；文件变小
（删会话会重写 chat.jsonl）就整份重建那个来源。行级去重用 sha1 做键，
所以重复同步、重建、并发同步都不会产生重复行。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Iterable

log = logging.getLogger("klee.store")

__all__ = ["MessageIndex"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS msg(
  id      INTEGER PRIMARY KEY,
  k       TEXT NOT NULL UNIQUE,   -- 去重键：来源+时间+会话+角色+正文的 sha1
  src     TEXT NOT NULL,          -- chat / messages
  ts      REAL NOT NULL,
  session TEXT,
  chat_id TEXT,                    -- 会话（group:xxx / private:xxx / web:xxx）：看板按它分组
  day     TEXT,                    -- 本地日期 YYYY-MM-DD：写入时算好，查的时候才能走索引
  character TEXT,
  role    TEXT,
  text    TEXT NOT NULL,
  elapsed REAL,
  via     TEXT
);
CREATE VIRTUAL TABLE IF NOT EXISTS msg_fts
  USING fts5(text, content='msg', content_rowid='id');
"""

#: 索引单独建：老库缺列时要先迁移再建，否则 CREATE INDEX 会先报错
SCHEMA_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_msg_ts      ON msg(ts);
CREATE INDEX IF NOT EXISTS idx_msg_sess    ON msg(session, ts);
CREATE INDEX IF NOT EXISTS idx_msg_char    ON msg(character, ts);
CREATE INDEX IF NOT EXISTS idx_msg_chat    ON msg(chat_id, role, ts);
CREATE INDEX IF NOT EXISTS idx_msg_day     ON msg(day, role);
"""


# 数「常用字」时要忽略的字：虚词、代词、标点。不是要做分词，
# 只是想让人一眼看出「她平时都在聊什么」。
_STOP_CHARS = set("的了是我你他她它们在有不这那就都也还很又和吗呢吧啊呀哦嗯"
                  "着过把被让给对从到说个一上下进出要会能可就才再没么什怎"
                  "0 1 2 3 4 5 6 7 8 9 \n\t")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def _top_chars(texts: list[str], limit: int = 16) -> list[dict]:
    """统计常用汉字（只留汉字，按出现次数）。"""
    from collections import Counter
    c = Counter()
    for t in texts:
        for ch in _CJK_RE.findall(str(t or "")):
            if ch not in _STOP_CHARS:
                c[ch] += 1
    return [{"ch": k, "n": v} for k, v in c.most_common(limit)]


class MessageIndex:
    """把 JSONL 消息流索引进 SQLite，提供搜索 / 统计。"""

    # 入库规则的版本号：改了「哪些行该进索引」就 +1（ensure() 会整表重建）
    EPOCH = 2

    def __init__(self, db_path: Path, sources: dict[str, Path],
                 state_path: Path | None = None):
        self.path = Path(db_path)
        self.sources = {k: Path(v) for k, v in sources.items()}
        self.state_path = Path(state_path or db_path.with_suffix(".offsets.json"))
        self._lock = threading.Lock()
        self._last_sync = 0.0
        self._offsets: dict = self._load_offsets()
        self.error = ""
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.ensure()
        except Exception as e:                                     # noqa: BLE001
            self.error = f"{type(e).__name__}: {e}"

    # ---------- 基础 ----------
    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path, timeout=5)
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=NORMAL")   # WAL 下崩溃最多丢最后几条索引
        con.execute("PRAGMA busy_timeout=3000")
        return con

    #: 索引是**可重建的派生数据**，加列就直接重建，不做逐行迁移
    NEED_COLUMNS = ("chat_id", "day")

    def ensure(self) -> None:
        with self._connect() as con:
            con.executescript(SCHEMA)
            # 索引是派生的（删了能重建），所以「入库规则」变了就直接整表重建：
            # 光在入库时过滤，老库里已经进去的坏行会一直留着（搜索/洞察里
            # 仍会出现「为什么没回」这种错误说明）。epoch 一变就重建一次。
            con.execute("CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT)")
            row = con.execute("SELECT v FROM meta WHERE k='epoch'").fetchone()
            epoch = str(row[0]) if row else ""
            if epoch != str(self.EPOCH):
                log.info("消息索引规则变了（epoch %s → %s），重建一次", epoch or "无", self.EPOCH)
                con.executescript("DROP TABLE IF EXISTS msg_fts; DROP TABLE IF EXISTS msg;")
                con.executescript(SCHEMA)
                self._offsets = {}
                # 偏移量必须真的清掉：bump 了 epoch、表也删了，但偏移量文件说
                # 「已经读到文件末尾」的话，重启后会因为 epoch 一致而永远不再索引
                try:
                    self.state_path.unlink()
                except FileNotFoundError:
                    pass
                except OSError as e:
                    log.warning("索引重建后偏移量文件删不掉（强制写空）：%s", e)
                    self._save_offsets()
                con.execute("INSERT OR REPLACE INTO meta (k, v) VALUES ('epoch', ?)",
                            (str(self.EPOCH),))
            # 列检查放在 epoch 重建之后：刚重建过的表一定是新结构，再查一遍会
            # 立刻触发第二次整表重建（还多打一条误导日志）
            cols = {r[1] for r in con.execute("PRAGMA table_info(msg)")}
            missing = [c for c in self.NEED_COLUMNS if c not in cols]
            if missing:
                # 旧索引没有这些列：整表重建（比 ALTER + 回填简单，而且这里的数据
                # 全部能从 JSONL 重新推出来，删掉不丢东西）
                log.info("消息索引缺少列 %s，重建（一次性）", ",".join(missing))
                con.executescript("DROP TABLE IF EXISTS msg_fts; DROP TABLE IF EXISTS msg;")
                con.executescript(SCHEMA)
                try:
                    self.state_path.unlink()          # 偏移量清零，下轮从头发一遍
                except OSError:
                    pass
                self._offsets = {}
            con.executescript(SCHEMA_INDEXES)

    def _load_offsets(self) -> dict:
        try:
            d = json.loads(self.state_path.read_text(encoding="utf-8"))
            return d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save_offsets(self) -> None:
        try:
            tmp = self.state_path.with_name(self.state_path.name + ".tmp")
            tmp.write_text(json.dumps(self._offsets, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self.state_path)
        except OSError:
            pass

    @staticmethod
    def _key(src: str, row: dict) -> str:
        blob = "|".join(str(row.get(k) or "") for k in
                        ("ts", "session", "character", "role", "text"))
        return hashlib.sha1(f"{src}|{blob}".encode("utf-8")).hexdigest()[:24]

    # ---------- 同步 ----------
    def sync(self, *, force: bool = False, max_rows: int = 20000) -> dict:
        """增量同步。返回 {新行数, 重建的来源}。可被多个线程调用（有锁）。"""
        with self._lock:
            return self._sync_locked(force=force, max_rows=max_rows)

    def _sync_locked(self, *, force: bool = False, max_rows: int = 20000) -> dict:
        """已经持锁时的实现。调用方负责加锁。"""
        if self.error:
            try:
                self.ensure()
                self.error = ""
            except Exception as e:                             # noqa: BLE001
                self.error = f"{type(e).__name__}: {e}"
                return {"added": 0, "rebuilt": [], "error": self.error}
        added = 0
        rebuilt: list[str] = []
        con = self._connect()
        try:
            for src, path in self.sources.items():
                try:
                    size = path.stat().st_size
                except OSError:
                    continue
                off = int(self._offsets.get(src, 0) or 0)
                if force or size < off:                    # 文件被重写过 → 重建
                    con.execute("DELETE FROM msg WHERE src=?", (src,))
                    con.execute("DELETE FROM msg_fts WHERE rowid NOT IN"
                                " (SELECT id FROM msg)")
                    off = 0
                    rebuilt.append(src)
                if size == off:
                    continue
                try:
                    with open(path, "rb") as f:
                        f.seek(off)
                        chunk = f.read()
                except OSError:
                    continue
                end = chunk.rfind(b"\n") + 1 if chunk else 0
                if end <= 0:
                    continue
                consumed = chunk[:end]
                rows = []
                for line in consumed.splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        d = json.loads(line)
                    except ValueError:
                        continue                            # 坏行跳过，下次也不会重来
                    if not isinstance(d, dict):
                        continue
                    if d.get("error"):
                        # 失败那一轮（预算拦下 / 模型报错）的 text 是「为什么没回」，
                        # 不是她说过的话：进了索引就会出现在搜索、洞察和导出里
                        continue
                    text = d.get("text")
                    if text is None:
                        text = d.get("content")
                    if not isinstance(text, str) or not text:
                        continue
                    ts = d.get("ts") or d.get("time") or 0
                    try:
                        ts = float(ts)
                    except (TypeError, ValueError):
                        ts = 0.0
                    row = {
                        "k": self._key(src, {"ts": ts, "session": d.get("session"),
                                             "character": d.get("character"),
                                             "role": d.get("role"), "text": text}),
                        "src": src, "ts": ts,
                        "session": str(d.get("session") or ""),
                        "chat_id": str(d.get("chat_id") or ""),
                        # 本地日期在写入时算一次：SQLite 的 'localtime' 修饰符是逐行
                        # 调 localtime_r，20 万行要 1.3 秒 —— 那是看板慢的真凶
                        "day": (time.strftime("%Y-%m-%d", time.localtime(ts)) if ts > 0 else ""),
                        "character": str(d.get("character") or ""),
                        "role": str(d.get("role") or ""),
                        "text": text[:8000],
                        "elapsed": float(d.get("elapsed") or 0) or None,
                        "via": str(d.get("via") or ""),
                    }
                    rows.append(row)
                    if len(rows) >= max_rows:
                        added += self._insert(con, rows)
                        rows = []
                added += self._insert(con, rows)
                self._offsets[src] = off + end
            con.commit()
            self._save_offsets()
        finally:
            con.close()
        self._last_sync = time.time()
        return {"added": added, "rebuilt": rebuilt}

    @staticmethod
    def _insert(con: sqlite3.Connection, rows: Iterable[dict]) -> int:
        rows = list(rows)
        if not rows:
            return 0
        n = 0
        for r in rows:
            cur = con.execute(
                "INSERT OR IGNORE INTO msg(k,src,ts,session,chat_id,day,character,role,text,elapsed,via)"
                " VALUES(:k,:src,:ts,:session,:chat_id,:day,:character,:role,:text,:elapsed,:via)", r)
            if cur.rowcount:
                con.execute("INSERT INTO msg_fts(rowid, text) VALUES(?, ?)",
                            (cur.lastrowid, r["text"]))
                n += cur.rowcount
        return n

    def maybe_sync(self, every: float = 20.0) -> bool:
        """给 HTTP 线程用的节流版：太快就不扫；**别人正在扫就直接跳过**。

        为什么必须能跳过：首次建库（20 万条约 9 秒）或删库重建时，
        `sync()` 会在整个过程中持着 `_lock`。以前搜索请求会老老实实排队等它，
        于是一个搜索请求能卡 9 秒。搜索宁可少看到最近几秒的新行，也不该卡住。
        """
        if time.time() - self._last_sync < every:
            return False
        if not self._lock.acquire(blocking=False):      # 有人在同步 → 直接返回
            return False
        try:
            return bool(self._sync_locked().get("added"))
        except Exception as e:                                     # noqa: BLE001
            self.error = f"{type(e).__name__}: {e}"
            return False
        finally:
            self._lock.release()

    # ---------- 查询 ----------
    def search(self, q: str, *, limit: int = 30, character: str = "",
               session: str = "", role: str = "", src: str = "") -> list[dict]:
        """全文搜索。中文没有空格，FTS5 的 unicode61 分词会整段当一个词 ——
        所以短查询退化成 LIKE，长查询（≥2 字）优先 FTS。"""
        q = str(q or "").strip()
        if not q:
            return []
        limit = max(1, min(int(limit or 30), 200))
        where, args = [], []
        if character:
            where.append("m.character=?")
            args.append(character)
        if session:
            where.append("m.session=?")
            args.append(session)
        if role:
            where.append("m.role=?")
            args.append(role)
        # `messages` 那份没有 session/character，只有 chat 那份能定位到具体气泡；
        # 全局搜索「跳过去」必须只要 chat，否则点进去无处可去。
        if src:
            where.append("m.src=?")
            args.append(src)
        tail = (" AND ".join(where) + " AND ") if where else ""
        con = self._connect()
        try:
            out: list[dict] = []
            if len(q) >= 2:
                try:
                    fts = ("SELECT m.ts, m.session, m.character, m.role, m.text, m.via,"
                           " snippet(msg_fts, 0, '<<', '>>', '…', 12) AS snip"
                           " FROM msg_fts JOIN msg m ON m.id = msg_fts.rowid"
                           " WHERE msg_fts MATCH ? AND " + tail +
                           " ORDER BY m.ts DESC LIMIT ?")
                    out = [dict(zip(("ts", "session", "character", "role", "text",
                                     "via", "snippet"), r))
                           for r in con.execute(fts, [self._fts_query(q), *args, limit])]
                except sqlite3.OperationalError:
                    out = []
            if not out:                       # 退路：LIKE（短词 / 中文整段 / FTS 语法不合法）
                like = ("SELECT m.ts, m.session, m.character, m.role, m.text, m.via"
                        " FROM msg m WHERE " + tail + "m.text LIKE ?"
                        " ORDER BY m.ts DESC LIMIT ?")
                out = [dict(zip(("ts", "session", "character", "role", "text", "via"), r))
                       for r in con.execute(like, [*args, f"%{q}%", limit])]
                for h in out:                 # LIKE 没有 snippet，这里自己截
                    h["snippet"] = self._snip(str(h.get("text") or ""), q)
            return self._dedupe(out)
        finally:
            con.close()

    @staticmethod
    def _snip(text: str, q: str, span: int = 24) -> str:
        i = text.lower().find(q.lower())
        if i < 0:
            return text[: span * 2] + ("…" if len(text) > span * 2 else "")
        a, b = max(0, i - span), min(len(text), i + len(q) + span)
        return ("…" if a else "") + text[a:i] + "<<" + text[i:i + len(q)] + ">>" \
            + text[i + len(q):b] + ("…" if b < len(text) else "")

    @staticmethod
    def _dedupe(rows: list[dict]) -> list[dict]:
        """同一条消息会同时出现在 chat.jsonl 和 messages.jsonl（两个来源都写），
        搜索里不该出现两次。按 (秒级时间, 正文) 去重，保留先出现的。"""
        seen, out = set(), []
        for r in rows:
            key = (int(float(r.get("ts") or 0)), str(r.get("text") or ""))
            if key in seen:
                continue
            seen.add(key)
            out.append(r)
        return out

    @staticmethod
    def _fts_query(q: str) -> str:
        """把用户输入变成 FTS5 查询：每个词加引号，避免 - " * 被当语法。"""
        parts = [p for p in q.replace('"', " ").split() if p]
        if not parts:
            parts = [q]
        return " AND ".join(f'"{p}"' for p in parts[:8])

    def summary(self, days: int = 14, src: str = "messages") -> dict:
        """看板要的聚合，全部下推到 SQL（不再在 Python 里遍历 20 万行）。

        返回 {total, user, assistant, today, daily[{date,n}], sources[{chat_id,n,last}]}
        """
        days = max(1, min(int(days or 14), 60))
        now = time.time()
        since = now - days * 86400
        out = {"total": 0, "user": 0, "assistant": 0, "today": 0,
               "daily": [], "sources": [], "from_sql": True}
        try:
            con = self._connect()
        except sqlite3.Error as e:
            return {**out, "from_sql": False, "error": str(e)}
        try:
            # 一次扫描同时拿到「逐日」和「角色合计」：
            # 分成两条 SQL 会各扫一遍 20 万行（实测 92 + 67 ms），合并后 60 ms
            # 一次全量扫描，同时得到「角色合计」（全时段）与「逐日」（只留近 N 天）：
            # 分两条 SQL 会各扫一遍 20 万行（实测 92 + 67 ms），合并后 60 ms
            since_day = time.strftime("%Y-%m-%d", time.localtime(since))
            today = time.strftime("%Y-%m-%d")
            # 必须限定 src：chat.jsonl 和 messages.jsonl 里存的是同一批消息，
            # 行键带 src 所以两边都在索引里 —— 不加这个条件，看板数字会翻倍
            rows = con.execute(
                "SELECT day, role, COUNT(*) FROM msg"
                " WHERE role IN ('user','assistant') AND src = ?"
                " GROUP BY day, role", (src,)).fetchall()
            per_day: dict[str, int] = {}
            for day, role, n in rows:
                n = int(n)
                if role == "user":
                    out["user"] += n
                elif role == "assistant":
                    out["assistant"] += n
                if day and day >= since_day:
                    per_day[day] = per_day.get(day, 0) + n
            out["daily"] = [{"date": d, "n": n} for d, n in sorted(per_day.items())]
            out["today"] = per_day.get(today, 0)
            out["total"] = out["user"] + out["assistant"]
            # 来源：按 chat_id 分组（群 / 私聊 / 网页）
            rows = con.execute(
                "SELECT chat_id, COUNT(*) n, MAX(ts) last"
                " FROM msg WHERE role IN ('user','assistant') AND src = ? AND chat_id <> ''"
                " GROUP BY chat_id ORDER BY n DESC LIMIT 8", (src,)).fetchall()
            out["sources"] = [{"chat_id": r[0], "n": int(r[1]), "last": float(r[2] or 0)}
                              for r in rows]
        except sqlite3.Error as e:
            return {**out, "from_sql": False, "error": str(e)}
        finally:
            con.close()
        return out

    def recent_user_texts(self, limit: int = 40, *, min_len: int = 2,
                          max_len: int = 200, src: str = "messages") -> list[str]:
        """最近 N 条用户发言（从新到旧）。

        为什么走索引而不是读 JSONL：`/api/similar` 每条消息都会调一次，
        读 74 MB 的 messages.jsonl 要好几秒；这里走 idx_msg_ts 倒序扫，
        几十毫秒拿完就停。
        """
        limit = max(1, min(int(limit or 40), 500))
        out: list[str] = []
        try:
            con = self._connect()
        except sqlite3.Error:
            return out
        try:
            cur = con.execute(
                "SELECT text FROM msg WHERE role='user' AND text <> '' AND src = ?"
                " ORDER BY ts DESC LIMIT ?", (src, limit * 4))
            for (text,) in cur:
                t = str(text or "").strip()
                if min_len <= len(t) <= max_len:
                    out.append(t)
                if len(out) >= limit:
                    break
        except sqlite3.Error:
            return []
        finally:
            con.close()
        return out

    def stats(self, days: int = 14) -> dict:
        con = self._connect()
        try:
            total = con.execute("SELECT count(*) FROM msg").fetchone()[0]
            by_char = con.execute(
                "SELECT character, count(*) FROM msg GROUP BY 1 ORDER BY 2 DESC"
            ).fetchall()
            by_day = con.execute(
                "SELECT strftime('%Y-%m-%d', ts, 'unixepoch', 'localtime') d, count(*)"
                " FROM msg WHERE ts > ? GROUP BY 1 ORDER BY 1 DESC LIMIT ?",
                (time.time() - days * 86400, days)).fetchall()
            span = con.execute("SELECT min(ts), max(ts) FROM msg").fetchone()
            return {"total": total,
                    "by_character": [{"id": c or "", "n": n} for c, n in by_char],
                    "by_day": [{"d": d, "n": n} for d, n in by_day],
                    "from": span[0] or 0, "to": span[1] or 0}
        finally:
            con.close()

    def insights(self, days: int = 30, src: str = "messages") -> dict:
        """看板的「数据洞察」：活跃热力图、角色分布、常用字、高频短句。

        全部下推到 SQL 聚合（20 万行也只扫一遍），只在算「常用字」时
        取最近 1500 条用户发言到 Python 里数 —— 中文没法用 FTS 的分词做词频。
        """
        days = max(1, min(int(days or 30), 120))
        since = time.time() - days * 86400
        out = {"days": days, "heat": [], "by_character": [], "by_day": [],
               "top_chars": [], "top_phrases": [], "avg_user_len": 0,
               "busiest": {"hour": -1, "weekday": -1, "n": 0}, "from_sql": True,
               "total": 0}
        try:
            con = self._connect()
        except sqlite3.Error as e:
            return {**out, "from_sql": False, "error": str(e)}
        try:
            where = "role IN ('user','assistant') AND src = ? AND ts > ?"
            out["total"] = int(con.execute(
                f"SELECT COUNT(*) FROM msg WHERE {where}", (src, since)).fetchone()[0])
            # 热力图：星期 × 小时。strftime 的 %w 是 0=周日，这里换成 0=周一
            for w, h, n in con.execute(
                    "SELECT CAST(strftime('%w', ts, 'unixepoch', 'localtime') AS INTEGER),"
                    " CAST(strftime('%H', ts, 'unixepoch', 'localtime') AS INTEGER),"
                    f" COUNT(*) FROM msg WHERE {where} GROUP BY 1, 2", (src, since)):
                out["heat"].append({"w": (int(w) + 6) % 7, "h": int(h), "n": int(n)})
            # 角色分布（含收发拆分）
            # 排序按「收发合计」：看板画条用的是 n，按用户条数排会让顺序和数字对不上
            for cid, u, a in con.execute(
                    "SELECT character, SUM(role='user'), SUM(role='assistant')"
                    # 注意不能写 `ORDER BY 2 + 3`：SQLite 会把 2+3 当成常量 5，
                    # 排序就变成「随便」了（测试 3.3 当场抓到）。
                    f" FROM msg WHERE {where} GROUP BY 1"
                    " ORDER BY SUM(role='user') + SUM(role='assistant') DESC", (src, since)):
                out["by_character"].append({"id": cid or "", "user": int(u or 0),
                                            "assistant": int(a or 0),
                                            "n": int(u or 0) + int(a or 0)})
            # 逐日
            for day, n in con.execute(
                    "SELECT strftime('%Y-%m-%d', ts, 'unixepoch', 'localtime') d, COUNT(*)"
                    f" FROM msg WHERE {where} GROUP BY 1 ORDER BY 1", (src, since)):
                out["by_day"].append({"date": day, "n": int(n)})
            # 用户发言长度
            row = con.execute(
                "SELECT AVG(LENGTH(text)), MAX(LENGTH(text)) FROM msg"
                f" WHERE role='user' AND text <> '' AND src = ? AND ts > ?",
                (src, since)).fetchone()
            out["avg_user_len"] = round(float(row[0] or 0), 1) if row else 0
            out["max_user_len"] = int(row[1] or 0) if row else 0
            # 最忙的那一格
            heat = sorted(out["heat"], key=lambda x: -x["n"])
            if heat:
                out["busiest"] = {"weekday": heat[0]["w"], "hour": heat[0]["h"],
                                  "n": heat[0]["n"]}
            # 高频短句（整句完全一样的，只取短句）
            for text, n in con.execute(
                    "SELECT text, COUNT(*) c FROM msg"
                    " WHERE role='user' AND src = ? AND ts > ?"
                    " AND LENGTH(text) BETWEEN 2 AND 12"
                    " GROUP BY text HAVING c >= 2 ORDER BY c DESC, MAX(ts) DESC LIMIT 12",
                    (src, since)):
                out["top_phrases"].append({"text": str(text or ""), "n": int(n)})
            # 常用字：取最近 1500 条用户发言在 Python 里数（中文没法靠 SQL 分词）
            texts = con.execute(
                "SELECT text FROM msg WHERE role='user' AND src = ? AND ts > ?"
                " AND text <> '' ORDER BY ts DESC LIMIT 1500", (src, since)).fetchall()
        except sqlite3.Error as e:
            return {**out, "from_sql": False, "error": str(e)}
        finally:
            con.close()
        out["top_chars"] = _top_chars([str(t[0] or "") for t in texts])
        return out

    def recent(self, session: str, limit: int = 50) -> list[dict]:
        con = self._connect()
        try:
            return [dict(zip(("ts", "session", "character", "role", "text", "via"), r))
                    for r in con.execute(
                        "SELECT ts, session, character, role, text, via FROM msg"
                        " WHERE session=? ORDER BY ts DESC LIMIT ?", (session, limit))]
        finally:
            con.close()

    def forget(self, src: str, *, session: str, character: str, ts_list,
               size: int | None = None) -> int:
        """文件被重写过：把**确实删掉的那些行**从索引里摘掉，并把游标推到新末尾。

        以前重写后只调 `maybe_sync()`，靠「文件比 offset 短」来判断要重建；
        但重写之后马上又会 append 新行（对话就是这样），文件反而更长，
        于是从旧 offset 读出来的是半截行 —— 已经在搜索里删掉的消息还能搜到，
        新消息反而搜不到。这里改成精确删除 + 把 offset 设成当前大小。
        """
        ts_list = [float(t) for t in (ts_list or [])]
        with self._lock:
            hidden = 0
            con = self._connect()
            try:
                for i in range(0, len(ts_list), 400):
                    chunk = ts_list[i:i + 400]
                    marks = ",".join("?" * len(chunk))
                    sql = ("DELETE FROM msg WHERE src=? AND session=? AND character=?"
                           f" AND ts IN ({marks})")
                    hidden += con.execute(sql, [src, session, character, *chunk]).rowcount
                if ts_list:
                    con.execute("DELETE FROM msg_fts WHERE rowid NOT IN (SELECT id FROM msg)")
                con.commit()
                # 游标要等 commit 成功之后再落盘：先写游标的话，
                # commit 失败（磁盘满/被锁）会回滚删除，但游标已经前进 ——
                # 那些消息就永远补不回来了。
                if size is not None:
                    self._offsets[src] = int(size)
                    self._save_offsets()
            finally:
                con.close()
            return max(0, hidden)

    def drop_session(self, session: str, character: str = "") -> int:
        """删会话时同步删索引（JSONL 那边自己会重写，这里不能留残影）。"""
        con = self._connect()
        try:
            if character:
                cur = con.execute("DELETE FROM msg WHERE session=? AND character=?",
                                  (session, character))
            else:
                cur = con.execute("DELETE FROM msg WHERE session=?", (session,))
            con.execute("DELETE FROM msg_fts WHERE rowid NOT IN (SELECT id FROM msg)")
            con.commit()
            return cur.rowcount
        finally:
            con.close()

    def health(self) -> dict:
        try:
            con = self._connect()
            try:
                n = con.execute("SELECT count(*) FROM msg").fetchone()[0]
            finally:
                con.close()
            return {"ok": not self.error, "rows": n,
                    "size": self.path.stat().st_size if self.path.exists() else 0,
                    "error": self.error}
        except Exception as e:                                     # noqa: BLE001
            return {"ok": False, "rows": 0, "error": f"{type(e).__name__}: {e}"}
