"""Caller-visible request/reply regressions from the earlier analysis review."""
import json
from pathlib import Path
import tempfile
import unittest

from offline_daily import build_daily_report
from test_daily_reports import DAY, formal_export, message


def conversation_export(base, name, *, client='wechat', mode='direct'):
    mention = {'mentions': {'ids': ['synthetic-owner']}}
    def reply(identifier, text, source, time):
        extra = {'kind': 'quote', 'quote': {'sourceMessageId': source}} if mode == 'group' else {}
        return message(identifier, text, own=True, time=time, **extra)
    return formal_export(base, name, [
        message('round-one', '请确认昨日报价', time='2026-10-26T09:00:00+08:00', **mention),
        reply('reply-one', '收到，正在核对', 'round-one', '2026-10-26T10:00:00+08:00'),
        message('round-two', '请安排今天发货', **mention),
        reply('reply-two', '明天再回复', 'round-two', '2026-10-27T09:30:00+08:00'),
        message('withdrawn-request', '请提供订单进度', time='2026-10-27T10:00:00+08:00', **mention),
        message('withdrawal', '取消该请求，不用了', kind='quote', time='2026-10-27T10:10:00+08:00',
                quote={'sourceMessageId': 'withdrawn-request'}),
        message('acknowledgement', '好的，收到', own=True, kind='quote', time='2026-10-27T10:20:00+08:00',
                quote={'sourceMessageId': 'withdrawn-request'}),
        message('cancel-action', '麻烦取消订单', time='2026-10-27T11:00:00+08:00', **mention),
        message('cancel-action-please', '请取消订单', time='2026-10-27T11:30:00+08:00', **mention),
    ], client=client, mode=mode)


class ConversationWorkflowTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='synthetic-daily-conversations-')
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)

    def generate(self, rows, *, name='case', client='wechat', mode='direct'):
        root = formal_export(self.base, name, rows, client=client, mode=mode)
        result = build_daily_report([root], client=client, day=DAY, output=self.base / (name + '-report'))
        return json.loads(Path(result['jsonFile']).read_text(encoding='utf-8'))

    def test_cancel_order_directives_and_questions_are_requests_in_both_clients_and_modes(self):
        for client in ('wechat', 'wecom'):
            for mode in ('direct', 'group'):
                with self.subTest(client=client, mode=mode):
                    texts = ['麻烦取消订单', '请取消订单', '能否帮忙取消订单', '订单取消了吗？']
                    report = self.generate([message(str(index), text, mentions={'ids': ['synthetic-owner']})
                                            for index, text in enumerate(texts)], name=client + mode, client=client, mode=mode)
                    self.assertEqual({item['text'] for item in report['followups']}, set(texts))
                    self.assertTrue(all(item['state'] == 'needs_response' for item in report['followups']))
                    self.assertTrue(all(item['reply'] is None for item in report['followups']))

    def test_acknowledgement_keeps_the_requesters_cancellation_and_its_evidence(self):
        for client in ('wechat', 'wecom'):
            for mode in ('direct', 'group'):
                with self.subTest(client=client, mode=mode):
                    report = self.generate([
                        message('request', '请确认报价', mentions={'ids': ['synthetic-owner']}),
                        message('cancel', '取消该请求，不用了', kind='quote', time='2026-10-27T10:00:00+08:00', quote={'sourceMessageId': 'request'}),
                        message('ack', '好的', own=True, time='2026-10-27T11:00:00+08:00',
                                **({'kind': 'quote', 'quote': {'sourceMessageId': 'request'}} if mode == 'group' else {})),
                    ], name=client + mode, client=client, mode=mode)
                    clue = report['followups'][0]
                    self.assertEqual(clue['state'], 'withdrawal_signal')
                    self.assertEqual(clue['reply']['text'], '取消该请求，不用了')
                    self.assertEqual(report['overview']['personalFollowups'], 0)

    def test_completion_evidence_survives_an_ack_but_explicit_deferral_can_correct_it(self):
        rows = [message('request', '请确认报价'),
                message('complete', '已经处理', own=True, time='2026-10-27T10:00:00+08:00'),
                message('ack', '收到', own=True, time='2026-10-27T11:00:00+08:00')]
        report = self.generate(rows)
        self.assertEqual(report['followups'][0]['state'], 'completion_signal')
        self.assertEqual(report['followups'][0]['reply']['text'], '已经处理')
        corrected = self.generate(rows + [message('correction', '还没处理，稍后回复', own=True, time='2026-10-27T12:00:00+08:00')], name='correction')
        self.assertEqual(corrected['followups'][0]['state'], 'deferred')

    def test_two_private_rounds_across_days_keep_each_request_and_its_own_response(self):
        for client in ('wechat', 'wecom'):
            with self.subTest(client=client):
                root = conversation_export(self.base, client, client=client)
                before = {path.name: path.read_bytes() for path in root.iterdir()}
                result = build_daily_report([root], client=client, day=DAY, output=self.base / (client + '-report'))
                clues = {item['text']: item for item in json.loads(Path(result['jsonFile']).read_text(encoding='utf-8'))['followups']}
                self.assertEqual(clues['请确认昨日报价']['state'], 'responded')
                self.assertEqual(clues['请确认昨日报价']['reply']['text'], '收到，正在核对')
                self.assertEqual(clues['请安排今天发货']['state'], 'deferred')
                self.assertEqual(clues['请安排今天发货']['reply']['text'], '明天再回复')
                self.assertEqual(before, {path.name: path.read_bytes() for path in root.iterdir()})

    def test_unquoted_reply_does_not_guess_between_two_unanswered_requests_or_in_a_group(self):
        for mode in ('direct', 'group'):
            report = self.generate([message('first', '请确认报价', mentions={'ids': ['synthetic-owner']}),
                                    message('second', '请安排发货', time='2026-10-27T09:10:00+08:00', mentions={'ids': ['synthetic-owner']}),
                                    message('reply', '已经处理', own=True, time='2026-10-27T10:00:00+08:00')], name=mode, mode=mode)
            self.assertTrue(all(item['state'] == 'needs_response' and item['reply'] is None for item in report['followups']))

    def test_new_cancel_action_does_not_withdraw_an_earlier_request(self):
        report = self.generate([message('first', '请确认报价'),
                                message('second', '麻烦取消订单', time='2026-10-27T09:10:00+08:00')])
        self.assertEqual(len(report['followups']), 2)
        self.assertTrue(all(item['state'] == 'needs_response' and item['reply'] is None for item in report['followups']))

    def test_explicit_reply_to_old_request_does_not_consume_the_new_pending_request(self):
        report = self.generate([
            message('first', '请确认报价'),
            message('first-reply', '稍后回复', own=True, time='2026-10-27T09:10:00+08:00'),
            message('second', '请安排发货', time='2026-10-27T09:20:00+08:00'),
            message('old-reply', '已经处理', own=True, kind='quote', quote={'sourceMessageId': 'first'}, time='2026-10-27T09:30:00+08:00'),
            message('new-reply', '明天再回复', own=True, time='2026-10-27T09:40:00+08:00')])
        clues = {item['text']: item for item in report['followups']}
        self.assertEqual(clues['请确认报价']['state'], 'completion_signal')
        self.assertEqual(clues['请安排发货']['state'], 'deferred')
        self.assertEqual(clues['请安排发货']['reply']['text'], '明天再回复')
