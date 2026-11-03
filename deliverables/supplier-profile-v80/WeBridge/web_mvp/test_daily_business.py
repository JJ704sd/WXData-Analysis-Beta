"""Business-facing completeness and navigation for local daily reports."""
import json
from html.parser import HTMLParser
from pathlib import Path
import tempfile
import unittest

from offline_daily import build_daily_report
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


class GapCards(HTMLParser):
    def __init__(self, source):
        super().__init__()
        self.cards = []
        self.gap_options = []
        self.in_gap_select = False
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == 'article' and values.get('class') == 'gap-card':
            self.cards.append(values)
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
