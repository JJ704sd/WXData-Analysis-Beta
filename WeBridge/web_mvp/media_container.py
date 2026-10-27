"""Resolve one authorized message attachment inside the Linux test container."""
import hashlib
import json
from pathlib import Path
import re
import struct
import subprocess
import tempfile

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7
from rich_content import xml_root, strip_sender, number

LIMIT = 100 * 1024 * 1024
CACHE_DIRECTORY = Path('/tmp/wechat-mvp-media')
VIDEO_CACHE_DIRECTORY = Path('/tmp/wechat-mvp-videos')
MASK = bytes.fromhex('dc2daafed0a06aabc7f27227236b04ab90960760d4ca64182802d4137ac52e3e')
EXPECTED = 'e40116597803a5e0d9fc4a2b727df4b8f7a3dd6de1c336140ea373be2b6626e9'


def aes_decrypt(data, key):
    decryptor = Cipher(algorithms.AES(key), modes.ECB()).decryptor()
    clear = decryptor.update(data) + decryptor.finalize()
    unpadder = PKCS7(128).unpadder()
    return unpadder.update(clear) + unpadder.finalize()


def image_key():
    """Exact-build, bounded /proc reads. Never calls or injects client code."""
    binary = Path('/opt/wechat/wechat')
    if hashlib.sha256(binary.read_bytes()).hexdigest() != EXPECTED:
        raise ValueError('客户端版本不匹配，无法解码图片缓存。')
    pids = subprocess.check_output(['pgrep', '-x', 'wechat'], text=True).split()
    if len(pids) != 1:
        raise ValueError('无法确认当前微信进程。')
    pid = pids[0]
    rows = [line.split() for line in Path(f'/proc/{pid}/maps').read_text().splitlines()]
    base = next(int(row[0].split('-')[0], 16) for row in rows
                if row[-1] == str(binary) and int(row[2], 16) == 0)
    ranges = [tuple(int(x, 16) for x in row[0].split('-')) for row in rows if row[1].startswith('r')]
    with open(f'/proc/{pid}/mem', 'rb') as stream:
        def read(address, size):
            if not any(start <= address and address + size <= end for start, end in ranges):
                raise ValueError('图片缓存密钥对象暂不可用。')
            stream.seek(address)
            result = stream.read(size)
            if len(result) != size:
                raise ValueError('图片缓存密钥读取未完成。')
            return result

        def pointer(address):
            return struct.unpack('<Q', read(address, 8))[0]

        owner = pointer(base + 0x82bb2a8)
        if pointer(owner) != base + 0x7f01f48 or pointer(pointer(owner) + 0x68) != base + 0x4832fa4:
            raise ValueError('图片缓存对象校验失败。')
        account_object = pointer(owner + 0x68)
        value = read(account_object + 0x1f8, 24)
        size = struct.unpack_from('<Q', value, 8)[0] if value[0] & 1 else value[0] >> 1
        if size != 32 or not value[0] & 1:
            raise ValueError('图片密钥尚未就绪，请先接收一张图片。')
        encoded = read(struct.unpack_from('<Q', value, 16)[0], size)
        clear = bytes(a ^ b for a, b in zip(encoded, MASK))
        if not re.fullmatch(rb'[0-9a-f]{32}', clear):
            raise ValueError('图片缓存密钥校验失败。')
        return clear[:16]


def media_type(data):
    if data.startswith(b'\xff\xd8\xff'): return 'image/jpeg', 'jpg'
    if data.startswith(b'\x89PNG\r\n\x1a\n'): return 'image/png', 'png'
    if data.startswith((b'GIF87a', b'GIF89a')): return 'image/gif', 'gif'
    if data.startswith(b'RIFF') and data[8:12] == b'WEBP': return 'image/webp', 'webp'
    if data[4:8] == b'ftyp': return 'video/mp4', 'mp4'
    if data.startswith(b'%PDF-'): return 'application/pdf', 'pdf'
    if data.startswith(b'wxgf'): return 'image/wxgf', 'wxgf'
    return 'application/octet-stream', 'bin'


def dat_decode(data, key):
    if not data.startswith(b'\x07\x08V2\x08\x07') or len(data) < 31:
        if media_type(data)[1] != 'bin': return data
        raise ValueError('暂不支持这张图片的缓存格式。')
    chunk, xor_size = struct.unpack_from('<II', data, 6)
    length = (chunk // 16 + 1) * 16
    if chunk > LIMIT or 15 + length + xor_size > len(data):
        raise ValueError('图片缓存尚未下载完整。')
    head = aes_decrypt(data[15:15 + length], key)
    if len(head) != chunk:
        raise ValueError('图片缓存解码校验失败。')
    remaining = data[15 + length:]
    if not xor_size: return head + remaining
    _, fmt = media_type(head)
    tails = {'jpg': b'\xff\xd9', 'png': b'IEND\xaeB`\x82', 'gif': b'\x00;'}
    expected = tails.get(fmt)
    if expected and xor_size >= len(expected):
        xor = data[-len(expected)] ^ expected[0]
        if bytes(v ^ xor for v in data[-len(expected):]) == expected:
            return head + remaining[:-xor_size] + bytes(v ^ xor for v in remaining[-xor_size:])
    # WXGF/WebP do not have a fixed trailer; the caller may fall back to
    # a separately cached JPEG/PNG thumbnail.
    raise ValueError('无法确认图片尾部编码，正在尝试缩略图。')


def safe_child(base, *parts):
    candidate = base.joinpath(*parts).resolve()
    if not candidate.is_relative_to(base.resolve()):
        raise ValueError('附件路径无效。')
    return candidate


def resolve_media(root, keys, group, row, part=''):
    from container_bridge import copied_query, quote, fields
    base = root.parent
    xml = xml_root(strip_sender(row['raw']))
    if xml is None: return {'status': 'unavailable', 'reason': '消息已撤回或没有可读取的附件。'}
    local_id = int(row['localId'])
    kind = row['kind']
    filename = row.get('media', {}).get('filename') or '附件'
    expected_md5 = ''
    if part:
        record = xml_root(xml.findtext('.//recorditem') or '')
        item = None
        indexes = part.split('.')
        if len(indexes) > 5 or any(not i.isdigit() for i in indexes): raise ValueError('记录附件编号无效。')
        for index in indexes:
            nodes = record.findall('./datalist/dataitem') if record is not None else []
            if int(index) >= len(nodes): raise ValueError('记录附件不存在。')
            item = nodes[int(index)]
            record = xml_root(item.findtext('recordxml') or item.findtext('recorditem') or '')
        kind = {2: 'image', 4: 'video', 8: 'file'}.get(number(item.get('datatype')), '')
        filename = item.findtext('datatitle') or filename
        expected_md5 = item.findtext('fullmd5') or item.findtext('md5') or ''
    elif kind == 'file':
        expected_md5 = xml.findtext('.//appmsg/md5') or ''
    else:
        node = xml.find({'image': 'img', 'video': 'videomsg', 'emoji': 'emoji'}.get(kind, 'none'))
        expected_md5 = node.get('md5', '') if node is not None else ''
    if kind not in ('image', 'video', 'file', 'emoji'):
        return {'status': 'unavailable', 'reason': '这条记录没有可预览的媒体附件。'}

    candidates = []
    month = __import__('datetime').datetime.fromtimestamp(row['timestamp']).strftime('%Y-%m')
    group_hash = hashlib.md5(group.encode()).hexdigest()
    if not part and kind in ('image', 'video') and 'message_resource.db' in keys:
        database = root/'message/message_resource.db'
        resources = copied_query(database, keys[database.name],
            f"SELECT hex(packed_info) AS data FROM MessageResourceInfo WHERE chat_id=(SELECT rowid FROM ChatName2Id WHERE user_name={quote(group)}) AND message_svr_id={int(row['serverId'])} LIMIT 1;")
        for resource in resources:
            nested = fields(bytes.fromhex(resource['data'] or '')).get(2, [])
            for value in nested:
                for raw in fields(value).get(1, []):
                    if re.fullmatch(rb'[0-9a-f]{32}', raw):
                        stem = raw.decode()
                        if kind == 'image':
                            candidates += [safe_child(base, 'msg/attach', group_hash, month, 'Img', stem + suffix + '.dat') for suffix in ('', '_t')]
                        else:
                            candidates += [safe_child(base, 'msg/video', month, stem + suffix) for suffix in ('.mp4', '.jpg', '_thumb.jpg')]
    if re.fullmatch(r'[0-9a-fA-F]{32}', expected_md5) and 'hardlink.db' in keys and kind != 'emoji':
        database = root/'hardlink/hardlink.db'
        rows = copied_query(database, keys[database.name],
            f"SELECT file_name,dir1,dir2 FROM {kind}_hardlink_info_v4 WHERE md5={quote(expected_md5)} LIMIT 8;")
        for link in rows:
            directories = copied_query(database, keys[database.name],
                f"SELECT rowid AS id,username FROM dir2id WHERE rowid IN ({int(link['dir1'])},{int(link['dir2'])});")
            dirs = {r['id']: r['username'] for r in directories}
            if kind == 'image':
                candidates.append(safe_child(base, 'msg/attach', dirs.get(link['dir1'], group_hash), dirs.get(link['dir2'], month), 'Img', link['file_name']))
            else:
                candidates.append(safe_child(base, 'msg', kind, dirs.get(link['dir1'], month), link['file_name']))
    if kind == 'file' and not part:
        candidates.append(safe_child(base, 'msg/file', month, Path(filename).name))
    if kind == 'emoji' and re.fullmatch(r'[a-fA-F0-9]{32}', expected_md5):
        candidates.append(safe_child(base, 'cache', month, 'Emoticon', expected_md5[:2], expected_md5))
    if kind in ('image', 'video') and not part:
        candidates.append(safe_child(base, 'cache', month, 'Message', group_hash, 'Thumb', f'{local_id}_{row["timestamp"]}_thumb.jpg'))

    issue = None
    video_node=xml.find('videomsg') if kind=='video' and not part else None
    video_cache=None
    if kind=='video':
        # Exhaust complete-file candidates before considering any cover image.
        covers=[p for p in candidates if p.suffix.lower() in ('.jpg','.jpeg','.png')]
        complete=[p for p in candidates if p not in covers]
        if video_node is not None:
            identity='|'.join((str(base),group,str(row['serverId']),expected_md5))
            video_cache=VIDEO_CACHE_DIRECTORY/(hashlib.sha256(identity.encode()).hexdigest()+'.mp4')
            candidates=[video_cache,*complete,None,*covers]
        else:candidates=[*complete,*covers]
    for source in dict.fromkeys(candidates):
        if source is None:
            from video_download import fetch_wecom_video
            try:
                data=fetch_wecom_video(video_node.attrib)
                VIDEO_CACHE_DIRECTORY.mkdir(mode=0o700,parents=True,exist_ok=True)
                temporary=None
                try:
                    with tempfile.NamedTemporaryFile(dir=VIDEO_CACHE_DIRECTORY,delete=False) as file:
                        temporary=Path(file.name);file.write(data)
                    temporary.replace(video_cache)
                finally:
                    if temporary is not None:temporary.unlink(missing_ok=True)
                source=video_cache
            except (ValueError,OSError) as exc:
                issue=str(exc) if isinstance(exc,ValueError) else '视频缓存保存失败，请重试。'
                continue
        else:
            if not source.is_file(): continue
            if source.stat().st_size > LIMIT:
                issue = '附件超过本版 100 MB 的预览上限。'; continue
            data = source.read_bytes()
        try:
            if data.startswith(b'\x07\x08V2\x08\x07'):
                data = dat_decode(data, image_key())
            elif kind == 'emoji' and media_type(data)[1] == 'bin':
                data = aes_decrypt(data, image_key())
        except (ValueError, OSError):
            issue = '附件缓存暂时无法解码，可在客户端查看后重试。'; continue
        mime, ext = media_type(data)
        if ext == 'wxgf':
            result = subprocess.run(['media-convert', 'wxgf2img'], input=data, capture_output=True, timeout=20)
            if result.returncode == 0:
                data = result.stdout; mime, ext = media_type(data)
        if kind in ('image', 'emoji') and not mime.startswith('image/'):
            issue = '图片缓存格式暂不支持。'; continue
        if kind=='video':
            if not mime.startswith(('video/','image/')):
                issue='视频缓存格式暂不支持。';continue
            if mime.startswith('video/') and expected_md5 and hashlib.md5(data).hexdigest()!=expected_md5.lower():
                issue='视频缓存尚未完整，正在尝试重新下载。';continue
        if kind == 'file' and expected_md5 and hashlib.md5(data).hexdigest() != expected_md5:
            issue = '文件完整性校验失败，请在客户端重新下载。'; continue
        if kind == 'file' and ext == 'bin' and Path(filename).suffix.lower() in ('.txt', '.md', '.csv', '.json', '.log'):
            try: data.decode('utf-8'); mime, ext = 'text/plain', 'txt'
            except UnicodeError: pass
        if kind != 'file': filename = f'{kind}_{local_id}.{ext}'
        filename = Path(filename).name.replace('\r', '').replace('\n', '').replace('\x00', '')[:180] or '附件'
        directory = CACHE_DIRECTORY;directory.mkdir(mode=0o700, exist_ok=True)
        digest = hashlib.sha256(data).hexdigest()
        destination = directory / (digest + '.blob')
        destination.write_bytes(data);destination.chmod(0o600)
        preview_only=(kind == 'video' and mime.startswith('image/')) or (kind == 'image' and (source.name.endswith('_t.dat') or 'Thumb' in source.parts))
        return {'status': 'ready', 'digest': digest, 'size': len(data), 'mime': mime, 'filename': filename,
                'previewOnly': preview_only, 'kind': kind,**({'reason':issue} if preview_only and issue else {})}
    return {'status': 'pending', 'reason': issue or '客户端尚未下载这份附件。请在 Linux 微信中打开或下载，然后点重试。'}
