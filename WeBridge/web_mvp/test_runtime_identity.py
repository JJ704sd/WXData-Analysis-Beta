"""Source-isolation checks through the CLI; never load native or private data."""
import json
import os
import shutil
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / 'scripts/check_runtime_identity.py'


class RuntimeIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def write(self, relative, source):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding='utf-8')

    def run_check(self, root=None):
        result = subprocess.run([sys.executable, str(CHECKER), '--root', str(root or self.root)],
                                capture_output=True, text=True, encoding='utf-8', timeout=10)
        self.assertTrue(result.stdout, result.stderr)
        return result.returncode, json.loads(result.stdout)

    def test_runtime_sources_have_no_embedded_account_identity(self):
        code, report = self.run_check(ROOT)
        self.assertEqual(code, 0, report)
        self.assertTrue(report['ok'])
        self.assertGreater(report['scannedFiles'], 0)

    def test_previous_smoke_account_guard_is_blocked_without_echoing_identity(self):
        identity = 'wxid_synthetic_self'
        self.write('scripts/bridge.py', "if config['selfId'] != " + repr(identity) + ":\n    raise ValueError()\n")
        code, report = self.run_check()
        self.assertEqual(code, 1)
        self.assertFalse(report['ok'])
        self.assertEqual(report['issues'][0]['path'], 'scripts/bridge.py')
        self.assertEqual(report['issues'][0]['line'], 1)
        self.assertNotIn(identity, json.dumps(report))

    def test_non_fixture_account_and_group_constants_are_also_blocked(self):
        self.write('web_mvp/runtime.py', "SELF = 'wxid_A1b2C3'\n")
        self.write('web_mvp/static/runtime.js', "const target = '12345678@chatroom';\n")
        self.write('scripts/runtime.ps1', "$account = 'fixture-account'\n")
        code, report = self.run_check()
        self.assertEqual(code, 1)
        self.assertEqual({issue['path'] for issue in report['issues']}, {
            'web_mvp/runtime.py', 'web_mvp/static/runtime.js', 'scripts/runtime.ps1'})

    def test_config_loading_patterns_and_explicit_test_demo_boundaries_are_allowed(self):
        self.write('scripts/bridge.py', "self_id = config['selfId']\npattern = r'^wxid_[A-Za-z0-9_]+$'\ntarget = 'filehelper'\n")
        self.write('web_mvp/test_fixture.py', "SELF = 'wxid_test_account'\n")
        self.write('web_mvp/diagnostics/check_fixture.js', "const selfId = 'wxid_fixture_account';\n")
        self.write('web_mvp/demo_backend.py', "SELF = 'synthetic-account'\n")
        self.write('scripts/__pycache__/unused.py', "SELF = 'wxid_unused_account'\n")
        self.write('.runtime/config.py', "SELF = 'wxid_private_account'\n")
        code, report = self.run_check()
        self.assertEqual(code, 0, report)
        self.assertEqual(report['scannedFiles'], 1)

    @unittest.skipUnless(os.name == 'nt', 'Windows launcher integration')
    def test_launcher_blocks_contaminated_source_during_preflight(self):
        self.write('scripts/bridge.py', "SELF = 'wxid_synthetic_self'\n")
        shutil.copyfile(CHECKER, self.root / 'scripts/check_runtime_identity.py')
        launcher = self.root / 'scripts/start_web_mvp.ps1'
        shutil.copyfile(ROOT / 'scripts/start_web_mvp.ps1', launcher)
        result = subprocess.run([
            'powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(launcher),
            '-Mode', 'Database', '-CheckOnly', '-NoBrowser', '-Python', sys.executable,
            '-Port', '58997', '-RuntimeDir', str(self.root / 'runtime')],
            capture_output=True, text=True, encoding='utf-8', timeout=20)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn('Runtime source identity check failed', result.stdout)
        self.assertFalse((self.root / 'runtime/server.pid').exists())

    def test_comments_and_docstrings_do_not_become_runtime_identity(self):
        self.write('scripts/bridge.py', '\"\"\"Example wxid_documented_account.\"\"\"\n# wxid_commented_account\nself_id = config["selfId"]\n')
        self.write('web_mvp/static/runtime.js', '// wxid_comment_account\nconst selfId = config.selfId;\n')
        code, report = self.run_check()
        self.assertEqual(code, 0, report)


if __name__ == '__main__':
    unittest.main()
