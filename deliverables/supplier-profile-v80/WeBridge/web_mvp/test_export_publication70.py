"""Reproduce Windows directory publication failures with real open file handles."""
import errno
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from offline_chat import ArchiveError
from offline_desktop import DesktopSession
from offline_review import REVIEW_CHECKS
from test_wecom_archive import fixture


@unittest.skipUnless(os.name == 'nt', 'Windows directory sharing semantics')
class ExportPublication70Tests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='synthetic-publication70-')
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()

    def session(self, label):
        base = self.base / label
        base.mkdir()
        source = base / 'source'
        fixture(source)
        session = DesktopSession(base / 'profile', client='wecom')
        session.open_source({'sourceRoot': str(source), 'includeMedia': False})
        return session

    def reviewed(self, label):
        session = self.session(label)
        prepared = session.prepare_review('R:synthetic', copy_media=False, load_view=False)
        session.approve_review(prepared['review']['reviewId'],
                               {key: True for key in REVIEW_CHECKS}, 'synthetic review', chat='R:synthetic')
        return session

    def test_transient_real_winerror5_recovers_preview_and_review_publication(self):
        original = Path.rename
        for phase, prefix in [('preview', '.offline-export-'), ('review', '.review-export-')]:
            with self.subTest(phase=phase):
                session = self.session(phase) if phase == 'preview' else self.reviewed(phase)
                observed = []

                def briefly_locked(stage, target):
                    if stage.name.startswith(prefix) and not observed:
                        with (stage / 'messages.json').open('rb'):
                            try:
                                return original(stage, target)
                            except PermissionError as error:
                                observed.append(error.winerror)
                                raise
                    return original(stage, target)

                with patch.object(Path, 'rename', briefly_locked):
                    if phase == 'preview':
                        result = session.prepare_review('R:synthetic', copy_media=False, load_view=False)
                        self.assertEqual(result['state'], 'awaiting_review')
                        self.assertTrue(Path(result['view']['viewRoot']).is_dir())
                    else:
                        result = session.export_reviewed(['R:synthetic'])
                        self.assertEqual(result['state'], 'completed')
                        self.assertEqual(len(result['exports']), 1)
                self.assertEqual(observed, [5])

    def test_persistent_lock_is_bounded_and_review_can_be_retried_without_reparse(self):
        session = self.reviewed('persistent')
        pending = session._reviews['R:synthetic']
        before = (pending['root'] / 'messages.json').read_bytes()
        original = Path.rename
        observed = []

        def still_locked(stage, target):
            if stage.name.startswith('.review-export-'):
                with (stage / 'messages.json').open('rb'):
                    try:
                        return original(stage, target)
                    except PermissionError as error:
                        observed.append(error.winerror)
                        raise
            return original(stage, target)

        with patch.object(Path, 'rename', still_locked), patch('offline_chat.time.sleep') as sleep:
            result = session.export_reviewed(['R:synthetic'])
        self.assertEqual(result['state'], 'partial')
        self.assertIn('重命名', result['failed'][0]['error'])
        self.assertTrue(observed and set(observed) == {5})
        self.assertGreater(sum(call.args[0] for call in sleep.call_args_list), 0)
        self.assertLessEqual(sum(call.args[0] for call in sleep.call_args_list), 3)
        self.assertEqual((pending['root'] / 'messages.json').read_bytes(), before)
        self.assertEqual(session.review_status('R:synthetic'), '已复核')
        with patch.object(session, 'prepare_review', side_effect=AssertionError('must reuse review')):
            retried = session.export_reviewed(['R:synthetic'])
        self.assertEqual(retried['state'], 'completed')
        self.assertEqual(len(retried['exports']), 1)

    def test_destination_created_during_lock_is_not_overwritten(self):
        session = self.reviewed('collision')
        original = Path.rename
        occupied = []

        def claimed_during_lock(stage, target):
            if stage.name.startswith('.review-export-') and not occupied:
                target = Path(target)
                with (stage / 'messages.json').open('rb'):
                    try:
                        return original(stage, target)
                    except PermissionError:
                        target.mkdir()
                        (target / 'owner.txt').write_text('existing owner', encoding='utf-8')
                        occupied.append(target)
                        raise
            return original(stage, target)

        with patch.object(Path, 'rename', claimed_during_lock), patch('offline_chat.time.sleep'):
            result = session.export_reviewed(['R:synthetic'])
        self.assertEqual(result['state'], 'partial')
        self.assertEqual((occupied[0] / 'owner.txt').read_text(encoding='utf-8'), 'existing owner')
        self.assertEqual(sorted(p.name for p in occupied[0].iterdir()), ['owner.txt'])

    def test_persistent_preview_lock_keeps_previous_review_and_native_error_code(self):
        session = self.reviewed('previous')
        previous = session._reviews['R:synthetic']
        original = Path.rename

        def locked_preview(stage, target):
            if stage.name.startswith('.offline-export-'):
                with (stage / 'messages.json').open('rb'):
                    return original(stage, target)
            return original(stage, target)

        with patch.object(Path, 'rename', locked_preview), patch('offline_chat.time.sleep'):
            with self.assertRaisesRegex(ArchiveError, '重命名') as failure:
                session.prepare_review('R:synthetic', copy_media=False, load_view=False)
        self.assertEqual(failure.exception.winerror, 5)
        self.assertIs(session._reviews['R:synthetic'], previous)
        self.assertTrue(previous['root'].is_dir())
        self.assertEqual(session.review_status('R:synthetic'), '已复核')

    def test_unrelated_disk_error_is_not_retried(self):
        session = self.session('disk')
        original = Path.rename

        def disk_error(stage, target):
            if stage.name.startswith('.offline-export-'):
                raise OSError(errno.ENOSPC, 'synthetic full disk')
            return original(stage, target)

        with patch.object(Path, 'rename', disk_error), patch('offline_chat.time.sleep') as sleep:
            with self.assertRaises(OSError) as failure:
                session.prepare_review('R:synthetic', copy_media=False, load_view=False)
        self.assertEqual(failure.exception.errno, errno.ENOSPC)
        sleep.assert_not_called()
