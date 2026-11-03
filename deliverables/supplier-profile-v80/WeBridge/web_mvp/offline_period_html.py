"""Offline period report: scrollable tables, searchable evidence and citations."""
import html

from offline_daily import display_text
from offline_period import readable_message


def render_report(report, records):
    esc = lambda value: html.escape(display_text(value), quote=True)
    def cite(identifier, label='查看原文'):
        return '<a href="#' + esc(identifier) + '">' + esc(label) + '</a>'
    scope = report['scope']
    overview = report['overview']
    notices = ''.join('<li>' + esc(value) + '</li>' for value in report['notices'])
    conversations = ''.join('<tr><td>' + esc(item['name']) + '</td><td>'
        + ('私聊' if item['mode'] == 'direct' else '群聊') + '</td><td>' + str(item['messages'])
        + '</td><td>' + str(item['speakers']) + '</td><td>'
        + ('身份未确认' if item['outgoing'] is None else str(item['outgoing'])) + '</td></tr>' for item in report['conversations'])
    topics = ''.join('<li>' + esc(item['label']) + '：' + str(item['messages']) + ' 条 · '
        + ' '.join(cite(identifier, str(index)) for index, identifier in enumerate(item['evidenceIds'], 1))
        + '</li>' for item in report['topics'])
    followups = ''.join('<li><strong>' + esc(item['stateLabel']) + '</strong> · ' + esc(item['chatName'])
        + ' · ' + esc(item['senderName']) + '<p>' + esc(item['text']) + '</p>' + cite(item['evidenceId'])
        + (' · ' + cite(item['replyEvidenceId'], '后续回应') if item['replyEvidenceId'] else '') + '</li>' for item in report['followups'])
    risks = ''.join('<li>' + esc('、'.join(item['matches'])) + ' · ' + cite(item['evidenceId']) + '</li>' for item in report['riskClues'])
    gaps = ''.join('<li>' + ('时段内' if item['dateScope'] == 'period' else '时间待确认') + ' · '
        + esc('；'.join(item['reasons'])) + ' · ' + cite(item['evidenceId']) + '</li>' for item in report['contentGaps'])
    messages = []
    for row in records:
        heading = (row['time'] or '时间待确认，未计入本时段') + ' · ' + row['chatName'] + ' · ' + row['senderName']
        body = readable_message(row)
        messages.append('<details class="message" id="' + row['evidenceId'] + '" data-time="'
            + ('dated' if row['time'] else 'undated') + '"><summary>' + esc(heading) + '</summary><pre>'
            + esc(body) + '</pre><p class="muted">引用：' + esc(row['evidenceId']) + ' · 原消息：'
            + esc(row['messageId']) + '</p></details>')
    days = ''.join('<tr><td>' + esc(day) + '</td><td>' + str(count) + '</td></tr>' for day, count in overview['byDay'].items())
    limits = ''.join('<li>' + esc(value) + '</li>' for value in report['limitations'])
    return ('''<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>时段信息分析</title>
<style>body{font:15px/1.7 "Microsoft YaHei",sans-serif;color:#24334a;background:#f3f5f9;margin:0}
main{max-width:1100px;margin:auto;padding:24px}section{background:white;padding:20px;margin:16px 0;border-radius:8px}
h1,h2{line-height:1.4}a{color:#215be4}pre{font:inherit;white-space:pre-wrap;overflow-wrap:anywhere}
.table{overflow:auto}table{border-collapse:collapse;min-width:540px;width:100%}td,th{text-align:left;padding:8px;border-bottom:1px solid #ddd}
.muted{color:#687489}li{margin:8px 0;overflow-wrap:anywhere}summary{cursor:pointer;padding:10px;background:#f1f5ff;overflow-wrap:anywhere}
details{margin:10px 0;scroll-margin-top:20px}details:target{outline:2px solid #215be4}input,select{padding:9px;max-width:100%;box-sizing:border-box}
.stats{display:flex;gap:24px;flex-wrap:wrap}.notice{background:#fff3d6}button{padding:8px;cursor:pointer}[hidden]{display:none!important}
</style><main><h1>时段信息分析</h1><p>北京时间：''' + esc(scope['startInput']) + ' 至 ' + esc(scope['endInput']) + '''</p>
<p class="muted">日期含结束当天；结束时间含所填分钟或秒。选择私聊联系人即分析双方对话，选择群聊即分析整个群。</p>
<nav><a href="#clues">主题与跟进</a> · <a href="#gaps">待补充信息</a> · <a href="#messages">原文依据</a></nav>
<section class="notice"><ul>''' + (notices or '<li>以下统计仅覆盖本次选择的正式导出。</li>') + '''</ul></section>
<section><div class="stats"><strong>时段消息 ''' + str(overview['messages']) + '</strong><span>选择对象 '
        + str(overview['selectedConversations']) + '</span><span>内容待核对 ' + str(overview['messagesWithGaps'])
        + '</span><span>时间待确认 ' + str(overview['undatedMessages']) + '''</span></div>
<div class="table"><table><thead><tr><th>对象</th><th>类型</th><th>消息</th><th>已知发言人</th><th>本人发出</th></tr></thead><tbody>'''
        + conversations + '</tbody></table></div><details><summary>按日统计</summary><div class="table"><table><tbody>'
        + days + '''</tbody></table></div></details></section>
<section id="clues"><h2>关键词主题线索</h2><ul>''' + (topics or '<li>未命中预设主题词，可在原文中继续查找。</li>')
        + '</ul><h2>待跟进与回应线索</h2><p class="muted">只在所选时段内查找请求和回应；回复、完成及取消均需人工核对。</p><ul>'
        + (followups or '<li>未识别到请求线索。</li>') + '</ul><h2>紧急等用词线索</h2><ul>'
        + (risks or '<li>未命中预设用词。</li>') + '</ul></section><section id="gaps"><h2>待补充信息</h2><ul>'
        + (gaps or '<li>当前材料未记录内容缺口。</li>') + '''</ul></section>
<section id="messages"><h2>原文依据</h2><input id="search" type="search" placeholder="搜索正文、附件、发送人或会话">
<select id="time"><option value="all">全部依据</option><option value="dated">时段内</option><option value="undated">时间待确认</option></select>
<p id="count" class="muted"></p>''' + ''.join(messages) + '''</section><section><h2>范围与统计说明</h2><ul>'''
        + limits + '''</ul><p>原件保留在正式导出目录；完整原始字段、出处及复核编号见 evidence.jsonl 和 report.json。</p></section></main>
<script>
const search=document.getElementById('search'), time=document.getElementById('time');
const rows=[...document.querySelectorAll('.message')];
function filter(){const q=search.value.toLocaleLowerCase();let count=0;rows.forEach(row=>{
row.hidden=!(row.textContent.toLocaleLowerCase().includes(q)&&(time.value==='all'||row.dataset.time===time.value));
if(!row.hidden)count++;});document.getElementById('count').textContent='显示 '+count+' 条依据';}
function reveal(){const row=document.getElementById(location.hash.slice(1));if(row&&row.classList.contains('message')){
search.value='';time.value='all';filter();row.open=true;row.scrollIntoView();}}
search.addEventListener('input',filter);time.addEventListener('change',filter);window.addEventListener('hashchange',reveal);filter();reveal();
document.querySelectorAll('a[href^="#m-"]').forEach(link=>link.addEventListener('click',()=>{
const row=document.getElementById(link.hash.slice(1));if(row){search.value='';time.value='all';filter();row.open=true;}}));
</script></html>''')


def markdown_report(report, records):
    scope = report['scope']
    lines = ['# 时段信息分析', '', '北京时间：' + scope['startInput'] + ' 至 ' + scope['endInput'], '',
             '时段消息：' + str(report['overview']['messages']) + '；时间待确认：' + str(report['overview']['undatedMessages']), '']
    lines += ['- ' + value for value in report['notices']]
    lines += ['', '## 对象与统计', '']
    for item in report['conversations']:
        lines.append('- ' + item['name'] + '：' + str(item['messages']) + ' 条；ID：' + item['id'])
    lines += ['', '## 关键词主题线索', '']
    for item in report['topics']:
        lines.append('- ' + item['label'] + '：' + '、'.join(item['evidenceIds']))
    lines += ['', '## 待跟进与回应线索（需核对）', '']
    for item in report['followups']:
        lines += ['- ' + item['stateLabel'] + ' · ' + item['evidenceId']
                  + (' → ' + item['replyEvidenceId'] if item['replyEvidenceId'] else ''),
                  '> ' + item['text'].replace('\n', '\n> ')]
    lines += ['', '## 原文依据', '']
    for row in records:
        lines += ['### ' + row['evidenceId'], '',
                  '> ' + (row['time'] or '时间待确认，未计入本时段') + ' · ' + display_text(row['chatName']) + ' · ' + display_text(row['senderName']),
                  '> ' + readable_message(row).replace('\n', '\n> '), '']
    lines += ['## 范围与统计说明', ''] + ['- ' + display_text(value) for value in report['limitations']]
    return display_text('\n'.join(lines)) + '\n'
