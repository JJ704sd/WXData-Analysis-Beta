"""Keep useful quoted material and card fields visible at the analysis seam."""
import json
from pathlib import Path
import tempfile
import unittest

from offline_analysis import AnalysisWriter, content, _body


class AnalysisCoverage55Tests(unittest.TestCase):
    def test_quoted_attachment_and_forwarded_quote_retain_content_and_authorship(self):
        document = {'kind': 'file', 'text': '运输条款.docx',
                    'attachmentContent': {'fileStatus': 'ready', 'extractionStatus': 'extracted',
                                          'text': '目的地深圳，运费123元', 'characters': 13}}
        quote = {'senderId': 'third-party', 'senderName': '材料提供人', 'message': document}
        rows = [
            {'id': 'quote', 'kind': 'quote', 'text': '请核对附件', 'quote': quote},
            {'id': 'forward', 'kind': 'record', 'text': '转发', 'record': {'status': 'ok', 'items': [
                {'kind': 'quote', 'text': '请核对附件', 'senderName': '转发内发言人', 'quote': quote}]}}
        ]
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with AnalysisWriter(root, {'id': 'peer', 'name': '合成私聊'}, conversation_mode='direct') as writer:
                for row in rows:
                    writer.add({**row, 'senderId': 'peer', 'senderName': '当前对方', 'isSelf': False,
                                'timestamp': 1700000000, 'time': '2023-11-15T06:13:20+08:00'})
                writer.finish()
            corpus = [json.loads(line) for line in (root/'analysis/corpus.jsonl').read_text(encoding='utf8').splitlines()]
            quoted = corpus[0]['quoted']
            self.assertEqual(quoted['content']['attachment']['text'], document['attachmentContent']['text'])
            self.assertEqual(quoted['senderId'], 'third-party')
            self.assertEqual(corpus[0]['senderId'], 'peer')
            self.assertIsNone(corpus[0]['replyTo'])
            self.assertEqual(corpus[1]['forward']['items'][0]['quoted']['content']['attachment']['text'],
                             document['attachmentContent']['text'])
            part = (root/'analysis/part-001.md').read_text(encoding='utf8')
            self.assertIn('运费123元', part)
            self.assertIn('材料提供人', part)
            self.assertIn('引用内容', part)

    def test_card_fields_are_preserved_without_creating_links_or_identity_claims(self):
        node = {'kind': 'miniprogram', 'text': '会议预约', 'links': [],
                'miniProgram': {'appId': 'synthetic-app', 'username': 'synthetic-user',
                                'pagePath': 'pages/meeting?id=42'}}
        result = content(node)
        self.assertEqual(result['miniProgram'], node['miniProgram'])
        self.assertIn('pages/meeting?id=42', '\n'.join(_body(result)))
        self.assertFalse(result.get('links'))


if __name__ == '__main__':
    unittest.main()
