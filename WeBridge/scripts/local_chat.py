"""Standalone CLI for authorized local WeChat desktop 4.x database copies."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if not getattr(sys, 'frozen', False):
    sys.path.insert(0, str(PROJECT_ROOT / 'web_mvp'))

from database_adapter import DatabaseError
from database_snapshot import SnapshotError, prepare_snapshot
from offline_chat import ArchiveError, LocalArchive, discover, workbench_source, write_json


def default_workbench():
    base = Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else PROJECT_ROOT
    for parent in dict.fromkeys([Path.cwd(), base, *list(base.parents)[:2]]):
        candidate = parent / '.runtime/web_mvp-database/database-config.json'
        if candidate.is_file():
            return candidate
    return PROJECT_ROOT / '.runtime/web_mvp-database/database-config.json'


def parser():
    app = argparse.ArgumentParser(description='本地个微聊天记录解析；不登录、不注入、不发送、不联网。')
    commands = app.add_subparsers(dest='command', required=True)
    review = commands.add_parser('review', help='列账号与全部群 ID，解析后由人工复核确认导出')
    review.add_argument('--client', choices=('wechat', 'wecom'))
    review.add_argument('--data-dir', type=Path, help='程序数据目录，默认使用当前 Windows 用户配置')
    review.add_argument('--source', type=Path, help='明确选择账号数据目录或已解密副本')
    review.add_argument('--keys', type=Path, help='已有数据库密钥 JSON')
    review.add_argument('--image-keys', type=Path, help='已有图片密钥 JSON')
    scan = commands.add_parser('discover', help='只读取本机账号目录及数据库文件元数据')
    scan.add_argument('--root', action='append', help='自定义 xwechat_files 根目录，可重复')
    scan.add_argument('--output', type=Path, help='另存发现结果 JSON，目标须不存在')
    snapshot = commands.add_parser('snapshot', help='复制 DB/WAL、用已有本地密钥解密并校验')
    snapshot.add_argument('--source', required=True, type=Path)
    snapshot.add_argument('--keys', type=Path, help='已有密钥 JSON 路径；不是密钥正文')
    snapshot.add_argument('--output', required=True, type=Path, help='副本批次的父目录')
    snapshot.add_argument('--media', action='store_true', help='纳入 hardlink/message_resource 附件索引')
    snapshot.add_argument('--voice', action='store_true', help='纳入 media_N.db 语音库，需对应密钥')
    for name, help_text in (('groups', '列出副本中的群名、ID 与本地消息分片'),
                            ('export', '导出一个指定会话的完整本地记录')):
        command = commands.add_parser(name, help=help_text)
        source = command.add_mutually_exclusive_group(required=True)
        source.add_argument('--snapshot', type=Path, help='已经解密、合并 WAL 的单账号副本')
        source.add_argument('--workbench', nargs='?', const=str(default_workbench()),
                            type=Path, help='复用工作台已经成功创建的副本；可指定配置文件')
        command.add_argument('--self-id', help='明确提供本人微信 ID，不从目录名猜测')
        if name == 'groups':
            command.add_argument('--include-direct', action='store_true', help='也显示私聊会话')
            command.add_argument('--output', type=Path, help='另存群列表 JSON，目标须不存在')
        else:
            chat = command.add_mutually_exclusive_group(required=True)
            chat.add_argument('--chat', help='从 groups 结果复制唯一会话 ID')
            chat.add_argument('--chat-name', help='精确匹配且必须唯一的会话名')
            command.add_argument('--start-date', help='YYYY-MM-DD，北京时间，含当天')
            command.add_argument('--end-date', help='YYYY-MM-DD，北京时间，含当天')
            command.add_argument('--limit', type=int, help='可选的显式条数上限；默认无上限')
            command.add_argument('--output', required=True, type=Path, help='新的导出目录，须不存在')
            command.add_argument('--copy-media', action='store_true', help='按消息索引读取并复制本机附件')
            command.add_argument('--media-root', type=Path, help='同账号的 db_storage 或账号根目录，用于本机附件定位')
            command.add_argument('--image-keys', type=Path, help='已有本机图片密钥 JSON 路径，不自动提取')
    return app


def run(args):
    if args.command == 'review':
        from offline_workflow import review_workflow
        config = {'sourceRoot': str(args.source), 'keyFile': str(args.keys) if args.keys else '',
                  'imageKeyFile': str(args.image_keys) if args.image_keys else '', 'includeMedia': True} if args.source else None
        return review_workflow(client=args.client, directory=args.data_dir, config=config)
    if args.command == 'discover':
        result = discover(args.root)
    elif args.command == 'snapshot':
        value = prepare_snapshot(args.source, args.output, args.keys, args.media, args.voice)
        return {'snapshotRoot': str(value['root']), 'source': value['source']}
    else:
        config = {}
        if args.workbench is not None:
            root, config = workbench_source(args.workbench)
        else:
            root = args.snapshot
        if args.command == 'export':
            if args.media_root:
                config['sourceRoot'] = str(args.media_root.resolve())
            if args.image_keys:
                config['imageKeyFile'] = str(args.image_keys.resolve())
        archive = LocalArchive(root, args.self_id if args.self_id is not None else config.get('selfId', ''), config)
        if args.command == 'groups':
            result = archive.groups(args.include_direct)
        else:
            result = archive.export(args.output, chat_id=args.chat, chat_name=args.chat_name,
                start=args.start_date, end=args.end_date, limit=args.limit, copy_media=args.copy_media)
            return {'output': str(args.output.resolve()), 'exportedRows': result['exportedRows'],
                    'exportFiles': {kind: str(args.output.resolve() / name) for kind, name in result['messageExports'].items()},
                    'truncatedByLimit': result['truncatedByLimit'], 'kinds': result['kinds'],
                    'decodeStatuses': result['decodeStatuses'], 'mediaStatuses': result['mediaStatuses'],
                    'mediaSummary': result['mediaSummary'], 'mediaSummaryText': result['mediaSummaryText'],
                    'reporting': result['reporting'],
                    'coverage': result['coverage']}
    if args.output:
        write_json(args.output, result)
    return result


def main(argv=None):
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    arguments = sys.argv[1:] if argv is None else argv
    if not arguments and sys.stdin.isatty():
        arguments = ['review']
    args = parser().parse_args(arguments)
    try:
        result = run(args)
    except (EOFError, KeyboardInterrupt):
        print(json.dumps({'ok': False, 'error': '人工复核已取消，未确认导出。'}, ensure_ascii=False), file=sys.stderr)
        return 2
    except (SnapshotError, DatabaseError, ArchiveError) as error:
        print(json.dumps({'ok': False, 'error': str(error)}, ensure_ascii=False), file=sys.stderr)
        return 2
    except PermissionError:
        print(json.dumps({'ok': False, 'error': '文件访问被系统拒绝，请使用对该本地副本有读取权限的用户运行。'}, ensure_ascii=False), file=sys.stderr)
        return 2
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error):
        # No raw exception may echo a key, XML payload or private file contents.
        print(json.dumps({'ok': False, 'error': '路径、参数、配置或数据格式无效；检查目标是否已存在、日期、会话 ID 及文件访问权限。'}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps({'ok': True, **result}, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
