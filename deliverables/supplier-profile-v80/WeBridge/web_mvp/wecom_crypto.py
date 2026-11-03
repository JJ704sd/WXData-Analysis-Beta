"""WeCom wxSQLite3 AES-128 pages; independent implementation of documented
wxSQLite3 algorithms (https://github.com/utelle/SQLite3MultipleCiphers).
Uses the crypto runtime already bundled by the portable application.
"""
import hashlib
import struct

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from database_snapshot import SQLITE_HEADER, SnapshotError


def password_key(password):
    """Windows DbKeyManager material is a password, not the final page key."""
    padding = bytes.fromhex('28bf4e5e4e758a4164004e56fffa01082e2e00b6d0683e802f0ca9fe6453697a')
    user = (password + padding)[:32]
    digest = hashlib.md5(padding).digest()
    for _ in range(50):
        digest = hashlib.md5(digest).digest()
    owner = user
    for turn in range(20):
        key = bytes(value ^ turn for value in digest)
        state, j = list(range(256)), 0
        for i in range(256):
            j = (j + state[i] + key[i % 16]) % 256
            state[i], state[j] = state[j], state[i]
        result, i, j = bytearray(), 0, 0
        for value in owner:
            i = (i + 1) % 256
            j = (j + state[i]) % 256
            state[i], state[j] = state[j], state[i]
            result.append(value ^ state[(state[i] + state[j]) % 256])
        owner = bytes(result)
    digest = hashlib.md5(user + owner).digest()
    for _ in range(50):
        digest = hashlib.md5(digest).digest()
    return digest


def page_iv(number):
    value, words = number + 1, []
    for _ in range(4):
        quotient = value // 52774
        value = 40692 * (value - 52774 * quotient) - 3791 * quotient
        if value < 0:
            value += 2147483399
        words.append(value)
    return hashlib.md5(struct.pack('<4I', *words)).digest()


def page_size(header):
    if len(header) < 100 or header[21:24] != b'\x40\x20\x20':
        raise SnapshotError('企微数据库格式不支持；需要 wxSQLite3 AES-128 或已解密 SQLite 副本。')
    size = int.from_bytes(header[16:18], 'big')
    size = 65536 if size == 1 else size
    if not 512 <= size <= 65536 or size & (size - 1):
        raise SnapshotError('企微数据库页大小无效。')
    return size


class WeComCipher:
    def __init__(self, key):
        if len(key) != 16:
            raise SnapshotError('企微 AES-128 主密钥长度必须为16字节。')
        self.key = key

    def page(self, payload, number):
        if len(payload) % 16:
            raise SnapshotError('企微加密页长度无效。')
        key = hashlib.md5(self.key + struct.pack('<I', number) + b'sAlT').digest()
        cipher = Cipher(algorithms.AES(key), modes.CBC(page_iv(number))).decryptor()
        data = payload[8:16] + payload[24:] if number == 1 else payload
        plain = cipher.update(data) + cipher.finalize()
        if number == 1:
            if plain[:8] != payload[16:24] or len(plain) <= 84 or plain[84] not in (2, 5, 10, 13):
                raise SnapshotError('企微数据库密钥不匹配或文件格式已变化，未发布解密结果。')
            plain = SQLITE_HEADER + plain
        return plain

    def verifies(self, payload):
        try:
            self.page(payload, 1)
            return True
        except (ValueError, SnapshotError):
            return False
