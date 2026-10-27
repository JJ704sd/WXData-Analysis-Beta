"""Prepare one explicit account's stable, validated, independent DB snapshot.

Never opens live files with SQLite, changes the source, searches process memory,
or acquires keys. Supplied keys and intermediate files are never logged.
"""
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile
import time

from database_wal import MAX_DATABASE_BYTES, WalError, apply_wal


SQLITE_HEADER = b'SQLite format 3\0'
CHUNK = 1024 * 1024
BUSINESS_DB = re.compile(r'(?:contact|session|(?:biz_)?message_\d+)\.db\Z', re.IGNORECASE)
BUSINESS_DIRS = {'contact', 'session', 'message', 'biz_message'}


class SnapshotError(ValueError):
    """Safe diagnostics composed only from this module and whitelisted DB names."""

    def __init__(self, message, *, code=None):
        super().__init__(message)
        self.public_message = message
        self.code = code


class SourceChanged(SnapshotError):
    pass


def _signature(path):
    value = path.stat()
    return (value.st_size, value.st_mtime_ns, value.st_ctime_ns, value.st_dev, value.st_ino)


def _inside(path, root):
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _inventory(root, include_media=False, include_voice=False):
    pattern = re.compile(r'(?:contact|session|(?:biz_)?message_\d+|message_resource|hardlink)\.db\Z', re.I) if include_media else BUSINESS_DB
    directories = BUSINESS_DIRS | {'hardlink'} if include_media else BUSINESS_DIRS
    if include_voice:
        pattern = re.compile('(?:' + pattern.pattern.removesuffix('\\Z') + r'|media_\d+\.db)\Z', re.I)
    paths = list(root.iterdir())
    files = [path for path in paths if path.is_file() and pattern.fullmatch(path.name)]
    for directory in paths:
        if directory.name.lower() not in directories or not directory.is_dir():
            continue
        if directory.is_symlink() or not _inside(directory, root):
            raise SnapshotError('数据库目录包含越界链接，请选择真实的单账号目录。')
        files.extend(path for path in directory.iterdir() if path.is_file() and pattern.fullmatch(path.name))
    if not files:
        raise SnapshotError('所选目录没有已支持的业务数据库；请选择单个账号的 db_storage 或已解密副本目录。')
    result = {}
    for database in sorted(files):
        journal = Path(str(database) + '-journal')
        if journal.exists() and journal.stat().st_size:
            # Without SQLite locking/recovery on the original, a nonempty
            # rollback journal cannot be proven cold. Never omit it and publish
            # a main file that may still contain uncommitted changes.
            raise SnapshotError('源库存在非空回滚日志，无法确认提交状态；请等待客户端写入结束后重试。')
        for path in (database, Path(str(database) + '-wal')):
            if not path.exists():
                continue
            if path.is_symlink() or not _inside(path, root):
                raise SnapshotError('数据库文件包含越界链接，未读取。')
            signature = _signature(path)
            if signature[0] > MAX_DATABASE_BYTES:
                raise SnapshotError('单文件超过 16 GiB 支持上限，请准备更小的授权副本。')
            result[path.relative_to(root).as_posix()] = signature
    return result


def _hash_file(path):
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        while True:
            data = handle.read(CHUNK)
            if not data:
                return digest.hexdigest()
            digest.update(data)


def _copy_file(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    with source.open('rb') as incoming, target.open('xb') as outgoing:
        while True:
            data = incoming.read(CHUNK)
            if not data:
                return digest.hexdigest()
            outgoing.write(data)
            digest.update(data)


def _remove_owned(path, owner):
    resolved, boundary = path.resolve(), owner.resolve()
    if resolved == boundary or not _inside(resolved, boundary):
        raise SnapshotError('清理路径校验失败；未删除任何目录。')
    if path.exists():
        shutil.rmtree(path)


def _capture_batch(source, generation, include_media=False, include_voice=False):
    # Keep the original signatures: comparing final mtime to a new current mtime
    # would miss same-length changes after an individually stable file read.
    for attempt in range(3):
        raw = generation / ('.raw-' + str(attempt))
        raw.mkdir()
        try:
            before = _inventory(source, include_media, include_voice)
            hashes = {}
            for relative, signature in before.items():
                original = source / relative
                hashes[relative] = _copy_file(original, raw / relative)
                if _signature(original) != signature:
                    raise SourceChanged('复制期间源数据库发生变化。')
            if _inventory(source, include_media, include_voice) != before:
                raise SourceChanged('复制期间数据库或 WAL 清单发生变化。')
            # A second complete streaming read detects equal-length replacements
            # even when their timestamps have been preserved.
            for relative, signature in before.items():
                original = source / relative
                if _hash_file(original) != hashes[relative] or _signature(original) != signature:
                    raise SourceChanged('复制后源数据库或 WAL 内容发生变化。')
            if _inventory(source, include_media, include_voice) != before:
                raise SourceChanged('复核期间数据库或 WAL 清单发生变化。')
            return raw, before, hashes
        except (SourceChanged, FileNotFoundError):
            _remove_owned(raw, generation)
            if attempt == 2:
                raise SnapshotError('源数据库持续变化，无法获得稳定副本；请稍后手动刷新。') from None
            time.sleep(.05)


def _load_keys(path):
    if path is None:
        return {}
    try:
        source = Path(path)
        if not source.is_file() or source.stat().st_size > 1024 * 1024:
            raise ValueError
        value = json.loads(source.read_text(encoding='utf-8-sig'))
        rows = value.get('keys') if isinstance(value, dict) else value
        if not isinstance(rows, list) or len(rows) > 4096:
            raise ValueError
        result = {}
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError
            key_text, salt_text = row.get('key_hex'), row.get('salt_hex')
            if not isinstance(key_text, str) or not re.fullmatch(r'[0-9a-fA-F]{64}', key_text):
                raise ValueError
            if not isinstance(salt_text, str) or not re.fullmatch(r'[0-9a-fA-F]{32}', salt_text):
                raise ValueError
            result.setdefault(bytes.fromhex(salt_text), []).append(bytes.fromhex(key_text))
        return result
    except (OSError, ValueError, TypeError):
        raise SnapshotError('密钥文件格式无效或无法读取；需要 keys 数组中的 key_hex / salt_hex，不记录其值。') from None


class _Cipher:
    def __init__(self, key, salt):
        try:
            from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        except ImportError:
            raise SnapshotError('加密数据库需要项目 Python 环境安装 cryptography；未尝试安装或获取密钥。') from None
        self.key, self.Cipher, self.algorithms, self.modes = key, Cipher, algorithms, modes
        self.mac_key = hashlib.pbkdf2_hmac('sha512', key, bytes(byte ^ 0x3a for byte in salt), 2, 32)
        self._mac = hmac.new(self.mac_key, digestmod=hashlib.sha512)

    def page(self, page, number):
        if len(page) != 4096:
            raise SnapshotError('加密页长度错误，已拒绝部分解密。')
        first = number == 1
        offset = 16 if first else 0
        mac = self._mac.copy()
        mac.update(page[offset:4032] + number.to_bytes(4, 'little'))
        expected = mac.digest()
        if not hmac.compare_digest(expected, page[4032:4096]):
            raise SnapshotError('数据库页 HMAC 校验失败；密钥不匹配、格式变化或副本受损。')
        decryptor = self.Cipher(self.algorithms.AES(self.key), self.modes.CBC(page[4016:4032])).decryptor()
        result = decryptor.update(page[offset:4016]) + decryptor.finalize() + page[4016:]
        return SQLITE_HEADER + result if first else result


def _decrypt_database(source, target, keys):
    with source.open('rb') as handle:
        first_page = handle.read(4096)
    if first_page.startswith(SQLITE_HEADER):
        if len(first_page) < 100:
            raise SnapshotError('SQLite 文件头不完整。')
        page_size = int.from_bytes(first_page[16:18], 'big')
        page_size = 65536 if page_size == 1 else page_size
        if not 512 <= page_size <= 65536 or page_size & (page_size - 1) or source.stat().st_size % page_size:
            raise SnapshotError('SQLite 页大小或文件长度无效。')
        target.parent.mkdir(parents=True, exist_ok=True)
        with source.open('rb') as incoming, target.open('xb') as outgoing:
            shutil.copyfileobj(incoming, outgoing, CHUNK)
        return page_size, None
    if len(first_page) != 4096 or source.stat().st_size % 4096:
        raise SnapshotError('加密数据库不符合完整 4096 字节页布局。')
    candidates = keys.get(first_page[:16], [])
    if not candidates:
        raise SnapshotError('加密数据库缺少匹配的已授权密钥；仅复制文件不能直接读取。')
    selected = None
    for key in candidates:
        cipher = _Cipher(key, first_page[:16])
        try:
            first_plain = cipher.page(first_page, 1)
            selected = cipher
            break
        except SnapshotError:
            continue
    if selected is None:
        raise SnapshotError('提供的密钥未通过数据库首页校验。')
    target.parent.mkdir(parents=True, exist_ok=True)
    with source.open('rb') as incoming, target.open('xb') as outgoing:
        # The matching key already authenticated and decoded the first page.
        outgoing.write(first_plain)
        incoming.seek(4096)
        number = 2
        while True:
            page = incoming.read(4096)
            if not page:
                break
            outgoing.write(selected.page(page, number))
            number += 1
    return 4096, selected


def _quick_check(path):
    try:
        with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro&immutable=1', uri=True)) as connection:
            connection.execute('PRAGMA query_only=ON')
            result = connection.execute('PRAGMA quick_check').fetchall()
            if result != [('ok',)]:
                raise SnapshotError('数据库副本未通过 quick_check，未发布本批次。')
    except sqlite3.Error:
        raise SnapshotError('无法验证数据库副本；未将损坏或格式不支持的文件作为成功结果。') from None


def prepare_snapshot(source_root, output_root, keys_file=None, include_media=False, include_voice=False):
    """Return {'root': Path, 'source': metadata} only after the whole batch passes."""
    source, output = Path(source_root).expanduser().resolve(), Path(output_root).expanduser().resolve()
    if not source.is_dir():
        raise SnapshotError('所选数据库源目录不存在。')
    if (source / 'db_storage').is_dir():
        source = (source / 'db_storage').resolve()
    if output == source or _inside(output, source):
        raise SnapshotError('输出目录不能位于源目录内；不会向微信数据目录写入文件。')
    keys = _load_keys(keys_file)
    _inventory(source, include_media, include_voice)
    output.mkdir(parents=True, exist_ok=True)
    generation = Path(tempfile.mkdtemp(prefix='snapshot-', dir=output))
    try:
        raw, signatures, hashes = _capture_batch(source, generation, include_media, include_voice)
        reports = []
        encrypted_count = 0
        for relative in signatures:
            if relative.endswith('-wal'):
                continue
            destination = generation / relative
            try:
                page_size, cipher = _decrypt_database(raw / relative, destination, keys)
                encrypted_count += cipher is not None
                wal_relative = relative + '-wal'
                wal_report = {'status': 'absent', 'bytes': 0, 'committedFrames': 0, 'uncommittedFrames': 0}
                if wal_relative in signatures:
                    wal_report = apply_wal(destination, raw / wal_relative, expected_page_size=page_size,
                                           decode_page=cipher.page if cipher else None)
                    wal_report['sourceSha256'] = hashes[wal_relative]
                _quick_check(destination)
            except (SnapshotError, WalError) as error:
                raise SnapshotError(relative + '：' + str(error),
                                    code=error.code if isinstance(error, SnapshotError) else None) from None
            reports.append({'path': relative, 'sourceBytes': signatures[relative][0],
                            'sourceSha256': hashes[relative], 'encrypted': cipher is not None,
                            'integrity': 'ok', 'wal': wal_report})
        _remove_owned(raw, generation)
        metadata = {'sourceRoot': str(source), 'createdAt': datetime.now(timezone.utc).isoformat(timespec='seconds'),
                    'dbCount': len(reports), 'walCount': sum(name.endswith('-wal') for name in signatures),
                    'encryptedCount': encrypted_count, 'integrity': 'ok', 'databases': reports,
                    'includeVoice': include_voice,
                    'limitations': ['只包含所选账号的业务库及显式开启的附件索引；不读取 FTS 或其他账号。',
                                    '副本反映本次稳定复制的状态，后续新消息需要手动刷新。']}
        if any(report['wal'].get('uncommittedFrames') for report in reports):
            metadata['limitations'].append('存在完整但未提交的 WAL 尾帧；仅合并最后可证明提交边界之前的页。')
        if any(report['wal'].get('tail') for report in reports):
            metadata['limitations'].append('WAL 连续有效前缀之后有上一世代尾部；未越过该边界继续扫描或读取。')
        (generation / 'manifest.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding='utf-8')
        return {'root': generation, 'source': metadata}
    except Exception as error:
        _remove_owned(generation, output)
        if isinstance(error, (SnapshotError, WalError)):
            raise SnapshotError(str(error), code=error.code if isinstance(error, SnapshotError) else None) from None
        raise SnapshotError('准备数据库副本失败；请检查访问权限和磁盘空间。现有成功副本未改动。',
                            code='snapshot_io' if isinstance(error, OSError) else None) from None
