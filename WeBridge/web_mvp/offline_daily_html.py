"""Self-contained, escaped HTML for daily reports; no external assets."""
import html
import json


def render_report(report, records):
    esc = lambda value: html.escape(str(value), quote=True)
    label = '个人微信' if report['client'] == 'wechat' else '企业微信'
    overview = report['overview']
    by_id = {row['evidenceId']: row for row in records}
    used = set()

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
                     + '</span></div><h3>' + esc(item['chatName']) + '</h3><p class="quote">' + esc(snippet)
                     + '</p><div class="clue-foot"><span>' + esc(item['senderName']) + ' · '
                     + ('本人相关线索' if item['target'] == 'self' else '不能确认为本人待办') + '</span>'
                     + evidence_link(item['evidenceId']) + evidence_link(item['replyEvidenceId'], '后续回应依据') + '</div></article>')
    conversation_rows = ''.join('<tr><td>' + esc(item['name']) + '<small>' + esc(item['id']) + '</small></td><td>'
                                + ('群聊' if item['mode'] == 'group' else '私聊') + '</td><td>' + str(item['messages'])
                                + '</td><td>' + str(item['speakers']) + '</td><td>'
                                + ('未确认' if item['outgoing'] is None else str(item['outgoing'])) + '</td></tr>'
                                for item in report['conversations'])
    topics = []
    for item in report['topics']:
        links = ' '.join(evidence_link(identifier, '#' + str(index)) for index, identifier in enumerate(item['evidenceIds'][:5], 1))
        topics.append('<div class="topic"><strong>' + esc(item['label']) + '</strong><span>' + str(item['messages'])
                      + ' 条 · ' + str(item['conversations']) + ' 个会话</span><div>' + links + '</div></div>')
    risks = []
    for item in report['riskClues']:
        row = by_id[item['evidenceId']]
        risks.append('<li><strong>' + esc(' / '.join(item['matches'])) + '</strong> · ' + esc(row['chatName']) + ' '
                     + evidence_link(row['evidenceId']) + '<p>' + esc(row['text'][:160]) + '</p></li>')
    evidence = []
    for identifier in sorted(used, key=lambda key: (by_id[key]['time'] or '', key)):
        row = by_id[identifier]
        references = '；'.join(item['sourceId'] + ' 第 ' + str(item['line']) + ' 行' for item in row['sources'])
        content = ('<details><summary>引用、转发或附件结构（保持原归属）</summary><pre>'
                   + esc(json.dumps(row['content'], ensure_ascii=False, indent=2)) + '</pre></details>') if row['content'] else ''
        evidence.append('<details class="evidence" id="' + identifier + '"><summary>' + esc(row['time'] or '时间未知') + ' · '
                        + esc(row['chatName']) + ' · ' + esc(row['senderName']) + '</summary><p class="quote">' + esc(row['text'])
                        + '</p><dl><dt>消息 ID</dt><dd>' + esc(row['messageId']) + '</dd><dt>来源</dt><dd>' + esc(references)
                        + '</dd><dt>解析缺口</dt><dd>' + esc('；'.join(row['issueReasons']) or '未记录内容缺口') + '</dd></dl>'
                        + content + '</details>')
    source_rows = ''.join('<tr><td>' + esc(source['sourceId']) + '<small>' + esc(source['chat'].get('name') or source['chat']['id'])
                          + '</small></td><td>' + ('群聊' if source['chat']['conversationKind'] == 'group' else '私聊')
                          + '</td><td>' + str(source['exportedRows']) + '</td><td class="path">' + esc(source['root'])
                          + '<small>复核 ' + esc(source['reviewId']) + '</small></td></tr>' for source in report['sources'])
    limitations = ''.join('<li>' + esc(item) + '</li>' for item in report['limitations'])
    max_hour = max(overview['hourly'], default=0)
    bars = ''.join('<div class="hour"><span>' + (str(count) if count else '') + '</span><i style="height:'
                   + str(round(110 * count / max_hour) if max_hour else 0) + 'px"></i><small>' + f'{hour:02}' + '</small></div>'
                   for hour, count in enumerate(overview['hourly']))
    outgoing = '未确认' if overview['outgoing'] is None else str(overview['outgoing'])
    kpis = [('当日消息', overview['messages']), ('活跃会话', overview['activeConversations']), ('本人发出', outgoing),
            ('本人相关待跟进', overview['personalFollowups']), ('含内容缺口的消息', overview['messagesWithGaps']), ('历史未读', '未提供')]
    metrics = ''.join('<div class="metric"><span>' + name + '</span><strong>' + esc(value) + '</strong></div>' for name, value in kpis)
    return '''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>''' + esc(label + '每日消息整理 · ' + report['date']) + '''</title><style>
:root{--ink:#19343d;--muted:#657981;--line:#dbe5e6;--teal:#087c78;--paper:#fff;--bg:#f2f6f6}*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.65 "Microsoft YaHei","Segoe UI",sans-serif}a{color:var(--teal);text-decoration:none}a:hover{text-decoration:underline}aside{position:fixed;inset:0 auto 0 0;width:210px;background:#153b44;color:#e5f1ef;padding:32px 22px}aside strong{display:block;font-size:19px;letter-spacing:1px}aside p{font-size:12px;color:#a8c5c8}nav a{display:block;color:#d2e6e4;padding:10px 0}main{margin-left:210px;padding:35px 42px 70px;max-width:1600px}header{display:flex;justify-content:space-between;gap:20px;align-items:flex-start;margin-bottom:26px}.eyebrow{font-size:12px;color:var(--teal);font-weight:700;letter-spacing:2px}h1{font-size:31px;margin:5px 0}h2{font-size:20px;margin:0 0 18px}h3{font-size:16px;margin:10px 0}.muted,small{color:var(--muted);font-size:12px}.date{font-size:18px;font-weight:600;white-space:nowrap}.metrics{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:12px}.metric{background:var(--paper);border:1px solid var(--line);border-radius:12px;padding:18px 16px}.metric span{display:block;font-size:12px;color:var(--muted)}.metric strong{display:block;font-size:27px;margin-top:6px}.notice{border-left:3px solid #d49a37;background:#fff8e9;padding:12px 16px;margin:20px 0 26px;font-size:13px}section{margin-top:28px;background:var(--paper);border:1px solid var(--line);border-radius:14px;padding:23px;scroll-margin-top:18px}.section-top{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap}.controls{display:flex;gap:10px;margin-bottom:18px}input,select{font:inherit;font-size:13px;padding:9px 11px;border:1px solid #b9ccce;border-radius:6px;background:#fff;color:var(--ink)}input{min-width:0;flex:1}.clues{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}.clue{border:1px solid var(--line);border-radius:10px;padding:17px;min-width:0}.clue-top,.clue-foot{display:flex;justify-content:space-between;gap:8px;flex-wrap:wrap;font-size:12px}.badge{font-size:11px;border-radius:4px;padding:3px 7px}.personal{background:#e4f3ef;color:#166855}.uncertain{background:#fff0d6;color:#91620c}.signal{background:#e8eef9;color:#465f91}.quote{white-space:pre-wrap;overflow-wrap:anywhere;background:#f5f8f8;border-radius:7px;padding:12px;font-size:13px}.clue-foot{color:var(--muted)}.evidence-link{font-size:12px;margin-left:8px}.chart{height:158px;display:flex;align-items:flex-end;gap:6px;min-width:530px}.chart-wrap,.table-wrap{overflow:auto}.hour{flex:1;display:flex;align-items:center;flex-direction:column;justify-content:flex-end;min-width:15px}.hour i{display:block;width:80%;background:#409c96;border-radius:3px 3px 0 0}.hour span{font-size:10px;min-height:19px}.hour small{font-size:10px;margin-top:5px}.two-col{display:grid;grid-template-columns:1fr 1fr;gap:20px}.topics{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}.topic{background:#f4f8f8;padding:13px;border-radius:8px}.topic strong,.topic span{display:block}.topic span{color:var(--muted);font-size:12px}.risk-list{padding-left:20px}.risk-list p{font-size:13px;color:var(--muted);overflow-wrap:anywhere}table{border-collapse:collapse;width:100%;font-size:13px}th,td{padding:12px 10px;text-align:left;border-bottom:1px solid var(--line);vertical-align:top}th{font-size:12px;color:var(--muted);font-weight:500}td small{display:block;overflow-wrap:anywhere}.path{max-width:380px;overflow-wrap:anywhere}.empty{color:var(--muted);background:#f5f8f8;padding:20px;border-radius:8px}.evidence{border-top:1px solid var(--line);padding:13px 0}.evidence:target{background:#fff8e5}.evidence summary{cursor:pointer;font-size:13px;overflow-wrap:anywhere}dl{font-size:12px}dt{color:var(--muted)}dd{margin-left:0;overflow-wrap:anywhere}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px}.limits{font-size:13px;padding-left:20px}.downloads{display:flex;gap:18px;font-size:13px;margin-top:18px}footer{margin-top:24px;font-size:12px;color:var(--muted)}[hidden]{display:none!important}
@media(max-width:1150px){.metrics{grid-template-columns:repeat(3,minmax(0,1fr))}main{padding:28px}.two-col{grid-template-columns:1fr}}@media(max-width:720px){aside{position:static;width:auto;padding:20px}aside p{margin:3px 0}nav{display:flex;gap:18px;overflow:auto}nav a{white-space:nowrap;font-size:12px}main{margin:0;padding:20px 14px}header{display:block}.date{margin-top:10px}.metrics{grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}.metric{padding:14px}.metric strong{font-size:24px}.clues{grid-template-columns:1fr}section{padding:17px}.controls{flex-direction:column}h1{font-size:27px}.topics{grid-template-columns:1fr}}
@media print{aside,.controls{display:none}main{margin:0;padding:0}section,.clue{break-inside:avoid}.clues{display:block}.clue{margin-bottom:10px}body{background:#fff}}
</style></head><body><aside><strong>本地消息 · 日报</strong><p>''' + esc(label) + ''' / DAILY BRIEF</p><nav><a href="#overview">每日概览</a><a href="#followups">待跟进线索</a><a href="#conversations">会话分布</a><a href="#topics">主题与风险</a><a href="#evidence">原文证据</a><a href="#sources">范围与来源</a></nav></aside><main>
<header><div><div class="eyebrow">LOCAL MESSAGE REVIEW</div><h1>每日消息概览</h1><div class="muted">已复核材料 · 原文可追溯 · 所有线索待人工判断</div></div><div class="date">''' + esc(report['date']) + '''<div class="muted">北京时间 / UTC+08:00</div></div></header>
<div class="metrics" id="overview">''' + metrics + '''</div><div class="notice">统计覆盖所选导出，不代表账号的全部消息。待跟进基于字面规则；暂缓、回复、完成和取消分别展示，不自动认定业务已办结。</div>
<section><h2>24 小时消息分布</h2><div class="chart-wrap"><div class="chart">''' + bars + '''</div></div></section>
<section id="followups"><div class="section-top"><h2>待跟进线索</h2><span class="muted">回看 ''' + str(report['lookback']['days']) + ''' 天（含当天），保留每条独立请求</span></div><div class="controls"><input id="clue-search" aria-label="搜索待跟进线索" placeholder="搜索会话、原文或状态"><select id="clue-filter" aria-label="筛选线索范围"><option value="all">全部线索</option><option value="personal">本人相关待跟进</option><option value="uncertain">对象或身份待确认</option><option value="signal">处理 / 取消信号</option></select></div><div class="clues">''' + (''.join(cards) or '<p class="empty">当前材料未命中待跟进规则；不代表没有待办。</p>') + '''</div><p class="empty" id="clue-empty" hidden>没有匹配的线索。</p></section>
<section id="conversations"><h2>当日活跃会话</h2><div class="table-wrap"><table><thead><tr><th>会话 / ID</th><th>类型</th><th>消息</th><th>已知发言人</th><th>本人发出</th></tr></thead><tbody>''' + (conversation_rows or '<tr><td colspan="5">当天没有可确认时间的消息。</td></tr>') + '''</tbody></table></div></section>
<section id="topics"><h2>主题与风险关键词</h2><p class="muted">主题按固定词组命中统计；风险只表示出现相关字样，不能替代上下文判断。主题展示前 5 条依据，全部引用保留在 report.json。</p><div class="two-col"><div class="topics">''' + (''.join(topics) or '<p class="empty">未命中主题词组。</p>') + '''</div><div><ul class="risk-list">''' + (''.join(risks) or '<li class="muted">未命中风险词。</li>') + '''</ul></div></div></section>
<section id="evidence"><h2>线索原文与出处</h2><p class="muted">点击依据自动展开对应消息。这里展示线索引用的消息；全部纳入回看范围的消息保存在 evidence.jsonl，完整原始导出保留原位置。</p>''' + (''.join(evidence) or '<p class="empty">当前没有引用的消息。</p>') + '''</section>
<section id="sources"><h2>范围、来源与限制</h2><div class="table-wrap"><table><thead><tr><th>来源</th><th>类型</th><th>原导出条数</th><th>目录与复核 ID</th></tr></thead><tbody>''' + source_rows + '''</tbody></table></div><ul class="limits">''' + limitations + '''</ul><div class="downloads"><a href="report.json">日报 JSON</a><a href="evidence.jsonl">全部证据 JSONL</a><a href="manifest.json">文件校验清单</a></div></section><footer>版本 ''' + esc(report['version']) + ' · 生成于 ' + esc(report['createdAt']) + ''' · 本页含消息片段，转发前请核对范围。</footer></main>
<script>
const search=document.getElementById('clue-search'),filter=document.getElementById('clue-filter'),cards=[...document.querySelectorAll('.clue')];function apply(){const query=search.value.toLocaleLowerCase();let visible=0;for(const card of cards){card.hidden=!(card.textContent.toLocaleLowerCase().includes(query)&&(filter.value==='all'||card.dataset.group===filter.value));if(!card.hidden)visible++;}document.getElementById('clue-empty').hidden=visible>0||cards.length===0;}search.addEventListener('input',apply);filter.addEventListener('change',apply);function reveal(){const target=document.getElementById(location.hash.slice(1));if(target&&target.tagName==='DETAILS')target.open=true;}window.addEventListener('hashchange',reveal);for(const link of document.querySelectorAll('a[href^="#"]')){link.addEventListener('click',event=>{const target=document.getElementById(link.getAttribute('href').slice(1));if(target){event.preventDefault();if(target.tagName==='DETAILS')target.open=true;target.scrollIntoView({behavior:'smooth',block:'start'});}});}reveal();
</script></body></html>'''
