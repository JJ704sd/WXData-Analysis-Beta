"""Shared CLI for client-bound daily reports; never reads a live client."""
import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

PROJECT = Path(__file__).resolve().parents[1]
if not getattr(sys, 'frozen', False):
    sys.path.insert(0, str(PROJECT / 'web_mvp'))

from chat_release import DAILY_REPORT_VERSION
from offline_daily import ArchiveError, DailyReportError, build_daily_report, json_text


def _interactive_values(args, label):
    if not sys.stdin or not sys.stdin.isatty():
        raise DailyReportError('交互模式需要本机控制台；宿主请传入导出目录、日期和新的输出目录。')
    print(label + '本地消息日报：选择原助手已人工确认的正式导出或批量导出目录。')
    if not args.export_root:
        print('每行输入一个目录，全部输入后按空行结束；可直接粘贴带引号的路径。')
        while True:
            value = input('导出目录：').strip().strip('"')
            if not value:
                break
            args.export_root.append(Path(value))
    now = datetime.now(timezone(timedelta(hours=8)))
    if not args.date:
        args.date = input('日报日期 [' + now.date().isoformat() + ']：').strip() or now.date().isoformat()
    if not args.output:
        default = Path.home() / 'Documents' / 'LocalChatDailyReports' / (args.date + '-' + now.strftime('%H%M%S') + '-' + label)
        args.output = Path(input('新的输出目录 [' + str(default) + ']：').strip().strip('"') or default)
    if not args.export_root:
        raise DailyReportError('未选择导出目录。')


def main(client, argv=None, *, standalone=False):
    label = '个微' if client == 'wechat' else '企微'
    parser = argparse.ArgumentParser(description=label + '每日概览、待跟进线索与本地 HTML 日报；读取已复核导出。')
    parser.add_argument('--export-root', action='append', type=Path, default=[], help='正式导出或批次目录；可重复')
    parser.add_argument('--date', help='日报日期 YYYY-MM-DD，使用北京时间')
    parser.add_argument('--output', type=Path, help='新的日报目录，不覆盖已有结果')
    parser.add_argument('--lookback-days', type=int, help='待跟进回看天数，含当天，默认 7')
    parser.add_argument('--my-name', action='append', default=[], help='用户明确提供的群内本人称呼，仅作点名线索；可重复')
    parser.add_argument('--capabilities', action='store_true', help='仅查询版本和能力，不读取材料')
    parser.add_argument('--interactive', action='store_true', help='在本机控制台逐项输入导出目录、日期和输出目录')
    parser.add_argument('--desktop', action='store_true', help='打开本地桌面窗口；不带参数时默认使用')
    parser.add_argument('--check-desktop', type=Path, metavar='NEW_JSON', help='检查桌面组件与日历初始化，将结果写入新的诊断文件后退出')
    parser.add_argument('--version', action='version', version=DAILY_REPORT_VERSION)
    arguments = list(sys.argv[1:] if argv is None else argv)
    args = parser.parse_args(arguments)
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    if args.capabilities:
        print(json_text({'schemaVersion': 1, 'role': 'daily_report', 'client': client, 'version': DAILY_REPORT_VERSION,
                          'conversationModes': ['group', 'direct'], 'input': 'human_reviewed_exports',
                          'readsLiveClient': False, 'callsModelAPI': False,
                          'standalone': standalone, 'requiresRepository': not standalone,
                          'desktopUI': True,
                          'requiresPythonInstallation': not getattr(sys, 'frozen', False)}))
        return 0
    if args.desktop or args.check_desktop or not arguments:
        from offline_daily_ui import run_daily_desktop
        return run_daily_desktop(client, args, DAILY_REPORT_VERSION)
    try:
        if args.interactive:
            _interactive_values(args, label)
    except (DailyReportError, EOFError, KeyboardInterrupt) as error:
        print(json_text({'ok': False, 'client': client, 'error': str(error) if isinstance(error, DailyReportError) else '输入已取消，未生成日报。'}))
        return 2
    if not args.export_root or not args.date or not args.output:
        parser.error('需要 --export-root、--date 和 --output。')
    try:
        result = build_daily_report(args.export_root, client=client, day=args.date, output=args.output,
                                    version=DAILY_REPORT_VERSION, lookback_days=7 if args.lookback_days is None else args.lookback_days, my_names=args.my_name)
    except (ArchiveError, OSError) as error:
        result = {'ok': False, 'client': client, 'error': str(error) if isinstance(error, ArchiveError) else '本地材料无法读取或日报无法写入，请检查路径、权限及空间。'}
        print(json_text(result))
        return 2
    print(json_text(result))
    return 0
