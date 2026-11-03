"""Resolve Windows attachments from authenticated local snapshot indexes only.

No client injection, CDN requests, recursive filename guessing or key extraction.
Raw XML, image keys and source paths never cross the HTTP boundary.
"""
import hashlib
import json
from pathlib import Path
import re
import secrets
import sqlite3
import itertools
from datetime import datetime

from database_adapter import _protobuf_fields
from media_host import MediaCache
from rich_content import xml_root, xml_text, strip_sender, number

LIMIT = 100 * 1024 * 1024
HASH = re.compile(r'[a-fA-F0-9]{32}\Z')


def child(root, *parts):
    root = Path(root).resolve()
    for part in parts:
        if not isinstance(part, str) or not part or Path(part).is_absolute() or ':' in part or '\0' in part:
            raise ValueError('附件索引路径无效。')
        if any(segment in ('.', '..') for segment in part.replace('\\', '/').split('/')):
            raise ValueError('附件索引路径无效。')
    path = root.joinpath(*parts).resolve()
    if not path.is_relative_to(root):
        raise ValueError('附件路径超出当前账号目录。')
    return path


def query(snapshot, relative, sql, params=()):
    path = child(snapshot, relative)
    if not path.is_file():
        return []
    if Path(str(path) + '-wal').exists():
        raise ValueError('附件索引不是已合并的独立副本。')
    connection = sqlite3.connect(path.as_uri() + '?mode=ro&immutable=1', uri=True)
    try:
        connection.execute('PRAGMA query_only=ON')
        connection.execute('PRAGMA trusted_schema=OFF')
        return connection.execute(sql, params).fetchall()
    finally:
        connection.close()


def descriptor(row, part):
    xml = xml_root(strip_sender(row['_raw']))
    if xml is None:
        raise ValueError('消息没有可解析的附件信息。')
    kind = row['kind']
    filename = row.get('media', {}).get('filename') or '附件'
    expected = ''
    if part:
        indexes = part.split('.')
        if len(indexes) > 5 or any(not re.fullmatch(r'\d{1,3}', value) for value in indexes):
            raise ValueError('记录附件编号无效。')
        app = xml if xml.tag == 'appmsg' else xml.find('appmsg')
        record = xml_root(xml_text(app, 'recorditem')) if app is not None else None
        for value in indexes:
            nodes = record.findall('./datalist/dataitem') if record is not None else []
            if int(value) >= min(len(nodes), 200):
                raise ValueError('记录附件不存在。')
            node = nodes[int(value)]
            record = xml_root(xml_text(node, 'recordxml') or xml_text(node, 'recorditem'))
        kind = {2: 'image', 4: 'video', 8: 'file'}.get(number(node.get('datatype')))
        expected = node.findtext('fullmd5') or node.findtext('md5') or ''
        filename = node.findtext('datatitle') or filename
    elif kind == 'file':
        expected = xml.findtext('.//appmsg/md5') or xml.findtext('.//appattach/md5') or ''
    else:
        node = xml.find({'image': 'img', 'video': 'videomsg', 'emoji': 'emoji'}.get(kind, 'none'))
        if node is not None:
            expected = node.get('md5', '')
    if kind not in ('image', 'video', 'file', 'emoji'):
        raise ValueError('这条消息没有可读取的媒体附件。')
    expected = expected.lower() if HASH.fullmatch(expected) else ''
    filename = filename.replace('\\', '/').split('/')[-1]
    filename = ''.join(c for c in filename if ord(c) >= 32)[:180] or '附件'
    return kind, filename, expected


def candidates(snapshot, account_root, group, row, part, kind, filename, expected):
    result = []
    month = datetime.fromtimestamp(row['timestamp']).strftime('%Y-%m')
    group_hash = hashlib.md5(group.encode()).hexdigest()
    if not part and kind in ('image', 'video'):
        rows = query(snapshot, 'message/message_resource.db',
            'SELECT packed_info FROM MessageResourceInfo WHERE chat_id=(SELECT rowid FROM ChatName2Id WHERE user_name=?) '
            'AND message_local_id=? AND message_svr_id=? LIMIT 2', (group, row['localId'], int(row['serverId'])))
        for (packed,) in rows:
            for nested in _protobuf_fields(packed).get(2, []):
                for raw in _protobuf_fields(nested).get(1, []):
                    if isinstance(raw, bytes) and re.fullmatch(rb'[a-fA-F0-9]{32}', raw):
                        stem = raw.decode()
                        if kind == 'image':
                            result += [(child(account_root, 'msg/attach', group_hash, month, 'Img', stem + suffix + '.dat'), bool(suffix)) for suffix in ('', '_h', '_t')]
                        else:
                            result += [(child(account_root, 'msg/video', month, stem + suffix), suffix != '.mp4') for suffix in ('.mp4', '.jpg', '_thumb.jpg')]
    if expected and kind in ('image', 'video', 'file'):
        rows = query(snapshot, 'hardlink/hardlink.db',
                     'SELECT file_name,dir1,dir2 FROM ' + kind + '_hardlink_info_v4 WHERE md5=? LIMIT 8', (expected,))
        for name, first, second in rows:
            dirs = dict(query(snapshot, 'hardlink/hardlink.db', 'SELECT rowid,username FROM dir2id WHERE rowid IN (?,?)', (first, second)))
            if kind == 'image' and first in dirs and second in dirs:
                result.append((child(account_root, 'msg/attach', dirs[first], dirs[second], 'Img', name), name.endswith('_t.dat')))
            elif kind != 'image' and first in dirs:
                result.append((child(account_root, 'msg', kind, dirs[first], name), False))
    if kind == 'file' and expected and not part:
        result.append((child(account_root, 'msg/file', month, filename), False))
    if part and expected:
        # Record folder names are opaque. Only inspect this conversation/month,
        # require the message's full checksum, and never accept index/size alone.
        record_root = child(account_root, 'msg/attach', group_hash, month, 'Rec')
        if record_root.is_dir():
            directories = list(itertools.islice(record_root.iterdir(), 257))
            if len(directories) <= 256:
                folder = {'image': 'Img', 'video': 'V', 'file': 'File'}.get(kind)
                if folder:
                    for directory in directories:
                        candidate_dir = child(account_root, str(directory.relative_to(account_root)), folder)
                        if candidate_dir.is_dir():
                            for path in itertools.islice(candidate_dir.iterdir(), 256):
                                result.append((child(account_root, str(path.relative_to(account_root))), False))
                                if len(result) >= 512:
                                    return sorted(dict.fromkeys(result), key=lambda item: item[1])
    if kind == 'emoji' and expected:
        result.append((child(account_root, 'cache', month, 'Emoticon', expected[:2], expected), False))
    if not part and kind in ('image', 'video'):
        result.append((child(account_root, 'cache', month, 'Message', group_hash, 'Thumb', f'{row["localId"]}_{row["timestamp"]}_thumb.jpg'), True))
    return sorted(dict.fromkeys(result), key=lambda item: item[1])


def image_key(config):
    path = Path(config.get('imageKeyFile') or '')
    if not path.is_file() or path.stat().st_size > 65536:
        raise ValueError('此图片需要已授权的本机图片密钥，请配置图片密钥文件。')
    try:
        records = json.loads(path.read_text(encoding='utf-8'))['keys']
        source = Path(config['sourceRoot']).resolve()
        if (source / 'db_storage').is_dir():
            source = (source / 'db_storage').resolve()
        for record in records:
            if Path(record['sourceRoot']).resolve() == source and re.fullmatch(r'[a-fA-F0-9]{32}', record['aes_hex']):
                return bytes.fromhex(record['aes_hex'])
    except (ValueError, KeyError, TypeError, OSError):
        pass
    raise ValueError('图片密钥文件无效或不属于当前账号。')


def resolve_local(snapshot, config, group, row, part='', *, recovery=None, destination=None):
    from media_container import media_type
    from local_image_keys import ImageKeys
    kind, filename, expected = descriptor(row, part)
    source = Path(config['sourceRoot']).resolve()
    if source.name == 'db_storage':
        account_root = source.parent
    elif (source / 'db_storage').is_dir():
        account_root = source
    else:
        return {'status': 'unavailable', 'reason': '独立明文副本没有绑定本机附件目录。'}, None
    issue = '本机尚未缓存这份附件，请在 Windows 微信打开或下载后重试。'
    best_preview = None
    keys = recovery.image_keys if recovery is not None else ImageKeys(config)
    def locations():
        seen = set()
        for path, preview in candidates(snapshot, account_root, group, row, part, kind, filename, expected):
            seen.add(path)
            yield path, preview, False
        if recovery is not None and expected:
            for path in recovery.paths_for(expected):
                path = child(account_root, str(path.relative_to(account_root)))
                if path not in seen:
                    seen.add(path)
                    yield path, False, True
    for path, preview, recovered in locations():
        if not path.is_file():
            continue
        before = path.stat()
        if destination is not None and kind in ('file','video') and expected and not preview:
            from offline_media_copy import copy_original
            result=copy_original(path,Path(destination),expected,filename,kind)
            if result:
                if recovered:
                    result.update(recoveredLocally=True,matchedBy='full_content_md5');recovery.recovered+=1
                return result,None
            continue
        if before.st_size <= 0 or before.st_size > LIMIT:
            issue = '附件为空或超过 100 MB 上限。'; continue
        with path.open('rb') as stream:
            data = stream.read(LIMIT + 1)
        after = path.stat()
        if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino) or len(data) != before.st_size:
            issue = '附件正在变化，请稍后重试。'; continue
        try:
            if data.startswith(b'\x07\x08V2\x08\x07'):
                data = keys.decode(data, expected_md5=expected if not preview else None)
        except ValueError as error:
            issue = str(error); continue
        mime, extension = media_type(data)
        if mime == 'image/wxgf':
            issue = '此图片使用 WXGF 格式，当前只能尝试兼容缩略图。'; continue
        if kind in ('image', 'emoji') and mime not in ('image/jpeg', 'image/png', 'image/gif', 'image/webp'):
            issue = '图片缓存格式尚未支持。'; continue
        if kind == 'video' and mime not in ('video/mp4', 'image/jpeg', 'image/png'):
            issue = '视频缓存格式尚未支持。'; continue
        preview = preview or kind == 'video' and mime.startswith('image/')
        if not preview and expected and hashlib.md5(data).hexdigest() != expected:
            issue = '附件完整性校验不一致，未返回该文件。'; continue
        if kind == 'file' and mime == 'application/octet-stream' and Path(filename).suffix.lower() in ('.txt', '.md', '.csv', '.json', '.log'):
            try:
                data.decode('utf-8'); mime = 'text/plain'
            except UnicodeError:
                pass
        if kind != 'file':
            filename = f'{kind}_{row["localId"]}.{extension}'
        result = {'status': 'ready', 'size': len(data), 'mime': mime, 'filename': filename,
                'kind': kind, 'previewOnly': preview, 'digest': hashlib.sha256(data).hexdigest(),
                **({'reason': '当前仅有缩略图或视频封面，尚未取得完整附件。'} if preview else {})}
        if preview:
            if best_preview is None:
                best_preview = (result, data)
            continue
        if recovered:
            result.update(recoveredLocally=True, matchedBy='full_content_md5')
            recovery.recovered += 1
        return result, data
    diagnosis = recovery.diagnosis(expected) if recovery is not None and expected else None
    reasons = {'local_candidate_not_verified': '发现本地同名候选，但格式、密钥或完整性校验未通过，未作为原件返回。',
               'local_search_incomplete': '本地媒体检查遇到读取限制或达到扫描上限，暂未匹配到原件。',
               'not_found_in_supported_local_media': '已检查当前账号的本地媒体目录，未找到通过消息完整校验值的原件。'}
    if best_preview:
        result, data = best_preview
        if diagnosis:
            result['localCheck'] = diagnosis
            result['reason'] = '当前仅有缩略图或视频封面。' + reasons.get(diagnosis['result'], '')
        return result, data
    return {'status': 'pending', 'reason': reasons.get(diagnosis['result'], issue) if diagnosis else issue,
            **({'localCheck': diagnosis} if diagnosis else {})}, None


class WindowsMediaCache(MediaCache):
    @staticmethod
    def fingerprint(row):
        return hashlib.sha256(json.dumps([row['serverId'], row['timestamp'], row['kind'], row['_raw']], ensure_ascii=False).encode()).hexdigest()

    def message(self, account, group, message_id):
        self.engine.validate(account, group)
        if group not in (self.engine.store.watched(account) or []):
            raise ValueError('请先勾选读取这个会话。')
        return self.engine.adapter.attachment_message(account, group, message_id)[0]

    def resolve(self, account, group, message_id, part=''):
        service = self.engine.database_service
        with service.lock, self.lock:
            self.message(account, group, message_id)
            row, snapshot, revision = self.engine.adapter.attachment_message(account, group, message_id)
            fingerprint = self.fingerprint(row)
            key = (account, group, message_id, part)
            previous = self.entries.get(self.by_message.get(key))
            if previous and previous['fingerprint'] == fingerprint and not previous['public']['previewOnly']:
                return previous['public']
            result, data = resolve_local(snapshot, service.config, group, row, part)
            if data is None:
                return result
            self.message(account, group, message_id)
            asset_id = secrets.token_urlsafe(24)
            path = self.directory / (asset_id + '.blob')
            with path.open('xb') as stream:
                stream.write(data)
            path.chmod(0o600)
            public = {key: value for key, value in result.items() if key != 'digest'}
            public['assetId'] = asset_id
            self.entries[asset_id] = {'key': key, 'fingerprint': fingerprint,
                                      'path': path, 'public': public}
            self.by_message[key] = asset_id
            return public

    def get(self, asset_id, account):
        with self.engine.database_service.lock:
            entry = super().get(asset_id, account)
            row = self.message(*entry['key'][:3])
            if entry['fingerprint'] != self.fingerprint(row):
                raise ValueError('消息已更新，请重新读取附件。')
            return entry
