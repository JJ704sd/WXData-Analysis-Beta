"""Standalone and Windows-delivery checks using only synthetic reviewed exports."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile

from build_daily_standalone import PROJECT, standalone_source
from chat_release import DAILY_REPORT_VERSION
from offline_daily import build_daily_report
from package_daily_reports import workbuddy_skill
from test_daily_reports import DAY, formal_export, message


def clean_environment():
    result = {key: value for key, value in os.environ.items() if key.upper() not in {'PATH', 'PYTHONPATH', 'PYTHONHOME', 'PSMODULEPATH', 'TCL_LIBRARY', 'TK_LIBRARY'}}
    if os.name == 'nt':
        windows = Path(os.environ.get('SystemRoot', 'C:/Windows'))
        result['PATH'] = str(windows / 'System32') + ';' + str(windows)
    return result


def run_process(arguments, *, cwd, environment=None):
    result = subprocess.run(list(map(str, arguments)), cwd=cwd, env=environment or clean_environment(),
                            input=b'', capture_output=True, timeout=60,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    for field in ('stdout', 'stderr'):
        value = getattr(result, field)
        try:
            decoded = value.decode('utf-8')
        except UnicodeDecodeError:
            # Windows PowerShell can emit local-encoded policy errors before
            # a script starts. Preserve that diagnostic instead of a pipe error.
            decoded = value.decode('gb18030')
        setattr(result, field, decoded)
    return result


def source_pair(base, client):
    direct = formal_export(base, client + '-私聊', [
        message('request', '请确认报价', time='2026-10-26T18:00:00+08:00'),
        message('reply', '稍后回复', own=True, time='2026-10-27T10:00:00+08:00')], client=client)
    group = formal_export(base, client + '-群聊', [
        message('other', '请确认他人的订单', mentions={'ids': ['synthetic-other']}),
        message('self', '请尽快处理发货安排', mentions={'ids': ['synthetic-owner']})], client=client, mode='group')
    return direct, group


def quoted_requests(base, client):
    return formal_export(base, client + '-引用回复', [
        message(101, '请确认报价'),
        message(102, '请安排发货', time='2026-10-27T09:01:00+08:00'),
        message('reply', '已处理', own=True, kind='quote', time='2026-10-27T09:02:00+08:00',
                quote={'sourceMessageId': 101, 'message': {'id': 101, 'text': '请确认报价'}}),
        message('same-second', '请确认方案', time='2026-10-27T10:00:00+08:00'),
        message('same-reply', '已处理', own=True, kind='quote', time='2026-10-27T10:00:00+08:00',
                quote={'sourceMessageId': 'same-second'})], client=client)


class StandaloneDistributionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='synthetic-daily-standalone-')
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)

    def script(self, client):
        path = self.base / (client + '_daily_report.py')
        path.write_text(standalone_source(client), encoding='utf-8')
        return path

    def test_both_single_files_run_isolated_without_site_packages_or_repository(self):
        for client in ('wechat', 'wecom'):
            with self.subTest(client=client):
                result = run_process([sys.executable, '-I', '-S', self.script(client), '--capabilities'], cwd=self.base)
                self.assertEqual(result.returncode, 0, result.stderr)
                capabilities = json.loads(result.stdout)
                self.assertEqual(capabilities['version'], DAILY_REPORT_VERSION)
                self.assertEqual(capabilities['client'], client)
                self.assertTrue(capabilities['standalone'])
                self.assertFalse(capabilities['requiresRepository'])
                self.assertTrue(capabilities['requiresPythonInstallation'])
                self.assertEqual(sorted(path.name for path in self.base.iterdir()),
                                 ['wechat_daily_report.py'] if client == 'wechat' else ['wechat_daily_report.py', 'wecom_daily_report.py'])

    def test_single_file_reports_preserve_module_results_and_diagnostics(self):
        for client in ('wechat', 'wecom'):
            with self.subTest(client=client):
                roots = source_pair(self.base, client)
                output = self.base / (client + '-单文件结果')
                args = [sys.executable, '-I', '-S', self.script(client), '--date', DAY, '--output', output]
                for root in roots:
                    args += ['--export-root', root]
                result = run_process(args, cwd=self.base)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                baseline = build_daily_report(roots, client=client, day=DAY, output=self.base / (client + '-module'), version=DAILY_REPORT_VERSION)
                generated = json.loads((output / 'report.json').read_text(encoding='utf-8'))
                expected = json.loads(Path(baseline['jsonFile']).read_text(encoding='utf-8'))
                generated.pop('createdAt'); expected.pop('createdAt')
                self.assertEqual(generated, expected)
                self.assertEqual((output / 'evidence.jsonl').read_bytes(), Path(baseline['evidenceFile']).read_bytes())
                self.assertEqual(generated['overview']['personalFollowups'], 2)
                self.assertEqual({item['state'] for item in generated['followups']}, {'deferred', 'needs_response'})
                self.assertTrue((output / 'diagnostics.jsonl').is_file())

    def test_guard_failures_do_not_publish_and_noninteractive_input_does_not_hang(self):
        script = self.script('wechat')
        wrong = formal_export(self.base, 'wrong', [message('m1', '请确认报价')], client='wecom')
        output = self.base / 'rejected'
        result = run_process([sys.executable, '-I', '-S', script, '--export-root', wrong, '--date', DAY, '--output', output], cwd=self.base)
        self.assertEqual(result.returncode, 2)
        self.assertFalse(output.exists())
        result = run_process([sys.executable, '-I', '-S', script, '--interactive'], cwd=self.base)
        self.assertEqual(result.returncode, 2)
        self.assertIn('控制台', json.loads(result.stdout)['error'])

    def test_isolated_single_files_link_numeric_and_same_second_quotes(self):
        for client in ('wechat', 'wecom'):
            with self.subTest(client=client):
                root = quoted_requests(self.base, client)
                output = self.base / (client + '-引用结果')
                result = run_process([sys.executable, '-I', '-S', self.script(client), '--date', DAY,
                                      '--export-root', root, '--output', output], cwd=self.base)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                report = json.loads((output / 'report.json').read_text(encoding='utf-8'))
                states = {item['text']: item['state'] for item in report['followups']}
                self.assertEqual(states, {'请确认报价': 'completion_signal', '请安排发货': 'needs_response', '请确认方案': 'completion_signal'})

    def test_client_binding_and_workbuddy_frontmatter_are_explicit(self):
        with self.assertRaises(ValueError):
            standalone_source('automatic-client')
        for client in ('wechat', 'wecom'):
            source = (PROJECT / 'skills' / (client + '-daily-report') / 'SKILL.md').read_text(encoding='utf-8')
            header = workbuddy_skill(source).split('\n---\n', 1)[0]
            self.assertNotIn('metadata:', header)
            for field in ('name', 'description', 'display_name', 'display_name_en', 'description_zh', 'description_en', 'version', 'author'):
                self.assertRegex(header, r'(?m)^' + field + ': .+')
            self.assertIn('version: "' + DAILY_REPORT_VERSION + '"', header)
            self.assertIn('name: ' + client + '-daily-report', header)

    def test_tracked_single_files_match_the_maintained_generation(self):
        for client in ('wechat', 'wecom'):
            path = PROJECT.parent / 'standalone' / (client + '_daily_report.py')
            self.assertEqual(path.read_text(encoding='utf-8'), standalone_source(client))

    def test_generation_is_identical_with_windows_or_lf_source_newlines(self):
        copied = self.base / 'newline-project'
        paths = ['scripts/chat_release.py', 'scripts/daily_report.py', 'web_mvp/offline_chat.py',
                 'web_mvp/database_snapshot.py', 'web_mvp/offline_review.py', 'web_mvp/offline_images.py',
                 'web_mvp/offline_reporting.py', 'web_mvp/offline_content_status.py',
                 'web_mvp/offline_daily.py', 'web_mvp/offline_daily_html.py',
                 'web_mvp/offline_daily_ui.py', 'web_mvp/offline_theme.py', 'web_mvp/offline_calendar.py']
        for relative in paths:
            target = copied / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text((PROJECT / relative).read_text(encoding='utf-8'), encoding='utf-8', newline='\r\n')
        for client in ('wechat', 'wecom'):
            self.assertEqual(standalone_source(client, copied), standalone_source(client))


@unittest.skipUnless(os.name == 'nt' and os.environ.get('DAILY_DELIVERY_ROOT'), 'Set DAILY_DELIVERY_ROOT to verify built Windows packages')
class WindowsDeliveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.delivery = Path(os.environ['DAILY_DELIVERY_ROOT']).resolve()
        cls.matrix = json.loads((cls.delivery / 'release-matrix.json').read_text(encoding='utf-8'))
        cls.temporary = tempfile.TemporaryDirectory(prefix='synthetic-daily-native-')
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.base = Path(cls.temporary.name) / '独立目录 with spaces'
        cls.base.mkdir()
        cls.packages = {}
        for product in cls.matrix['products']:
            target = cls.base / product['directory']
            with zipfile.ZipFile(cls.delivery / product['archive']) as bundle:
                bundle.extractall(target)
            cls.packages[product['client'], product['delivery']] = target

    def test_all_four_archives_have_complete_hashes_licenses_and_skill_roots(self):
        self.assertEqual(len(self.matrix['products']), 4)
        for product in self.matrix['products']:
            archive = self.delivery / product['archive']
            self.assertEqual(hashlib.sha256(archive.read_bytes()).hexdigest(), product['sha256'])
            with zipfile.ZipFile(archive) as bundle:
                self.assertIsNone(bundle.testzip())
                metadata = json.loads(bundle.read('package.json'))
                for item in metadata['files']:
                    value = bundle.read(item['path'])
                    self.assertEqual(len(value), item['bytes'])
                    self.assertEqual(hashlib.sha256(value).hexdigest(), item['sha256'])
                for license_name in ('Python-LICENSE.txt', 'PyInstaller-COPYING.txt', 'Tcl-license.terms', 'Tk-license.terms', 'SOURCE-NOTICE.md'):
                    self.assertGreater(len(bundle.read('licenses/' + license_name)), 100)
                if product['delivery'] == 'skill':
                    self.assertIn('SKILL.md', bundle.namelist())
                    self.assertIn('scripts/' + product['client'] + '_daily_report.py', bundle.namelist())
                    header = bundle.read('SKILL.md').decode('utf-8').split('\n---\n', 1)[0]
                    self.assertIn('display_name:', header)
                    self.assertNotIn('metadata:', header)
                for line in bundle.read('SHA256SUMS.txt').decode('utf-8').splitlines():
                    digest, relative = line.split('  ', 1)
                    self.assertEqual(hashlib.sha256(bundle.read(relative)).hexdigest(), digest)

    def test_all_packaged_desktops_initialize_without_external_python_or_tcl_paths(self):
        for (client, kind), directory in self.packages.items():
            with self.subTest(client=client, delivery=kind):
                runtime = json.loads((directory / 'runtime.json').read_text(encoding='utf-8'))
                binary = directory / runtime['desktopRelativePath']
                self.assertEqual(hashlib.sha256(binary.read_bytes()).hexdigest(), runtime['desktopSha256'])
                receipt = self.base / (client + '-' + kind + '-desktop.json')
                result = run_process([binary, '--check-desktop', receipt], cwd=self.base)
                self.assertEqual(result.returncode, 0, result.stderr)
                check = json.loads(receipt.read_text(encoding='utf-8'))
                self.assertEqual(check['client'], client)
                self.assertEqual(check['version'], DAILY_REPORT_VERSION)
                self.assertTrue(check['datePickerInitialized'])
                self.assertFalse(check['requiresPythonInstallation'])
                self.assertEqual(check['reportColumns'], ['conversation', 'sender', 'time', 'state', 'request'])
                self.assertIn('--desktop', (directory / 'Start.cmd').read_text(encoding='ascii'))
                duplicate = run_process([binary, '--check-desktop', receipt], cwd=self.base)
                self.assertNotEqual(duplicate.returncode, 0)
                self.assertEqual(check, json.loads(receipt.read_text(encoding='utf-8')))

    def test_both_portable_and_skill_executables_generate_without_python_on_path(self):
        for client in ('wechat', 'wecom'):
            with self.subTest(client=client):
                fixture = self.base / ('data-' + client)
                fixture.mkdir()
                roots = source_pair(fixture, client)
                portable = self.packages[client, 'portable']
                binary = portable / (client + '-daily-report.exe')
                result = run_process([binary, '--capabilities'], cwd=self.base)
                self.assertEqual(result.returncode, 0, result.stderr)
                capabilities = json.loads(result.stdout)
                self.assertEqual(capabilities['client'], client)
                self.assertFalse(capabilities['requiresPythonInstallation'])
                self.assertFalse(capabilities['requiresRepository'])
                self.assertTrue(capabilities['standalone'])
                native_output = self.base / ('EXE 结果-' + client)
                args = [binary, '--date', DAY, '--output', native_output]
                for root in roots:
                    args += ['--export-root', root]
                generated = run_process(args, cwd=self.base)
                self.assertEqual(generated.returncode, 0, generated.stdout + generated.stderr)
                self.assertEqual(json.loads(generated.stdout)['overview']['personalFollowups'], 2)
                skill_output = self.base / ('Skill 结果-' + client)
                args[0] = self.packages[client, 'skill'] / ('bin/' + client + '-daily-report.exe')
                args[4] = skill_output
                skill_result = run_process(args, cwd=self.base)
                self.assertEqual(skill_result.returncode, 0, skill_result.stdout + skill_result.stderr)
                self.assertTrue(json.loads(skill_result.stdout)['ok'])
                for filename in ('evidence.jsonl',):
                    self.assertEqual((native_output / filename).read_bytes(), (skill_output / filename).read_bytes())
                self.assertTrue((skill_output / 'diagnostics.jsonl').is_file())

    def test_native_client_guard_and_output_reuse_do_not_overwrite_results(self):
        root = formal_export(self.base, 'wrong-client', [message('request', '请确认报价')], client='wecom')
        binary = self.packages['wechat', 'skill'] / 'bin/wechat-daily-report.exe'
        output = self.base / 'client-rejected'
        result = run_process([binary, '--export-root', root, '--date', DAY, '--output', output], cwd=self.base)
        self.assertEqual(result.returncode, 2)
        self.assertFalse(output.exists())
        existing = self.base / 'existing'
        existing.mkdir()
        marker = existing / 'preserve.txt'
        marker.write_text('preserve original output', encoding='utf-8')
        own = formal_export(self.base, 'own-client', [message('request', '请确认报价')])
        result = run_process([binary, '--export-root', own, '--date', DAY, '--output', existing], cwd=self.base)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(marker.read_text(encoding='utf-8'), 'preserve original output')
        self.assertEqual([path.name for path in existing.iterdir()], ['preserve.txt'])

    def test_native_quote_regressions_in_both_portable_and_skill_executables(self):
        for client in ('wechat', 'wecom'):
            root = quoted_requests(self.base, client)
            for kind in ('portable', 'skill'):
                with self.subTest(client=client, delivery=kind):
                    directory = self.packages[client, kind]
                    runtime = json.loads((directory / 'runtime.json').read_text(encoding='utf-8'))
                    output = self.base / (client + '-' + kind + '-引用结果')
                    result = run_process([directory / runtime['exeRelativePath'], '--date', DAY,
                                          '--export-root', root, '--output', output], cwd=self.base)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    report = json.loads((output / 'report.json').read_text(encoding='utf-8'))
                    states = {item['text']: item['state'] for item in report['followups']}
                    self.assertEqual(states, {'请确认报价': 'completion_signal', '请安排发货': 'needs_response', '请确认方案': 'completion_signal'})


if __name__ == '__main__':
    unittest.main()
