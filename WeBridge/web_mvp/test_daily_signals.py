"""Signal polarity and sentence-boundary regressions on reviewed exports."""
import json
from pathlib import Path
import tempfile
import unittest

from offline_daily import build_daily_report
from test_daily_reports import DAY, formal_export, message


def signal_export(base, name, *, client='wechat', mode='direct'):
    pairs = [('请确认报价', '不要取消该请求', False),
             ('请处理发票', '已经处理了吗？', True),
             ('请安排发货', '没有问题，已经处理好了', True),
             ('请提供方案', '不用稍后回复，已经处理好了', True),
             ('请确认验收进度', '尚未完成验收', True),
             ('请确认订单', '不用了，请不要再发送', False)]
    rows = []
    for index, (request, response, own) in enumerate(pairs):
        identifier = 'signal-request-' + str(index)
        rows += [message(identifier, request, time=f'2026-10-27T09:{index * 2:02}:00+08:00', mentions={'ids': ['synthetic-owner']}),
                 message('signal-reply-' + str(index), response, own=own, kind='quote',
                         time=f'2026-10-27T09:{index * 2 + 1:02}:00+08:00', quote={'sourceMessageId': identifier})]
    return formal_export(base, name, rows, client=client, mode=mode)


class SignalPolarityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='synthetic-daily-signals-')
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)

    def generate(self, response, *, client='wechat', mode='direct', own=True, preceding=()):
        name = str(len(list(self.base.iterdir())))
        rows = [message('request', '请确认报价', mentions={'ids': ['synthetic-owner']}), *preceding,
                message('reply', response, own=own, kind='quote', time='2026-10-27T12:00:00+08:00', quote={'sourceMessageId': 'request'})]
        root = formal_export(self.base, name, rows, client=client, mode=mode)
        before = {path.name: path.read_bytes() for path in root.iterdir()}
        result = build_daily_report([root], client=client, day=DAY, output=self.base / (name + '-report'))
        self.assertEqual(before, {path.name: path.read_bytes() for path in root.iterdir()})
        report = json.loads(Path(result['jsonFile']).read_text(encoding='utf-8'))
        return next(item for item in report['followups'] if item['text'] == '请确认报价')

    def test_negated_withdrawal_does_not_cancel_the_original_request(self):
        for client in ('wechat', 'wecom'):
            for mode in ('direct', 'group'):
                for text in ('不要取消订单', '别取消该请求', '没有取消这笔订单', '不是说不用了'):
                    with self.subTest(client=client, mode=mode, text=text):
                        clue = self.generate(text, client=client, mode=mode, own=False)
                        self.assertEqual(clue['state'], 'needs_response')
                        self.assertIsNone(clue['replyEvidenceId'])

    def test_completion_questions_and_conditions_remain_unconfirmed_responses(self):
        for client in ('wechat', 'wecom'):
            for mode in ('direct', 'group'):
                for text in ('如果已经处理，请告诉我', '已经处理了吗？', '问题是否解决了？', '问题如果解决了再说'):
                    with self.subTest(client=client, mode=mode, text=text):
                        clue = self.generate(text, client=client, mode=mode)
                        self.assertEqual(clue['state'], 'responded')
                        self.assertEqual(clue['reply']['text'], text)

    def test_unrelated_negative_clause_does_not_erase_affirmative_completion(self):
        for text in ('没有问题，已经处理好了', '没有异常；问题已经解决', '是否已经处理？我已完成'):
            with self.subTest(text=text):
                self.assertEqual(self.generate(text)['state'], 'completion_signal')

    def test_explicit_unfinished_response_corrects_an_earlier_completion_signal(self):
        earlier = [message('complete', '已经处理好了', own=True, time='2026-10-27T10:00:00+08:00')]
        for text in ('尚未完成验收', '没有处理', '未能发送', '没完成'):
            with self.subTest(text=text):
                clue = self.generate(text, preceding=earlier)
                self.assertEqual(clue['state'], 'deferred')
                self.assertEqual(clue['reply']['text'], text)

    def test_negated_deferral_does_not_hide_completed_work(self):
        self.assertEqual(self.generate('不用稍后回复，已经处理好了')['state'], 'completion_signal')

    def test_affirmative_withdrawal_survives_an_unrelated_negative_clause(self):
        for text in ('不用了，请不要再发送', '不着急，先不用处理了'):
            with self.subTest(text=text):
                clue = self.generate(text, own=False)
                self.assertEqual(clue['state'], 'withdrawal_signal')
                self.assertEqual(clue['reply']['text'], text)
