"""Versioned restart checkpoints, constrained to this client's managed caches."""
import json
import hashlib
import os
from pathlib import Path
import tempfile
import threading
from datetime import datetime

from offline_chat import ArchiveError, LocalArchive
from offline_accounts import same_account_source
from database_snapshot import _hash_file
from offline_reporting import REPORT_FILE
from offline_review import inventory, validated_approval

MAX_STATE_BYTES = 16 * 1024 * 1024


class ReviewProgress:
    def __init__(self, directory, client, conversation_mode='group', *, export_roots=()):
        self.directory, self.client = Path(directory).resolve(), client
        if conversation_mode not in ('group', 'direct'):
            raise ArchiveError('会话模式必须为 group 或 direct。')
        self.conversation_mode = conversation_mode
        filename = 'session-direct-v1.json' if conversation_mode == 'direct' else 'session-v1.json'
        self.path = self.directory / 'reviews' / filename
        self._snapshot, self._files = None, None
        self._lock = threading.RLock()
        self.export_roots = list(export_roots)

    def export_path(self, entry):
        root = entry.get('exportRoot')
        if root is None:
            return self._managed(entry.get('exportTarget'), 'exports')
        if (self.conversation_mode != 'group' or not isinstance(root, str) or
                root not in self.export_roots or not Path(root).is_absolute()):
            raise ValueError('unapproved_export_root')
        relative = entry.get('exportTarget')
        if not isinstance(relative, str) or not relative or Path(relative).is_absolute() or '..' in Path(relative).parts:
            raise ValueError('invalid_export_target')
        base = Path(root)
        target = base / relative
        if any(p.is_symlink() or p.is_junction() for p in (target, *target.parents)):
            raise ValueError('redirected_export_target')
        resolved = target.resolve()
        if resolved == base.resolve() or not resolved.is_relative_to(base.resolve()):
            raise ValueError('outside_export_root')
        return resolved

    def export_reference(self, path):
        path = Path(path).absolute()
        if path.is_relative_to(self.directory / 'exports'):
            entry = {'exportTarget': path.relative_to(self.directory).as_posix()}
        else:
            roots = [Path(root) for root in self.export_roots if path.is_relative_to(Path(root))]
            if not roots:
                raise ValueError('unapproved_export_root')
            base = max(roots, key=lambda p: len(p.parts))
            entry = {'exportRoot': str(base), 'exportTarget': path.relative_to(base).as_posix()}
        self.export_path(entry)
        return entry

    def _managed(self, relative, area):
        if not isinstance(relative, str) or not relative:
            raise ValueError('invalid_progress_path')
        original = self.directory / relative
        if any(path.is_symlink() or path.is_junction() for path in (original, *original.parents)):
            raise ValueError('redirected_progress_path')
        path = original.resolve()
        base = self.directory / area
        if not path.is_relative_to(base) or path == base:
            raise ValueError('outside_progress_cache')
        return path

    def clear(self):
        with self._lock:
            if os.path.lexists(self.path):
                try:
                    self._managed(self.path.relative_to(self.directory).as_posix(), 'reviews').unlink()
                except (OSError, ValueError):
                    raise ArchiveError('无法清除旧复核进度，请检查工具缓存权限。') from None

    def warm_snapshot(self, archive):
        """Hash a newly loaded snapshot before selection changes reach the UI."""
        with self._lock:
            snapshot = self._managed(archive.root.relative_to(self.directory).as_posix(), 'snapshots')
            if snapshot != self._snapshot:
                self._files, self._snapshot = inventory(snapshot), snapshot
            return snapshot

    def save(self, archive, reviews, ui):
        with self._lock:
            if not reviews and not (self.conversation_mode == 'group' and ui.get('selected')):
                self.clear()
                return
            temporary = None
            try:
                snapshot = self.warm_snapshot(archive)
                entries = []
                for pending in reviews.values():
                    relative = pending['root'].relative_to(self.directory).as_posix()
                    self._managed(relative, 'reviews')
                    entry = {'root': relative, 'summary': pending['summary'], 'files': pending['files']}
                    if pending.get('approval'):
                        entry['approval'] = pending['approval']
                    if pending.get('exportTarget'):
                        entry['exportTarget'] = pending['exportTarget']
                        if pending.get('exportRoot'):
                            entry['exportRoot'] = pending['exportRoot']
                    entries.append(entry)
                value = {'version': 1, 'client': self.client, 'conversationMode': self.conversation_mode,
                         'sourceRoot': archive.media_config['sourceRoot'],
                         'selfId': archive.state['self_id'], 'snapshot': snapshot.relative_to(self.directory).as_posix(),
                         'snapshotFiles': self._files, 'reviews': entries, 'ui': ui}
                if any(entry.get('exportRoot') for entry in entries):
                    value['version'] = 2
                encoded = json.dumps(value, ensure_ascii=False).encode('utf8')
                if len(encoded) > MAX_STATE_BYTES:
                    raise ValueError('progress_too_large')
                self._managed(self.path.relative_to(self.directory).as_posix(), 'reviews')
                self.path.parent.mkdir(parents=True, exist_ok=True)
                fd, temporary = tempfile.mkstemp(prefix='.session-', dir=self.path.parent)
                with os.fdopen(fd, 'wb') as stream:
                    stream.write(encoded)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, self.path)
            except (OSError, ValueError):
                raise ArchiveError('复核进度未保存，尚未完成本次确认；请检查工具缓存空间或权限后重试。') from None
            finally:
                if temporary:
                    Path(temporary).unlink(missing_ok=True)

    def _validate_published(self, entry, archive, snapshot, known_chats, snapshot_time,
                            source_fingerprint):
        """Validate a published v1 entry before exposing it as completed."""
        def managed(value, area):
            if isinstance(value, Path):
                value = value.relative_to(self.directory).as_posix()
            return self._managed(value, area)

        summary = entry['summary']
        conversation = (known_chats.get(summary['chat']['id'])
                        if isinstance(known_chats, dict) else None)
        root = managed(entry['root'], 'reviews')
        if (root.name != summary['reviewId'] or summary['client'] != self.client or
                summary.get('conversationMode', 'group') != self.conversation_mode or
                summary['selfId'] != archive.state['self_id'] or
                summary['snapshotTime'] != snapshot_time or
                summary['sourceFingerprint'] != source_fingerprint or
                summary['chat']['id'] not in known_chats or
                inventory(root) != entry['files']):
            raise ValueError('progress_preview_changed')
        if (self.conversation_mode == 'direct' and
                (not isinstance(conversation, dict) or
                 summary.get('peerId') != conversation.get('peerId') or
                 summary.get('relationship', 'unknown') != conversation.get('relationship', 'unknown'))):
            raise ValueError('progress_peer_mapping_changed')
        report = json.loads((root / 'manifest.json').read_text(encoding='utf8'))
        if (report['chat'] != summary['chat'] or
                report['exportedRows'] != summary['exportedRows'] or
                any(report.get(key) != summary.get(key)
                    for key in ('startDate', 'endDate', 'copyMediaRequested')) or
                not same_account_source(report['sourceRoot'], snapshot, self.client)):
            raise ValueError('progress_preview_identity_changed')
        target = self.export_path(entry)
        if not target.is_dir():
            raise ValueError('published_target_missing')
        published_manifest = json.loads((target / 'manifest.json').read_text(encoding='utf8'))
        review = json.loads((target / 'review.json').read_text(encoding='utf8'))
        human_review = published_manifest.get('humanReview', {})
        if (human_review.get('status') != 'confirmed' or
                human_review.get('reviewId') != summary['reviewId'] or
                review.get('status') != 'confirmed' or
                review.get('reviewId') != summary['reviewId'] or
                review.get('chat') != summary['chat'] or
                review.get('reviewedArtifacts') != entry['files']):
            raise ValueError('published_review_invalid')
        if (published_manifest.get('chat') != report.get('chat') or
                published_manifest.get('exportedRows') != report.get('exportedRows') or
                published_manifest.get('startDate') != report.get('startDate') or
                published_manifest.get('endDate') != report.get('endDate') or
                published_manifest.get('copyMediaRequested') != report.get('copyMediaRequested') or
                not same_account_source(published_manifest.get('sourceRoot'),
                                        report.get('sourceRoot'), self.client)):
            raise ValueError('published_manifest_identity_changed')
        for key in ('client', 'conversationMode', 'selfId', 'sourceFingerprint',
                    'snapshotTime', 'startDate', 'endDate', 'copyMediaRequested',
                    'exportedRows', 'peerId', 'relationship'):
            if key in summary and review.get(key) != summary.get(key):
                raise ValueError('published_review_identity_changed')
        actual = [item for item in inventory(target) if item['path'] != 'manifest.json']
        if published_manifest.get('artifacts') != actual:
            raise ValueError('published_manifest_changed')
        for item in entry['files']:
            relative = item.get('path') if isinstance(item, dict) else None
            if relative in ('manifest.json', REPORT_FILE):
                continue
            if not isinstance(relative, str) or not relative or '..' in Path(relative).parts:
                raise ValueError('published_artifact_path_changed')
            copied = target / relative
            if (not copied.is_file() or copied.is_symlink() or
                    copied.stat().st_size != item.get('bytes') or
                    _hash_file(copied) != item.get('sha256')):
                raise ValueError('published_artifact_changed')
        pending = {'archive': archive, 'root': root, 'summary': summary,
                   'files': entry['files'], 'approval': entry.get('approval'),
                   'exportTarget': entry['exportTarget']}
        if entry.get('exportRoot'):
            pending['exportRoot'] = entry['exportRoot']
        if not isinstance(pending['approval'], dict):
            raise ValueError('published_approval_missing')
        validated_approval(pending)
        return pending, target, published_manifest

    def validate_published(self, entry, archive):
        """Public read-only validation used by explicit metadata repair."""
        snapshot = self._snapshot or archive.root
        from offline_direct import conversations
        known_chats = {group['id']: group for group in conversations(archive, self.conversation_mode)}
        snapshot_time = archive.adapter.get_source_info()['createdAt']
        source_fingerprint = hashlib.sha256(
            str(Path(archive.media_config['sourceRoot']).resolve()).encode()).hexdigest()
        return self._validate_published(entry, archive, snapshot, known_chats,
                                       snapshot_time, source_fingerprint)

    def restore(self, config):
        if not self.path.exists():
            return None
        try:
            path = self._managed(self.path.relative_to(self.directory).as_posix(), 'reviews')
            if path.stat().st_size > MAX_STATE_BYTES:
                raise ValueError('progress_too_large')
            value = json.loads(path.read_text(encoding='utf8'))
            if (not isinstance(value, dict) or type(value.get('version')) is not int or
                    value['version'] not in ((1, 2) if self.conversation_mode == 'group' else (1,)) or value.get('client') != self.client or
                    value.get('conversationMode', 'group') != self.conversation_mode or
                    not same_account_source(value.get('sourceRoot'), config.get('sourceRoot'), self.client)):
                raise ValueError('progress_source_changed')
            snapshot = self._managed(value['snapshot'], 'snapshots')
            if inventory(snapshot) != value['snapshotFiles']:
                raise ValueError('progress_snapshot_changed')
            metadata = json.loads((snapshot / 'manifest.json').read_text(encoding='utf8'))
            if not same_account_source(metadata.get('sourceRoot'), config['sourceRoot'], self.client):
                raise ValueError('progress_snapshot_source_changed')
            factory = LocalArchive
            if self.client == 'wecom':
                from wecom_archive import WeComArchive
                factory = WeComArchive
            archive = factory(snapshot, config.get('selfId', ''), config)
            if archive.state['self_id'] != value['selfId']:
                raise ValueError('progress_identity_changed')
            snapshot_time = archive.adapter.get_source_info()['createdAt']
            from offline_direct import conversations
            known_chats = {group['id']: group for group in conversations(archive, self.conversation_mode)}
            source_fingerprint = hashlib.sha256(str(Path(value['sourceRoot']).resolve()).encode()).hexdigest()
            entries = value['reviews']
            if not isinstance(entries, list) or len(entries) > 1000:
                raise ValueError('invalid_progress_entries')
            restored, published, exports, rejected, exported = {}, {}, {}, 0, 0
            batch_roots = set()
            for entry in entries:
                try:
                    summary = entry['summary']
                    root = self._managed(entry['root'], 'reviews')
                    if (root.name != summary['reviewId'] or summary['client'] != self.client or
                            summary.get('conversationMode', 'group') != self.conversation_mode or
                            summary['selfId'] != archive.state['self_id'] or
                            summary['snapshotTime'] != snapshot_time or summary['sourceFingerprint'] != source_fingerprint or
                            summary['chat']['id'] not in known_chats or
                            inventory(root) != entry['files']):
                        raise ValueError('progress_preview_changed')
                    report = json.loads((root / 'manifest.json').read_text(encoding='utf8'))
                    if (report['chat'] != summary['chat'] or report['exportedRows'] != summary['exportedRows'] or
                            any(report.get(key) != summary.get(key) for key in ('startDate','endDate','copyMediaRequested')) or
                            not same_account_source(report['sourceRoot'], snapshot, self.client)):
                        raise ValueError('progress_preview_identity_changed')
                    if entry.get('exportTarget'):
                        chat = summary['chat']['id']
                        if chat in restored or chat in published:
                            raise ValueError('duplicate_progress_group')
                        if value['version'] == 1 and entry.get('exportRoot'):
                            raise ValueError('external_export_requires_v2')
                        target = self.export_path(entry)
                        if not target.exists():
                            # _publish_pending checkpoints the intended target before
                            # publishing. A failed copy is still an approved pending
                            # review and must remain retryable after restart.
                            pending = {'archive': archive, 'root': root,
                                       'summary': summary, 'files': entry['files'],
                                       'approval': entry.get('approval'),
                                       'exportTarget': entry['exportTarget']}
                            if entry.get('exportRoot'):
                                pending['exportRoot'] = entry['exportRoot']
                            if not isinstance(pending['approval'], dict):
                                raise ValueError('pending_approval_missing')
                            validated_approval(pending)
                            restored[chat] = pending
                            batch_roots.add(target.parent)
                            continue
                        pending, target, _ = self._validate_published(
                            entry, archive, snapshot, known_chats, snapshot_time,
                            source_fingerprint)
                        published[chat] = pending
                        exports[chat] = target
                        batch_roots.add(target.parent)
                        exported += 1
                        continue
                    pending = {'archive': archive, 'root': root, 'summary': summary, 'files': entry['files']}
                    if entry.get('approval'):
                        pending['approval'] = entry['approval']
                        try:
                            approval = pending['approval']
                            if (not isinstance(approval, dict) or
                                    any(approval.get(key) != item for key, item in summary.items()) or
                                    approval.get('confirmationBasis') not in ('explicit_review_checklist','explicit_batch_review_checklist') or
                                    not isinstance(approval.get('note'), str) or len(approval['note']) > 4000 or
                                    datetime.fromisoformat(approval['confirmedAt']).tzinfo is None):
                                raise ArchiveError('invalid_saved_approval')
                            validated_approval(pending)
                        except (ValueError, KeyError, TypeError):
                            pending.pop('approval', None)
                            rejected += 1
                    chat = summary['chat']['id']
                    if chat in restored:
                        raise ValueError('duplicate_progress_group')
                    restored[chat] = pending
                except (OSError, ValueError, KeyError, TypeError, ArchiveError):
                    rejected += 1
            ui = value.get('ui', {})
            ui = ui if isinstance(ui, dict) else {}
            selected = ui.get('selected', [])
            available = set(restored) | set(published)
            ui = {'selected': [chat for chat in selected if isinstance(chat, str) and chat in known_chats] if isinstance(selected, list) else [],
                  'active': ui.get('active') if isinstance(ui.get('active'), str) else None}
            if ui['active'] not in available:
                ui['active'] = next(iter(available), None)
            if not restored and not published and not (self.conversation_mode == 'group' and ui['selected']):
                if rejected:
                    raise ValueError('no_valid_progress')
                return None
            self._snapshot, self._files = snapshot, value['snapshotFiles']
            return {'archive': archive, 'reviews': restored, 'published': published,
                    'exports': exports, 'batchRoots': sorted(batch_roots, key=lambda p: p.name), 'ui': ui,
                    'rejected': rejected, 'exported': exported}
        except (OSError, ValueError, KeyError, TypeError):
            raise ArchiveError('上次复核进度未通过来源或文件校验；已保留缓存，请重新解析。') from None
