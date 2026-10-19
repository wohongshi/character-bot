"""OneBot 11 适配器：NapCat / Lagrange / go-cqhttp 都走这套 HTTP 接口。

这是本项目原本唯一的实现，现在只做一层「把 OneBotHTTP 的方法名对齐到框架协议」
的包装 —— 传输逻辑（base64 上传、段构造、错误码解析）全部留在 `bot/onebot.py`，
这样老代码和测试都不用动。

配置项（state/bot_config.json）：

    "adapter":       "onebot11"          # 选中本适配器
    "napcat_http":   "http://127.0.0.1:3101"
    "napcat_token":  "…"
"""
from __future__ import annotations

from typing import Any, Optional

from bot.onebot import OneBotError, OneBotHTTP

import json

from .base import AdapterError, BaseAdapter, Inbound, register
from .http import header_lookup, safe_equal


@register
class OneBotAdapter(BaseAdapter):
    """OneBot 11（HTTP）：QQ 侧最常用的一套协议。"""

    platform = "onebot11"
    title = "OneBot 11（QQ / NapCat）"
    DESCRIPTION = "接 NapCat / Lagrange 等协议端：功能最全（群成员、撤回、审批）"
    VERSION = "1.0"
    INGRESS = "/onebot/event"
    CONFIG_SCHEMA = {
        "napcat_http": {"type": "str", "label": "协议端地址", "default": "http://127.0.0.1:3101",
                        "description": "NapCat 的 HTTP 服务地址（不是 WebUI 端口）"},
        "napcat_token": {"type": "str", "label": "访问 Token", "default": "",
                         "secret": True,
                         "description": "协议端和事件上报都要用同一个 token"},
    }
    CAPABILITIES = {
        "text": True, "image": True, "voice": True,
        "group_member_list": True, "message_lookup": True,
        "request_approval": True, "recall": True,
    }

    def __init__(self, config: Optional[dict] = None, **kw) -> None:
        super().__init__(config, **kw)
        self.base = str(self.config.get("napcat_http") or "http://127.0.0.1:3101")
        self.token = str(self.config.get("napcat_token") or "")
        self.raw = OneBotHTTP(self.base, self.token)

    # ---------- 入站 ----------
    def parse_inbound(self, method: str, path: str, query: dict,
                      headers: dict, body: bytes) -> Inbound:
        """NapCat 主动 push 过来的事件：校验 token 后原样交给运行时。

        OneBot 11 的事件结构就是这个框架的事件结构，不需要转换 —— 这也是
        为什么当初选它做第一个适配器。
        """
        if method.upper() != "POST":
            return Inbound(status=405, error="只接受 POST")
        if not self.token:
            return Inbound(status=503, error="napcat_token 未配置，拒绝事件")
        auth = header_lookup(headers, "Authorization")
        if not (safe_equal(auth, f"Bearer {self.token}") or safe_equal(auth, self.token)):
            return Inbound(status=403, error="bad token")
        try:
            ev = json.loads(body.decode("utf-8") or "{}")
        except ValueError:
            return Inbound(status=400, error="请求体不是 JSON")
        if not isinstance(ev, dict):
            return Inbound(status=400, error="请求体不是对象")
        return Inbound(event=ev, reply=("application/json", b'{"ok":true}'))

    # ---------- 传输 ----------
    def _wrap(self, fn, *a, **kw):
        """把 OneBotError 统一成 AdapterError，上层只认一种异常。"""
        try:
            return fn(*a, **kw)
        except OneBotError as e:
            raise AdapterError(str(e)) from e

    def send_text(self, chat_id: str, text: str, *,
                  reply_to: Optional[str] = None,
                  at_uid: Optional[str] = None) -> Any:
        return self._wrap(self.raw.send_text, chat_id, text,
                          reply_to=reply_to, at_uid=at_uid)

    def send_image(self, chat_id: str, path: str, caption: Optional[str] = None, *,
                   reply_to: Optional[str] = None,
                   at_uid: Optional[str] = None) -> Any:
        return self._wrap(self.raw.send_image, chat_id, str(path), caption,
                          reply_to=reply_to, at_uid=at_uid)

    def send_voice(self, chat_id: str, path: str, *,
                   reply_to: Optional[str] = None) -> Any:
        return self._wrap(self.raw.send_voice, chat_id, str(path), reply_to=reply_to)

    def send_raw(self, chat_id: str, segments: Any, **kw) -> Any:
        """发原始消息段（插件里要拼 at/reply/image 混合段时用）。"""
        return self._wrap(self.raw.send, chat_id, segments, **kw)

    # ---------- 查询 ----------
    def get_group_member_list(self, group_id: str) -> list:
        return self._wrap(self.raw.get_group_member_list, str(group_id))

    def get_msg(self, message_id: str) -> dict:
        return self._wrap(self.raw.get_msg, str(message_id))

    def get_image(self, file: str) -> dict:
        return self._wrap(self.raw.get_image, str(file))

    def recall(self, message_id: str) -> Any:
        return self._wrap(self.raw.delete_msg, str(message_id))

    def get_login_info(self) -> dict:
        return self._wrap(self.raw.get_login_info)

    # ---------- 审批 ----------
    def set_friend_add_request(self, flag: str, approve: bool = False,
                               remark: str = "") -> Any:
        return self._wrap(self.raw.set_friend_add_request, flag, approve, remark)

    def set_group_add_request(self, flag: str, sub_type: str = "add",
                              approve: bool = False, reason: str = "") -> Any:
        return self._wrap(self.raw.set_group_add_request, flag, sub_type, approve, reason)

    # ---------- 自检 / 配置 ----------
    def health(self) -> dict:
        try:
            st = self.raw.get_status()
            st = st if isinstance(st, dict) else {}
            # HTTP 通了 ≠ QQ 在线：NapCat 掉线时照样回 {"online": false}。
            # 之前只看 bool(st)，面板会把「未登录」显示成健康。
            online = st.get("online")
            ok = bool(online) if online is not None else bool(st)
            return {"ok": ok, "platform": self.platform, "base": self.base,
                    "online": online,
                    "detail": "" if ok else ("QQ 未登录" if online is False
                                             else "get_status 返回空")}
        except Exception as e:                                    # noqa: BLE001
            return {"ok": False, "platform": self.platform, "base": self.base,
                    "error": f"{type(e).__name__}: {e}"}

    def describe(self) -> dict:
        d = super().describe()
        d.update({"base": self.base, "has_token": bool(self.token)})
        return d

    def reconfigure(self, config: dict) -> bool:
        super().reconfigure(config)
        base = str(self.config.get("napcat_http") or self.base).rstrip("/")
        token = str(self.config.get("napcat_token") or "")
        if base != self.base or token != self.token:
            self.base, self.token = base, token
            self.raw = OneBotHTTP(base, token)
        return True

    # ---------- 自带小页面（QQ 自己的事，不摊在运维页上） ----------
    def page(self) -> str:
        """QQ 的登录/审批/告警都在这一个页面里。

        为什么搬进来：面板现在是「多平台」的了，运维页不该知道 QQ 的扫码接口、
        NapCat 的阶段名、好友申请的 flag 长什么样 —— 这些全是这个平台自己的细节。
        """
        return _PAGE_HTML


_PAGE_HTML = """
<div class="card">
  <div class="row" style="justify-content:space-between">
    <div><b>连接状态</b></div>
    <button class="btn" style="padding:3px 9px;font-size:12px" onclick="refreshAll()">⟳ 刷新</button>
  </div>
  <div id="st" class="kv" style="margin-top:6px">读取中…</div>
</div>

<div class="card">
  <h4>扫码登录</h4>
  <div id="qr" class="kv">正在读取二维码…</div>
  <div class="row" style="margin-top:8px">
    <button class="btn" onclick="qrReload()">⟳ 刷新二维码</button>
    <button class="btn" onclick="restartNapcat()">↻ 重启 NapCat 取新码</button>
    <a class="btn" id="webui" target="_blank" rel="noopener">↗ NapCat WebUI</a>
  </div>
  <div class="kv" style="margin-top:6px">被踢下线时在这里扫，不用再等人发截图。</div>
</div>

<div class="card">
  <h4>好友 / 入群请求 <span class="kv" style="font-weight:400">只记录，不自动通过</span></h4>
  <div id="req" class="kv">读取中…</div>
  <div class="row" style="margin-top:8px"><button class="btn" onclick="loadReq()">⟳ 刷新</button></div>
</div>

<div class="card">
  <h4>掉线告警 <span class="kv" style="font-weight:400">最近 20 条</span></h4>
  <div id="alerts" class="kv">读取中…</div>
  <div class="row" style="margin-top:8px"><button class="btn" onclick="loadAlerts()">⟳ 刷新</button></div>
</div>

<script>
let qrTimer = null;
function esc(s){ return ks.esc(s); }
async function loadStatus(){
  const d = await ks.api("/api/bot");
  const ad = ((d && d.adapters) || []).find(a => a.platform === "onebot11") || {};
  const n = (d && d.napcat) || {};
  const note = ad.ready_note
    ? ' · <span class="kv">' + esc(ad.ready_note) + '</span>' : '';
  document.getElementById("st").innerHTML =
    "运行：<b>" + (d && d.online ? "在线" : "待机") + "</b>" +
    " · 协议端：<b>" + (ad.ready ? "已连通" : "未连通") + "</b>" + note +
    "<br>登录：<b>" + (n.is_login ? "已登录" : "未登录") + "</b>" +
    (n.uin ? " · " + esc(n.uin) + (n.nick ? " " + esc(n.nick) : "") : "") +
    (n.phase ? " · 阶段 <code>" + esc(n.phase) + "</code>" : "") +
    (d && d.last_event_age_s != null ? "<br>上次消息：" + d.last_event_age_s + " 秒前" : "");
  const w = document.getElementById("webui");
  if (w) w.href = "http://" + (location.hostname || "127.0.0.1") + ":6099/webui";
}
async function qrReload(){
  const box = document.getElementById("qr");
  const d = await ks.api("/api/napcat/qr");
  if (!d || !d.ok){
    box.innerHTML = "<b>现在没有可扫的码。</b><br>" + esc((d && d.error) || "NapCat 可能不在登录流程里") +
      "<br>点「重启 NapCat 取新码」，或用右侧的 NapCat WebUI 扫码。";
    return;
  }
  const mins = Math.round((d.age_s || 0) / 60);
  const head = d.stale
    ? '<b class="bad">这张码 ' + mins + ' 分钟没更新，多半已失效。</b><br>点「重启 NapCat 取新码」重新生成。<br>'
    : '<b class="ok">' + (d.age_s || 0) + ' 秒前生成的。</b><br>';
  box.innerHTML = head
    + (d.b64 ? '<img src="' + d.b64 + '" alt="QQ 登录二维码" width="200" height="200" ' +
        'style="image-rendering:pixelated;background:#fff;padding:6px;border-radius:10px;margin:8px 0">' : "")
    + (d.url ? '<div style="word-break:break-all;font-size:11px">' + esc(d.url) + "</div>" : "")
    + '<div class="kv" style="margin-top:6px">用手机 QQ 扫上面这张。</div>';
}
async function restartNapcat(){
  if (!confirm("重启 NapCat 会断开当前 QQ 连接并重新生成二维码，确定？")) return;
  document.getElementById("qr").innerHTML = "正在重启 NapCat…（约 10 秒）";
  await ks.post("/api/action", {action: "restart_napcat"});
  setTimeout(() => { qrReload(); loadStatus(); }, 9000);
}
async function loadReq(){
  const box = document.getElementById("req");
  const d = await ks.api("/api/requests?n=20");
  const items = ((d && d.items) || []).filter(x => x.kind && !x.action);
  if (!items.length){ box.innerHTML = '<span class="kv">最近没有待处理的请求</span>'; return; }
  // 用 data- 属性传参：拼 onclick 字符串要在 JS 里再转义一层引号，容易出错
  box.innerHTML = items.map(r => {
    const what = r.kind === "friend" ? "加好友" : ("进群 " + esc(r.group_id || ""));
    const btn = (ok, label) => '<button class="btn" data-flag="' + esc(r.flag) + '" data-kind="'
      + esc(r.kind) + '" data-ok="' + (ok ? "1" : "0") + '">' + label + "</button>";
    return '<div style="padding:5px 0;border-bottom:1px solid var(--line)">'
      + "<b>" + esc(r.nickname || r.user_id || "?") + "</b>（" + esc(r.user_id || "") + "）想" + what
      + (r.comment ? " · 备注：" + esc(r.comment) : "")
      + '<div class="row" style="margin-top:4px">' + btn(true, "通过") + btn(false, "拒绝") + "</div></div>";
  }).join("");
  box.querySelectorAll("button[data-flag]").forEach(b => {
    b.onclick = () => reqAct(b.dataset.flag, b.dataset.kind, b.dataset.ok === "1");
  });
}
async function reqAct(flag, kind, approve){
  if (!confirm((approve ? "通过" : "拒绝") + "这个请求？")) return;
  const d = await ks.post("/api/requests/action", {flag: flag, kind: kind, approve: approve});
  ks.toast(d && d.ok ? (approve ? "✓ 已通过" : "✓ 已拒绝") : ("✗ " + ((d && d.error) || "失败")));
  loadReq();
}
async function loadAlerts(){
  const box = document.getElementById("alerts");
  const d = await ks.api("/api/alerts?n=20");
  const h = (d && d.health) || {};
  const list = ((d && d.alerts) || []).slice().reverse();
  if (!list.length){ box.innerHTML = (h.connected ? '<b class="ok">当前在线</b> · ' : "") + '<span class="kv">还没有告警</span>'; return; }
  box.innerHTML = (h.connected ? '<b class="ok">当前在线</b>' : '<b class="bad">当前离线</b>') +
    '<div style="max-height:200px;overflow:auto;line-height:1.9">' +
    list.slice(0, 8).map(e => "· <b>" + esc(e.time || "") + "</b> " +
      (e.kind === "offline" ? "掉线" : "恢复") + " " +
      esc(String(e.text || "").slice(0, 40)) +
      (e.down_s != null ? ' <span class="kv">离线 ' + e.down_s + "s</span>" : "")).join("<br>") +
    (list.length > 8
      ? '<details style="margin-top:6px"><summary class="kv">更早 ' + (list.length - 8) + " 条</summary>"
        + list.slice(8).map(e => "· " + esc(e.time || "") + " " + esc(String(e.text || "").slice(0, 40))).join("<br>")
        + "</details>"
      : "") +
    "</div>";
}
function refreshAll(){ loadStatus(); qrReload(); loadReq(); loadAlerts(); }
refreshAll();
if (window.parent) qrTimer = setInterval(() => { loadStatus(); loadAlerts(); }, 20000);
</script>
"""
