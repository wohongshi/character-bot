#!/usr/bin/env python3
"""平台账号与密钥：QQ 官方 / 微信公众号 / 飞书 的凭证表单。

这几家都要「AppID + AppSecret（+ Token）」才能收发消息，以前面板里虽然能填，
但是三个坑：

1. **密钥明文回传**：`/api/plugins/schema` 把 `values` 里的 AppSecret 原样吐给前端，
   任何人打开面板就能看到；
2. **字段名被 description 顶掉**：三个必填框分别显示成「同一页」「同一页」
   「Ed25519 校验用」，用户不知道该往哪个框里贴什么；
3. **没有回调地址**：平台后台要你填一个完整 URL，面板从来没告诉过你地址是什么。

这里把规则钉死：密钥只报「填过没有、几位」；字段名用 label；回调地址拼好可复制；
留空不改、清除要显式。
"""
from __future__ import annotations

import json
import sys
import types
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PASS, FAIL = [], []


def check(name, ok, extra=""):
    (PASS if ok else FAIL).append(name)
    print(f"  {'✓' if ok else '✗'} {name}" + (f"   [{extra}]" if extra and not ok else ""))


def section(t):
    print(f"\n== {t}")


def main():
    import server

    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")

    section("1. 补丁语义：留空不改，清除要显式")
    schema = {
        "app_id": {"type": "str", "label": "AppID", "required": True},
        "app_secret": {"type": "str", "label": "AppSecret", "secret": True, "required": True},
        "sandbox": {"type": "bool", "label": "沙箱", "default": False},
        "timeout": {"type": "int", "label": "超时", "default": 10},
    }
    p = server._adapter_patch(schema, {"app_id": "123", "app_secret": "", "sandbox": "true"})
    check("1.1 密钥留空 → 不写", "app_secret" not in p, str(p))
    check("1.2 普通字段照写", p.get("app_id") == "123", str(p))
    check("1.3 布尔字符串收敛成 True", p.get("sandbox") is True, str(p))

    p = server._adapter_patch(schema, {"app_secret": "s3cr3t"})
    check("1.4 填了密钥就写进去", p.get("app_secret") == "s3cr3t", str(p))

    p = server._adapter_patch(schema, {"app_secret": "", "app_id": "9"}, clear=["app_secret"])
    check("1.5 显式清除 → 空串", p.get("app_secret") == "", str(p))

    try:
        server._adapter_patch(schema, {"timeout": "abc"})
        check("1.6 坏数字要报错", False)
    except ValueError as e:
        check("1.6 坏数字要报错（带字段名）", "timeout" in str(e), str(e))

    p = server._adapter_patch(schema, {})
    check("1.7 空提交 = 什么都不改", p == {}, str(p))

    section("2. 密钥不回传前端")
    real_bot = server.BOT
    stub = types.SimpleNamespace(
        config={"qq_app_id": "102000000", "qq_app_secret": "s" * 32,
                "qq_bot_secret": "b" * 32, "qq_sandbox": True,
                "public_base": "https://bot.example.com/"},
        runtime=None)
    try:
        server.BOT = stub
        d = server._plugin_schema("qqofficial")
        check("2.1 AppSecret 不在 values 里", d["values"].get("qq_app_secret") == "", str(d["values"]))
        check("2.2 只报「填过没有 + 几位」",
              d["secret_set"].get("qq_app_secret") == {"set": True, "len": 32}, str(d["secret_set"]))
        check("2.3 非密钥字段照常回值", d["values"].get("qq_app_id") == "102000000", str(d["values"]))
        check("2.4 必填齐了 missing 为空", d["missing"] == [], str(d["missing"]))
        check("2.5 回调地址是绝对路径",
              d["callback"].get("url") == "https://bot.example.com/qqofficial/event", str(d["callback"]))
        check("2.6 声明为绝对", d["callback"].get("absolute") is True)

        stub.config = {"public_base": ""}
        d = server._plugin_schema("wechat")
        check("2.7 没填公网地址 → 只有相对路径",
              d["callback"].get("url") == "" and d["callback"].get("path") == "/wechat/event",
              str(d["callback"]))
        check("2.8 缺项按必填列出来",
              set(d["missing"]) == {"wechat_app_id", "wechat_app_secret", "wechat_token"},
              str(d["missing"]))

        stub.config = {"public_base": "https://x.example.com/"}   # 结尾斜杠不能拼成 //
        d = server._plugin_schema("feishu")
        check("2.9 结尾斜杠不会拼出 //",
              d["callback"]["url"] == "https://x.example.com/feishu/event", str(d["callback"]))
    finally:
        server.BOT = real_bot

    section("3. 四家平台的必填项")
    from bot import adapters as ad
    want = {
        "qqofficial": {"qq_app_id", "qq_app_secret", "qq_bot_secret"},
        "wechat": {"wechat_app_id", "wechat_app_secret", "wechat_token"},
        "feishu": {"feishu_app_id", "feishu_app_secret", "feishu_verification_token"},
        # token 也是必填：空着 parse_inbound 直接 503，一条事件都进不来
        # （面板会标「缺 1 项」，而密钥已存过时不会被误报，见 4.5）
        "onebot11": {"napcat_http", "napcat_token"},
    }
    got = {x["name"]: {k for k, f in (x.get("schema") or {}).items() if f.get("required")}
           for x in ad.system_plugins()}
    for k, v in want.items():
        check(f"3.x {k} 必填 = {sorted(v)}", got.get(k) == v, str(got.get(k)))

    section("4. 面板接线")
    check("4.1 密钥框是 password", 'type="password" id="pls_' in html)
    check("4.2 回填时 value 为空（不回显密钥）",
          'value="" placeholder="' in html and "已保存 ${info.len} 位" in html)
    check("4.3 有显示/清除按钮", "peekSecret(" in html and "clearSecret(" in html)
    check("4.4 必填星号", 'class="req"' in html and "必填" in html)
    # 密钥已存过、这次留空 → 不算缺（否则「留空不改」和「必填」互相打架，永远存不进去）
    check("4.5 必填校验认已保存的密钥",
          "__pdSecret" in html and "return !stored[k]" in html)
    check("4.5 字段名用 label 而不是 description",
          "esc(f.label || k)" in html and "const hint = esc(f.description" in html)
    check("4.6 回调地址那一行", "callbackRowHTML" in html and "回调地址（填到平台后台）" in html)
    check("4.7 缺项清单", "missingHTML" in html and "还差" in html)
    check("4.8 测一下按钮", "testAdapter(" in html and "测一下" in html)
    check("4.9 保存时带 clear", "clear.push(k)" in html and "values, clear" in html)
    check("4.10 保存前先查必填", "还差必填：" in html)
    check("4.11 平台卡片显示缺项", "缺 ${p.missing.length} 项" in html)
    check("4.12 配置页有公网地址", 'k:"public_base"' in html)
    check("4.13 服务端有自检接口", '"/api/adapters/test"' in html)
    check("4.14 错误不甩内部类名",
          'for pref in ("AdapterError: "' in (ROOT / "server.py").read_text(encoding="utf-8"))

    section("5. 活服务抽查")
    base = "http://127.0.0.1:8798"
    hdr = {"X-Klee-Studio": "1", "Content-Type": "application/json"}

    def post(path, body):
        req = urllib.request.Request(base + path, data=json.dumps(body).encode(), headers=hdr)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    try:
        st, d = post("/api/adapters/test", {"name": "onebot11"})
        check("5.1 自检接口可用", st == 200 and "ok" in d, f"{st} {d}")
        check("5.2 自检带耗时", isinstance(d.get("ms"), int), str(d)[:120])
        st, d = post("/api/adapters/test", {"name": "不存在的平台"})
        check("5.3 未知平台 400", st == 400, f"{st} {d}")

        req = urllib.request.Request(base + "/api/plugins/schema?name=qqofficial", headers=hdr)
        with urllib.request.urlopen(req, timeout=15) as r:
            d = json.loads(r.read())
        raw = json.dumps(d, ensure_ascii=False)
        cfg = json.loads((ROOT / "state" / "bot_config.json").read_text(encoding="utf-8")) \
            if (ROOT / "state" / "bot_config.json").exists() else {}
        secret = str(cfg.get("qq_app_secret") or "")
        check("5.4 接口里搜不到已保存的密钥", not secret or secret not in raw)
        check("5.5 回调地址字段存在", "callback" in d and d["callback"].get("path") == "/qqofficial/event")
    except (urllib.error.URLError, OSError, ValueError) as e:
        print(f"  · 服务没起来，跳过（{type(e).__name__}）")

    print(f"\nPASS {len(PASS)} / {len(PASS) + len(FAIL)}")
    if FAIL:
        print("失败：" + "、".join(FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
