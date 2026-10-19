#!/usr/bin/env python3
"""第四轮功能增强（自动朗读 / 全局搜索升级 / 编辑重发）的回归。

三层判据：
- 后端能真跑：`/api/commands`、`/api/search?src=`、`/api/chat/edit`、索引精确回退；
- 前端契约：面板里该有的元素/分支/守卫都在（JS 不好在 Python 里跑，就锁形状）；
- 不污染生产状态：临时会话、临时目录一律清掉。
"""
from __future__ import annotations

import json
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PASS, FAIL = [], []


def check(name, ok, extra=""):
    (PASS if ok else FAIL).append(name)
    print(f"  {'✓' if ok else '✗'} {name}" + (f"   [{extra}]" if extra and not ok else ""))


BASE = "http://127.0.0.1:8798"
H = {"X-Klee-Studio": "1"}


def post(path, body, timeout=120):
    req = urllib.request.Request(
        BASE + path, data=json.dumps(body).encode(),
        headers={**H, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8", "replace") or "{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8", "replace") or "{}")
        except ValueError:
            return e.code, {}


def get(path):
    req = urllib.request.Request(BASE + path, headers=H)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read().decode("utf-8", "replace") or "{}")
    except urllib.error.HTTPError as e:
        return e.code, {}


def rows(session, cid):
    _st, d = get(f"/api/chat?session={urllib.parse.quote(session)}&character={cid}")
    return d.get("messages") or []


def wait_rows(session, cid, n, timeout=40.0):
    end = time.time() + timeout
    while time.time() < end:
        r = rows(session, cid)
        if len(r) >= n:
            return r
        time.sleep(0.5)
    return rows(session, cid)


def main() -> int:
    import server as S
    from bot.store import MessageIndex

    page = (ROOT / "static" / "index.html").read_text(encoding="utf-8")

    print("[1] 自动朗读：开关、上限、只念新回复")
    check("1.1 输入行有开关按钮", 'id="autoSpk"' in page and "toggleAutoSpeak()" in page)
    check("1.2 偏好存在本地（换个设备不该互相影响）", 'ks_autospeak' in page)
    check("1.3 超过上限不自动念", "AUTO_SPEAK_MAX" in page and "太长就不自动念" in page)
    check("1.4 只挑助手回复", "m.role !== \"assistant\"" in page and "function maybeAutoSpeak" in page)
    check("1.5 解锁发生在手势里（不是合成完才 play）",
          "function unlockAudio" in page and "if (on){ unlockAudio();" in page)
    check("1.6 停止时不销毁元素（否则解锁状态丢了）",
          "st.audio = null" not in page.split("function stopSpeak()")[1].split("/* ---------- 自动朗读")[0])
    check("1.7 迟到的 ended 不能当播完", "if (!a.ended) return;" in page and "a.currentTime + 0.2 < dur" in page)
    check("1.8 换页/换会话就停", page.count("stopSpeak();") >= 3)

    print("\n[2] 全局搜索：命令清单")
    st, d = get("/api/commands")
    cmds = d.get("commands") or []
    check("2.1 能拿到命令", st == 200 and d.get("ok") and len(cmds) > 10, f"{st} {len(cmds)}")
    names = [c["name"] for c in cmds]
    check("2.2 别名不重复占一行", len(names) == len(set(names)), str(len(names)))
    check("2.3 每条都有 help（不然搜出来也不知道干什么）",
          all(c.get("help") for c in cmds), str([c["name"] for c in cmds if not c.get("help")])[:80])
    check("2.4 内置命令显示「内置」而不是 __builtin__",
          all(c["title"] not in ("__builtin__", "_builtin", "builtin") for c in cmds),
          str([c["title"] for c in cmds if c["title"] in ("__builtin__", "_builtin")])[:80])
    check("2.5 带所属插件标题", all(c.get("title") for c in cmds))
    check("2.6 和当前角色一致", d.get("character") == S._active_cid(), str(d.get("character")))

    print("\n[3] 全局搜索：只跳得进去的那份消息")
    tmp = Path(tempfile.mkdtemp(prefix="klee-r4-idx-"))
    try:
        chat = tmp / "chat.jsonl"
        msg = tmp / "messages.jsonl"
        chat.write_text(json.dumps({"ts": 1.0, "session": "s1", "character": "klee",
                                    "role": "user", "text": "火龙果暗号一"}, ensure_ascii=False) + "\n",
                        encoding="utf-8")
        msg.write_text(json.dumps({"ts": 2.0, "chat_id": "web:s1", "role": "user",
                                   "text": "火龙果暗号二"}, ensure_ascii=False) + "\n",
                       encoding="utf-8")
        idx = MessageIndex(tmp / "t.db", {"chat": chat, "messages": msg}, tmp / "off.json")
        idx.sync()
        both = idx.search("火龙果暗号")
        only_chat = idx.search("火龙果暗号", src="chat")
        check("3.1 不过滤时两份都能搜到", len(both) == 2, str(len(both)))
        check("3.2 src=chat 只留能定位会话的那份", len(only_chat) == 1 and only_chat[0]["session"] == "s1",
              str(only_chat)[:160])
        check("3.3 返回里带 snippet（面板要高亮）", bool(only_chat and only_chat[0].get("snippet")))

        print("\n[4] 索引精确回退（重写文件后不能留残影）")
        # 再写三行，索引与文件同步
        with open(chat, "a", encoding="utf-8") as f:
            for i, t in enumerate(["蓝莓一号", "蓝莓二号", "蓝莓三号"]):
                f.write(json.dumps({"ts": 10.0 + i, "session": "s2", "character": "klee",
                                    "role": "user", "text": t}, ensure_ascii=False) + "\n")
        idx.sync()
        check("4.1 三行都进索引了", len(idx.search("蓝莓", src="chat")) == 3,
              str(len(idx.search("蓝莓", src="chat"))))
        # 模拟 _chat_rewrite：删掉 10.0 / 11.0 两行，然后在文件末尾追加一行
        lines = [ln for ln in chat.read_text(encoding="utf-8").splitlines() if ln.strip()]
        keep = [ln for ln in lines
                if not (json.loads(ln).get("session") == "s2"
                        and float(json.loads(ln).get("ts") or 0) in (10.0, 11.0))]
        chat.write_text("\n".join(keep) + "\n", encoding="utf-8")
        # offset 要在「重写完、新行还没写进来」这一刻量 —— 生产里 _chat_rewrite
        # 就是在锁内量完再放锁的，之后 worker 才 append。
        size_at_rewrite = chat.stat().st_size
        with open(chat, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": 20.0, "session": "s3", "character": "klee",
                                "role": "user", "text": "蓝莓四号"}, ensure_ascii=False) + "\n")
        idx.forget("chat", session="s2", character="klee", ts_list=[10.0, 11.0],
                   size=size_at_rewrite)
        idx.sync()
        got = sorted(h["text"] for h in idx.search("蓝莓", src="chat"))
        check("4.2 删掉的两条不再出现", "蓝莓一号" not in got and "蓝莓二号" not in got, str(got))
        check("4.3 只追加的那条能搜到（offset 没跑偏）", "蓝莓四号" in got, str(got))
        check("4.4 没删的那条还在", "蓝莓三号" in got, str(got))
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
    check("4.5 临时目录已删", not tmp.exists())

    print("\n[5] 编辑重发：后端")
    cid = "klee"
    sess = "t-r4-%d" % int(time.time())
    try:
        st, _ = post("/api/chat", {"session": sess, "text": "甲：第一句", "character": cid})
        r = wait_rows(sess, cid, 2)
        check("5.1 第一轮落了两行", len(r) == 2, str(len(r)))
        first_ts = float(r[0]["ts"])
        post("/api/chat", {"session": sess, "text": "乙：第二句", "character": cid})
        r = wait_rows(sess, cid, 4)
        check("5.2 第二轮四行", len(r) == 4, str(len(r)))

        st, d = post("/api/chat/edit", {"session": sess, "character": cid,
                                        "ts": first_ts, "text": "丙：改过的一句"})
        check("5.3 编辑成功且删掉了这条之后的全部", st == 200 and d.get("removed") == 4,
              f"{st} {d}")
        r = wait_rows(sess, cid, 2)
        users = [m.get("text") for m in r if m.get("role") == "user"]
        check("5.4 旧的两句都没了", "甲：第一句" not in users and "乙：第二句" not in users, str(users))
        check("5.5 新的那句在", any("丙：改过的一句" in (t or "") for t in users), str(users))
        check("5.6 又有了新一轮回复", len(r) >= 2 and r[-1].get("role") == "assistant", str(len(r)))

        st, d = post("/api/chat/edit", {"session": sess, "character": cid,
                                        "ts": first_ts, "text": "再改"})
        check("5.7 旧 ts 已经不存在 → 404", st == 404, f"{st} {d}")
        st, d = post("/api/chat/edit", {"session": sess, "character": cid, "ts": 1.0, "text": " "})
        check("5.8 空文本 → 400", st == 400, str(st))
        asst = [m for m in r if m.get("role") == "assistant"][0]
        st, d = post("/api/chat/edit", {"session": sess, "character": cid,
                                        "ts": float(asst["ts"]), "text": "想改她的回复"})
        check("5.9 不能改助手的话 → 400", st == 400 and "自己" in str(d.get("error")), f"{st} {d}")

        # 生成中不许改：真去占住 worker（测试进程里的 _chat_state 是另一份，
        # 直接改它没用 —— 服务在另一个进程里）。
        target_ts = float(rows(sess, cid)[0]["ts"])
        post("/api/chat", {"session": sess, "character": cid,
                           "text": "写五句简短的话，一句一行。"})
        seen_running = False
        for _ in range(120):
            _st, dd = get(f"/api/chat?session={urllib.parse.quote(sess)}&character={cid}")
            if dd.get("running"):
                seen_running = True
                break
            time.sleep(0.05)
        st, d = post("/api/chat/edit", {"session": sess, "character": cid,
                                        "ts": target_ts, "text": "偷改"})
        if seen_running:
            check("5.10 正在生成时 → 409", st == 409 and "生成" in str(d.get("error")), f"{st} {d}")
        else:
            check("5.10 正在生成时 → 409（这次没抓到生成窗口，跳过）", True, "")

        # 等队列排空再清，否则 worker 会在删除之后又追加一行
        for _ in range(120):
            _st, dd = get(f"/api/chat?session={urllib.parse.quote(sess)}&character={cid}")
            if not dd.get("running") and not dd.get("queue"):
                break
            time.sleep(0.5)
    finally:
        post("/api/chat/delete", {"session": sess, "character": cid})
    left = rows(sess, cid)
    check("5.11 收尾：测试会话已清空", not left, str(len(left)))

    print("\n[6] 前端契约：搜索与编辑")
    check("6.1 搜索框有防抖", "cmdTimer = setTimeout" in page and "}, 160)" in page)
    check("6.2 输入法组词时的 Enter 不提交", "isComposing" in page and "keyCode === 229" in page)
    check("6.3 消息结果点进去要跳转", "function cmdkJumpTo" in page and 'classList.add("hit")' in page)
    check("6.4 命令结果填进输入框", "function cmdkFillCommand" in page and '+"."+name' not in page)
    check("6.5 搜索只要 chat 那份", "src=chat" in page)
    check("6.6 用户消息才有「编辑」", 'role === "user" ? `<button onclick="editMsg(this)"' in page)
    check("6.7 编辑条会说明后果", "会作废" in page and 'id="ebar"' in page)
    check("6.8 发送分岔到 /api/chat/edit", 'post("/api/chat/edit"' in page)
    check("6.9 换会话时清掉编辑态", "chatEdit = null;                              // 换会话时" in page)
    check("6.10 轮询按 ts 去重（跳转时两个 poll 不会插两遍）",
          ":scope > .msg[data-ts]" in page and "chat.after = Math.max(chat.after, j.total)" in page)

    print(f"\nPASS {len(PASS)} / {len(PASS) + len(FAIL)}")
    if FAIL:
        print("失败：" + "、".join(FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
