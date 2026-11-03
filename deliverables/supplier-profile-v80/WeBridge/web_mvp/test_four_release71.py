"""Version/package and Windows publication integration for all four products."""
import contextlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from chat_release import GROUP_RELEASE, GROUP_VERSION, PRIVATE_VERSION, release_matrix
import local_chat_distribution
import package_local_chat_skill
from package_four_chat import verify_package
from offline_desktop import DesktopSession
from offline_review import REVIEW_CHECKS
from test_four_exports53 import CASES, source_fixture


ROOT = Path(__file__).resolve().parents[2]


class FourRelease71Tests(unittest.TestCase):
    def test_four_source_launchers_report_current_versions_without_creating_profiles(self):
        with tempfile.TemporaryDirectory() as folder:
            for client, mode in CASES:
                with self.subTest(client=client, mode=mode):
                    directory = Path(folder) / (client + '-' + mode)
                    result = subprocess.run([
                        'powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(ROOT / 'start-assistant.ps1'),
                        '-Client', client, '-ConversationMode', mode, '-Python', sys.executable,
                        '-DataDirectory', str(directory), '-Capabilities',
                    ], capture_output=True, text=True, encoding='utf-8', timeout=30)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    value = json.loads(result.stdout)
                    self.assertEqual(value['conversationMode'], mode)
                    self.assertEqual(value['version'], PRIVATE_VERSION if mode == 'direct' else GROUP_VERSION)
                    self.assertFalse(directory.exists())
        self.assertEqual(len(release_matrix()['products']), 4)

    def test_group_packager_refuses_old_or_private_exe_before_writing_output(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            desktop = root / 'desktop'
            (desktop / 'licenses').mkdir(parents=True)
            (desktop / 'local-chat-desktop.exe').write_bytes(b'synthetic-exe')
            for version, mode in (('1.51.2', 'group'), (PRIVATE_VERSION, 'direct')):
                with self.subTest(version=version, mode=mode):
                    def probe(command, **kwargs):
                        Path(command[command.index('--result-json') + 1]).write_text(json.dumps({
                            'schemaVersion': 1, 'role': 'desktop', 'version': version,
                            'conversationMode': mode, 'privateAnalysis': mode == 'direct', 'release': 'v68',
                        }), encoding='utf-8')
                        return subprocess.CompletedProcess(command, 0, b'', b'')
                    output = root / (mode + '-output')
                    arguments = ['packager', '--desktop-package', str(desktop), '--output', str(output), '--client', 'wechat']
                    with patch.object(sys, 'argv', arguments), patch.object(package_local_chat_skill, 'verified_desktop_inputs', return_value=[]), \
                            patch.object(local_chat_distribution.subprocess, 'run', side_effect=probe):
                        with self.assertRaisesRegex(ValueError, 'requested group release'):
                            package_local_chat_skill.main()
                    self.assertFalse(output.exists())
                    self.assertFalse(Path(str(output) + '.zip').exists())

    def test_current_group_exe_reports_the_requested_release(self):
        value = {'schemaVersion': 1, 'role': 'desktop', 'version': GROUP_VERSION,
                 'conversationMode': 'group', 'privateAnalysis': False, 'release': GROUP_RELEASE}

        def probe(command, **kwargs):
            Path(command[command.index('--result-json') + 1]).write_text(json.dumps(value), encoding='utf-8')
            return subprocess.CompletedProcess(command, 0, b'', b'')
        with patch.object(local_chat_distribution.subprocess, 'run', side_effect=probe):
            self.assertEqual(local_chat_distribution.verify_group_desktop(Path('new-desktop.exe'), GROUP_VERSION), value)

    def test_current_group_packages_keep_both_client_bindings_and_valid_digests(self):
        value = {'schemaVersion': 1, 'role': 'desktop', 'version': GROUP_VERSION,
                 'conversationMode': 'group', 'privateAnalysis': False, 'release': GROUP_RELEASE}

        def probe(command, **kwargs):
            Path(command[command.index('--result-json') + 1]).write_text(json.dumps(value), encoding='utf-8')
            return subprocess.CompletedProcess(command, 0, b'', b'')

        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            desktop = base / 'desktop'
            (desktop / 'licenses').mkdir(parents=True)
            (desktop / 'local-chat-desktop.exe').write_bytes(b'synthetic-current-exe')
            (desktop / 'licenses/LICENSE.txt').write_text('synthetic license', encoding='utf-8')
            local_chat_distribution.complete_metadata(desktop, {'interface': 'desktop'})
            for client in ('wechat', 'wecom'):
                with self.subTest(client=client):
                    output = base / (client + '-' + GROUP_VERSION + '-Windows-x64')
                    arguments = ['packager', '--desktop-package', str(desktop), '--output', str(output),
                                 '--client', client, '--shareable']
                    with patch.object(sys, 'argv', arguments), contextlib.redirect_stdout(io.StringIO()), \
                            patch.object(local_chat_distribution.subprocess, 'run', side_effect=probe):
                        package_local_chat_skill.main()
                    checked = verify_package(Path(str(output) + '.zip'), client=client, mode='group', version=GROUP_VERSION)
                    self.assertEqual(checked['client'], client)
                    runtime = json.loads((output / 'runtime.json').read_text(encoding='utf-8'))
                    self.assertEqual(runtime['release'], GROUP_RELEASE)
                    for relative in ('SKILL.md', 'references/attachment-content.md', 'references/input-and-errors.md'):
                        document = output / relative
                        for target in re.findall(r'\]\(([^)]+)\)', document.read_text(encoding='utf-8')):
                            if not re.match(r'[A-Za-z]+:', target) and not target.startswith('#'):
                                self.assertTrue((document.parent / target.split('#')[0]).exists(), (relative, target))

    @unittest.skipUnless(os.name == 'nt', 'Windows directory sharing semantics')
    def test_four_products_preserve_review_during_real_windows_lock_and_retry_export(self):
        original = Path.rename
        for client, mode in CASES:
            with self.subTest(client=client, mode=mode), tempfile.TemporaryDirectory() as folder:
                base = Path(folder)
                config, chat, _ = source_fixture(base, client, mode, count=0)
                session = DesktopSession(base / 'profile', client=client, conversation_mode=mode)
                session.open_source(config)
                prepared = session.prepare_review(chat, copy_media=False, load_view=False)
                session.approve_review(prepared['review']['reviewId'],
                                       {key: True for key in REVIEW_CHECKS}, 'synthetic review', chat=chat)
                pending = session._reviews[chat]
                before = (pending['root'] / 'messages.json').read_bytes()

                def locked(stage, target):
                    if stage.name.startswith('.review-export-'):
                        with (stage / 'messages.json').open('rb'):
                            return original(stage, target)
                    return original(stage, target)

                with patch.object(Path, 'rename', locked), patch('offline_chat.time.sleep') as sleep:
                    blocked = session.export_reviewed([chat])
                self.assertEqual(blocked['state'], 'partial')
                self.assertIn('重命名', blocked['failed'][0]['error'])
                self.assertLessEqual(sum(call.args[0] for call in sleep.call_args_list), 3)
                self.assertEqual(session.review_status(chat), '已复核')
                self.assertEqual((pending['root'] / 'messages.json').read_bytes(), before)
                with patch.object(session, 'prepare_review', side_effect=AssertionError('must reuse review')):
                    retried = session.export_reviewed([chat])
                self.assertEqual(retried['state'], 'completed')
                exported = Path(retried['exports'][0]['output'])
                self.assertEqual(json.loads((exported / 'review.json').read_text(encoding='utf-8'))['status'], 'confirmed')


if __name__ == '__main__':
    unittest.main()
