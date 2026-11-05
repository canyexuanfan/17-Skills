#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""wb_auth.py —— market-lens 自包含的本机 WorkBuddy 凭证发现与解密模块。

作用：让本 Skill 的用户在**自己的机器**上找到并使用**自己的** WorkBuddy 客户端登录态
（accessToken），实现零配置认证。模块特性：

  · 只读：仅读取登录态文件与密钥材料，绝不修改、绝不写盘、绝不打印完整凭证
    （对外只暴露 mask_token() 掩码形式）；
  · 不含任何硬编码凭证：所有凭证均来自用户本机登录态 / 用户自己设置的环境变量；
  · 兼容 WorkBuddy 5.6.2+ AES-256-GCM 加密登录态（明文 JWT 与加密信封双兼容）；
  · 密钥多级定位：环境变量 WORKBUDDY_ATREST_KEY / WORKBUDDY_ATREST_KEY_FILE →
    密钥文件扫描 → DPAPI blob → 运行中客户端进程内存扫描。

来源声明：本模块由作者本人的 totorosir-workbuddy-checkin（v3.1.7，开源仓库 canyexuanfan/agent-checkin-automation，MIT）技能中的登录态
解密段原样移植（同一作者），算法复刻自 WorkBuddy 客户端 packages/at-rest-crypto。
"""

import base64
import hashlib
import json
import os
import sys
import time


# ===========================================================================
# 登录态文件定位
# ===========================================================================

def _auth_candidates():
    """按操作系统返回 WorkBuddy 登录态文件候选路径，依次探测。"""
    home = os.path.expanduser("~")
    cands = []
    if sys.platform.startswith("win"):
        for env in ("LOCALAPPDATA", "APPDATA"):
            base = os.environ.get(env, "")
            if base:
                cands.append(os.path.join(
                    base, "CodeBuddyExtension", "Data", "Public",
                    "auth", "workbuddy-desktop.info"))
    elif sys.platform == "darwin":
        cands.append(os.path.join(
            home, "Library", "Application Support", "CodeBuddyExtension",
            "Data", "Public", "auth", "workbuddy-desktop.info"))
    else:  # linux / 其他类 Unix
        cands.append(os.path.join(
            home, ".config", "CodeBuddyExtension", "Data", "Public",
            "auth", "workbuddy-desktop.info"))
    # 兜底：便携版 / 未知布局（与 ~/.workbuddy 同根）
    cands.append(os.path.join(
        home, ".workbuddy", "auth", "workbuddy-desktop.info"))
    return [p for p in cands if p]


def find_auth_file():
    for p in _auth_candidates():
        if os.path.isfile(p):
            return p
    return None


def _check_expiry(auth):
    """登录态已过期时给出友好提示（只读取 expiresAt，绝不打印 token）。"""
    raw = auth.get("expiresAt")
    if not raw:
        return  # 无过期字段则跳过检查
    try:
        exp = int(raw)
    except (TypeError, ValueError):
        return
    # 兼容 epoch 毫秒（13 位）/ 秒（10 位）
    if exp > 10 ** 11:
        exp = exp / 1000.0
    if exp <= time.time():
        expire_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(exp))
        raise RuntimeError(
            "登录态已过期（过期时间 %s），请重新登录 WorkBuddy 客户端后再试" % expire_str)


# =============================================================================
# WorkBuddy 5.6.2+ 登录态 AtRestEncryption 解密（读端兼容，自包含）
# -----------------------------------------------------------------------------
# 背景：5.6.2 客户端把 AtRestEncryption 的 buildMode 从 disabled 改为 required，
# 登录态 workbuddy-desktop.info 中的 auth.accessToken（及 refreshToken 等敏感字段）
# 由明文 JWT 变成 AES-256-GCM 信封： {"$wbEncrypted":1, "envelope":"<base64>"}
# 旧脚本直接把字段当明文 JWT 读取 -> 鉴权失败 -> 技能无法签到。
#
# 本段仅做「读端」兼容解密，复刻客户端 packages/at-rest-crypto 的算法：
#   envelope(suite=1) = {suite, keyId, nonce(b64), authTag(b64), ciphertext(b64)}
#   AES 密钥 key = SHA256(atRestSecretKey 字符串 UTF-8)  -> 32 字节
#   keyId      = SHA256(key 字节).hexdigest()[:16]
#   AAD(sym-v1, framing=field) = 见 _build_field_aad()
# atRestSecretKey 由客户端原生模块 electron.workbuddyStorage.loggerGet() 运行时提供，
# 5.6.2 起不落盘、只驻留运行中的 WorkBuddy.exe 进程内存（同一把密钥也用于包裹
# ~/.workbuddy/keyblob 里的主密钥）。因此这里按多级策略自动定位：
#   环境变量 → 密钥文件 → DPAPI blob 扫描（兼容个别落盘情况）→
#   ReadProcessMemory 扫描客户端进程内存（keyId 定向 + base64 候选严格校验）。
# 密钥材料仅在内存中校验使用，绝不写盘、绝不打印。
# =============================================================================

# ---- 常量（与客户端 at-rest-crypto/dist/key-normalize / field.mjs 完全一致）----
_AAD_DOMAIN = b"WB-AAD\x00"
_FRAMING_CODE = {"file": 1, "field": 2, "record": 3, "stream": 4}
_STANDARD_FORMAT_ID = {"file": "WBEF1", "field": "WBEV1", "record": "WBER1", "stream": "WBES1"}


def _b64_canonical_decode(value, field):
    """复刻 decodeCanonicalBase64：标准 base64、末尾补 =，且与重编码一致才算合法。"""
    if not isinstance(value, str):
        raise ValueError("%s 不是字符串" % field)
    decoded = base64.b64decode(value)
    if base64.b64encode(decoded).decode("ascii") != value:
        raise ValueError("%s 不是规范 base64" % field)
    return decoded


def _derive_at_rest_key(at_rest_secret_key):
    """复刻 normalizeAtRestKeyPayload：key=SHA256(secret字符串UTF-8)，keyId=SHA256(key)[:16]。

    返回 (key_32bytes, key_id_16hex)。
    """
    if not isinstance(at_rest_secret_key, str) or len(at_rest_secret_key) != 44:
        raise ValueError("atRestSecretKey 必须是 44 字符规范 base64（32 字节）")
    # 校验是规范 base64 且解码为 32 字节
    raw = _b64_canonical_decode(at_rest_secret_key, "atRestSecretKey")
    if len(raw) != 32:
        raise ValueError("atRestSecretKey 解码后必须是 32 字节")
    key = hashlib.sha256(at_rest_secret_key.encode("utf-8")).digest()
    key_id = hashlib.sha256(key).hexdigest()[:16]
    return key, key_id


def _build_field_aad(key_id):
    """复刻 buildAuthenticatedContextAad(keyId, suite=1, {framing:'field'}, scheme='sym-v1')。"""
    def encode_uint32(v):
        return v.to_bytes(4, "big")
    def encode_length_prefixed(s):
        b = s.encode("utf-8")
        return encode_uint32(len(b)) + b
    return b"".join([
        _AAD_DOMAIN,
        bytes([1]),
        encode_length_prefixed(_STANDARD_FORMAT_ID["field"]),  # "WBEV1"
        encode_length_prefixed("sym-v1"),
        encode_uint32(1),                                       # suite
        encode_length_prefixed(key_id),                          # 16 hex 字符
        bytes([_FRAMING_CODE["field"]]),                         # 2
        bytes([0]),                                              # sequence 缺失
        bytes([0]),                                              # final 缺失
    ])


# ---- AES-256-GCM 后端（cryptography > PyCryptodome > 纯 Python 兜底）----
def _aes_gcm_decrypt(key, nonce, ciphertext, aad, auth_tag):
    """AES-256-GCM 解密，返回明文 bytes；校验失败抛 IntegrityError。"""
    # 1) cryptography（WorkBuddy 托管 Python 自带，优先）
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        aes = AESGCM(key)
        return aes.decrypt(nonce, ciphertext + auth_tag, aad)
    except ImportError:
        pass
    # 2) PyCryptodome（常见纯 Python 可安装包）
    try:
        from Crypto.Cipher import AES  # type: ignore
        cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
        cipher.update(aad)
        return cipher.decrypt_and_verify(ciphertext, auth_tag)
    except ImportError:
        pass
    # 3) 纯 Python 兜底（零依赖，已与 cryptography 对拍校验）
    return _aes_gcm_decrypt_pure(key, nonce, ciphertext, aad, auth_tag)


def _aes_gcm_encrypt(key, nonce, plaintext, aad):
    """AES-256-GCM 加密，返回 (ciphertext, auth_tag)；供自测 roundtrip 使用。"""
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        aes = AESGCM(key)
        ct_and_tag = aes.encrypt(nonce, plaintext, aad)
        return ct_and_tag[:-16], ct_and_tag[-16:]
    except ImportError:
        pass
    try:
        from Crypto.Cipher import AES  # type: ignore
        cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
        cipher.update(aad)
        ct, tag = cipher.encrypt_and_digest(plaintext)
        return ct, tag
    except ImportError:
        pass
    return _aes_gcm_encrypt_pure(key, nonce, plaintext, aad)


# ---- 纯 Python AES-256-GCM 实现（仅作兜底，已与 cryptography 对拍）----
def _aes_gcm_decrypt_pure(key, nonce, ciphertext, aad, auth_tag):
    pt = _aes_gcm_pure(key, nonce, ciphertext, aad, auth_tag, decrypt=True)
    return pt


def _aes_gcm_encrypt_pure(key, nonce, plaintext, aad):
    ct, tag = _aes_gcm_pure(key, nonce, plaintext, aad, None, decrypt=False)
    return ct, tag


def _aes_gcm_pure(key, nonce, data, aad, auth_tag_in, decrypt):
    # AES 密钥扩展
    Nk = len(key) // 4
    Nr = Nk + 6
    RCON = [0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1B, 0x36]
    SBOX = _aes_sbox()
    w = [list(key[4 * i:4 * i + 4]) for i in range(Nk)]
    for i in range(Nk, 4 * (Nr + 1)):
        temp = list(w[i - 1])
        if i % Nk == 0:
            temp = temp[1:] + temp[:1]
            temp = [SBOX[b] for b in temp]
            temp[0] ^= RCON[i // Nk - 1]
        elif Nk > 6 and i % Nk == 4:
            temp = [SBOX[b] for b in temp]
        w.append([w[i - Nk][j] ^ temp[j] for j in range(4)])
    round_keys = [[w[i][j] for i in range(4 * (Nr + 1))] for j in range(4)]

    def xtime(a):
        a <<= 1
        if a & 0x100:
            a ^= 0x11B
        return a & 0xFF

    def gmul(a, b):
        p = 0
        for _ in range(8):
            if b & 1:
                p ^= a
            hi = a & 0x80
            a = (a << 1) & 0xFF
            if hi:
                a ^= 0x1B
            b >>= 1
        return p & 0xFF

    def add_round_key(state, rnd):
        for c in range(4):
            for r in range(4):
                state[r][c] ^= round_keys[r][4 * rnd + c]

    def sub_bytes(state):
        for r in range(4):
            for c in range(4):
                state[r][c] = SBOX[state[r][c]]

    def shift_rows(state):
        state[1] = state[1][1:] + state[1][:1]
        state[2] = state[2][2:] + state[2][:2]
        state[3] = state[3][3:] + state[3][:3]

    def mix_columns(state):
        for c in range(4):
            a = [state[r][c] for r in range(4)]
            state[0][c] = gmul(a[0], 2) ^ gmul(a[1], 3) ^ a[2] ^ a[3]
            state[1][c] = a[0] ^ gmul(a[1], 2) ^ gmul(a[2], 3) ^ a[3]
            state[2][c] = a[0] ^ a[1] ^ gmul(a[2], 2) ^ gmul(a[3], 3)
            state[3][c] = gmul(a[0], 3) ^ a[1] ^ a[2] ^ gmul(a[3], 2)

    def cipher_block(block):
        state = [[block[r + 4 * c] for c in range(4)] for r in range(4)]
        add_round_key(state, 0)
        for rnd in range(1, Nr):
            sub_bytes(state)
            shift_rows(state)
            mix_columns(state)
            add_round_key(state, rnd)
        sub_bytes(state)
        shift_rows(state)
        add_round_key(state, Nr)
        return bytes(state[r][c] for c in range(4) for r in range(4))

    # GCM
    H = cipher_block(b"\x00" * 16)
    # GHASH
    def gf_mult(x, y):
        R = 0xE1000000000000000000000000000000
        z = 0
        v = y
        for i in range(128):
            if (x >> (127 - i)) & 1:
                z ^= v
            if v & 1:
                v = (v >> 1) ^ R
            else:
                v >>= 1
        return z

    def ghash_blocks(blocks):
        y = 0
        for blk in blocks:
            x = int.from_bytes(blk, "big")
            y = gf_mult(y ^ x, int.from_bytes(H, "big"))
        return y.to_bytes(16, "big")

    # 构造计数器块：nonce(12) + 32位计数（从 1 开始）
    def ctr_block(counter):
        cb = nonce + counter.to_bytes(4, "big")
        return cipher_block(cb)

    # 分块（16 字节，末尾补零），空输入返回 []
    def split_blocks(b):
        if not b:
            return []
        pad = b + b"\x00" * ((16 - len(b) % 16) % 16)
        return [pad[i:i + 16] for i in range(0, len(pad), 16)]

    # CTR 加/解密（对称：明文 ^ 密钥流 = 密文）
    # 关键：数据计数器从 inc32(J0) 开始，即 nonce||0x00000002（J0=nonce||0x00000001 只用于给 tag 做 E_K）
    aad_blocks = split_blocks(aad)
    if decrypt:
        ct_blocks = split_blocks(data)          # data = 密文
        out = bytearray()
        for i, blk in enumerate(ct_blocks):
            ks = ctr_block(i + 2)
            out.extend(bytes(blk[j] ^ ks[j] for j in range(len(blk))))
        out = bytes(out[:len(data)])
        ct_for_ghash = data
    else:
        pt_blocks = split_blocks(data)          # data = 明文
        ct = bytearray()
        for i, blk in enumerate(pt_blocks):
            ks = ctr_block(i + 2)
            ct.extend(bytes(blk[j] ^ ks[j] for j in range(len(blk))))
        ct = bytes(ct[:len(data)])
        out = ct
        ct_for_ghash = ct

    # GHASH 必须基于"密文"计算（加密时即 CTR 输出；解密时即输入密文）
    ghash_input = aad_blocks + split_blocks(ct_for_ghash)
    ghash_input.append((len(aad) * 8).to_bytes(8, "big") + (len(ct_for_ghash) * 8).to_bytes(8, "big"))
    S = ghash_blocks(ghash_input)

    # J0：12 字节 nonce 时 J0 = nonce || 0x00000001；其它长度按 GHASH(nonce) 处理
    if len(nonce) == 12:
        j0 = nonce + b"\x00\x00\x00\x01"
    else:
        j0 = ghash_blocks(split_blocks(nonce) + [(len(nonce) * 8).to_bytes(16, "big")])
    ek_j0 = cipher_block(j0)
    tag = bytes(S[k] ^ ek_j0[k] for k in range(16))

    if decrypt:
        if auth_tag_in is not None and int.from_bytes(tag, "big") != int.from_bytes(auth_tag_in, "big"):
            raise ValueError("ciphertext authentication failed (GCM tag mismatch)")
        return out
    else:
        return out, tag


_AES_SBOX_CACHE = None


def _aes_sbox():
    # 纯 Python 兜底用的标准 AES S-Box（公开常量，已验证）。
    return _AES_STD_SBOX


def _rotl(x, n):
    return ((x << n) | (x >> (8 - n))) & 0xFF


def _xtime_lite(x):
    x <<= 1
    if x & 0x100:
        x ^= 0x11B
    return x & 0xFF


def _gmul_lookup(a, b):
    # 仅用于扩展欧几里得，简单实现
    p = 0
    for _ in range(8):
        if b & 1:
            p ^= a
        hi = a & 0x80
        a = (a << 1) & 0xFF
        if hi:
            a ^= 0x1B
        b >>= 1
    return p & 0xFF


# 标准 AES S-Box（公开常量，用于纯 Python 兜底，确保正确性）
_AES_STD_SBOX = [
    0x63, 0x7c, 0x77, 0x7b, 0xf2, 0x6b, 0x6f, 0xc5, 0x30, 0x01, 0x67, 0x2b, 0xfe, 0xd7, 0xab, 0x76,
    0xca, 0x82, 0xc9, 0x7d, 0xfa, 0x59, 0x47, 0xf0, 0xad, 0xd4, 0xa2, 0xaf, 0x9c, 0xa4, 0x72, 0xc0,
    0xb7, 0xfd, 0x93, 0x26, 0x36, 0x3f, 0xf7, 0xcc, 0x34, 0xa5, 0xe5, 0xf1, 0x71, 0xd8, 0x31, 0x15,
    0x04, 0xc7, 0x23, 0xc3, 0x18, 0x96, 0x05, 0x9a, 0x07, 0x12, 0x80, 0xe2, 0xeb, 0x27, 0xb2, 0x75,
    0x09, 0x83, 0x2c, 0x1a, 0x1b, 0x6e, 0x5a, 0xa0, 0x52, 0x3b, 0xd6, 0xb3, 0x29, 0xe3, 0x2f, 0x84,
    0x53, 0xd1, 0x00, 0xed, 0x20, 0xfc, 0xb1, 0x5b, 0x6a, 0xcb, 0xbe, 0x39, 0x4a, 0x4c, 0x58, 0xcf,
    0xd0, 0xef, 0xaa, 0xfb, 0x43, 0x4d, 0x33, 0x85, 0x45, 0xf9, 0x02, 0x7f, 0x50, 0x3c, 0x9f, 0xa8,
    0x51, 0xa3, 0x40, 0x8f, 0x92, 0x9d, 0x38, 0xf5, 0xbc, 0xb6, 0xda, 0x21, 0x10, 0xff, 0xf3, 0xd2,
    0xcd, 0x0c, 0x13, 0xec, 0x5f, 0x97, 0x44, 0x17, 0xc4, 0xa7, 0x7e, 0x3d, 0x64, 0x5d, 0x19, 0x73,
    0x60, 0x81, 0x4f, 0xdc, 0x22, 0x2a, 0x90, 0x88, 0x46, 0xee, 0xb8, 0x14, 0xde, 0x5e, 0x0b, 0xdb,
    0xe0, 0x32, 0x3a, 0x0a, 0x49, 0x06, 0x24, 0x5c, 0xc2, 0xd3, 0xac, 0x62, 0x91, 0x95, 0xe4, 0x79,
    0xe7, 0xc8, 0x37, 0x6d, 0x8d, 0xd5, 0x4e, 0xa9, 0x6c, 0x56, 0xf4, 0xea, 0x65, 0x7a, 0xae, 0x08,
    0xba, 0x78, 0x25, 0x2e, 0x1c, 0xa6, 0xb4, 0xc6, 0xe8, 0xdd, 0x74, 0x1f, 0x4b, 0xbd, 0x8b, 0x8a,
    0x70, 0x3e, 0xb5, 0x66, 0x48, 0x03, 0xf6, 0x0e, 0x61, 0x35, 0x57, 0xb9, 0x86, 0xc1, 0x1d, 0x9e,
    0xe1, 0xf8, 0x98, 0x11, 0x69, 0xd9, 0x8e, 0x94, 0x9b, 0x1e, 0x87, 0xe9, 0xce, 0x55, 0x28, 0xdf,
    0x8c, 0xa1, 0x89, 0x0d, 0xbf, 0xe6, 0x42, 0x68, 0x41, 0x99, 0x2d, 0x0f, 0xb0, 0x54, 0xbb, 0x16,
]


# ---- 解析信封（复刻 parseSupportedEnvelope，仅 suite=1）----
def _parse_suite1_envelope(envelope_bytes):
    try:
        env = json.loads(envelope_bytes.decode("utf-8"))
    except Exception as e:
        raise ValueError("信封不是合法 JSON: %s" % e)
    if not isinstance(env, dict):
        raise ValueError("信封必须是对象")
    if env.get("suite") != 1:
        raise ValueError("只支持 suite=1，实际 %r" % env.get("suite"))
    key_id = env.get("keyId")
    if not isinstance(key_id, str) or not _re_hex16(key_id):
        raise ValueError("envelope.keyId 非法")
    nonce = _b64_canonical_decode(env["nonce"], "nonce")
    auth_tag = _b64_canonical_decode(env["authTag"], "authTag")
    ct = env.get("ciphertext")
    ciphertext = b"" if (isinstance(ct, str) and ct == "") else _b64_canonical_decode(ct, "ciphertext")
    if len(nonce) != 12 or len(auth_tag) != 16:
        raise ValueError("nonce(12)/authTag(16) 长度错误")
    return key_id, nonce, ciphertext, auth_tag


def _re_hex16(s):
    import re
    return bool(re.fullmatch(r"[0-9a-f]{16}", s))


# ---- 当前用户 DPAPI（Windows）解开 atRestSecretKey 落盘文件 ----
def _dpapi_unprotect(blob):
    """调用 crypt32.CryptUnprotectData（当前用户）解开 DPAPI blob，返回原文 bytes。"""
    if not sys.platform.startswith("win"):
        raise OSError("DPAPI 仅在 Windows 可用")
    import ctypes
    from ctypes import wintypes
    crypt32 = ctypes.windll.crypt32  # type: ignore
    blob_len = len(blob)
    blob_buf = ctypes.create_string_buffer(blob, blob_len)
    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]
    in_blob = DATA_BLOB(blob_len, blob_buf)
    out_blob = DATA_BLOB()
    res = crypt32.CryptUnprotectData(
        ctypes.byref(in_blob), None, None, None, None, 0, ctypes.byref(out_blob))
    if not res:
        raise OSError("CryptUnprotectData 失败（可能不是当前用户/本机加密的 blob）")
    try:
        size = out_blob.cbData
        ptr = ctypes.cast(out_blob.pbData, ctypes.POINTER(ctypes.c_byte * size))
        data = bytes(ptr.contents[:size])
    finally:
        ctypes.windll.kernel32.LocalFree(out_blob.pbData)  # type: ignore
    return data


# ---- 5.6.2+ 进程内存密钥发现（atRestSecretKey 只驻留运行中客户端内存、磁盘无落盘文件时使用）----
# WorkBuddy 5.6.2 客户端由原生模块 workbuddyStorage.loggerGet() 运行时提供密钥，
# 密钥不存在于任何文件；可用 ReadProcessMemory 扫描运行中 WorkBuddy.exe 进程内存，
# 定位信封 keyId 的 ASCII 串，提取附近 44 字符 base64 候选并按 sha256 派生 keyId 严格校验。
# 密钥材料仅在内存中校验使用，绝不写盘、绝不打印。
_B64_CANDIDATE_RE = r"[A-Za-z0-9+/]{43}="
_B64_CANDIDATE_TEXT_RE = r"[A-Za-z0-9+/]{43}="
# 同一候选模式的 UTF-16LE 形式（V8/Electron 字符串可能以宽字符驻留内存）
_B64_CANDIDATE_RE_UTF16 = rb"(?:[A-Za-z0-9+/]\x00){43}=\x00"
# 全内存兜底扫描的候选去重上限（防长尾）
_MAX_MEMORY_CANDIDATES = 200000


def _list_workbuddy_pids():
    """枚举运行中的 WorkBuddy 客户端进程 PID（仅 Windows）。"""
    if not sys.platform.startswith("win"):
        return []
    import ctypes
    from ctypes import wintypes
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    TH32CS_SNAPPROCESS = 0x2
    INVALID_HANDLE_VALUE = ctypes.c_size_t(-1).value

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD),
                    ("cntUsage", wintypes.DWORD),
                    ("th32ProcessID", wintypes.DWORD),
                    ("th32DefaultHeapID", ctypes.c_size_t),   # ULONG_PTR
                    ("th32ModuleID", wintypes.DWORD),
                    ("cntThreads", wintypes.DWORD),
                    ("th32ParentProcessID", wintypes.DWORD),
                    ("pcPriClassBase", wintypes.LONG),         # 注意是 LONG，不是指针
                    ("dwFlags", wintypes.DWORD),
                    ("szExeFile", wintypes.WCHAR * 260)]

    names = {"workbuddy.exe"}
    extra = os.environ.get("WORKBUDDY_PROCESS_NAMES", "")
    if extra:
        names |= {n.strip().lower() for n in extra.split(",") if n.strip()}

    kernel32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.Process32FirstW.restype = wintypes.BOOL
    kernel32.Process32FirstW.argtypes = [ctypes.c_void_p, ctypes.POINTER(PROCESSENTRY32W)]
    kernel32.Process32NextW.restype = wintypes.BOOL
    kernel32.Process32NextW.argtypes = [ctypes.c_void_p, ctypes.POINTER(PROCESSENTRY32W)]
    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snap or snap == INVALID_HANDLE_VALUE:
        return []
    pids = []
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        ok = kernel32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            name = entry.szExeFile
            if name and name.lower() in names:
                pids.append(int(entry.th32ProcessID))
            ok = kernel32.Process32NextW(snap, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snap)
    return pids


def _iter_process_readable_regions(kernel32, handle, max_total_bytes=3 * 1024 * 1024 * 1024):
    """逐段产出目标进程可读已提交内存块（bytes）。只读，跳过不可读/守护页。"""
    import ctypes
    from ctypes import wintypes

    class MEMORY_BASIC_INFORMATION(ctypes.Structure):
        _fields_ = [("BaseAddress", ctypes.c_void_p),
                    ("AllocationBase", ctypes.c_void_p),
                    ("AllocationProtect", wintypes.DWORD),
                    ("PartitionId", wintypes.DWORD),      # x64 对齐填充
                    ("RegionSize", ctypes.c_size_t),
                    ("State", wintypes.DWORD),
                    ("Protect", wintypes.DWORD),
                    ("Type", wintypes.DWORD),
                    ("PartitionId2", wintypes.DWORD)]     # x64 对齐填充

    kernel32.VirtualQueryEx.restype = ctypes.c_size_t
    kernel32.VirtualQueryEx.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                        ctypes.POINTER(MEMORY_BASIC_INFORMATION), ctypes.c_size_t]
    kernel32.ReadProcessMemory.restype = wintypes.BOOL
    kernel32.ReadProcessMemory.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                                           ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
    MEM_COMMIT = 0x1000
    PAGE_NOACCESS = 0x01
    PAGE_GUARD = 0x100
    CHUNK = 8 * 1024 * 1024
    total = 0
    addr = 0
    mbi = MEMORY_BASIC_INFORMATION()
    while total < max_total_bytes:
        ret = kernel32.VirtualQueryEx(handle, ctypes.c_void_p(addr), ctypes.byref(mbi), ctypes.sizeof(mbi))
        if not ret:
            break
        base = mbi.BaseAddress or addr
        region_size = mbi.RegionSize or 0
        if region_size <= 0:
            break
        if mbi.State == MEM_COMMIT and mbi.Protect != PAGE_NOACCESS and not (mbi.Protect & PAGE_GUARD):
            read = 0
            while read < region_size and total < max_total_bytes:
                n = min(CHUNK, region_size - read)
                buf = ctypes.create_string_buffer(n)
                got = ctypes.c_size_t(0)
                ok = kernel32.ReadProcessMemory(handle, ctypes.c_void_p(base + read),
                                                buf, n, ctypes.byref(got))
                if ok and got.value:
                    data = buf.raw[:got.value]
                    total += len(data)
                    yield data
                    read += got.value
                else:
                    # 该子段不可读，跳到区域尾
                    read = region_size
        nxt = base + region_size
        if nxt <= addr:
            break
        addr = nxt


def _key_candidate_matches(candidate_text, target_key_id):
    """校验 44 字符 base64 候选是否派生出目标 keyId。"""
    try:
        _, key_id = _derive_at_rest_key(candidate_text)
        return key_id == target_key_id
    except Exception:
        return False


def _extract_key_near_hits(data, hit_indexes, pattern_len, target_key_id, utf16=False):
    """在 keyId 命中点附近 ±4KB 窗口提取 base64 候选并校验。"""
    import re
    win = 4096
    checked = set()
    for h in hit_indexes:
        lo = max(0, h - win)
        hi = min(len(data), h + pattern_len + win)
        seg = data[lo:hi]
        if utf16:
            for m in re.finditer(_B64_CANDIDATE_TEXT_RE, seg.decode("utf-16-le", "ignore")):
                cand = m.group(0)
                if cand in checked:
                    continue
                checked.add(cand)
                if _key_candidate_matches(cand, target_key_id):
                    return cand
        else:
            for m in re.finditer(_B64_CANDIDATE_RE.encode("ascii", "ignore"), seg):
                cand = m.group(0).decode("ascii")
                if cand in checked:
                    continue
                checked.add(cand)
                if _key_candidate_matches(cand, target_key_id):
                    return cand
    return None


def _discover_at_rest_key_from_memory(target_key_id):
    """扫描运行中 WorkBuddy 客户端进程内存寻找 atRestSecretKey（仅 Windows）。

    策略：
      Pass 1（定向，快速路径）：搜索信封 keyId 的 ASCII / UTF-16LE 串，
                                在命中点 ±4KB 窗口内提取 44 字符 canonical base64 候选并校验；
      Pass 2（兜底）：对全部可读内存做 base64 候选（ASCII 与 UTF-16LE）扫描逐个校验。

    ⚠️ 设计要点：Pass 2 必须无条件执行，不能用「Pass 1 是否命中 keyId」来门控。
      客户端内存中 keyId 的 ASCII 串会因 AAD / 日志等上下文大量出现，而密钥本体未必
      落在这些命中点的 ±4KB 邻域内；若以「是否命中」作门控，就会出现「命中 keyId 但
      取不到密钥 → 跳过全内存兜底 → 误报无法定位」的失败。故 Pass 1 失败后无条件继续
      Pass 2，并补齐 UTF-16LE 候选（V8 字符串可能以宽字符驻留），二者共用去重集合与上限。

    找到返回密钥字符串；找不到返回 None。任何异常都不向外抛（签到主流程不应因扫描崩溃）。
    """
    if not sys.platform.startswith("win"):
        return None
    try:
        import ctypes
        from ctypes import wintypes
        import re as _re
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        PROCESS_QUERY_INFORMATION = 0x0400
        PROCESS_VM_READ = 0x0010
        pattern = target_key_id.encode("ascii", "ignore")
        if len(pattern) != 16:
            return None
        utf16_pattern = target_key_id.encode("utf-16-le")
        b64_ascii_re = _re.compile(_B64_CANDIDATE_RE.encode("ascii", "ignore"))
        b64_utf16_re = _re.compile(_B64_CANDIDATE_RE_UTF16)
        pids = _list_workbuddy_pids()
        if not pids:
            return None
        seen_candidates = set()
        for pid in pids:
            handle = kernel32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
            if not handle:
                continue
            try:
                for data in _iter_process_readable_regions(kernel32, handle):
                    # ---- Pass 1：keyId 邻域快速路径（ASCII 与 UTF-16LE 各试一次）----
                    for pat, is_u16 in ((pattern, False), (utf16_pattern, True)):
                        hits = []
                        start = 0
                        while True:
                            i = data.find(pat, start)
                            if i < 0:
                                break
                            hits.append(i)
                            start = i + 1
                        if hits:
                            key = _extract_key_near_hits(
                                data, hits, len(pat), target_key_id, utf16=is_u16)
                            if key:
                                return key
                    # ---- Pass 2：全内存候选兜底（不再被 Pass 1 命中与否门控）----
                    for m in b64_ascii_re.finditer(data):
                        cand = m.group(0).decode("ascii")
                        if cand in seen_candidates:
                            continue
                        seen_candidates.add(cand)
                        if len(seen_candidates) > _MAX_MEMORY_CANDIDATES:
                            return None
                        if _key_candidate_matches(cand, target_key_id):
                            return cand
                    for m in b64_utf16_re.finditer(data):
                        cand = m.group(0).decode("utf-16-le")
                        if cand in seen_candidates:
                            continue
                        seen_candidates.add(cand)
                        if len(seen_candidates) > _MAX_MEMORY_CANDIDATES:
                            return None
                        if _key_candidate_matches(cand, target_key_id):
                            return cand
            finally:
                kernel32.CloseHandle(ctypes.c_void_p(handle))
        return None
    except Exception:
        return None


def _discover_at_rest_key(target_key_id):
    """定位并解开 atRestSecretKey（44 字符规范 base64），要求其派生 keyId == target_key_id。

    优先顺序：
      1) 环境变量 WORKBUDDY_ATREST_KEY（直接给密钥串）
      2) 环境变量 WORKBUDDY_ATREST_KEY_FILE（指向密钥文件）
      3) 扫描登录态所在 Data 目录下的 DPAPI blob，逐个解开并校验 keyId
      4) 扫描运行中 WorkBuddy.exe 进程内存（5.6.2 密钥只驻留内存、磁盘无落盘文件的可靠途径）
    返回 atRestSecretKey 字符串；找不到则抛 RuntimeError（带可操作提示）。
    """
    # 1) 直接给密钥
    env_key = os.environ.get("WORKBUDDY_ATREST_KEY")
    if env_key:
        _derive_at_rest_key(env_key)  # 仅校验格式
        return env_key
    # 2) 密钥文件（可能是 DPAPI 密文，也可能是明文 44 字符串）
    env_file = os.environ.get("WORKBUDDY_ATREST_KEY_FILE")
    if env_file and os.path.isfile(env_file):
        try:
            raw = open(env_file, "rb").read()
            return _candidate_to_at_rest_key(raw, target_key_id)
        except Exception:
            pass
    # 3) 扫描 DPAPI blob 自动发现
    roots = _at_rest_scan_roots()
    DPAPI_MAGIC = b"\x01\x00\x00\x00\xd0\x8c\x9d\x0a"
    dpapi_tried = 0
    for root in roots:
        if not os.path.isdir(root):
            continue
        for path, size in _iter_candidate_files(root):
            if size > 256 * 1024:
                continue
            try:
                content = open(path, "rb").read()
            except Exception:
                continue
            idx = content.find(DPAPI_MAGIC)
            if idx < 0:
                continue
            # 从版本字（magic 前 4 字节）到文件尾，作为完整 blob 尝试
            blob = content[idx:]
            dpapi_tried += 1
            try:
                candidate = _candidate_to_at_rest_key(blob, target_key_id)
                if candidate:
                    return candidate
            except Exception:
                continue
    # 4) 扫描运行中客户端进程内存（5.6.2 的 atRestSecretKey 不落盘、只驻留 WorkBuddy.exe 内存）
    try:
        mem_key = _discover_at_rest_key_from_memory(target_key_id)
    except Exception:
        mem_key = None
    if mem_key:
        return mem_key
    raise RuntimeError(
        "无法在本机定位 WorkBuddy 5.6.2+ 的 atRestSecretKey"
        "（已尝试 %d 个 DPAPI blob 与运行中客户端进程内存扫描）。\n"
        "可选项：\n"
        "  (a) 确认 WorkBuddy 客户端已启动并登录（密钥只驻留客户端进程内存，客户端未运行则无法解密），"
        "且本脚本与客户端在同一 Windows 用户下运行；\n"
        "  (b) 复制本机登录态所在 Data 目录下由 WorkBuddy 加密的密钥文件，设置环境变量 "
        "WORKBUDDY_ATREST_KEY_FILE=该文件绝对路径；\n"
        "  (c) 直接设置 WORKBUDDY_ATREST_KEY=<44字符规范base64>；\n"
        "  (d) 退回 WorkBuddy 5.5.x 客户端（该版本登录态为明文，旧脚本即可用）。\n"
        "注：客户端以管理员权限运行而本脚本未提权（或相反）时，进程内存扫描会被系统拒绝。"
        % dpapi_tried)


def _candidate_to_at_rest_key(blob_or_text, target_key_id):
    """尝试把一段字节变成 atRestSecretKey 并校验 keyId。失败抛异常。"""
    # 先尝试作为 DPAPI 密文解开
    text = None
    try:
        plain = _dpapi_unprotect(blob_or_text)
        text = plain.decode("utf-8", "replace").strip()
    except Exception:
        # 也可能就是明文串（无 DPAPI 包裹）
        try:
            text = blob_or_text.decode("utf-8", "replace").strip()
        except Exception:
            text = None
    if not text:
        raise ValueError("无法解析候选密钥")
    # 去掉可能的引号/空白
    text = text.strip().strip('"').strip("'")
    try:
        key, key_id = _derive_at_rest_key(text)
    except Exception:
        raise ValueError("候选不是合法 atRestSecretKey")
    if key_id != target_key_id:
        raise ValueError("keyId 不匹配（%s != %s）" % (key_id, target_key_id))
    return text


def _at_rest_scan_roots():
    roots = []
    home = os.path.expanduser("~")
    for env in ("LOCALAPPDATA", "APPDATA"):
        base = os.environ.get(env, "")
        if base:
            roots.append(os.path.join(base, "CodeBuddyExtension", "Data"))
            roots.append(os.path.join(base, "WorkBuddyExtension", "Data"))
    roots.append(os.path.join(home, ".config", "CodeBuddyExtension", "Data"))
    roots.append(os.path.join(home, ".workbuddy"))
    # 去重保序
    seen = set()
    out = []
    for r in roots:
        if r and r not in seen:
            seen.add(r)
            out.append(r)
    return out


_SKIP_DIRS = {
    "cache", "code cache", "gpucache", "file-tree", "extensions", "blob_storage",
    "indexeddb", "service worker", "logs", "crashpad", "swreporter", "network",
    "application cache", "shadercache", "webrtc", "videoDecodeStats",
}


def _iter_candidate_files(root):
    count = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d.lower() not in _SKIP_DIRS]
        for fn in filenames:
            p = os.path.join(dirpath, fn)
            try:
                sz = os.path.getsize(p)
            except Exception:
                continue
            yield p, sz
            count += 1
            if count > 4000:
                return


def decrypt_access_token_field(raw_value):
    """复刻 ProtectedFieldCodec.decodeString：字符串直接返回；信封则解 AES-GCM。

    raw_value: auth.accessToken 字段（可能是 str，也可能是 {$wbEncrypted:1, envelope}）
    返回明文 JWT 字符串。
    """
    if isinstance(raw_value, str):
        return raw_value  # 明文 JWT（5.5.x 及更早 / 加密关闭时）
    if isinstance(raw_value, dict) and raw_value.get("$wbEncrypted") == 1:
        envelope_b64 = raw_value.get("envelope")
        if not isinstance(envelope_b64, str):
            raise ValueError("加密信封缺少 envelope 字段")
        envelope_bytes = base64.b64decode(envelope_b64)
        if base64.b64encode(envelope_bytes).decode("ascii") != envelope_b64:
            raise ValueError("envelope 不是规范 base64")
        key_id, nonce, ciphertext, auth_tag = _parse_suite1_envelope(envelope_bytes)
        at_rest_secret_key = _discover_at_rest_key(key_id)
        key, _ = _derive_at_rest_key(at_rest_secret_key)
        aad = _build_field_aad(key_id)
        plaintext = _aes_gcm_decrypt(key, nonce, ciphertext, aad, auth_tag)
        return plaintext.decode("utf-8")
    raise ValueError("accessToken 既不是明文也不是加密信封")


def load_token(auth_path):
    with open(auth_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    auth = data.get("auth", {})
    raw = auth.get("accessToken")
    if raw is None:
        raise RuntimeError("登录态文件中未找到 accessToken（可能未登录或登录态已失效）")
    try:
        token = decrypt_access_token_field(raw)
    except Exception as e:
        raise RuntimeError(
            "读取/解密 accessToken 失败（可能为 5.6.2+ 加密登录态）: %s" % e)
    domain = auth.get("domain") or "www.codebuddy.cn"
    _check_expiry(auth)
    return token, domain


def load_token_best():
    """依次尝试所有候选登录态文件，返回 (token, domain, auth_path)。

    对每个候选：
      - 明文 JWT 直接用；
      - 加密信封（5.6.2+）则尝试本地解密（密钥可定位才成功）。
    某候选读取/解密失败（如加密态找不到 atRestSecretKey）则跳过、继续下一个；
    全部失败才汇总报错。这样在「桌面客户端登录态被加密、但 WorkBuddy 代理自带明文
    兜底登录态 ~/.workbuddy/auth 可用」的 5.6.2 场景下，仍能正常签到，
    无需手动设置 WORKBUDDY_ATREST_KEY。
    """
    errors = []
    for p in _auth_candidates():
        if not os.path.isfile(p):
            continue
        try:
            token, domain = load_token(p)
            return token, domain, p
        except Exception as e:
            errors.append((p, str(e)))
            continue
    if not errors:
        raise RuntimeError("未找到本机登录态文件，请确认 WorkBuddy 已登录")
    raise RuntimeError(
        "所有候选登录态文件均无法读取/解密 accessToken（共 %d 个）：\n" % len(errors)
        + "\n".join("  - %s: %s" % (pp, ee) for pp, ee in errors)
        + "\n建议：若均为 5.6.2+ 加密登录态且无法自动定位密钥，请设置 "
          "WORKBUDDY_ATREST_KEY（或 WORKBUDDY_ATREST_KEY_FILE）后重试，"
          "或确保以同一 Windows 用户运行脚本。"
    )


def mask_token(t):
    if not t:
        return "<empty>"
    return t[:6] + "..." + t[-4:]
