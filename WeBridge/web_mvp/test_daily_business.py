"""Business-facing completeness and navigation for local daily reports."""
import json
from html.parser import HTMLParser
from pathlib import Path
import tempfile
import unittest

from offline_daily import build_daily_report
from offline_daily_html import render_followups_text
from test_daily_reports import DAY, formal_export, message


def gap_export(base, name, *, client='wechat'):
    return formal_export(base, name, [
        message('document', '对账附件.pdf', kind='file',
                attachmentContent={'extractionStatus': 'partial', 'text': '附件独有词 <b>37/KG</b>'}),
        message('forward', '转发记录', kind='record', time='2026-10-26T18:00:00+08:00',
                record={'status': 'item_limit', 'count': 3, 'items': [
                    {'kind': 'text', 'text': '转发独有词，请尽快安排发货', 'senderName': '原作者'}]}),
        message('undated', '原图待补齐', kind='image', time='invalid-time'),
        message('normal', '普通确认')], client=client)


def followup_export(base, name, *, client='wechat', mode='direct'):
    return formal_export(base, name, [
        message('trace-request-a', '请确认报价 ' + '业务正文 ' * 50 + '请求尾部独有词', mentions={'ids': ['synthetic-owner']}),
        message('trace-request-b', '请确认方案', time='2026-10-27T09:01:00+08:00', mentions={'ids': ['synthetic-owner']}),
        message('trace-request-c', '请安排发货', time='2026-10-27T09:02:00+08:00'),
        message('trace-reply-a', '稍后回复，等待确认独有词', own=True, kind='quote', time='2026-10-27T10:00:00+08:00',
                quote={'sourceMessageId': 'trace-request-a', 'message': {'text': '被引用第三方原文独有词', 'senderName': '第三方'}}),
        message('trace-reply-b', '已处理，处理结果独有词', own=True, kind='quote', time='2026-10-27T11:00:00+08:00',
                quote={'sourceMessageId': 'trace-request-b'},
                attachmentContent={'extractionStatus': 'partial', 'text': '附件作者的正文'})], client=client, mode=mode)


class GapCards(HTMLParser):
    def __init__(self, source):
        super().__init__()
        self.cards = []
        self.clues = []
        self.gap_options = []
        self.in_gap_select = False
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == 'article' and values.get('class') == 'gap-card':
            self.cards.append(values)
        if tag == 'article' and values.get('class') == 'clue':
            self.clues.append(values)
        if tag == 'select' and values.get('id') == 'gap-filter':
            self.in_gap_select = True
        if tag == 'option' and self.in_gap_select:
            self.gap_options.append(values.get('value'))

    def handle_endtag(self, tag):
        if tag == 'select':
            self.in_gap_select = False


class DailyBusinessTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='synthetic-daily-business-')
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)

    def test_followup_previews_keep_the_matched_reply_and_its_content_gaps(self):
        for client in ('wechat', 'wecom'):
            for mode in ('direct', 'group'):
                with self.subTest(client=client, mode=mode):
                    name = client + '-' + mode
                    source = followup_export(self.base, name, client=client, mode=mode)
                    result = build_daily_report([source], client=client, day=DAY, output=self.base / (name + '-report'))
                    report = json.loads(Path(result['jsonFile']).read_text(encoding='utf-8'))
                    clues = {item['text'][:5]: item for item in report['followups']}
                    self.assertTrue(all(item['conversationMode'] == mode for item in report['followups']))
                    deferred = clues['请确认报价']
                    self.assertEqual(deferred['state'], 'deferred')
                    self.assertEqual(deferred['reply']['text'], '稍后回复，等待确认独有词')
                    self.assertEqual(deferred['reply']['senderName'], '合成本人')
                    self.assertEqual(deferred['reply']['time'], '2026-10-27T10:00:00+08:00')
                    self.assertNotIn('被引用第三方原文独有词', deferred['reply']['text'])
                    completed = clues['请确认方案']
                    self.assertEqual(completed['state'], 'completion_signal')
                    self.assertIn('document_partial', completed['reply']['contentIssues'])
                    self.assertIsNone(clues['请安排发货']['reply'])

    def test_business_checklist_contains_full_requests_replies_and_warnings_without_trace_ids(self):
        for client in ('wechat', 'wecom'):
            with self.subTest(client=client):
                source = followup_export(self.base, client, client=client, mode='group')
                before = {path.name: path.read_bytes() for path in source.iterdir()}
                result = build_daily_report([source], client=client, day=DAY, output=self.base / (client + '-report'))
                path = Path(result['followupsFile'])
                text = path.read_text(encoding='utf-8-sig')
                for value in ('本人相关待跟进', '对象或身份待确认', '处理 / 取消待核对',
                              '请求尾部独有词', '稍后回复，等待确认独有词', '已处理，处理结果独有词',
                              '后续回应提示：附件正文尚未完整读取', '候选线索须人工核对'):
                    self.assertIn(value, text)
                for value in ('trace-request', 'trace-reply', 'synthetic-owner', 'evidenceId', '被引用第三方原文独有词'):
                    self.assertNotIn(value, text)
                manifest = json.loads((path.parent / 'manifest.json').read_text(encoding='utf-8'))
                self.assertIn('followups.txt', {item['path'] for item in manifest['files']})
                self.assertEqual(before, {item.name: item.read_bytes() for item in source.iterdir()})

    def test_checklist_is_not_limited_to_the_desktops_first_500_matches(self):
        source = formal_export(self.base, 'many', [message('request-' + str(index), f'请确认报价，项目 {index:03}') for index in range(505)])
        result = build_daily_report([source], client='wechat', day=DAY, output=self.base / 'many-report')
        self.assertEqual(result['overview']['personalFollowups'], 505)
        text = Path(result['followupsFile']).read_text(encoding='utf-8-sig')
        self.assertEqual(text.count('请求：请确认报价，项目'), 505)
        self.assertIn('项目 504', text)

    def test_filtered_checklist_contains_only_matches_and_records_the_selected_scope(self):
        source = followup_export(self.base, 'filtered', mode='group')
        result = build_daily_report([source], client='wechat', day=DAY, output=self.base / 'filtered-report')
        report = json.loads(Path(result['jsonFile']).read_text(encoding='utf-8'))
        before = json.dumps(report, ensure_ascii=True)
        selected = [item for item in report['followups'] if item['state'] == 'deferred']
        text = render_followups_text(report, items=selected, filters=['仅群聊', '本人相关待跟进', '关键词：等待确认独有词'])
        for value in ('当前筛选清单', '匹配 1 / 全部 3 条', '仅群聊', '关键词：等待确认独有词',
                      '请求尾部独有词', '稍后回复，等待确认独有词', '候选线索须人工核对'):
            self.assertIn(value, text)
        self.assertNotIn('处理结果独有词', text)
        self.assertNotIn('trace-request', text)
        self.assertEqual(before, json.dumps(report, ensure_ascii=True))
        empty = render_followups_text(report, items=[], filters=['关键词：没有匹配'])
        self.assertIn('匹配 0 / 全部 3 条', empty)
        self.assertIn('当前筛选下没有线索', empty)
        self.assertNotIn('请求：', empty)

    def test_signal_with_unknown_target_appears_once_in_the_checklist(self):
        source = followup_export(self.base, 'unknown-signal', mode='group')
        result = build_daily_report([source], client='wechat', day=DAY, output=self.base / 'unknown-signal-report')
        report = json.loads(Path(result['jsonFile']).read_text(encoding='utf-8'))
        item = next(item for item in report['followups'] if item['state'] == 'completion_signal')
        item['target'] = 'unknown'
        report['followups'] = [item]
        text = render_followups_text(report)
        self.assertEqual(text.count('请求：请确认方案'), 1)
        self.assertIn('对象或身份待确认（0 条）', text)
        self.assertIn('处理 / 取消待核对（1 条）', text)

    def test_html_clues_search_complete_requests_and_matched_replies_by_conversation_mode(self):
        source = followup_export(self.base, 'business-html', mode='group')
        result = build_daily_report([source], client='wechat', day=DAY, output=self.base / 'business-html-report')
        html = Path(result['htmlFile']).read_text(encoding='utf-8')
        cards = GapCards(html).clues
        self.assertEqual(len(cards), 3)
        self.assertTrue(all(card['data-mode'] == 'group' for card in cards))
        self.assertTrue(any('请求尾部独有词' in card['data-search'] and '等待确认独有词' in card['data-search'] for card in cards))
        self.assertTrue(all('被引用第三方原文独有词' not in card['data-search'] for card in cards))
        self.assertEqual(html.count('class="reply-preview"'), 2)
        self.assertIn('href="followups.txt"', html)
        self.assertIn('id="clue-mode"', html)
        self.assertIn('id="clue-download-filtered"', html)
        self.assertIn('id="clue-count"', html)

    def test_empty_selected_day_warns_without_discarding_earlier_followups(self):
        for client in ('wechat', 'wecom'):
            with self.subTest(client=client):
                source = formal_export(self.base, client, [
                    message('yesterday', '请确认报价', time='2026-10-26T18:00:00+08:00')], client=client)
                before = {path.name: path.read_bytes() for path in source.iterdir()}
                result = build_daily_report([source], client=client, day=DAY, output=self.base / (client + '-report'))
                self.assertTrue(result['ok'])
                self.assertEqual(result['overview']['messages'], 0)
                self.assertEqual(result['overview']['personalFollowups'], 1)
                self.assertTrue(any('未找到当日消息' in notice and '日期' in notice for notice in result['notices']))
                report = json.loads(Path(result['jsonFile']).read_text(encoding='utf-8'))
                self.assertEqual(report['notices'], result['notices'])
                html = Path(result['htmlFile']).read_text(encoding='utf-8')
                self.assertIn('不能据此判断当天没有消息', html)
                self.assertLess(html.index('未找到当日消息'), html.index('id="overview"'))
                self.assertEqual(before, {path.name: path.read_bytes() for path in source.iterdir()})

    def test_all_content_gaps_can_be_found_without_promoting_nested_words_to_requests(self):
        for client in ('wechat', 'wecom'):
            with self.subTest(client=client):
                source = gap_export(self.base, client, client=client)
                result = build_daily_report([source], client=client, day=DAY, output=self.base / (client + '-report'))
                report = json.loads(Path(result['jsonFile']).read_text(encoding='utf-8'))
                self.assertEqual(report['overview']['messages'], 2)
                self.assertEqual(report['overview']['messagesWithGaps'], 1)
                self.assertEqual(report['followups'], [])
                self.assertEqual(report['riskClues'], [])
                self.assertEqual({item['dateScope'] for item in report['contentGaps']}, {'daily', 'lookback', 'undated'})
                self.assertEqual(len(report['contentGaps']), 3)
                html = Path(result['htmlFile']).read_text(encoding='utf-8')
                parsed = GapCards(html)
                cards = parsed.cards
                self.assertEqual(len(cards), 3)
                self.assertEqual(parsed.gap_options, ['all', 'daily', 'lookback', 'undated'])
                self.assertTrue(any('附件独有词 <b>37/KG</b>' in card['data-search'] for card in cards))
                self.assertTrue(any('转发独有词' in card['data-search'] for card in cards))
                self.assertNotIn('<b>37/KG</b>', html)
                self.assertIn('id="content-gaps"', html)
                for item in report['contentGaps']:
                    self.assertIn('href="#' + item['evidenceId'] + '"', html)
                    self.assertIn('id="' + item['evidenceId'] + '"', html)


if __name__ == '__main__':
    unittest.main()
