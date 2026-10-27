"""Validate a copied SQLite WAL and apply only its contiguous committed prefix.

All paths passed here must be private snapshot files, never live client files.
Format: https://www.sqlite.org/fileformat.html#walformat
"""
import struct


MAX_DATABASE_BYTES = 16 * 1024 * 1024 * 1024


class WalError(ValueError):
    pass


def checksum(data, state=(0, 0), *, little=True):
    if len(data) % 8:
        raise WalError('WAL 校验区长度无效。')
    a, b = state
    for x, y in struct.iter_unpack('<II' if little else '>II', data):
        a = (a + x + b) & 0xffffffff
        b = (b + y + a) & 0xffffffff
    return a, b


def apply_wal(database, wal, *, expected_page_size, decode_page=None):
    size = wal.stat().st_size
    if not size:
        return {'status': 'empty', 'bytes': 0, 'committedFrames': 0, 'uncommittedFrames': 0}
    if size < 32:
        raise WalError('WAL 文件头不完整，无法证明一致性。')
    committed = valid = commit_size = 0
    tail = ''
    with wal.open('rb') as handle:
        header = handle.read(32)
        magic, version, page_size, _sequence, salt1, salt2, a, b = struct.unpack('>8I', header)
        if magic not in (0x377f0682, 0x377f0683) or version != 3007000:
            raise WalError('WAL 格式未适配，已停止准备副本。')
        if page_size != expected_page_size or not 512 <= page_size <= 65536 or page_size & (page_size - 1):
            raise WalError('WAL 页大小与主库不一致。')
        little = magic == 0x377f0682
        state = checksum(header[:24], little=little)
        if state != (a, b):
            raise WalError('WAL 文件头校验失败。')
        while True:
            frame = handle.read(24)
            if not frame:
                break
            payload = handle.read(page_size)
            if len(frame) != 24 or len(payload) != page_size:
                # A stable but partial physical frame cannot establish whether a
                # newer committed transaction was lost during acquisition.
                raise WalError('WAL 尾帧不完整，请在客户端写入稳定后重试。')
            page_no, db_size, fs1, fs2, fa, fb = struct.unpack('>6I', frame)
            if (fs1, fs2) != (salt1, salt2):
                # A reused WAL can retain an older generation after the current
                # valid prefix, including at frame 1 after a complete checkpoint
                # and header reset but before any new frame has been written.
                # SQLite's walIndexRecover/walDecodeFrame stop at this boundary;
                # zero current commits means the checkpointed DB is the result.
                # Never scan past the boundary for later matching salts.
                # https://sqlite.org/fileformat.html#wal_reset
                tail = 'previous_generation_tail'
                break
            if page_no == 0 or page_no * page_size > MAX_DATABASE_BYTES or db_size * page_size > MAX_DATABASE_BYTES:
                raise WalError('WAL 页号或数据库大小超出支持范围。')
            state = checksum(frame[:8] + payload, state, little=little)
            if state != (fa, fb):
                raise WalError('WAL 帧校验失败，未发布任何副本。')
            valid += 1
            if db_size:
                committed, commit_size = valid, db_size
    if committed:
        with wal.open('rb') as source, database.open('r+b') as target:
            source.seek(32)
            for _ in range(committed):
                frame = source.read(24)
                page = source.read(page_size)
                page_no = struct.unpack('>I', frame[:4])[0]
                if decode_page is not None:
                    page = decode_page(page, page_no)
                if len(page) != page_size:
                    raise WalError('解密后的 WAL 页大小无效。')
                target.seek((page_no - 1) * page_size)
                target.write(page)
            target.truncate(commit_size * page_size)
    return {'status': 'merged' if committed else 'no_committed_frames', 'bytes': size,
            'committedFrames': committed, 'uncommittedFrames': valid - committed,
            'tail': tail, 'pageSize': page_size}
