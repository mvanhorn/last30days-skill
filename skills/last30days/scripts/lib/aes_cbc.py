"""Minimal pure-Python AES-128-CBC *decryption* (stdlib only).

Used to decrypt Chromium cookie values in-process. The previous approach piped
ciphertext to ``openssl enc -K <hex key>``, which exposed the derived Safe
Storage key in the child's argv (readable by any local user via ``ps``).
Decrypt-only, AES-128 only, no padding handling (the caller strips PKCS#7).
Verified against the FIPS-197 Appendix C.1 vector and NIST SP 800-38A F.2.2.
Cookie values are tiny, so speed is irrelevant here.
"""

from __future__ import annotations


def _xtime(a: int) -> int:
    a <<= 1
    return (a ^ 0x11B) & 0xFF if a & 0x100 else a


def _mul(a: int, b: int) -> int:
    out = 0
    while b:
        if b & 1:
            out ^= a
        a = _xtime(a)
        b >>= 1
    return out


def _build_sboxes() -> tuple[list[int], list[int]]:
    sbox = [0] * 256
    p = q = 1
    while True:
        # p iterates over the multiplicative group via multiplication by 3.
        p = p ^ ((p << 1) & 0xFF) ^ (0x1B if p & 0x80 else 0)
        # q iterates by division by 3 (the inverse of p).
        q ^= q << 1
        q ^= q << 2
        q ^= q << 4
        q &= 0xFF
        if q & 0x80:
            q ^= 0x09
        rot = q ^ ((q << 1) | (q >> 7)) ^ ((q << 2) | (q >> 6)) ^ ((q << 3) | (q >> 5)) ^ ((q << 4) | (q >> 4))
        sbox[p] = (rot ^ 0x63) & 0xFF
        if p == 1:
            break
    sbox[0] = 0x63
    inv = [0] * 256
    for i, v in enumerate(sbox):
        inv[v] = i
    return sbox, inv


_SBOX, _INV_SBOX = _build_sboxes()
_RCON = [0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1B, 0x36]


def _expand_key(key: bytes) -> list[list[int]]:
    if len(key) != 16:
        raise ValueError("AES-128 key must be 16 bytes")
    words = [list(key[i:i + 4]) for i in range(0, 16, 4)]
    for i in range(4, 44):
        temp = list(words[i - 1])
        if i % 4 == 0:
            temp = temp[1:] + temp[:1]
            temp = [_SBOX[b] for b in temp]
            temp[0] ^= _RCON[i // 4 - 1]
        words.append([a ^ b for a, b in zip(words[i - 4], temp)])
    # 11 round keys of 16 bytes, column-major like the state.
    return [sum(words[r * 4:r * 4 + 4], []) for r in range(11)]


def _decrypt_block(block: bytes, round_keys: list[list[int]]) -> bytes:
    s = [b ^ k for b, k in zip(block, round_keys[10])]
    for rnd in range(9, -1, -1):
        # InvShiftRows (state is column-major: index = col*4 + row).
        s = [s[((c - r) % 4) * 4 + r] for c in range(4) for r in range(4)]
        s = [_INV_SBOX[b] for b in s]
        s = [b ^ k for b, k in zip(s, round_keys[rnd])]
        if rnd:
            out = []
            for c in range(4):
                a0, a1, a2, a3 = s[c * 4:c * 4 + 4]
                out += [
                    _mul(a0, 14) ^ _mul(a1, 11) ^ _mul(a2, 13) ^ _mul(a3, 9),
                    _mul(a0, 9) ^ _mul(a1, 14) ^ _mul(a2, 11) ^ _mul(a3, 13),
                    _mul(a0, 13) ^ _mul(a1, 9) ^ _mul(a2, 14) ^ _mul(a3, 11),
                    _mul(a0, 11) ^ _mul(a1, 13) ^ _mul(a2, 9) ^ _mul(a3, 14),
                ]
            s = out
    return bytes(s)


def decrypt_cbc(key: bytes, iv: bytes, ciphertext: bytes) -> bytes:
    """AES-128-CBC decrypt without unpadding. ``ciphertext`` must be a
    multiple of 16 bytes."""
    if len(iv) != 16:
        raise ValueError("IV must be 16 bytes")
    if len(ciphertext) % 16:
        raise ValueError("ciphertext length must be a multiple of 16")
    round_keys = _expand_key(key)
    out = bytearray()
    prev = iv
    for i in range(0, len(ciphertext), 16):
        block = ciphertext[i:i + 16]
        out += bytes(a ^ b for a, b in zip(_decrypt_block(block, round_keys), prev))
        prev = block
    return bytes(out)
