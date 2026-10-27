"""Private-chat profile CLI and explicit human review windows."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

if not getattr(sys, 'frozen', False):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'web_mvp'))

from private_profiles import ProfileError, ProfileStore
from chat_release import PRIVATE_VERSION

CAPABILITIES = {'schemaVersion': 1, 'privateAnalysis': True, 'conversationMode': 'direct',
                'role': 'profile', 'version': PRIVATE_VERSION}


def default_store():
    base = os.environ.get('LOCALAPPDATA')
    if not base:
        raise ProfileError('缺少 LOCALAPPDATA；请显式提供 --store 画像库目录。')
    return Path(base) / 'LocalChatPrivateProfiles'


def read_input(path, limit=4 * 1024 * 1024, *, expected=dict):
    path = Path(path).absolute()
    if any(p.is_symlink() or p.is_junction() for p in (path, *path.parents)):
        raise ProfileError('输入文件路径不能包含链接。')
    if not path.is_file() or path.stat().st_size > limit:
        raise ProfileError('输入文件不存在或超过大小限制。')
    try:
        with path.open('rb') as stream:
            data = stream.read(limit + 1)
        if len(data) > limit:
            raise ProfileError('输入文件超过大小限制。')
        value = json.loads(data.decode('utf-8-sig'))
    except (UnicodeError, ValueError, RecursionError) as error:
        raise ProfileError('输入文件不是有效 JSON。') from error
    if not isinstance(value, expected):
        raise ProfileError('输入 JSON 的顶层结构不符合此操作要求。')
    return value


def _bound(actual, expected):
    if expected and actual != expected:
        raise ProfileError('材料属于另一客户端，请使用对应客户端的私聊 Skill。')


def stage_candidate_file(store, candidate_path, *, export_root, bound_client,
                         expected_request_id=None):
    """Stage host output for the selected reviewed identity, without confirming it."""
    source = store.inspect_export(export_root)
    identity = source['identity']
    _bound(identity['client'], bound_client)
    candidate = read_input(candidate_path)
    request_id = candidate.get('requestId')
    request = store.request(request_id)
    _bound(request['client'], bound_client)
    if any(request[key] != identity[key] for key in ('client', 'accountKey', 'peerId')):
        raise ProfileError('候选与当前正式导出的账号或联系人不一致，请切换到对应会话后重试。')
    if expected_request_id is not None and request_id != expected_request_id:
        raise ProfileError('候选不属于此材料窗口的分析请求，请使用本次请求生成的候选。')
    # The original request owns evidence and date coverage. Another batch for
    # the same identity is valid only while its evidence and base still pass.
    staged = store.stage(request_id, candidate)
    return store.candidate(staged['candidateId'])


def load_saved_profile(store, export_root, *, bound_client, overview=False):
    """Resolve the account from reviewed source metadata before reading a profile."""
    source = store.inspect_export(export_root)
    identity = source['identity']
    _bound(identity['client'], bound_client)
    if overview:
        value = store.overview(identity['client'], identity['accountKey'])
    else:
        value = store.current(identity['client'], identity['accountKey'], identity['peerId']) or {}
    return {**value, 'identity': identity, 'overview': overview,
            'observations': value.get('observations', []),
            'emptyHint': ('当前账号尚无已确认的本人或关系观察。先完成候选复核，再查看总览。' if overview else
                          '此联系人尚未确认画像。准备画像材料，由宿主生成候选，再人工复核入库。')}


def packaged_client(requested):
    if not getattr(sys, 'frozen', False):
        return requested
    executable = Path(sys.executable).resolve()
    package = executable.parent.parent if executable.parent.name.lower() == 'bin' else executable.parent
    manifest = package / 'runtime.json'
    if not manifest.exists():
        return requested
    runtime = read_input(manifest, 65536)
    relative = runtime.get('profileCliRelativePath')
    if (not isinstance(relative, str) or (package / relative).resolve() != executable or
            not executable.is_relative_to(package) or runtime.get('client') not in ('wechat', 'wecom')):
        raise ProfileError('画像程序与分发包绑定不一致，请使用完整 Skill 包。')
    with executable.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    if digest != runtime.get('profileCliSha256'):
        raise ProfileError('画像程序摘要与分发包不一致。')
    _bound(runtime['client'], requested)
    return runtime['client']


def _review_window(title, content, save, *, parent=None, candidate=None):
    from private_profile_ui import review_window
    return review_window(title, content, save, parent=parent, candidate=candidate)


def show_candidate(store, value, *, parent=None, bound_client=None):
    _bound(value['identity']['client'], bound_client)
    return _review_window('复核私聊画像候选', '',
                         lambda note: store.confirm(value['candidateId'], confirmed_by='local_user', note=note),
                         parent=parent, candidate=value)


def review_candidate(store, candidate_id, *, parent=None, bound_client=None):
    return show_candidate(store, store.candidate(candidate_id), parent=parent, bound_client=bound_client)


def review_link(store, identities, *, parent=None, bound_client=None):
    if not isinstance(identities, list) or not 2 <= len(identities) <= 20:
        raise ProfileError('身份关联须提供 2 至 20 个已建档身份。')
    profiles = []
    for item in identities:
        if not isinstance(item, dict) or set(item) != {'client', 'accountKey', 'peerId'}:
            raise ProfileError('关联身份字段必须为 client、accountKey、peerId。')
        profile = store.current(item['client'], item['accountKey'], item['peerId'])
        if not profile:
            raise ProfileError('身份尚未建档，不能关联。')
        profiles.append(profile)
    if bound_client and not any(i['client'] == bound_client for i in identities):
        raise ProfileError('关联范围必须包含当前 Skill 客户端的身份。')
    content = ('请人工核对下列身份是否为同一人。关联保留各端独立身份和消息来源。\n'
               '相同昵称不构成身份确认依据。\n\n'
               + json.dumps({'identities': identities, 'currentProfiles': profiles}, ensure_ascii=False, indent=2))
    return _review_window('确认跨端身份关联', content,
                         lambda note: store.link(identities, confirmed_by='local_user', note=note), parent=parent)


def validate_report_destination(output, export_root, store_root):
    target = Path(output).absolute()
    if any(p.is_symlink() or p.is_junction() for p in (target, *target.parents)):
        raise ProfileError('报告输出路径不能包含链接。')
    target = target.resolve()
    if any(target.is_relative_to(Path(root).resolve()) for root in (export_root, store_root)):
        raise ProfileError('报告须保存到正式导出和画像库之外的新目录。')


def parser():
    app = argparse.ArgumentParser(description=__doc__)
    app.add_argument('--capabilities', action='store_true')
    app.add_argument('--result-json', type=Path, help='仅能力探测时写入不存在的新 JSON 文件')
    app.add_argument('--store', type=Path)
    app.add_argument('--bound-client', choices=('wechat', 'wecom'))
    commands = app.add_subparsers(dest='action')
    prepare = commands.add_parser('prepare')
    prepare.add_argument('--export-root', type=Path, required=True)
    stage = commands.add_parser('stage')
    stage.add_argument('--request-id', required=True)
    stage.add_argument('--candidate', type=Path, required=True)
    review = commands.add_parser('review')
    review.add_argument('--candidate-id', required=True)
    show = commands.add_parser('show')
    overview = commands.add_parser('overview')
    for sub in (show, overview):
        sub.add_argument('--client', choices=('wechat', 'wecom'), required=True)
        sub.add_argument('--account-key', required=True)
    show.add_argument('--peer-id', required=True)
    insights = commands.add_parser('insights')
    insights.add_argument('--export-root', type=Path, required=True)
    insights.add_argument('--output-dir', type=Path)
    insights.add_argument('--keyword')
    insights.add_argument('--offset', type=int, default=0)
    insights.add_argument('--limit', type=int, default=50)
    link = commands.add_parser('link-review')
    link.add_argument('--identities', type=Path, required=True)
    return app


def execute(args):
    if args.capabilities:
        return CAPABILITIES
    args.bound_client = packaged_client(args.bound_client)
    if not args.action:
        raise ProfileError('请选择 prepare/stage/review/show/overview/insights/link-review 操作。')
    store = ProfileStore(args.store or default_store())
    if args.action == 'insights':
        from private_insights import summarize, search_messages, write_report
        source = store.inspect_export(args.export_root)
        _bound(source['identity']['client'], args.bound_client)
        result = {'status': 'ready', 'summary': summarize(source)}
        if args.keyword is not None:
            result['search'] = search_messages(source, args.keyword, offset=args.offset, limit=args.limit)
        if args.output_dir:
            validate_report_destination(args.output_dir, args.export_root, store.root)
            result['report'] = write_report(result['summary'], args.output_dir)
        return result
    if args.action == 'prepare':
        review = read_input(args.export_root / 'review.json', 16 * 1024 * 1024)
        _bound(review.get('client'), args.bound_client)
        return store.prepare(args.export_root)
    if args.action == 'stage':
        _bound(store.request(args.request_id)['client'], args.bound_client)
        return store.stage(args.request_id, read_input(args.candidate))
    if args.action == 'review':
        return review_candidate(store, args.candidate_id, bound_client=args.bound_client)
    if args.action in ('show', 'overview'):
        _bound(args.client, args.bound_client)
        if args.action == 'overview':
            return store.overview(args.client, args.account_key)
        value = store.current(args.client, args.account_key, args.peer_id)
        return {'status': 'confirmed' if value else 'not_found', 'profile': value}
    return review_link(store, read_input(args.identities, expected=list), bound_client=args.bound_client)


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.result_json and not args.capabilities:
            raise ProfileError('--result-json 仅用于 --capabilities 能力探测。')
        result = {'ok': True, **execute(args)}
        if args.result_json:
            with args.result_json.open('x', encoding='utf8') as stream:
                json.dump(result, stream)
    except ProfileError as error:
        result = {'ok': False, 'status': 'failed', 'error': str(error)}
    except (OSError, ValueError, TypeError, KeyError):
        result = {'ok': False, 'status': 'failed', 'error': '画像操作失败；请核对输入与本地文件，原画像未主动覆盖。'}
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result['ok'] else 2


if __name__ == '__main__':
    import multiprocessing
    multiprocessing.freeze_support()
    raise SystemExit(main())
