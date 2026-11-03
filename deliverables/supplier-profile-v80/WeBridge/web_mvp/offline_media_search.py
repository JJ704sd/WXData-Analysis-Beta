"""Bounded content-checksum search inside one selected account's media stores.

Only media directories are inspected. No login/config databases, other accounts,
network requests, process access, or writes to source files.
"""
import hashlib
import os
from pathlib import Path
import re
import stat
import time

from local_image_keys import ImageKeys

MAX_FILES = None
MAX_BYTES = None
MAX_FILE_BYTES = 100 * 1024 ** 2


class LocalMediaSearch:
    def begin_export(self):
        recovered = self.recovered
        cache=self._digest_cache
        self.__init__(self.config, max_files=self.max_files, max_bytes=self.max_bytes)
        self._digest_cache=cache
        self.recovered = recovered

    def __init__(self, config, *, max_files=MAX_FILES, max_bytes=MAX_BYTES):
        self.config = config
        source = Path(config.get('sourceRoot') or '').resolve()
        self.account = source.parent if source.name == 'db_storage' else source if (source / 'db_storage').is_dir() else None
        self.max_files, self.max_bytes = max_files, max_bytes
        self.built = False
        self.image_keys = ImageKeys(config)
        self.matches, self.names = {}, {}
        self._digest_cache={}
        self.progress=None
        self.recovered = 0
        self.stats = {'scope': 'selected_account_media_directories', 'filesScanned': 0, 'bytesScanned': 0,
                      'decodedContainers': 0, 'unreadableFiles': 0, 'unsupportedContainers': 0,
                      'oversizeFiles': 0, 'limitReached': False, 'elapsedMs': 0,'hashCacheReused':0}

    def _safe(self, path):
        value = path.lstat()
        return (not stat.S_ISLNK(value.st_mode) and not getattr(value, 'st_file_attributes', 0) & 0x400
                and path.resolve().is_relative_to(self.account))

    def _roots(self):
        for relative in ('msg/attach', 'msg/file', 'msg/video', 'business/emoticon', 'resource', 'temp'):
            yield self.account / relative
        cache = self.account / 'cache'
        if cache.is_dir() and self._safe(cache):
            for month in sorted(cache.iterdir()):
                if re.fullmatch(r'\d{4}-\d{2}', month.name) and month.is_dir() and self._safe(month):
                    for category in ('Emoticon', 'Message', 'HttpResource'):
                        yield month / category

    def _files(self):
        def failed(_error):
            self.stats['unreadableFiles'] += 1
        try:
            roots = list(self._roots())
        except OSError:
            failed(None)
            return
        for root in roots:
            try:
                if not root.is_dir() or not self._safe(root):
                    continue
                for directory, directories, names in os.walk(root, followlinks=False, onerror=failed):
                    safe_directories = []
                    for name in sorted(directories):
                        try:
                            if self._safe(Path(directory) / name):
                                safe_directories.append(name)
                        except OSError:
                            failed(None)
                    directories[:] = safe_directories
                    for name in sorted(names):
                        path = Path(directory) / name
                        try:
                            if path.is_file() and self._safe(path):
                                yield path
                        except OSError:
                            failed(None)
            except OSError:
                failed(None)

    def _build(self):
        self.built = True
        if self.account is None:
            return
        started = time.monotonic()
        current={};last_progress=started
        for path in self._files():
            if self.max_files is not None and self.stats['filesScanned'] >= self.max_files:
                self.stats['limitReached'] = True
                break
            self.stats['filesScanned'] += 1
            for digest in re.findall(r'(?<![0-9a-f])[0-9a-f]{32}(?![0-9a-f])', path.name.lower()):
                self.names[digest] = self.names.get(digest, 0) + 1
            try:
                before = path.stat()
                if before.st_size<=0:continue
                if self.max_bytes is not None and self.stats['bytesScanned'] + before.st_size > self.max_bytes:
                    self.stats['limitReached'] = True
                    break
                signature=(before.st_size,before.st_mtime_ns,before.st_ctime_ns,before.st_ino)
                previous=self._digest_cache.get(path)
                if previous and previous[0]==signature:
                    digest=previous[1];current[path]=previous
                    self.stats['hashCacheReused']+=1
                else:
                    with path.open('rb') as stream:
                        head=stream.read(6);stream.seek(0)
                        if head==b'\x07\x08V2\x08\x07':
                            if before.st_size>MAX_FILE_BYTES:
                                self.stats['oversizeFiles']+=1;continue
                            data=stream.read(MAX_FILE_BYTES+1)
                            self.stats['bytesScanned']+=len(data)
                            try:data=self.image_keys.decode(data)
                            except ValueError:
                                self.stats['unsupportedContainers']+=1;continue
                            self.stats['decodedContainers']+=1
                            digest=hashlib.md5(data).hexdigest()
                        else:
                            hasher=hashlib.md5()
                            for chunk in iter(lambda:stream.read(1024*1024),b''):
                                hasher.update(chunk);self.stats['bytesScanned']+=len(chunk)
                                if self.progress and time.monotonic()-last_progress>.25:
                                    self.progress({'files':self.stats['filesScanned'],'bytes':self.stats['bytesScanned']});last_progress=time.monotonic()
                            digest=hasher.hexdigest()
                            current[path]=(signature,digest)
                after = path.stat()
                if signature != (after.st_size, after.st_mtime_ns, after.st_ctime_ns,after.st_ino):
                    self.stats['unreadableFiles'] += 1
                    current.pop(path,None)
                    continue
                self.matches.setdefault(digest, []).append(path)
            except OSError:
                self.stats['unreadableFiles'] += 1
            if self.progress and time.monotonic()-last_progress>.25:
                self.progress({'files':self.stats['filesScanned'],'bytes':self.stats['bytesScanned']});last_progress=time.monotonic()
        self._digest_cache=current
        self.stats['elapsedMs'] = round((time.monotonic() - started) * 1000)

    def paths_for(self, expected):
        if not self.built:
            self._build()
        # The caller re-reads and verifies the complete checksum before use.
        return self.matches.get(expected, [])

    def diagnosis(self, expected):
        if self.account is None:
            code = 'source_not_bound'
        elif self.names.get(expected) or self.matches.get(expected):
            code = 'local_candidate_not_verified'
        elif self.stats['limitReached'] or self.stats['unreadableFiles'] or self.stats['oversizeFiles']:
            code = 'local_search_incomplete'
        else:
            code = 'not_found_in_supported_local_media'
        return {'result': code, 'namedCandidates': self.names.get(expected, 0),
                'checked': self.built, 'scope': self.stats['scope']}

    def report(self, recovered_since=0):
        return {**self.stats, 'status': 'checked' if self.built else 'not_needed',
                'recoveredReferences': self.recovered - recovered_since}
