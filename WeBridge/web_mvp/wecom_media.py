"""Match media only inside the selected WeCom account, with content checks."""
from collections import defaultdict
import hashlib
import itertools
import mimetypes
import os
from pathlib import Path
import re
import sqlite3
import time
import weakref

from wecom_content import leaves

LIMIT = 100 * 1024 * 1024


class WeComMedia:
    def __init__(self, config):
        source = Path(config['sourceRoot']).resolve()
        self.account = source.parent if source.name == 'Data' else source
        self.files, self.sizes, self.names = [], defaultdict(list), defaultdict(list)
        self.recovered = 0
        self.indexed = False
        self.diagnostics = defaultdict(int)
        self.hashes = None
        self._hash_db = None
        self._generation = 0
        self.content_search_complete = None
        self.progress = None

    def begin_export(self):
        """Newly opened/downloaded client files are visible on every target export."""
        self.files, self.sizes, self.names = [], defaultdict(list), defaultdict(list)
        self.indexed = False
        self.hashes = None
        self.diagnostics.clear()
        self.content_search_complete = None

    def _cache_files(self):
        folder = self.account / 'Cache'
        if folder.is_symlink():
            self.diagnostics['cacheReadErrors'] += 1
            return
        def failed(error):
            self.diagnostics['cacheReadErrors'] += 1
        if not folder.exists():
            return
        for parent, dirs, files in os.walk(folder, followlinks=False, onerror=failed):
            dirs[:] = [name for name in dirs if not (Path(parent) / name).is_symlink()
                       and (Path(parent) / name).resolve().is_relative_to(self.account)]
            for name in files:
                path = Path(parent) / name
                if not path.is_symlink() and path.resolve().is_relative_to(self.account):
                    yield path

    def _refresh_hashes(self):
        # SQLite's empty filename creates a temporary on-disk database, removed
        # on close. Memory remains bounded even for very large account caches.
        if self._hash_db is None:
            self._hash_db = sqlite3.connect('', check_same_thread=False)
            weakref.finalize(self, self._hash_db.close)
            self._hash_db.execute('PRAGMA cache_size=-2048')
            self._hash_db.execute('CREATE TABLE digests(path TEXT PRIMARY KEY,size INTEGER,mtime INTEGER,inode TEXT,digest TEXT,generation INTEGER)')
            self._hash_db.execute('CREATE INDEX by_digest ON digests(digest)')
        db = self._hash_db
        self._generation += 1
        generation = self._generation
        last_progress = time.monotonic()
        scanned = 0
        with db:
            for path in self._cache_files():
                scanned += 1
                relative = path.relative_to(self.account).as_posix()
                try:
                    stamp = path.stat()
                    signature = (stamp.st_size, stamp.st_mtime_ns, str(stamp.st_ino))
                    old = db.execute('SELECT size,mtime,inode,digest FROM digests WHERE path=?', (relative,)).fetchone()
                    if stamp.st_size <= 0:
                        db.execute('DELETE FROM digests WHERE path=?', (relative,))
                        continue
                    if old and old[:3] == signature:
                        digest = old[3]
                        self.diagnostics['hashCacheReused'] += 1
                    else:
                        digest = hashlib.md5()
                        with path.open('rb') as stream:
                            unread = stamp.st_size
                            while unread:
                                chunk = stream.read(min(1024 * 1024, unread))
                                if not chunk:
                                    break
                                digest.update(chunk)
                                unread -= len(chunk)
                                self.diagnostics['hashBytesRead'] += len(chunk)
                                if self.progress and time.monotonic() - last_progress >= .25:
                                    self.progress({'files': scanned, 'bytes': self.diagnostics['hashBytesRead']})
                                    last_progress = time.monotonic()
                        after = path.stat()
                        if unread or (after.st_size, after.st_mtime_ns, str(after.st_ino)) != signature:
                            self.diagnostics['changedDuringSearch'] += 1
                            db.execute('DELETE FROM digests WHERE path=?', (relative,))
                            continue
                        digest = digest.hexdigest()
                        self.diagnostics['filesHashed'] += 1
                    db.execute('INSERT OR REPLACE INTO digests VALUES(?,?,?,?,?,?)',
                               (relative, *signature, digest, generation))
                except OSError:
                    self.diagnostics['cacheReadErrors'] += 1
                    db.execute('DELETE FROM digests WHERE path=?', (relative,))
                if self.progress and time.monotonic() - last_progress >= .25:
                    self.progress({'files': scanned, 'bytes': self.diagnostics['hashBytesRead']})
                    last_progress = time.monotonic()
            db.execute('DELETE FROM digests WHERE generation!=?', (generation,))
        self.hashes = True
        self.content_search_complete = not any(self.diagnostics[key] for key in
            ('cacheReadErrors', 'oversizeFiles', 'changedDuringSearch'))
        self.diagnostics['digestIndexedFiles'] = db.execute('SELECT count(*) FROM digests').fetchone()[0]
        if self.progress:
            self.progress({'files': scanned, 'bytes': self.diagnostics['hashBytesRead']})

    def _fallback(self, media):
        """Find exact bytes across the cache; reuse unchanged file digests."""
        digests = [v.casefold() for v in (media.get('md5'), media.get('thumbnailMd5'))
                   if isinstance(v, str) and re.fullmatch('[a-fA-F0-9]{32}', v)]
        if not digests:
            return
        if self.hashes is None:
            self._refresh_hashes()
        for digest in digests:
            for (relative,) in self._hash_db.execute('SELECT path FROM digests WHERE digest=?', (digest,)):
                yield self.account / relative

    def _index(self):
        if self.indexed:
            return
        self.indexed = True
        for name in ('Cache', 'Emotion'):
            folder = self.account / name
            if not folder.is_dir() or folder.is_symlink():
                continue
            for parent, dirs, files in os.walk(folder, followlinks=False):
                dirs[:] = [v for v in dirs if not (Path(parent) / v).is_symlink() and (Path(parent) / v).resolve().is_relative_to(self.account)]
                for name in files:
                    path = Path(parent) / name
                    if path.is_symlink() or not path.resolve().is_relative_to(self.account):
                        continue
                    try:
                        size = path.stat().st_size
                    except OSError:
                        continue
                    if size > 0:
                        self.files.append(path)
                        self.sizes[size].append(path)
                        self.names[path.name.casefold()].append(path)
                    if len(self.files) >= 50000:
                        self.diagnostics['fileIndexLimited'] = 1
                        return

    def report(self, before=0):
        return {'scope': 'selected_wecom_account_cache', 'indexedFiles': len(self.files),
                'recoveredReferences': self.recovered - before, 'networkFetched': False,
                'contentSearchComplete': self.content_search_complete, 'singleFileLimitBytes': None,
                'previewDecodeLimitBytes': LIMIT,
                'refreshedForExport': True, 'diagnostics': dict(self.diagnostics)}

    def attach(self, node, destination, local_extra=b''):
        media, kind = node.get('media', {}), node['kind']
        unavailable = {'status': 'unavailable', 'reasonCode': 'local_cache_not_matched',
                       'reason': '所选企微账号中未找到可验证的本地图片或附件；这不代表客户端从未下载。打开原消息的图片或下载附件后，重新解析此群即可检查新缓存。'}
        if kind == 'emoji':
            return {**unavailable, 'reason': '表情为可选内容，未匹配时不影响业务消息保留。'}
        self._index()
        candidates, linked = [], set()
        for value in leaves(local_extra):
            # Old absolute paths are remapped only at known account subtrees.
            for subtree in ('Cache', 'Emotion'):
                marker = '\\' + subtree + '\\'
                if marker.casefold() in value.casefold():
                    tail = re.split(re.escape(marker), value, maxsplit=1, flags=re.I)[1]
                    candidate = self.account / subtree / Path(tail.replace('\\', '/'))
                    if candidate.resolve().is_relative_to(self.account):
                        candidates.append(candidate)
                        linked.add(candidate)
        filename = media.get('filename', '')
        for name in (filename, filename + '.temp'):
            candidates.extend(self.names.get(name.casefold(), []))
        candidates.extend(self.sizes.get(media.get('size', 0), []))
        candidates.extend(self.sizes.get(media.get('thumbnailSize', 0), []))
        if len(candidates) > 1000:
            candidates = candidates[:1000]
        if self.hashes is not None and re.fullmatch('[a-fA-F0-9]{32}', media.get('md5', '')):
            # The complete digest index already identifies exact candidates.
            # Avoid reading hundreds of same-size files again for each message.
            candidates = [path for path in candidates if path in linked]
        preview = None
        linked_existing, linked_invalid = False, False
        seen = set()
        for path in itertools.chain(candidates, self._fallback(media)):
            if path in seen:
                continue
            seen.add(path)
            try:
                if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(self.account):
                    continue
                stamp = path.stat()
                linked_existing = linked_existing or path in linked
                if media.get('md5'):
                    from offline_media_copy import copy_original
                    asset=copy_original(path,destination,media['md5'],filename or path.name,kind)
                    if asset:
                        asset['sourceRelativePath']=path.relative_to(self.account).as_posix()
                        self.recovered+=1
                        return asset
                if stamp.st_size > LIMIT:
                    continue
                with path.open('rb') as stream:
                    data = stream.read(LIMIT + 1)
                if path.stat().st_mtime_ns != stamp.st_mtime_ns or len(data) != stamp.st_size:
                    continue
                digest = hashlib.md5(data).hexdigest()
                original = bool(media.get('md5')) and digest.casefold() == media['md5'].casefold()
                thumbnail = bool(media.get('thumbnailMd5')) and digest.casefold() == media['thumbnailMd5'].casefold()
                # An exact path in THIS message's local metadata can identify a
                # client-converted preview. A name or dimensions alone cannot.
                converted = kind == 'image' and path in linked and not original and not thumbnail
                if not original and not thumbnail and not converted:
                    continue
                mime, extension = mimetypes.guess_type(filename or path.name)[0], Path(filename).suffix.lower()
                if kind == 'image' or thumbnail:
                    from PIL import Image
                    import io
                    try:
                        with Image.open(io.BytesIO(data)) as image:
                            width, height = image.size
                            image.verify()
                            fmt = (image.format or '').lower()
                    except Image.DecompressionBombError:
                        unavailable.update(reasonCode='image_pixel_limit',
                                           reason='图片像素数超过本机安全解码上限；此附件未载入，消息与原始字段保留。')
                        continue
                    extension = '.' + ('jpg' if fmt == 'jpeg' else fmt)
                    mime = 'image/' + ('jpeg' if fmt == 'jpeg' else fmt)
                    if converted:
                        expected_w, expected_h = media.get('width', 0), media.get('height', 0)
                        if (not expected_w or not expected_h or width > expected_w or height > expected_h
                                or abs(width * expected_h / (height * expected_w) - 1) > .02):
                            linked_invalid = True
                            continue
                sha = hashlib.sha256(data).hexdigest()
                extension = extension if re.fullmatch(r'\.[a-zA-Z0-9]{1,10}', extension) else '.bin'
                relative = 'assets/' + sha + extension
                assets = destination / 'assets'
                assets.mkdir(exist_ok=True)
                target = destination / relative
                if not target.exists():
                    with target.open('xb') as stream:
                        stream.write(data)
                asset = {'status': 'ready', 'path': relative, 'filename': filename or path.name,
                         'size': len(data), 'digest': sha, 'mime': mime or 'application/octet-stream',
                         'previewOnly': not original, 'reason': '' if original else '仅找到经摘要校验的缩略图或视频封面。',
                         'matchBasis': 'content_md5', 'sourceRelativePath': path.relative_to(self.account).as_posix()}
                if converted:
                    asset.update(previewVariant='local_converted', matchBasis='原消息缓存路径+图片格式与尺寸校验',
                        reason='本地预览：企微缓存已转码或缩放，原图摘要未匹配；可查看，但不计为原图。',
                        width=width, height=height)
                if original:
                    self.recovered += 1
                    return asset
                if not preview or (converted and preview.get('previewVariant') != 'local_converted'):
                    preview = asset
            except (OSError, ValueError, ImportError) as error:
                if isinstance(error, ValueError) and str(error) == 'image_pixel_limit':
                    unavailable.update(reasonCode='image_pixel_limit',
                                       reason='图片像素数超过本机安全解码上限；此附件未载入，消息与原始字段保留。')
                continue
        if preview:
            self.recovered += 1
            self.diagnostics['localConverted' if preview.get('previewVariant') == 'local_converted' else 'thumbnail'] += 1
            return preview
        if linked and unavailable.get('reasonCode') != 'image_pixel_limit':
            code = 'linked_cache_not_validated' if linked_existing or linked_invalid else 'linked_cache_absent'
            unavailable.update(reasonCode=code, reason=(
                '原消息指向的缓存存在，但格式、尺寸或完整性未通过校验，未作为可用图片。请在企微打开原图后重新解析此群。'
                if code == 'linked_cache_not_validated' else
                '原消息指向的缓存文件当前不可访问或已被清理，其他候选也未匹配。请在企微打开原图后重新解析此群。'))
        if self.content_search_complete is False and unavailable.get('reasonCode') != 'image_pixel_limit':
            unavailable.update(reasonCode='local_search_incomplete',
                reason='本地检索存在不可读、处理中变化或超过单文件上限的文件，未取得原件；不能据此断定原件不在本机。详见解析结果与补齐清单。')
        self.diagnostics[unavailable['reasonCode']] += 1
        return unavailable
