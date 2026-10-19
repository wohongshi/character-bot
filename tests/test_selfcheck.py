#!/usr/bin/env python3
"""系统自检（`bot/selfcheck.py` + `/api/selfcheck`）。

判三件事：
1. 「一切正常」的时候不能报虚警（否则用户就学会无视它了）；
2. 每一类真问题都能被查出来，并且带上能修的修复动作；
3. 修复动作真的把问题修掉（不是只回一句「已修复」）。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PASS, FAIL = [], []
BASE = "http://127.0.0.1:8798"
HDR = {"X-Klee-Studio": "1"}


def check(name, ok, extra=""):
    (PASS if ok else FAIL).append(name)
    print(f"  {'✓' if ok else '✗'} {name}" + (f"   [{extra}]" if extra and not ok else ""))


def section(t):
    print(f"\n== {t}")


def env_for(tmp: Path, **kw) -> dict:
    base = {"state_dir": tmp, "features_dir": tmp / "features",
            "index": {"ok": True, "rows": 10},
            "model": {"provider": "p", "model": "m", "has_key": True, "local": False},
            "adapter": {"platforms": [{"name": "onebot11", "connected": True, "detail": ""}],
                        "auth_streak": 0, "name": "onebot11"},
            "workers": {"chat-worker-1": True},
            "orphan_sessions": [], "cards_dir": tmp / "images"}
    base.update(kw)
    return base


def by_id(items):
    return {i["id"]: i for i in items}


def main():
    from bot import selfcheck as sc

    tmp = Path(tempfile.mkdtemp(prefix="klee-chk-"))
    (tmp / "features").mkdir(parents=True)
    (tmp / "chat.jsonl").write_text("".join('{"x":1}\n' for _ in range(30)), encoding="utf-8")
    (tmp / "bot_config.json").write_text('{"a":1}', encoding="utf-8")
    (tmp / "features" / "config.json").write_text('{"enabled":[]}', encoding="utf-8")
    (tmp / "usage.jsonl").write_text(
        json.dumps({"ts": time.time(), "total_tokens": 10}) + "\n", encoding="utf-8")

    section("1. 干净的环境不该报警")
    items = sc.run(env_for(tmp, index={"ok": True, "rows": 30}))
    ids = by_id(items)
    check("1.1 覆盖了 12 类检查", len(items) == 12, str([i["id"] for i in items]))
    bads = [i["id"] for i in items if i["level"] == "bad"]
    check("1.2 没有 bad 项", not bads, str(bads))
    check("1.3 整体 ok", sc.overall(items) == "ok", sc.overall(items))
    check("1.4 每一项字段齐全",
          all({"id", "name", "level", "detail"} <= set(i) for i in items))
    check("1.5 索引一致时不建议重建", not ids["index"].get("fix"), ids["index"])

    section("2. 索引漂移 / 报错")
    items = sc.run(env_for(tmp, index={"ok": True, "rows": 2}))
    it = by_id(items)["index"]
    check("2.1 差 28 条 → warn", it["level"] == "warn", it)
    check("2.2 带重建动作", it["fix"] == "index_rebuild", it)
    items = sc.run(env_for(tmp, index={"ok": False, "error": "database is locked"}))
    it = by_id(items)["index"]
    check("2.3 索引报错 → bad", it["level"] == "bad", it)
    check("2.4 详情里有原因", "locked" in it["detail"], it["detail"])

    section("3. 配置坏了 / 写不进去")
    (tmp / "bot_config.json").write_text("{ 坏掉的", encoding="utf-8")
    items = sc.run(env_for(tmp))
    check("3.1 配置解析失败 → bad", by_id(items)["config"]["level"] == "bad")
    (tmp / "bot_config.json").write_text('{"a":1}', encoding="utf-8")
    deep = Path("/proc/self/definitely/not/writable")
    items = sc.run(env_for(deep))
    check("3.2 状态目录不可写 → bad", by_id(items)["state_write"]["level"] == "bad")

    section("4. 模型")
    items = sc.run(env_for(tmp, model={"provider": "", "model": ""}))
    check("4.1 没选模型 → bad", by_id(items)["model"]["level"] == "bad")
    items = sc.run(env_for(tmp, model={"provider": "p", "model": "m", "has_key": False,
                                       "local": True}))
    check("4.2 本地网关没填 key 算正常", by_id(items)["model"]["level"] == "ok")
    # 用量文件改成很久以前 → 应该提示「最近没调用」
    old = tmp / "usage.jsonl"
    old.write_text(json.dumps({"ts": time.time() - 10 * 86400}) + "\n", encoding="utf-8")
    items = sc.run(env_for(tmp, model={"provider": "p", "model": "m", "has_key": True}))
    check("4.3 三天没成功调用 → warn", by_id(items)["model"]["level"] == "warn")
    old.write_text(json.dumps({"ts": time.time()}) + "\n", encoding="utf-8")

    section("5. 平台 / 线程")
    items = sc.run(env_for(tmp, adapter={"platforms": [
        {"name": "onebot11", "connected": False, "detail": "connection refused"}],
        "auth_streak": 0}))
    it = by_id(items)["adapter"]
    check("5.1 连不上 → bad", it["level"] == "bad", it)
    check("5.2 详情带上适配器的报错", "refused" in it["detail"], it["detail"])
    items = sc.run(env_for(tmp, adapter={"platforms": [
        {"name": "onebot11", "connected": True, "detail": ""}], "auth_streak": 7}))
    it = by_id(items)["adapter"]
    check("5.3 连续被拒 → bad 且指向鉴权", it["level"] == "bad" and "token" in it["hint"], it)
    items = sc.run(env_for(tmp, workers={"chat-worker-1": True, "sched-loop": False}))
    it = by_id(items)["workers"]
    check("5.4 线程死了 → bad", it["level"] == "bad", it)
    check("5.5 点名是哪个线程", "sched-loop" in it["detail"], it["detail"])

    section("6. 缓存超限 + 清理")
    tts = tmp / "tts_cache"
    tts.mkdir()
    for i in range(90):
        (tts / f"v{i}.wav").write_bytes(b"x" * 10)
    items = sc.run(env_for(tmp))
    it = by_id(items)["cache"]
    check("6.1 语音超过 80 段 → warn", it["level"] == "warn" and "90" in it["detail"], it)
    res = sc.fix(env_for(tmp), "trim_cache")
    left = len(list(tts.glob("*.wav")))
    check("6.2 清理后只剩 40 段", res["ok"] and left == 40, f"{left} {res}")
    check("6.3 报告里说了删了多少", any("50" in d for d in res["done"]), str(res["done"]))
    # 用量记录超上限
    (tmp / "usage.jsonl").write_text("".join('{"ts":1}\n' for _ in range(20050)), encoding="utf-8")
    sc.fix(env_for(tmp), "trim_cache")
    n = len((tmp / "usage.jsonl").read_text().splitlines())
    check("6.4 用量记录裁到 2 万行", n == 20000, str(n))

    section("7. 修复动作的分发")
    called = {}
    res = sc.fix(env_for(tmp), "index_rebuild",
                 {"index_rebuild": lambda: called.setdefault("n", 1) and None or {"added": 42}})
    check("7.1 重建动作被调用", called.get("n") == 1 and "42" in res["done"][0], str(res))
    res = sc.fix(env_for(tmp), "index_rebuild", {})
    check("7.2 没 hooks 时明确说不行", not res["ok"] and "没有索引" in res["error"], str(res))
    res = sc.fix(env_for(tmp), "unknown_action")
    check("7.3 未知动作被拒", not res["ok"], str(res))
    res = sc.fix(env_for(tmp, orphan_sessions=["s1", "s2"]), "drop_orphan_sessions",
                 {"drop_orphan_sessions": lambda: 2})
    check("7.4 孤儿会话修复报告数量", res["ok"] and "2" in res["done"][0], str(res))

    section("7b. has_key 读的是 key_env 指向的变量")
    # 模型的 key **从不落盘**：provider 里只存 key_env（变量名）。
    # 读 prov["key"] 永远是空 → 配好的云端模型会被常报「没填密钥」。
    import server as S

    class FakeModels:
        data = {}

        def active(self):
            return {"provider": "cloud", "model": "m1"}

        def get_provider(self, pid):
            return {"id": pid, "api": "https://api.example.com/v1", "key_env": "KLEE_FAKE_KEY"}

    class FakeBot:
        models = FakeModels()
        runtime = None

    os.environ.pop("KLEE_FAKE_KEY", None)
    env = S._selfcheck_env(FakeBot())
    check("7b.1 没设变量 → has_key False", env["model"]["has_key"] is False, str(env["model"]))
    os.environ["KLEE_FAKE_KEY"] = "sk-test"
    env = S._selfcheck_env(FakeBot())
    check("7b.2 设了变量 → has_key True", env["model"]["has_key"] is True, str(env["model"]))
    os.environ.pop("KLEE_FAKE_KEY", None)

    class LanModels(FakeModels):
        def get_provider(self, pid):
            return {"id": pid, "api": "http://10.0.0.5:8000/v1", "key_env": "KLEE_FAKE_KEY"}

    class LanBot(FakeBot):
        models = LanModels()

    env = S._selfcheck_env(LanBot())
    check("7b.3 内网网关（10.x）算本地", env["model"]["local"] is True, str(env["model"]))
    model_item = by_id(sc.run(env))["model"]
    check("7b.4 内网网关没 key 也不报警", model_item["level"] == "ok", model_item["detail"])

    section("8. 活服务")
    def get(url):
        req = urllib.request.Request(url, headers=HDR)
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, json.loads(r.read())

    try:
        code, d = get(BASE + "/api/selfcheck")
        check("8.1 接口 200", code == 200 and d.get("ok"), str(code))
        check("8.2 条目齐全", len(d.get("items") or []) == 12, str(len(d.get("items") or [])))
        check("8.3 带整体等级", d.get("level") in ("ok", "warn", "bad"), str(d.get("level")))
        check("8.4 带计数", set((d.get("counts") or {})) == {"ok", "warn", "bad"}, str(d.get("counts")))
        # 真服务上不该有「状态目录不可写」这种硬伤
        bads = [i["id"] for i in d["items"] if i["level"] == "bad"]
        check("8.5 生产环境没有 bad 项", not bads, str(bads))
        wk = next(i for i in d["items"] if i["id"] == "workers")
        # 一次性线程（cache-warm）跑完就退，不能算成「后台任务已停止」——
        # 第一版就误报过它，所以这里既看等级也看数字
        check("8.6 后台任务全绿（含一次性线程不误报）", wk["level"] == "ok", wk["detail"])
        # 刚建几秒的会话还没进索引（worker 20 秒一轮）：只靠 SQL 会把标签当孤儿删掉
        import server as S2
        tmp2 = Path(tempfile.mkdtemp(prefix="klee-known-"))
        real_chat = S2.CHAT_JSONL
        try:
            p2 = tmp2 / "chat.jsonl"
            p2.write_text(json.dumps({"session": "brand-new", "character": "klee",
                                      "role": "user", "text": "hi", "ts": time.time()},
                                     ensure_ascii=False) + "\n", encoding="utf-8")
            S2.CHAT_JSONL = p2
            check("8.7b 没进索引的新会话也算「有记录」",
                  "brand-new" in S2._known_chat_sessions(), str(S2._known_chat_sessions()))
        finally:
            S2.CHAT_JSONL = real_chat
        # 索引问不到时必须 fail-closed（返回空集 = 不知道），
        # 只拿尾部凑一份不完整名单会让所有老会话被判成孤儿、一点就被删
        real_index = S2._index
        class DeadIndex:
            def _connect(self):
                raise RuntimeError("database is locked")
        try:
            S2._index = lambda: DeadIndex()
            check("8.7c 索引不可用时返回空集（不判孤儿）",
                  S2._known_chat_sessions() == set(), str(S2._known_chat_sessions()))
        finally:
            S2._index = real_index
        n_workers = int("".join(ch for ch in wk["detail"] if ch.isdigit()) or 0)
        check("8.7 长驻线程都被数到（≥4）", n_workers >= 4, wk["detail"])
    except (urllib.error.URLError, OSError) as e:
        print(f"  · 服务没起来，跳过（{type(e).__name__}）")

    print(f"\nPASS {len(PASS)} / {len(PASS) + len(FAIL)}")
    if FAIL:
        print("失败：" + "、".join(FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
