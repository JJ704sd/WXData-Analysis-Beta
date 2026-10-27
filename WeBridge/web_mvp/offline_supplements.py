"""Read-only recovery from one explicitly selected directory; never match by filename."""
from collections import Counter
import hashlib
import mimetypes
import os
from pathlib import Path
import re
import tempfile
import time

from PIL import Image
from offline_images import message_nodes


def fingerprint(path):
    value = path.stat()
    return value.st_size, value.st_mtime_ns, value.st_ctime_ns, value.st_ino


def linked(path):
    return path.is_symlink() or path.is_junction()


class SupplementalMedia:
    def __init__(self, root):
        self.root = Path(root).expanduser().absolute()
        self.cache = {}
        self.begin_export()

    def begin_export(self):
        self.index = None
        self.copied = {}
        self.stats = Counter()
        self.progress = None
        self.last_progress = 0

    def _notify(self):
        now = time.monotonic()
        if self.progress and now - self.last_progress > .25:
            self.progress(dict(self.stats))
            self.last_progress = now

    def _scan(self, excluded):
        self.index = {}
        if not self.root.is_dir() or any(linked(p) for p in (self.root, *self.root.parents)):
            self.stats['directoryUnavailable'] += 1
            return
        current = {}
        def failed(_): self.stats.update({'unreadable': 1})
        for folder, directories, files in os.walk(self.root, onerror=failed, followlinks=False):
            directories[:] = [d for d in directories if not linked(Path(folder) / d)
                              and not (Path(folder) / d).resolve().is_relative_to(excluded)]
            for name in files:
                path = Path(folder) / name
                try:
                    if linked(path) or path.resolve().is_relative_to(excluded): continue
                    before = fingerprint(path)
                    cached = self.cache.get(path)
                    if cached and cached[0] == before:
                        digest = cached[1]; self.stats['reused'] += 1
                    else:
                        hasher = hashlib.md5()
                        with path.open('rb') as stream:
                            for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                                hasher.update(chunk); self.stats['bytesHashed'] += len(chunk); self._notify()
                        digest = hasher.hexdigest()
                        if fingerprint(path) != before:
                            self.stats['changedDuringRead'] += 1
                            continue
                    current[path] = before, digest
                    self.index.setdefault(digest, []).append(path)
                    self.stats['filesChecked'] += 1
                except OSError:
                    self.stats['unreadable'] += 1
                self._notify()
        self.cache = current

    def attach(self, row, destination):
        for node, _, _ in message_nodes(row):
            if node.get('kind') not in ('image', 'file', 'video'): continue
            old = node.get('asset') or {}
            if old.get('status') == 'ready' and not old.get('previewOnly'): continue
            media = node.get('media') or {}
            wanted = str(media.get('md5') or '').lower()
            if not re.fullmatch('[a-f0-9]{32}', wanted): continue
            key = wanted, node['kind']
            if key in self.copied:
                node['asset'] = {**self.copied[key], 'filename': media.get('filename') or self.copied[key]['filename']}
                self.stats['recoveredReferences'] += 1
                continue
            if self.index is None: self._scan(destination.resolve())
            for candidate in self.index.get(wanted, []):
                temporary = None
                try:
                    # Resolved path checks are repeated when copying, after indexing.
                    if any(linked(p) for p in (candidate, *candidate.parents)) or not candidate.resolve().is_relative_to(self.root.resolve()): continue
                    before = fingerprint(candidate)
                    assets = destination / 'assets'; assets.mkdir(exist_ok=True)
                    fd, name = tempfile.mkstemp(prefix='.supplement-', dir=assets)
                    temporary = Path(name)
                    md5, sha = hashlib.md5(), hashlib.sha256()
                    with os.fdopen(fd, 'wb') as output, candidate.open('rb') as source:
                        for chunk in iter(lambda: source.read(1024 * 1024), b''):
                            md5.update(chunk); sha.update(chunk); output.write(chunk)
                            self._notify()
                    if md5.hexdigest() != wanted or fingerprint(candidate) != before:
                        self.stats['changedDuringRead'] += 1
                        continue
                    filename = media.get('filename') or candidate.name
                    suffix = Path(filename).suffix.lower()
                    if node['kind'] == 'image':
                        with Image.open(temporary) as image:
                            suffix = {'JPEG': '.jpg', 'PNG': '.png', 'WEBP': '.webp', 'GIF': '.gif',
                                      'BMP': '.bmp', 'TIFF': '.tiff'}.get(image.format, '.bin')
                            image.verify()
                    if not re.fullmatch(r'\.[a-z0-9]{1,12}', suffix): suffix = '.bin'
                    relative = 'assets/' + sha.hexdigest() + suffix
                    os.replace(temporary, destination / relative)
                    node['asset'] = {'status': 'ready', 'filename': filename, 'path': relative,
                        'digest': sha.hexdigest(), 'size': before[0], 'previewOnly': False,
                        'variant': 'original', 'source': 'user_supplement',
                        'mime': mimetypes.guess_type('file' + suffix)[0] or 'application/octet-stream',
                        'localCheck': {'checked': True, 'result': 'matched_original_md5'}}
                    self.copied[key] = dict(node['asset'])
                    self.stats['recoveredReferences'] += 1
                    break
                except (OSError, ValueError, Image.DecompressionBombError):
                    self.stats['copyFailed'] += 1
                finally:
                    if temporary: temporary.unlink(missing_ok=True)

    def report(self):
        return {'enabled': True, 'match': 'original_md5', 'scanned': self.index is not None,
                'unit': 'reference_occurrence', **dict(self.stats)}
