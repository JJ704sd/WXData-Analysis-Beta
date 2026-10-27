"""Shared CLI for client-bound daily reports; never reads a live client."""
import argparse
import json
from pathlib import Path
import sys

PROJECT = Path(__file__).resolve().parents[1]
if not getattr(sys, 'frozen', False):
    sys.path.insert(0, str(PROJECT / 'web_mvp'))

from chat_release import DAILY_REPORT_VERSION
from offline_daily import DailyReportError, build_daily_report


def main(client, argv=None):
    label = '个微' if client == 'wechat' else '企微'
    parser = argparse.ArgumentParser(description=label + '每日概览、待跟进线索与本地 HTML 日报；读取已复核导出。')
    parser.add_argument('--export-root', action='append', type=Path, default=[], help='正式导出或批次目录；可重复')
    parser.add_argument('--date', help='日报日期 YYYY-MM-DD，使用北京时间')
    parser.add_argument('--output', type=Path, help='新的日报目录，不覆盖已有结果')
    parser.add_argument('--lookback-days', type=int, default=7, help='待跟进回看天数，含当天，默认 7')
    parser.add_argument('--my-name', action='append', default=[], help='用户明确提供的群内本人称呼，仅作点名线索；可重复')
    parser.add_argument('--capabilities', action='store_true', help='仅查询版本和能力，不读取材料')
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    if args.capabilities:
        print(json.dumps({'schemaVersion': 1, 'role': 'daily_report', 'client': client, 'version': DAILY_REPORT_VERSION,
                          'conversationModes': ['group', 'direct'], 'input': 'human_reviewed_exports',
                          'readsLiveClient': False, 'callsModelAPI': False}, ensure_ascii=False))
        return 0
    if not args.export_root or not args.date or not args.output:
        parser.error('需要 --export-root、--date 和 --output。')
    try:
        result = build_daily_report(args.export_root, client=client, day=args.date, output=args.output,
                                    version=DAILY_REPORT_VERSION, lookback_days=args.lookback_days, my_names=args.my_name)
    except (DailyReportError, OSError) as error:
        result = {'ok': False, 'client': client, 'error': str(error) if isinstance(error, DailyReportError) else '本地材料无法读取或日报无法写入，请检查路径、权限及空间。'}
        print(json.dumps(result, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0
