"""网页 —— 面板自带的消息平台（系统插件 `web`）。

为什么它是「一个平台」而不是一段设置
------------------------------------
面板里的聊天本来就跑在一个 web 会话上（`chat_id = web:<session>`）。把它登记成
一个系统插件之后：

- 插件里 `ctx.platform == "web"`，不再「没配 QQ 就假装是 onebot11」；
- 定时提醒、主动播报可以把消息 `send` 到网页会话（不用先登录任何平台）；
- 「网页」这件事有了自己的小页面：会话列表、试发、清空 —— 平台细节留在平台里，
  运维页只做汇总（和 onebot11 的扫码页一个道理）。

它**常开**：关掉它等于把面板自己的聊天掐了，没有意义。所以面板上不给开关。
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, Optional

from .base import BaseAdapter, register

log = logging.getLogger("klee.bot.adapters.web")

#: 没有 KLEE_STATE_DIR 时的兜底（bot/adapters/web.py → 仓库根/state）
_ROOT = Path(__file__).resolve().parents[2]


def _state_dir() -> Path:
    import os
    env = os.environ.get("KLEE_STATE_DIR")
    return Path(env) if env else (_ROOT / "state")


def _active_character() -> str:
    """当前角色：网页消息要带 character 字段，否则历史里会串到别人身上。"""
    try:
        d = json.loads((_state_dir() / "characters.json").read_text(encoding="utf-8"))
        return str(d.get("active") or "").strip().lower()
    except (OSError, ValueError, TypeError):
        return ""


@register
class WebAdapter(BaseAdapter):
    platform = "web"
    title = "网页"
    DESCRIPTION = "面板自带的网页聊天。不用任何平台账号，装上就有；它也是「消息平台」之一。"
    VERSION = "1.0"
    DEFAULT_OFF = False          # 常开（见模块说明）
    INGRESS = None               # 入站走 /api/chat，不走适配器 ingress
    CAPABILITIES: Dict[str, bool] = {
        "text": True, "image": False, "voice": False,
        "group_member_list": False, "message_lookup": False,
        "request_approval": False, "recall": False,
    }
    CONFIG_SCHEMA: Dict[str, dict] = {
        "web_push": {"type": "bool", "label": "允许主动消息", "default": True,
                     "description": "定时提醒、播报这类「没人问也要说话」的消息，"
                                    "要不要推到网页会话里。关掉只是不主动说话，聊天不受影响。"},
    }

    # ---------- 出站 ----------
    def send_text(self, chat_id: str, text: str, *,
                  reply_to: Optional[str] = None,
                  at_uid: Optional[str] = None) -> Any:
        """把一条助手消息写进网页会话（面板轮询 /api/chat 会看到）。

        正常对话不走这里（server.py 自己落库）；这条路径是给「主动说话」用的：
        定时提醒、晨报、插件播报。
        """
        if not bool(self.config.get("web_push", True)):
            return {"success": False, "error": "网页主动消息已关闭（插件设置里可开）"}
        session = str(chat_id or "").strip()
        if session.startswith("web:"):
            session = session[4:]
        session = session or "studio-main"
        txt = str(text or "").strip()
        if not txt:
            return {"success": False, "error": "空消息"}
        d = _state_dir()
        cid = _active_character()
        row = {"ts": time.time(), "session": session, "role": "assistant",
               "text": txt, "character": cid or "klee", "via": "push"}
        try:
            d.mkdir(parents=True, exist_ok=True)
            with (d / "chat.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        except OSError as e:
            log.warning("写网页会话失败：%s", e)
            return {"success": False, "error": str(e)}
        return {"success": True, "chat_id": "web:" + session, "session": session}

    def health(self) -> dict:
        """网页平台永远「在线」—— 它就是这台进程自己。"""
        return {"ok": True, "platform": "web", "detail": "面板自带，无需外部服务"}

    # ---------- 自带小页面 ----------
    def page(self) -> str:
        """小型 webui：会话列表 + 试发一条 + 清空。

        跑在同源 iframe 里，`ks.api` 已经注入了（见 static/index.html 的
        pluginFrameDoc）。所有读写都是面板已有的接口，不新开后台通道。
        """
        return _PAGE_HTML


_PAGE_HTML = """
<div class="card">
  <div class="row" style="justify-content:space-between">
    <div><b>网页聊天</b> <span class="tag ok">面板自带 · 常开</span></div>
    <button class="btn" onclick="openChat()">去聊天 →</button>
  </div>
  <div class="kv" style="margin-top:6px">这个平台不需要任何账号：面板打开就能用。
    它的会话和 QQ 的会话在同一个消息库里，只是 <code>chat_id</code> 以 <code>web:</code> 开头。</div>
</div>

<div class="card">
  <h4>会话</h4>
  <div id="sess" class="kv">读取中…</div>
</div>

<div class="card">
  <h4>试发一条</h4>
  <div class="kv" style="margin-bottom:6px">走真实链路（模型 + 插件 + agent），用来验证「不用 QQ 也能跑通」。</div>
  <div class="row">
    <input id="msg" placeholder="说点什么，回车发送" style="flex:1;min-width:160px"
           onkeydown="if(event.key==='Enter')sendMsg()">
    <button class="btn primary" id="sendBtn" onclick="sendMsg()">发送</button>
  </div>
  <pre id="out" style="display:none;max-height:220px"></pre>
</div>

<script>
function openChat(){ parent.postMessage({ks:"goto", view:"chat"}, "*"); }
async function loadSessions(){
  const box = document.getElementById("sess");
  const d = await ks.api("/api/chat/sessions");
  const list = (d && d.sessions) || [];
  if (!list.length){ box.innerHTML = '<span class="kv">还没有会话</span>'; return; }
  box.innerHTML = list.slice(0, 12).map(s =>
    `<div class="row" style="justify-content:space-between;padding:4px 0;border-bottom:1px solid var(--line)">
       <span><code>${ks.esc(s)}</code></span>
       <span class="row" style="gap:6px">
         <button class="btn" style="padding:2px 8px;font-size:12px" onclick="peek('${ks.esc(s)}')">看最后一条</button>
         <button class="btn" style="padding:2px 8px;font-size:12px" onclick="clearOne('${ks.esc(s)}')">清空</button>
       </span>
     </div>`).join("");
}
async function peek(s){
  const out = document.getElementById("out");
  const d = await ks.api("/api/chat?session=" + encodeURIComponent(s));
  const ms = (d && d.messages) || [];
  const last = ms.slice(-3).map(m => "[" + m.role + "] " + String(m.text || "").slice(0, 200));
  out.style.display = "block";
  out.textContent = "会话 " + s + "（" + ms.length + " 条）\\n" + (last.join("\\n") || "（空）");
}
async function clearOne(s){
  if (!confirm("清空会话 " + s + " 的聊天记录？")) return;
  const d = await ks.post("/api/chat/delete", {session: s});
  ks.toast(d && d.ok ? "✓ 已清空" : ("✗ " + ((d && d.error) || "失败")));
  loadSessions();
}
async function sendMsg(){
  const inp = document.getElementById("msg");
  const out = document.getElementById("out");
  const btn = document.getElementById("sendBtn");
  const text = (inp.value || "").trim();
  if (!text) return;
  btn.disabled = true; out.style.display = "block"; out.textContent = "发送中…";
  const t0 = Date.now();
  try {
    const d = await ks.post("/api/chat", {session: "web-console", text: text});
    const ms = (d && d.messages) || [];
    const reply = ms.length ? ms[ms.length - 1] : null;
    out.textContent = (reply ? reply.text : JSON.stringify(d))
      + "\\n\\n（" + ((Date.now() - t0) / 1000).toFixed(2) + " 秒）";
    inp.value = "";
    if (d && d.ok === false) ks.toast("✗ " + (d.error || "发送失败"));
  } catch (e) {
    out.textContent = "✗ " + e;
  } finally { btn.disabled = false; loadSessions(); }
}
loadSessions();
</script>
"""
