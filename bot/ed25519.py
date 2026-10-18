"""Ed25519 签名/验签（RFC 8032），纯标准库。

为什么自己写：QQ 官方机器人开放平台的回调校验要求用 Ed25519 签名（op 13 的
`plain_token`，以及每条事件的 `X-Signature-Ed25519`）。这台机器上不能随便
`pip install pynacl`（整个项目的卖点是零运行时依赖），而 Ed25519 本身只有
一百来行：SHA-512 + 一条曲线加法。

实现要点（都是 RFC 8032 的原文要求，别改）：

- 私钥是 32 字节 **seed**，不是展开后的标量；`public` 由 `a·B` 算出来。
- 签名前对 seed 做 SHA-512，前 32 字节 clamp 成标量 `a`，后 32 字节当前缀。
- 签名是 `R || S`，共 64 字节；`S` 必须小于曲线阶 `L`，否则视为非法。
- 验签时对 `[S]B = R + [h]A` 做比较，且拒绝非规范点。

自检：`python3 bot/ed25519.py` 会跑 RFC 8032 的测试向量。
"""
from __future__ import annotations

import hashlib
import os

__all__ = ["public_key", "sign", "verify", "keypair"]

# 曲线参数（edwards25519）
_P = 2 ** 255 - 19
_L = 2 ** 252 + 27742317777372353535851937790883648493   # 曲线的阶
_D = -121665 * pow(121666, _P - 2, _P) % _P
_I = pow(2, (_P - 1) // 4, _P)


def _sha512(data: bytes) -> bytes:
    return hashlib.sha512(data).digest()


def _inv(x: int) -> int:
    return pow(x, _P - 2, _P)


def _recover_x(y: int, sign: int) -> int | None:
    if y >= _P:
        return None
    x2 = (y * y - 1) * _inv(_D * y * y + 1) % _P
    if x2 == 0:
        return None if sign else 0
    x = pow(x2, (_P + 3) // 8, _P)
    if (x * x - x2) % _P != 0:
        x = x * _I % _P
    if (x * x - x2) % _P != 0:
        return None
    if (x & 1) != sign:
        x = _P - x
    return x


_BY = 4 * _inv(5) % _P
_BX = _recover_x(_BY, 0)
_B = (_BX, _BY, 1, _BX * _BY % _P)          # 扩展坐标 (X, Y, Z, T)


def _add(p, q):
    x1, y1, z1, t1 = p
    x2, y2, z2, t2 = q
    a = (y1 - x1) * (y2 - x2) % _P
    b = (y1 + x1) * (y2 + x2) % _P
    c = t1 * 2 * _D * t2 % _P
    d = z1 * 2 * z2 % _P
    e, f, g, h = b - a, d - c, d + c, b + a
    return (e * f % _P, g * h % _P, f * g % _P, e * h % _P)


def _mul(s: int, p):
    q = (0, 1, 1, 0)                        # 单位元
    while s > 0:
        if s & 1:
            q = _add(q, p)
        p = _add(p, p)
        s >>= 1
    return q


def _encode(p) -> bytes:
    x, y, z, _ = p
    zi = _inv(z)
    x, y = x * zi % _P, y * zi % _P
    return int.to_bytes(y | ((x & 1) << 255), 32, "little")


def _decode(data: bytes):
    if len(data) != 32:
        return None
    y = int.from_bytes(data, "little")
    sign = y >> 255
    y &= (1 << 255) - 1
    x = _recover_x(y, sign)
    if x is None:
        return None
    return (x, y, 1, x * y % _P)


def _expand(seed: bytes):
    if len(seed) != 32:
        raise ValueError("Ed25519 seed 必须是 32 字节")
    h = _sha512(seed)
    a = int.from_bytes(h[:32], "little")
    a &= (1 << 254) - 8
    a |= 1 << 254
    return a, h[32:]


def public_key(seed: bytes) -> bytes:
    """由 32 字节 seed 推出 32 字节公钥。"""
    a, _ = _expand(seed)
    return _encode(_mul(a, _B))


def sign(seed: bytes, msg: bytes) -> bytes:
    """签名，返回 64 字节 `R || S`。"""
    a, prefix = _expand(seed)
    r = int.from_bytes(_sha512(prefix + msg), "little") % _L
    r_enc = _encode(_mul(r, _B))
    pub = _encode(_mul(a, _B))
    k = int.from_bytes(_sha512(r_enc + pub + msg), "little") % _L
    s = (r + k * a) % _L
    return r_enc + int.to_bytes(s, 32, "little")


def verify(pub: bytes, msg: bytes, sig: bytes) -> bool:
    """验签。任何格式错误都返回 False，不抛异常（回调里不能因此 500）。"""
    if len(pub) != 32 or len(sig) != 64:
        return False
    a = _decode(pub)
    r = _decode(sig[:32])
    if a is None or r is None:
        return False
    s = int.from_bytes(sig[32:], "little")
    if s >= _L:
        return False
    k = int.from_bytes(_sha512(sig[:32] + pub + msg), "little") % _L
    left = _mul(s, _B)
    right = _add(r, _mul(k, a))
    return _encode(left) == _encode(right)


def keypair() -> tuple[bytes, bytes]:
    seed = os.urandom(32)
    return seed, public_key(seed)


def _selftest() -> int:
    # RFC 8032 §7.1 的前两组测试向量
    vectors = [
        ("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60",
         "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a",
         "",
         "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e0652249015"
         "55fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"),
        ("4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb",
         "3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c",
         "72",
         "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da"
         "085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00"),
    ]
    ok = True
    for sk_hex, pk_hex, msg_hex, sig_hex in vectors:
        seed = bytes.fromhex(sk_hex)
        msg = bytes.fromhex(msg_hex)
        pk = public_key(seed)
        sig = sign(seed, msg)
        good = (pk.hex() == pk_hex and sig.hex() == sig_hex
                and verify(pk, msg, sig) and not verify(pk, msg + b"x", sig))
        ok = ok and good
        print(("✓" if good else "✗"), "RFC 8032 vector", pk.hex()[:16], "…", sig.hex()[:16], "…")
    # QQ 官方机器人的用法：secret 重复到 32 字节当 seed
    seed = (b"DG5g3B4j9X2KOErG" * 3)[:32]
    pk = public_key(seed)
    sig = sign(seed, b"1718000000" + b"Arq0D5Aq9fW8j8dQ")
    print("✓ QQ 风格 seed 可用:", verify(pk, b"1718000000" + b"Arq0D5Aq9fW8j8dQ", sig))
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":                                   # pragma: no cover
    raise SystemExit(_selftest())
