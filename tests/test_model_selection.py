"""模型选择：选了就得算数，不能「保存成功、实际换了个模型」。

这一组守的是 class-level 的坑：`providers[].models` 是**面板下拉里显示哪些**，
不是「上游支持哪些」。它被当成白名单用过一次，结果：

  · 选了清单里没有的模型（刚 discover 到、没勾进清单）→ 保存后静默回退到第一项；
  · vision 的模型不在清单里 → 状态里直接变成「未配置」，用户看到「明明配了却没了」；
  · 而面板的「保存」按钮在 select 里没有对应 option 时，提交的是空值 ——
    一次误点就把看图能力关掉了（前端已加确认，这里是后端那半边）。
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bot.llm import ModelStore          # noqa: E402

PASS = FAIL = 0


def check(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}  {detail}")


tmp = Path(tempfile.mkdtemp())
path = tmp / "models.json"
path.write_text(json.dumps({
    "active": {"provider": "p1", "model": "m1"},
    "providers": [{"id": "p1", "name": "P1", "api": "http://x/v1",
                   "key_env": "K", "models": ["m1", "m2"]}],
}, ensure_ascii=False), encoding="utf-8")
st = ModelStore(path)

# ---------- active ----------
print("· 聊天模型")
st.set_active("p1", "m9")                       # 不在下拉清单里的模型
check("选了清单外的模型 → 不会被偷偷换掉", st.active()["model"] == "m9", str(st.active()))
check("选了就自动登记进清单（下次下拉能选到）",
      "m9" in st.get_provider("p1")["models"], str(st.get_provider("p1")["models"]))
st.set_active("p1", "m2")
check("正常模型照常保存", st.active()["model"] == "m2", str(st.active()))
on_disk = json.loads(path.read_text(encoding="utf-8"))
check("落盘了（不是只在内存里）",
      on_disk["active"]["model"] == "m2", str(on_disk["active"]))

# ---------- vision ----------
print("\n· 看图模型")
st.set_vision("p1", "v9")
v = st.vision()
check("清单外也能配（不再假装未配置）", v["configured"] is True and v["model"] == "v9", str(v))
check("同时补进清单，面板能选中", "v9" in st.get_provider("p1")["models"])
st.set_vision("p1", "m1")
check("换成清单内的模型", st.vision()["configured"] and st.vision()["model"] == "m1",
      str(st.vision()))
st.set_vision("p1", "")
check("传空 = 关闭看图", st.vision()["configured"] is False, str(st.vision()))

# ---------- agent ----------
print("\n· Agent 模型")
st.set_agent("p1", "a7")
a = st.agent()
check("清单外也能配", a["model"] == "a7", str(a))
check("补进清单", "a7" in st.get_provider("p1")["models"])
st.set_agent("", "")
check("空 = 跟聊天同一个", st.agent()["model"] == st.active()["model"], str(st.agent()))

# ---------- provider 被删 ----------
print("\n· 容错")
st.set_vision("p1", "m2")
st.data["providers"] = []
check("provider 没了 → vision 说未配置而不是崩", st.vision()["configured"] is False,
      str(st.vision()))
check("provider 没了 → agent 退回聊天模型", st.agent()["model"] == st.active()["model"],
      str(st.agent()))

print(f"\nPASS {PASS} / {PASS + FAIL}")
sys.exit(1 if FAIL else 0)
