"""/api/plugins/config 的合并规则（防「一次空对象清空所有插件」）。

这个接口原来的实现是「拿 body.config 直接覆盖 config.json」：
一次 `POST {"config":{}}` 就把 118 个插件的启用表清成空，而且没有备份。
现在改成纯函数校验 + 合并，这里把规则钉死。
"""
import sys
from pathlib import Path

STUDIO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(STUDIO))

from server import apply_plugins_config  # noqa: E402

PASS = FAIL = 0
KNOWN = {"help", "weather", "repeater", "dice"}


def check(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name} {detail}")


def run(cur, body):
    return apply_plugins_config(dict(cur), body, KNOWN)


print("· 拒绝会清空的写法")
cfg = {"enabled": ["help", "weather"], "settings": {"x": {"a": 1}}}
out, err = run(cfg, {"config": {}})
check("空 config 被拒", bool(err) and "enabled" in err, err)
check("被拒时不改动原配置", out == cfg, out)

out, err = run(cfg, {"config": {"enabled": []}})
check("显式空 enabled 允许（用户真的要全关）", not err and out["enabled"] == [], err)

out, err = run(cfg, {"config": {"settings": {}}})
check("缺 enabled 的整份替换被拒", bool(err), err)

out, err = run(cfg, {"config": {"enabled": ["nope"]}})
check("不存在的插件名被拒", bool(err) and "nope" in err, err)

out, err = run(cfg, {})
check("空 body 被拒", bool(err), err)

print("\n· 单点开关")
out, err = run(cfg, {"set": {"repeater": True}})
check("打开一个：新增并排序", not err and out["enabled"] == ["help", "repeater", "weather"], out["enabled"])
out, err = run(cfg, {"set": {"help": False}})
check("关掉一个：从列表移除", not err and out["enabled"] == ["weather"], out["enabled"])
out, err = run(cfg, {"set": {"help": False, "dice": True}})
check("一次改多个", not err and out["enabled"] == ["dice", "weather"], out["enabled"])
out, err = run(cfg, {"set": {"ghost": True}})
check("未知名字被拒", bool(err) and "ghost" in err, err)

print("\n· 全量列表")
out, err = run(cfg, {"enabled": ["weather", "help", "weather"]})
check("去重 + 排序", not err and out["enabled"] == ["help", "weather"], out["enabled"])
out, err = run(cfg, {"enabled": "help"})
check("非数组被拒", bool(err), err)

print("\n· 不碰别人的字段")
out, err = run(cfg, {"set": {"help": False}})
check("settings 原样保留", not err and out.get("settings") == cfg["settings"], out.get("settings"))
out, err = run({"settings": {"x": 1}}, {"set": {"help": True}})
check("没有 enabled 字段时也能自愈", not err and out["enabled"] == ["help"], out)
out, err = run({"enabled": "garbage"}, {"set": {"help": True}})
check("enabled 是脏数据时不炸", not err, err)

print(f"\nPASS {PASS} / {PASS + FAIL}")
sys.exit(1 if FAIL else 0)
