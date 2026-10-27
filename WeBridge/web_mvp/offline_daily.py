"""Daily summaries from explicitly selected, human-reviewed local exports."""
from collections import Counter, defaultdict
from bisect import bisect_right
from datetime import date, datetime, timedelta
import hashlib
import json
from pathlib import Path
import re
import shutil
import tempfile

from offline_chat import ArchiveError, BEIJING, publish_directory
from offline_content_status import content_issues
from offline_images import message_nodes
from offline_reporting import REPORT_FILE
from offline_review import REVIEW_CHECKS, inventory


class DailyReportError(ArchiveError):
    pass


REQUEST = re.compile(r'请(?:您|你|大家|各位)?(?:尽快|及时|务必|协助|帮忙|先)?(?:问|帮|确认|回复|提供|处理|安排|发|查|看|跟进)|麻烦|能否|是否|可以吗|什么时候|多少钱|进展(?:如何|怎么样)|跟进(?:一下|下)|[?？]', re.I)
DEFER = re.compile(r'稍后|晚[点些]|回头|稍等|待(?:确认|核实)|(?:明天|下周).{0,12}(?:回复|处理|再|确认|发)|还(?:没|未)')
COMPLETION = re.compile(r'已(?:经)?(?:处理|完成|发送|发出|解决|确认|回复)|问题.{0,4}解决')
NEGATION = re.compile(r'没有|未(?:能|完成|处理|解决|确认)|还没|尚未|暂未|不能|无法|别|不要')
WITHDRAWAL = re.compile(r'不用了|不需要了|取消(?:这|该|本次|请求|需求|订单)|先不(?:用|处理|做)|暂缓')
URGENCY = re.compile(r'紧急|尽快|催|截止|最后期限|逾期|投诉|索赔')
TOPICS = {
    '报价与价格': ('报价', '价格', '费用', 'quotation'),
    '订单与交付': ('订单', '交付', '发货', '运单', '提货'),
    '合同与方案': ('合同', '方案', '协议'),
    '付款与对账': ('付款', '支付', '发票', '对账', 'invoice'),
    '需求与上线': ('需求', '上线', '发布', '功能'),
    '问题与售后': ('故障', '异常', '售后', '投诉', '索赔'),
    '会议与安排': ('会议', '开会', '安排', '预约'),
}
STATE_LABELS = {
    'needs_response': '尚未找到对应回复', 'deferred': '本人暂缓处理，仍待跟进',
    'responded': '已有后续回复，处理结果待核对', 'completion_signal': '本人表示已处理，待核对',
    'withdrawal_signal': '对方表示取消或暂缓，待核对',
    'target_uncertain': '群内请求，对象待确认', 'identity_unknown': '本人身份未知，归属待确认',
}


def _hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()


def _read_json(path):
    try:
        return json.loads(path.read_text(encoding='utf-8-sig'))
    except (OSError, UnicodeError, ValueError) as error:
        raise DailyReportError('无法读取导出元数据，请检查所选材料。') from error


def _roots(paths):
    roots, limitations = [], []
    for value in paths:
        path = Path(value).expanduser().absolute()
        if any(part.is_symlink() or part.is_junction() for part in (path, *path.parents)):
            raise DailyReportError('导出目录路径不能包含链接。')
        if (path / 'manifest.json').is_file():
            roots.append(path)
            continue
        batch = _read_json(path / 'batch.json')
        if not isinstance(batch, dict) or not isinstance(batch.get('groups'), list):
            raise DailyReportError('需要正式导出目录，或包含 batch.json 的批量导出目录。')
        for group in batch['groups']:
            relative = group.get('directory') if isinstance(group, dict) else None
            if not isinstance(relative, str) or not relative or Path(relative).is_absolute() or '..' in Path(relative).parts:
                raise DailyReportError('批次目录引用无效。')
            child = path / relative
            if child.is_symlink() or child.is_junction() or not child.resolve().is_relative_to(path.resolve()):
                raise DailyReportError('批次目录引用越界或包含链接。')
            roots.append(child)
        if batch.get('failed') or batch.get('alreadyExported') or batch.get('metadataErrors'):
            limitations.append('选定批次存在失败、另处已导出或清单未完成项；日报仅覆盖本批次实际提供的正式导出。')
    unique = list(dict.fromkeys(path.resolve() for path in roots))
    if not unique:
        raise DailyReportError('没有找到可用于日报的正式导出。')
    return unique, limitations


def _source(root, client):
    files = {item['path']: item for item in inventory(root)}
    manifest, review = _read_json(root / 'manifest.json'), _read_json(root / 'review.json')
    if not isinstance(manifest, dict) or not isinstance(review, dict):
        raise DailyReportError('正式导出元数据格式无效。')
    human = manifest.get('humanReview', {})
    checks = review.get('checks')
    if (review.get('status') != 'confirmed' or not isinstance(human, dict) or human.get('status') != 'confirmed'
            or not review.get('reviewId') or human.get('reviewId') != review['reviewId']
            or review.get('confirmationBasis') not in {'explicit_review_checklist', 'explicit_batch_review_checklist'}
            or not isinstance(checks, dict) or set(checks) != set(REVIEW_CHECKS)
            or any(value is not True for value in checks.values())):
        raise DailyReportError('材料尚未完成原助手的人工复核，不能生成正式日报。')
    source_client = manifest.get('client') or review.get('client') or manifest.get('coverage', {}).get('client')
    if source_client != client or review.get('client') != client:
        raise DailyReportError('材料客户端与日报脚本不一致，请使用对应的个微或企微入口。')
    chat = manifest.get('chat')
    if not isinstance(chat, dict) or not isinstance(chat.get('id'), str) or not chat['id'] or review.get('chat') != chat:
        raise DailyReportError('正式导出的会话身份与复核不一致。')
    mode = chat.get('conversationKind')
    if mode not in {'group', 'direct'}:
        raise DailyReportError('日报只接受明确的群聊或私聊导出。')
    for key in ('client', 'selfId', 'sourceFingerprint', 'peerId', 'startDate', 'endDate', 'conversationMode'):
        if key in manifest and key in review and manifest[key] != review[key]:
            raise DailyReportError('正式导出的账号或日期与复核不一致。')
    artifacts = manifest.get('artifacts')
    if not isinstance(artifacts, list):
        raise DailyReportError('正式导出缺少文件校验清单。')
    listed = {}
    for item in artifacts:
        if not isinstance(item, dict) or not isinstance(item.get('path'), str):
            raise DailyReportError('正式导出文件清单无效。')
        if item['path'] == 'manifest.json':
            continue
        if files.get(item['path']) != item:
            raise DailyReportError('正式导出文件摘要已变化，请重新复核导出。')
        listed[item['path']] = item
    if set(listed) != set(files) - {'manifest.json'} or 'messages.jsonl' not in listed:
        raise DailyReportError('正式导出文件没有完整纳入校验。')
    reviewed = review.get('reviewedArtifacts')
    if not isinstance(reviewed, list):
        raise DailyReportError('缺少已复核文件清单。')
    for item in reviewed:
        if not isinstance(item, dict):
            raise DailyReportError('已复核文件清单无效。')
        if item.get('path') in {'manifest.json', 'review.json', REPORT_FILE}:
            continue
        if files.get(item.get('path')) != item:
            raise DailyReportError('已复核消息或附件已变化。')
    if 'messages.jsonl' not in {item.get('path') for item in reviewed}:
        raise DailyReportError('消息正文未纳入人工复核。')
    rewritten = {'manifest.json', 'review.json', REPORT_FILE}
    if {item.get('path') for item in reviewed} - rewritten != set(files) - rewritten:
        raise DailyReportError('正式导出包含未纳入人工复核的文件。')
    self_id = review.get('selfId') or manifest.get('selfId') or ''
    fingerprint = review.get('sourceFingerprint') or manifest.get('sourceFingerprint') or ''
    if not isinstance(self_id, str) or not isinstance(fingerprint, str) or not (self_id or fingerprint):
        raise DailyReportError('材料缺少可靠账号或来源标识。')
    account = 'acct-' + hashlib.sha256((client + ':' + (self_id or fingerprint)).encode()).hexdigest()[:32]
    limitations = []
    for container in (manifest, review):
        for key in ('limitations', 'warnings'):
            values = container.get(key, [])
            limitations.extend([values] if isinstance(values, str) else [v for v in values if isinstance(v, str)] if isinstance(values, list) else [])
    if not self_id:
        limitations.append('来源没有确认本人身份；不推断本人发言数量、个人待回复或内部外部关系。')
    return {'root': root, 'sourceId': 'src-' + _hash([client, account, review['reviewId'], files['messages.jsonl']['sha256']])[:24],
            'client': client, 'accountKey': account, 'selfId': self_id, 'chat': chat,
            'reviewId': review['reviewId'], 'startDate': manifest.get('startDate'), 'endDate': manifest.get('endDate'),
            'messagesSha256': files['messages.jsonl']['sha256'], 'exportedRows': manifest.get('exportedRows'),
            'manifestSha256': files['manifest.json']['sha256'], 'reviewSha256': files['review.json']['sha256'],
            'sourceRoot': manifest.get('sourceRoot', ''), 'limitations': limitations}


def _moment(value):
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00')) if isinstance(value, str) else None
        return (result.replace(tzinfo=BEIJING) if result.tzinfo is None else result.astimezone(BEIJING)) if result else None
    except (ValueError, OverflowError):
        return None


def _records(sources, start, end):
    records, duplicate_count, undated = {}, 0, 0
    for source in sources:
        digest, count = hashlib.sha256(), 0
        with (source['root'] / 'messages.jsonl').open('rb') as stream:
            for line_number, raw in enumerate(stream, 1):
                digest.update(raw)
                if not raw.strip():
                    continue
                try:
                    row = json.loads(raw.decode('utf-8-sig'))
                except (UnicodeError, ValueError) as error:
                    raise DailyReportError('消息 JSONL 格式无效。') from error
                if not isinstance(row, dict) or row.get('chatId') not in (None, source['chat']['id']):
                    raise DailyReportError('消息会话与导出来源不一致。')
                count += 1
                identifier = row.get('id')
                if isinstance(identifier, bool) or not isinstance(identifier, (str, int)) or identifier == '':
                    raise DailyReportError('消息缺少稳定 ID，无法生成可追溯日报。')
                moment = _moment(row.get('time'))
                if moment is not None and not start <= moment < end:
                    continue
                sender = row.get('senderId')
                sender = str(sender) if isinstance(sender, (str, int)) and not isinstance(sender, bool) else ''
                direction = ('outgoing' if sender == source['selfId'] else 'incoming') if sender and source['selfId'] else 'unknown'
                if row.get('direction') in {'incoming', 'outgoing'} and direction != 'unknown' and row['direction'] != direction:
                    raise DailyReportError('消息方向与已复核本人身份冲突。')
                payload = {key: value for key, value in row.items() if key not in {'ref'}}
                key = _hash([source['client'], source['accountKey'], source['chat']['id'], str(identifier)])[:28]
                reference = {'sourceId': source['sourceId'], 'reviewId': source['reviewId'], 'line': line_number}
                signature = _hash(payload)
                if key in records:
                    if records[key]['_signature'] != signature:
                        raise DailyReportError('同一消息 ID 在所选导出中内容冲突，请明确选择一致版本。')
                    records[key]['sources'].append(reference)
                    duplicate_count += 1
                    continue
                codes, reasons = [], []
                for node, _trail, _path in message_nodes(row):
                    found, detail, _actions = content_issues(node)
                    codes.extend(found); reasons.extend(detail)
                entry = {'evidenceId': 'm-' + key, 'messageId': str(identifier), 'serverId': row.get('serverId'),
                         'chatId': source['chat']['id'], 'chatName': source['chat'].get('name') or source['chat']['id'],
                         'conversationMode': source['chat']['conversationKind'], 'selfId': source['selfId'],
                         'senderId': sender, 'senderName': row.get('senderName') or '身份未知', 'direction': direction,
                         'time': moment.isoformat() if moment else None, 'originalTime': row.get('time'),
                         'kind': row.get('kind') or 'unknown', 'text': row.get('text') if isinstance(row.get('text'), str) else '',
                         'mentions': row.get('mentions') or {}, 'provenance': row.get('provenance') or {},
                         'content': {key: row[key] for key in ('quote', 'record', 'attachmentContent', 'location', 'contact', 'miniProgram', 'asset') if key in row},
                         'issues': sorted(set(codes)), 'issueReasons': list(dict.fromkeys(reasons)),
                         'sources': [reference], '_signature': signature}
                records[key] = entry
                if moment is None:
                    undated += 1
        if count != source['exportedRows'] or digest.hexdigest() != source['messagesSha256']:
            raise DailyReportError('消息条数或摘要与正式导出不一致。')
        for filename, field in (('manifest.json', 'manifestSha256'), ('review.json', 'reviewSha256')):
            if hashlib.sha256((source['root'] / filename).read_bytes()).hexdigest() != source[field]:
                raise DailyReportError('读取期间导出元数据发生变化。')
    values = sorted(records.values(), key=lambda row: (row['time'] is None, row['time'] or '', row['evidenceId']))
    for row in values:
        del row['_signature']
    return values, duplicate_count, undated


def _primary(row):
    return row['text'].strip() if row['kind'] in {'text', 'quote'} else ''


def _target(row, my_names):
    if not row['selfId'] or row['direction'] == 'unknown':
        return 'unknown', 'identity_unknown'
    if row['conversationMode'] == 'direct':
        return 'self', 'direct_conversation'
    mentions = row['mentions'] if isinstance(row['mentions'], dict) else {}
    ids = mentions.get('ids') or []
    if row['selfId'] in ids:
        return 'self', 'structured_mention'
    if ids:
        return 'other', 'other_mention'
    if any(re.search(r'(?:^|[\s@，,:：])' + re.escape(name) + r'(?=$|[\s，,:：])', row['text']) for name in my_names):
        return 'self', 'explicit_name_clue'
    return 'unknown', 'group_target_unknown'


def _quote_targets(row, request):
    quote = row['content'].get('quote') or {}
    linked = quote.get('sourceMessageId') or quote.get('messageId')
    message = quote.get('message') or {}
    return linked == request['messageId'] or message.get('id') == request['messageId']


def _followups(records, my_names):
    groups = defaultdict(list)
    for row in records:
        if row['time']:
            groups[row['chatId']].append(row)
    results = []
    for rows in groups.values():
        requests = [row for row in rows if row['direction'] != 'outgoing' and REQUEST.search(_primary(row))
                    and not WITHDRAWAL.search(_primary(row))]
        request_times = [row['time'] for row in requests]
        responses = [row for row in rows if _primary(row) and
                     (row['direction'] == 'outgoing' or WITHDRAWAL.search(_primary(row)))]
        for request in requests:
            target, basis = _target(request, my_names)
            if target == 'other':
                continue
            state = 'needs_response' if target == 'self' else 'identity_unknown' if basis == 'identity_unknown' else 'target_uncertain'
            reply = None
            for later in responses:
                if later['time'] <= request['time'] or not _primary(later):
                    continue
                explicit = _quote_targets(later, request)
                competing = bisect_right(request_times, later['time'])
                chronological = (request['conversationMode'] == 'direct' and competing == 1
                                 and not later['content'].get('quote'))
                if target != 'self' or not (explicit or chronological):
                    continue
                if later['direction'] == 'outgoing':
                    reply = later
                    body = _primary(later)
                    state = 'deferred' if DEFER.search(body) else 'completion_signal' if COMPLETION.search(body) and not NEGATION.search(body) else 'responded'
                elif later['senderId'] == request['senderId'] and WITHDRAWAL.search(_primary(later)):
                    reply, state = later, 'withdrawal_signal'
            score = (3 if basis == 'structured_mention' else 2 if target == 'self' else 0) + (2 if URGENCY.search(request['text']) else 0)
            results.append({'id': 'f-' + request['evidenceId'][2:], 'evidenceId': request['evidenceId'],
                            'chatId': request['chatId'], 'chatName': request['chatName'], 'time': request['time'],
                            'senderName': request['senderName'], 'text': request['text'], 'target': target, 'targetBasis': basis,
                            'state': state, 'stateLabel': STATE_LABELS[state], 'priority': score,
                            'replyEvidenceId': reply['evidenceId'] if reply else None,
                            'replyBasis': ('explicit_quote' if _quote_targets(reply, request) else 'direct_chronology') if reply else None,
                            'humanReviewRequired': True})
    return sorted(results, key=lambda row: (-row['priority'], row['time'], row['id']))


def build_daily_report(export_roots, *, client, day, output, version='1.0.0', lookback_days=7, my_names=()):
    """Validate selected exports, aggregate one account, and publish a new report bundle."""
    if client not in {'wechat', 'wecom'}:
        raise DailyReportError('日报客户端无效。')
    try:
        selected_day = date.fromisoformat(day)
        if selected_day.isoformat() != day:
            raise ValueError('date format')
    except (TypeError, ValueError) as error:
        raise DailyReportError('日期须为 YYYY-MM-DD。') from error
    if not isinstance(lookback_days, int) or not 1 <= lookback_days <= 90:
        raise DailyReportError('回看范围须为 1 至 90 天（含日报当天）。')
    start = datetime.combine(selected_day - timedelta(days=lookback_days - 1), datetime.min.time(), BEIJING)
    day_start = datetime.combine(selected_day, datetime.min.time(), BEIJING)
    end = day_start + timedelta(days=1)
    roots, limitations = _roots(export_roots)
    sources = [_source(root, client) for root in roots]
    if len({source['accountKey'] for source in sources}) != 1:
        raise DailyReportError('所选材料的账号标识不一致，请按同一账号分别生成日报。')
    destination = Path(output).expanduser().absolute()
    if any(part.is_symlink() or part.is_junction() for part in (destination, *destination.parents)):
        raise DailyReportError('日报输出路径不能包含链接。')
    for source in sources:
        protected = [source['root']]
        if source['sourceRoot']:
            protected.append(Path(source['sourceRoot']).resolve())
        if any(destination.resolve() == root or destination.resolve().is_relative_to(root) or root.is_relative_to(destination.resolve()) for root in protected):
            raise DailyReportError('日报目录必须与原始数据和正式导出分开。')
    if destination.exists():
        raise DailyReportError('日报目录已存在，请选择新的目录。')
    records, duplicates, undated = _records(sources, start, end)
    today = [row for row in records if row['time'] and day_start <= _moment(row['time']) < end]
    followups = _followups(records, tuple(name.strip() for name in my_names if name.strip()))
    hours = Counter(_moment(row['time']).hour for row in today)
    directions = Counter(row['direction'] for row in today)
    conversations = []
    for chat_id in sorted({row['chatId'] for row in today}):
        rows = [row for row in today if row['chatId'] == chat_id]
        conversations.append({'id': chat_id, 'name': rows[0]['chatName'], 'mode': rows[0]['conversationMode'],
                              'messages': len(rows), 'speakers': len({row['senderId'] for row in rows if row['senderId']}),
                              'outgoing': sum(row['direction'] == 'outgoing' for row in rows) if all(row['selfId'] for row in rows) else None})
    conversations.sort(key=lambda item: (-item['messages'], item['id']))
    topics = []
    for label, terms in TOPICS.items():
        matches = [row for row in today if any(term in _primary(row).casefold() for term in terms)]
        if matches:
            topics.append({'label': label, 'messages': len(matches), 'conversations': len({row['chatId'] for row in matches}),
                           'evidenceIds': [row['evidenceId'] for row in matches]})
    topics.sort(key=lambda item: (-item['conversations'], -item['messages'], item['label']))
    limitations += [value for source in sources for value in source['limitations']]
    limitations += ['仅覆盖明确选择并通过校验的导出；未提供的会话、日期和本地未保存消息不在统计内。',
                    '待跟进和风险均为字面规则线索；后续回复不等于问题已解决，完成或取消信号仍需人工核对。',
                    '规则只读取根文本及引用消息自身正文，不把被引原文、转发或附件作者的话归给当前发送者。',
                    '没有历史时点未读状态，未读数不统计；日报阶段只读已复核导出，不连接客户端或调用模型。']
    if undated:
        limitations.append(f'有 {undated} 条时间缺失或无效的消息，保留在证据中，未纳入当天统计和待跟进识别。')
    report = {'schemaVersion': 1, 'version': version, 'client': client, 'date': day, 'timezone': 'Asia/Shanghai',
              'createdAt': datetime.now(BEIJING).isoformat(timespec='seconds'), 'accountKey': sources[0]['accountKey'],
              'lookback': {'days': lookback_days, 'start': start.isoformat(), 'endExclusive': end.isoformat()},
              'overview': {'messages': len(today), 'activeConversations': len(conversations), 'selectedConversations': len({s['chat']['id'] for s in sources}),
                           'outgoing': directions['outgoing'] if all(source['selfId'] for source in sources) else None,
                           'directionCounts': dict(directions), 'hourly': [hours[hour] for hour in range(24)],
                           'kinds': dict(Counter(row['kind'] for row in today)),
                           'messagesWithGaps': sum(bool(row['issues']) for row in today), 'undatedMessages': undated,
                           'duplicateOccurrences': duplicates, 'personalFollowups': sum(item['target'] == 'self' and item['state'] not in {'completion_signal', 'withdrawal_signal'} for item in followups)},
              'conversations': conversations, 'followups': followups, 'topics': topics,
              'riskClues': [{'evidenceId': row['evidenceId'], 'matches': sorted(set(URGENCY.findall(_primary(row))))}
                            for row in today if row['direction'] != 'outgoing' and URGENCY.search(_primary(row))],
              'sources': [{key: str(value) if key == 'root' else value for key, value in source.items()} for source in sources],
              'limitations': list(dict.fromkeys(limitations)), 'unread': {'available': False},
              'evidenceFile': 'evidence.jsonl', 'diagnosticsFile': 'diagnostics.jsonl',
              'containsMessageText': True, 'humanReviewRequiredForFollowups': True}
    from offline_daily_html import render_report
    destination.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix='.daily-report-', dir=destination.parent))
    try:
        (stage / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        with (stage / 'evidence.jsonl').open('w', encoding='utf-8') as stream:
            for row in records:
                stream.write(json.dumps(row, ensure_ascii=False) + '\n')
        with (stage / 'diagnostics.jsonl').open('w', encoding='utf-8') as stream:
            def audit(event, **fields):
                stream.write(json.dumps({'event': event, **fields}, ensure_ascii=False) + '\n')
            audit('daily_report_created', version=version, client=client, date=day,
                  createdAt=report['createdAt'], accountKey=report['accountKey'], lookback=report['lookback'])
            for source in report['sources']:
                audit('source_validated', **source)
            for row in records:
                audit('message_traced', **{key: row[key] for key in
                      ('evidenceId', 'messageId', 'serverId', 'chatId', 'senderId', 'direction', 'kind', 'issues', 'issueReasons', 'sources')})
            for item in followups:
                audit('followup_linked', **{key: item[key] for key in
                      ('id', 'evidenceId', 'chatId', 'target', 'targetBasis', 'state', 'replyEvidenceId', 'replyBasis')})
            audit('scope_limitations', limitations=report['limitations'])
        (stage / 'report.html').write_text(render_report(report, records), encoding='utf-8')
        checksums = [{'path': path.name, 'bytes': path.stat().st_size, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
                     for path in sorted(stage.iterdir())]
        (stage / 'manifest.json').write_text(json.dumps({'schemaVersion': 1, 'role': 'daily_report', 'client': client,
            'date': day, 'version': version, 'files': checksums}, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        publish_directory(stage, destination)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    return {'ok': True, 'client': client, 'date': day, 'version': version, 'output': str(destination.resolve()),
            'htmlFile': str(destination / 'report.html'), 'jsonFile': str(destination / 'report.json'),
            'evidenceFile': str(destination / 'evidence.jsonl'),
            'diagnosticsFile': str(destination / 'diagnostics.jsonl'), 'overview': report['overview']}
