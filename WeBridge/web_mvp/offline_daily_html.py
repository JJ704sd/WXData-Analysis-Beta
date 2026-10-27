"""Self-contained, escaped HTML for daily reports; no external assets."""
import html

from offline_daily import display_text


def _business_name(value, identifier, fallback):
    return display_text(value) if value and str(value) != str(identifier) else fallback


def _business_gaps(row):
    notes = []
    for code in row['issues']:
        if code == 'message_unicode_partial':
            note = '部分字符异常，已用转义标记保留，请核对原记录。'
        elif code.startswith('document_'):
            note = '附件正文尚未完整读取，请核对原件。'
        elif code in {'original_unavailable', 'media_not_checked'}:
            note = '原图或附件尚未补齐，需要时请查看原记录。'
        elif code == 'forward_partial':
            note = '转发记录尚未完整读取，请核对完整对话。'
        elif code == 'quote_unlinked':
            note = '引用内容尚未与原消息核对，请结合上下文判断。'
        else:
            note = '部分正文尚未完整读取，请核对原消息。'
        if note not in notes:
            notes.append(note)
    return ' '.join(notes) or '当前未记录需要补充的信息。'


def _business_content(content):
    esc = lambda value: html.escape(display_text(value), quote=True)
    parts = []

    def add(node, heading):
        sender = _business_name(node.get('senderName'), node.get('senderId'), '发送人未标注')
        text = node.get('text')
        if isinstance(text, str) and text:
            parts.append('<p class="muted">' + esc(heading + ' · ' + sender) + '</p><p class="quote">' + esc(text) + '</p>')
        document = node.get('attachmentContent') or {}
        if isinstance(document.get('text'), str) and document['text']:
            parts.append('<p class="muted">附件中已读取的正文</p><p class="quote">' + esc(document['text']) + '</p>')
        quote = (node.get('quote') or {}).get('message')
        if isinstance(quote, dict):
            add(quote, '引用原文')
        for child in (node.get('record') or {}).get('items', []):
            if isinstance(child, dict):
                add(child, '转发原文')

    add(content, '补充原文')
    return '<details><summary>查看引用、转发或附件原文</summary>' + ''.join(parts) + '</details>' if parts else ''


def render_report(report, records):
    esc = lambda value: html.escape(display_text(value), quote=True)
    label = '个人微信' if report['client'] == 'wechat' else '企业微信'
    overview = report['overview']
    by_id = {row['evidenceId']: row for row in records}
    used = {row['evidenceId'] for row in records if 'message_unicode_partial' in row['issues']}

    def evidence_link(identifier, title='查看依据'):
        if not identifier or identifier not in by_id:
            return ''
        used.add(identifier)
        return '<a class="evidence-link" href="#' + identifier + '">' + esc(title) + '</a>'

    cards = []
    for item in report['followups']:
        state = item['state']
        group = 'signal' if state in {'completion_signal', 'withdrawal_signal'} else 'personal' if item['target'] == 'self' else 'uncertain'
        body = item['text']
        snippet = body[:180] + ('…' if len(body) > 180 else '')
        cards.append('<article class="clue" data-group="' + group + '"><div class="clue-top"><span class="badge ' + group + '">'
                     + esc(item['stateLabel']) + '</span><span class="muted">' + esc(item['time'][5:16].replace('T', ' '))
                     + '</span></div><h3>' + esc(_business_name(item['chatName'], item['chatId'], '会话名称待补充')) + '</h3><p class="quote">' + esc(snippet)
                     + '</p><div class="clue-foot"><span>' + esc(_business_name(item['senderName'], by_id[item['evidenceId']]['senderId'], '发送人待确认')) + ' · '
                     + ('本人相关线索' if item['target'] == 'self' else '不能确认为本人待办') + '</span>'
                     + evidence_link(item['evidenceId']) + evidence_link(item['replyEvidenceId'], '后续回应依据') + '</div></article>')
    conversation_rows = ''.join('<tr><td>' + esc(_business_name(item['name'], item['id'], '会话名称待补充')) + '</td><td>'
                                + ('群聊' if item['mode'] == 'group' else '私聊') + '</td><td>' + str(item['messages'])
                                + '</td><td>' + str(item['speakers']) + '</td><td>'
                                + ('未确认' if item['outgoing'] is None else str(item['outgoing'])) + '</td></tr>'
                                for item in report['conversations'])
    topics = []
    for item in report['topics']:
        links = ' '.join(evidence_link(identifier, '消息 ' + str(index)) for index, identifier in enumerate(item['evidenceIds'][:5], 1))
        topics.append('<div class="topic"><strong>' + esc(item['label']) + '</strong><span>' + str(item['messages'])
                      + ' 条 · ' + str(item['conversations']) + ' 个会话</span><div>' + links + '</div></div>')
    risks = []
    for item in report['riskClues']:
        row = by_id[item['evidenceId']]
        risks.append('<li><strong>' + esc(' / '.join(item['matches'])) + '</strong> · ' + esc(_business_name(row['chatName'], row['chatId'], '会话名称待补充')) + ' '
                     + evidence_link(row['evidenceId']) + '<p>' + esc(row['text'][:160]) + '</p></li>')
    evidence = []
    for identifier in sorted(used, key=lambda key: (by_id[key]['time'] or '', key)):
        row = by_id[identifier]
        evidence.append('<details class="evidence" id="' + identifier + '"><summary>' + esc((row['time'][:16].replace('T', ' ')) if row['time'] else '时间待确认') + ' · '
                        + esc(_business_name(row['chatName'], row['chatId'], '会话名称待补充')) + ' · '
                        + esc(_business_name(row['senderName'], row['senderId'], '发送人待确认')) + '</summary><p class="quote">' + esc(row['text'])
                        + '</p><p class="muted">' + esc(_business_gaps(row)) + '</p>'
                        + _business_content(row['content']) + '</details>')
    source_rows = ''.join('<tr><td>' + esc(_business_name(source['chat'].get('name'), source['chat']['id'], '会话名称待补充'))
                          + '</td><td>' + ('群聊' if source['chat']['conversationKind'] == 'group' else '私聊')
                          + '</td><td>' + str(source['exportedRows']) + '</td><td>'
                          + esc((source['startDate'] or '未限定') + ' 至 ' + (source['endDate'] or '未限定'))
                          + '</td></tr>' for source in report['sources'])
    business_limits = ['仅覆盖本次提供的会话和导出日期，未提供或本地未保存的消息不在统计内。',
                       '待跟进是候选线索，已有回复、表示完成或取消均需结合原文核对。']
    if any('message_unicode_partial' in row['issues'] for row in records):
        business_limits.append('部分字符异常，已用转义标记保留，可在原文依据中核对；相关内容仍需人工确认。')
    if overview['undatedMessages']:
        business_limits.append('部分消息时间待确认，未计入当天概览和待跟进。')
    if any(source['limitations'] for source in report['sources']) or len(report['limitations']) > 4:
        business_limits.append('部分材料存在范围或内容限制，需要时请结合完整原记录核对。')
    limitations = ''.join('<li>' + esc(item) + '</li>' for item in business_limits)
    max_hour = max(overview['hourly'], default=0)
    bars = ''.join('<div class="hour"><span>' + (str(count) if count else '') + '</span><i style="height:'
                   + str(round(110 * count / max_hour) if max_hour else 0) + 'px"></i><small>' + f'{hour:02}' + '</small></div>'
                   for hour, count in enumerate(overview['hourly']))
    outgoing = '未确认' if overview['outgoing'] is None else str(overview['outgoing'])
    kpis = [('当日消息', overview['messages']), ('活跃会话', overview['activeConversations']), ('本人发出', outgoing),
            ('本人相关待跟进', overview['personalFollowups']), ('信息待补充', overview['messagesWithGaps']),
            ('处理 / 取消待核对', sum(item['state'] in {'completion_signal', 'withdrawal_signal'} for item in report['followups']))]
    metrics = ''.join('<div class="metric"><span>' + name + '</span><strong>' + esc(value) + '</strong></div>' for name, value in kpis)
    return '''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>''' + esc(label + '每日消息整理 · ' + report['date']) + '''</title><style>
:root{--ink:#19343d;--muted:#657981;--line:#dbe5e6;--teal:#087c78;--paper:#fff;--bg:#f2f6f6}*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.65 "Microsoft YaHei","Segoe UI",sans-serif}a{color:var(--teal);text-decoration:none}a:hover{text-decoration:underline}aside{position:fixed;inset:0 auto 0 0;width:210px;background:#153b44;color:#e5f1ef;padding:32px 22px}aside strong{display:block;font-size:19px;letter-spacing:1px}aside p{font-size:12px;color:#a8c5c8}nav a{display:block;color:#d2e6e4;padding:10px 0}main{margin-left:210px;padding:35px 42px 70px;max-width:1600px}header{display:flex;justify-content:space-between;gap:20px;align-items:flex-start;margin-bottom:26px}.eyebrow{font-size:12px;color:var(--teal);font-weight:700;letter-spacing:2px}h1{font-size:31px;margin:5px 0}h2{font-size:20px;margin:0 0 18px}h3{font-size:16px;margin:10px 0}.muted,small{color:var(--muted);font-size:12px}.date{font-size:18px;font-weight:600;white-space:nowrap}.metrics{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:12px}.metric{background:var(--paper);border:1px solid var(--line);border-radius:12px;padding:18px 16px}.metric span{display:block;font-size:12px;color:var(--muted)}.metric strong{display:block;font-size:27px;margin-top:6px}.notice{border-left:3px solid #d49a37;background:#fff8e9;padding:12px 16px;margin:20px 0 26px;font-size:13px}section{margin-top:28px;background:var(--paper);border:1px solid var(--line);border-radius:14px;padding:23px;scroll-margin-top:18px}.section-top{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap}.controls{display:flex;gap:10px;margin-bottom:18px}input,select{font:inherit;font-size:13px;padding:9px 11px;border:1px solid #b9ccce;border-radius:6px;background:#fff;color:var(--ink)}input{min-width:0;flex:1}.clues{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}.clue{border:1px solid var(--line);border-radius:10px;padding:17px;min-width:0}.clue-top,.clue-foot{display:flex;justify-content:space-between;gap:8px;flex-wrap:wrap;font-size:12px}.badge{font-size:11px;border-radius:4px;padding:3px 7px}.personal{background:#e4f3ef;color:#166855}.uncertain{background:#fff0d6;color:#91620c}.signal{background:#e8eef9;color:#465f91}.quote{white-space:pre-wrap;overflow-wrap:anywhere;background:#f5f8f8;border-radius:7px;padding:12px;font-size:13px}.clue-foot{color:var(--muted)}.evidence-link{font-size:12px;margin-left:8px}.chart{height:158px;display:flex;align-items:flex-end;gap:6px;min-width:530px}.chart-wrap,.table-wrap{overflow:auto}.hour{flex:1;display:flex;align-items:center;flex-direction:column;justify-content:flex-end;min-width:15px}.hour i{display:block;width:80%;background:#409c96;border-radius:3px 3px 0 0}.hour span{font-size:10px;min-height:19px}.hour small{font-size:10px;margin-top:5px}.two-col{display:grid;grid-template-columns:1fr 1fr;gap:20px}.topics{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}.topic{background:#f4f8f8;padding:13px;border-radius:8px}.topic strong,.topic span{display:block}.topic span{color:var(--muted);font-size:12px}.risk-list{padding-left:20px}.risk-list p{font-size:13px;color:var(--muted);overflow-wrap:anywhere}table{border-collapse:collapse;width:100%;font-size:13px}th,td{padding:12px 10px;text-align:left;border-bottom:1px solid var(--line);vertical-align:top}th{font-size:12px;color:var(--muted);font-weight:500}td small{display:block;overflow-wrap:anywhere}.path{max-width:380px;overflow-wrap:anywhere}.empty{color:var(--muted);background:#f5f8f8;padding:20px;border-radius:8px}.evidence{border-top:1px solid var(--line);padding:13px 0}.evidence:target{background:#fff8e5}.evidence summary{cursor:pointer;font-size:13px;overflow-wrap:anywhere}dl{font-size:12px}dt{color:var(--muted)}dd{margin-left:0;overflow-wrap:anywhere}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px}.limits{font-size:13px;padding-left:20px}.downloads{display:flex;gap:18px;font-size:13px;margin-top:18px}footer{margin-top:24px;font-size:12px;color:var(--muted)}[hidden]{display:none!important}
@media(max-width:1150px){.metrics{grid-template-columns:repeat(3,minmax(0,1fr))}main{padding:28px}.two-col{grid-template-columns:1fr}}@media(max-width:720px){aside{position:static;width:auto;padding:20px}aside p{margin:3px 0}nav{display:flex;gap:18px;overflow:auto}nav a{white-space:nowrap;font-size:12px}main{margin:0;padding:20px 14px}header{display:block}.date{margin-top:10px}.metrics{grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}.metric{padding:14px}.metric strong{font-size:24px}.clues{grid-template-columns:1fr}section{padding:17px}.controls{flex-direction:column}h1{font-size:27px}.topics{grid-template-columns:1fr}}
@media print{aside,.controls{display:none}main{margin:0;padding:0}section,.clue{break-inside:avoid}.clues{display:block}.clue{margin-bottom:10px}body{background:#fff}}
</style></head><body><aside><strong>本地消息 · 日报</strong><p>''' + esc(label) + '''</p><nav><a href="#overview">每日概览</a><a href="#followups">待跟进线索</a><a href="#conversations">会话分布</a><a href="#topics">主题与风险</a><a href="#evidence">查看原文</a><a href="#sources">统计范围</a></nav></aside><main>
<header><div><div class="eyebrow">''' + esc(label) + '''</div><h1>每日消息概览</h1><div class="muted">先看重点，再按需查看原文</div></div><div class="date">''' + esc(report['date']) + '''<div class="muted">北京时间</div></div></header>
<div class="metrics" id="overview">''' + metrics + '''</div><div class="notice">仅统计所选材料。待跟进与处理进展请结合原文核对。</div>
<section><h2>24 小时消息分布</h2><div class="chart-wrap"><div class="chart">''' + bars + '''</div></div></section>
<section id="followups"><div class="section-top"><h2>待跟进线索</h2><span class="muted">回看 ''' + str(report['lookback']['days']) + ''' 天（含当天），保留每条独立请求</span></div><div class="controls"><input id="clue-search" aria-label="搜索待跟进线索" placeholder="搜索会话、原文或状态"><select id="clue-filter" aria-label="筛选线索范围"><option value="all">全部线索</option><option value="personal">本人相关待跟进</option><option value="uncertain">对象或身份待确认</option><option value="signal">处理 / 取消信号</option></select></div><div class="clues">''' + (''.join(cards) or '<p class="empty">当前材料未命中待跟进规则；不代表没有待办。</p>') + '''</div><p class="empty" id="clue-empty" hidden>没有匹配的线索。</p></section>
<section id="conversations"><h2>当日活跃会话</h2><div class="table-wrap"><table><thead><tr><th>会话</th><th>类型</th><th>消息</th><th>已知发言人</th><th>本人发出</th></tr></thead><tbody>''' + (conversation_rows or '<tr><td colspan="5">当天没有可确认时间的消息。</td></tr>') + '''</tbody></table></div></section>
<section id="topics"><h2>重点主题与需关注事项</h2><p class="muted">按消息字样整理，每个主题可先查看 5 条相关原文；是否需要处理请结合上下文判断。</p><div class="two-col"><div class="topics">''' + (''.join(topics) or '<p class="empty">暂未发现相关主题。</p>') + '''</div><div><ul class="risk-list">''' + (''.join(risks) or '<li class="muted">暂未发现需提示的关键词。</li>') + '''</ul></div></div></section>
<section id="evidence"><h2>线索对应原文</h2><p class="muted">点击“查看依据”展开消息，核对发送人、时间和上下文。</p>''' + (''.join(evidence) or '<p class="empty">当前没有引用的消息。</p>') + '''</section>
<section id="sources"><h2>本次统计范围</h2><div class="table-wrap"><table><thead><tr><th>会话</th><th>类型</th><th>所选材料条数</th><th>导出日期范围</th></tr></thead><tbody>''' + source_rows + '''</tbody></table></div><ul class="limits">''' + limitations + '''</ul><details class="diagnostics"><summary>诊断日志与数据文件（排查时使用）</summary><div class="downloads"><a href="diagnostics.jsonl">诊断日志</a><a href="report.json">结构化日报</a><a href="evidence.jsonl">完整消息依据</a><a href="manifest.json">文件校验清单</a></div></details></section><footer>生成于 ''' + esc(report['createdAt'][:16].replace('T', ' ')) + ''' · 本页含聊天原文，分享前请核对范围。</footer></main>
<script>
const search=document.getElementById('clue-search'),filter=document.getElementById('clue-filter'),cards=[...document.querySelectorAll('.clue')];function apply(){const query=search.value.toLocaleLowerCase();let visible=0;for(const card of cards){card.hidden=!(card.textContent.toLocaleLowerCase().includes(query)&&(filter.value==='all'||card.dataset.group===filter.value));if(!card.hidden)visible++;}document.getElementById('clue-empty').hidden=visible>0||cards.length===0;}search.addEventListener('input',apply);filter.addEventListener('change',apply);function reveal(){const target=document.getElementById(location.hash.slice(1));if(target&&target.tagName==='DETAILS')target.open=true;}window.addEventListener('hashchange',reveal);for(const link of document.querySelectorAll('a[href^="#"]')){link.addEventListener('click',event=>{const target=document.getElementById(link.getAttribute('href').slice(1));if(target){event.preventDefault();if(target.tagName==='DETAILS')target.open=true;target.scrollIntoView({behavior:'smooth',block:'start'});}});}reveal();
</script></body></html>'''
