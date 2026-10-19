"""子网计算：``.子网 192.168.1.10/24`` / ``.子网 10.0.0.0 255.255.255.0``。

通用插件。纯离线（标准库 ``ipaddress``）。

配路由器、填防火墙、跟人说「这个网段能放几台机器」时用得上。
两种写法都收：CIDR 和后缀掩码；掩码前缀不连续（比如 255.0.255.0）会直接报错，
那种掩码本来就不合法。
"""
from __future__ import annotations

import ipaddress

NAME = "子网计算"
NAME_EN = "Subnet calculator"
DESCRIPTION = "给一个 IP/掩码，算出网段、广播地址、可用范围和主机数。" 
DESCRIPTION_EN = "Work out network, broadcast, usable range and host count."
VERSION = "1.0"


def raw_args(ctx) -> str:
    t = str(getattr(ctx, "text", "") or "").strip()
    body = t[1:].strip() if t[:1] in "./／" else t
    parts = body.split(None, 1)
    return parts[1] if len(parts) > 1 else ""


def calc(spec: str) -> list[str]:
    """返回给人看的多行；非法输入抛 ValueError。"""
    s = str(spec or "").strip().replace(" ", "/") if "/" not in str(spec or "") else str(spec or "").strip()
    parts = s.split()
    if len(parts) == 2 and "." in parts[1]:          # 192.168.1.10 255.255.255.0
        s = f"{parts[0]}/{parts[1]}"
    if "/" not in s:
        raise ValueError("要带掩码：192.168.1.10/24 或 192.168.1.10 255.255.255.0")
    try:
        iface = ipaddress.ip_interface(s)
    except ValueError as e:
        raise ValueError(f"看不懂这个地址：{e}") from None
    net = iface.network
    lines = [f"🌐 地址：{iface.ip}／{net.prefixlen}（掩码 {net.netmask}）",
             f"网段：{net.network_address}",
             f"广播：{net.broadcast_address}",
             f"反掩码（wildcard）：{net.hostmask}"]
    # 绝不能 `list(net.hosts())`：IPv6 的 /64、IPv4 的 /8 会真去构造 2^64 / 1600 万个对象，
    # 事件循环直接卡死或者 OOM。首尾地址可以直接算，数量也是算术。
    if net.num_addresses <= 2:
        # /31（RFC 3021）两个地址都是主机；/32 只有一个地址。
        if net.num_addresses == 2:
            h = list(net)
            lines.append(f"可用主机：2（点对点链路，{h[0]} 与 {h[1]} 都是主机）")
        else:
            lines.append(f"可用主机：1（{net.network_address} 自己，/32 单主机）")
    else:
        first = net.network_address + 1
        last = net.broadcast_address - 1
        lines.append(f"可用范围：{first} ~ {last}")
        lines.append(f"可用主机数：{net.num_addresses - 2}（共 {net.num_addresses} 个地址）")
    try:
        priv = iface.ip.is_private
        lines.append(f"私有地址：{'是' if priv else '否'}" +
                     ("（公网可直接路由）" if not priv else ""))
    except AttributeError:
        pass
    if iface.version == 4 and net.prefixlen <= 30:
        lines.append(f"换算：/{net.prefixlen} = {net.num_addresses} 个地址，"
                     f"{max(0, net.num_addresses - 2)} 台可用")
    return lines


def register(api):
    async def cmd(ctx):
        raw = raw_args(ctx).strip()
        if not raw:
            await ctx.reply("用法：.子网 192.168.1.10/24\n"
                            "或：.子网 10.0.0.0 255.255.255.0")
            return
        try:
            lines = calc(raw)
        except ValueError as e:
            await ctx.reply(str(e))
            return
        await ctx.reply("\n".join(lines))

    api.on_command("子网", cmd, aliases=("subnet", "cidr"), cooldown=2,
                   help=".子网 192.168.1.10/24 —— 网段/广播/可用范围")
