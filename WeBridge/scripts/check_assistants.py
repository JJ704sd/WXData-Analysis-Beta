"""Run the bounded synthetic acceptance suite for archive and daily-report products."""
import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys
import time
import unittest

PROJECT = Path(__file__).resolve().parents[1]
for directory in (PROJECT, PROJECT / 'web_mvp', PROJECT / 'scripts', PROJECT.parent / 'poc'):
    sys.path.insert(0, str(directory))
dependencies = PROJECT / '.runtime/offline-build-deps'
if dependencies.is_dir():
    sys.path.insert(0, str(dependencies))

TARGETS = (
    'test_four_release71', 'test_group_quote_schema51', 'test_group_unicode52',
    'test_export_resources54', 'test_export_publication70', 'test_wecom_discovery68',
    'test_group_scrolling68', 'test_group_ui42.GroupCapabilities42Tests',
    'test_analysis_coverage55', 'test_document_coverage55', 'test_private_search55',
    'test_four_exports66', 'test_private_chat_distribution', 'test_private_chat_integration',
    'test_private_evidence_integrity', 'test_runtime_identity',
    'test_windows_hook_smoke.SourceBindingTests',
    'test_daily_reports',
    'test_daily_distribution.StandaloneDistributionTests',
    'test_daily_ui',
)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', type=Path)
    parser.add_argument('targets', nargs='*', help='Optional narrower unittest targets')
    args = parser.parse_args(argv)
    targets = args.targets or TARGETS
    suite = unittest.defaultTestLoader.loadTestsFromNames(targets)
    started = time.monotonic()
    result = unittest.TextTestRunner(verbosity=2, buffer=True).run(suite)
    native_run = (sys.platform == 'win32' and bool(os.environ.get('DAILY_DELIVERY_ROOT')) and
                  any(target == 'test_daily_distribution' or target.startswith('test_daily_distribution.WindowsDeliveryTests')
                      for target in targets))
    report = {
        'schemaVersion': 1, 'date': datetime.now(timezone(timedelta(hours=8))).date().isoformat(), 'targets': targets,
        'testsRun': result.testsRun, 'failures': len(result.failures), 'errors': len(result.errors),
        'skipped': len(result.skipped), 'seconds': round(time.monotonic() - started, 2),
        'ok': result.wasSuccessful(), 'usesSyntheticData': True,
        'realAccountValidation': False, 'compiledBinaryValidation': native_run and result.wasSuccessful(),
        'python': sys.version.split()[0], 'platform': sys.platform,
        'failedTests': [test.id() for test, _ in (*result.failures, *result.errors)],
    }
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False))
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    raise SystemExit(main())
