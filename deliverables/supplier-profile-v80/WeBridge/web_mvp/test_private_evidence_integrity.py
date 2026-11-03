"""Malformed export/candidate boundaries using fully rehashed synthetic fixtures."""
import hashlib
import json
import unittest

import test_private_profiles as fixtures
from private_profiles import ProfileError


class EvidenceIntegrityTests(unittest.TestCase):
    setUp = fixtures.PrivateProfileFixture.setUp
    tearDown = fixtures.PrivateProfileFixture.tearDown
    candidate = fixtures.PrivateProfileFixture.candidate

    def rewrite_messages(self, rows):
        def write(path, value):
            path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf8')
        (self.export / 'messages.jsonl').write_text(''.join(
            json.dumps(row, ensure_ascii=False) + '\n' for row in rows), encoding='utf8')
        (self.export / 'analysis/corpus.jsonl').write_text(''.join(
            json.dumps({**row, 'ref': index}, ensure_ascii=False) + '\n'
            for index, row in enumerate(rows, 1)), encoding='utf8')
        write(self.export / 'analysis/index.json', {'chunks': [{'file': 'part-001.md', 'refs': [1, len(rows)]}]})
        review_path, manifest_path = self.export / 'review.json', self.export / 'manifest.json'
        review = json.loads(review_path.read_text(encoding='utf8'))
        for item in review['reviewedArtifacts']:
            data = (self.export / item['path']).read_bytes()
            item.update(bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
        write(review_path, review)
        manifest = json.loads(manifest_path.read_text(encoding='utf8'))
        for item in manifest['artifacts']:
            data = (self.export / item['path']).read_bytes()
            item.update(bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
        write(manifest_path, manifest)

    def test_numeric_and_textual_message_id_collisions_are_rejected(self):
        row = json.loads((self.export / 'messages.jsonl').read_text(encoding='utf8'))
        for left, right in ((7, 7), ('7', 7), (7, '7')):
            with self.subTest(left=left, right=right):
                self.rewrite_messages([{**row, 'id': left, 'text': '第一条原消息'},
                                       {**row, 'id': right, 'text': '第二条不同消息'}])
                with self.assertRaises(ProfileError):
                    self.store.prepare(self.export)

    def test_boolean_ref_is_not_silently_treated_as_reference_one(self):
        request = self.store.prepare(self.export)
        candidate = self.candidate(request)
        candidate['observations'][0]['evidence'][0]['ref'] = True
        with self.assertRaises(ProfileError):
            self.store.stage(request['requestId'], candidate)

    def test_invalid_message_id_types_fail_with_a_validation_error(self):
        row = json.loads((self.export / 'messages.jsonl').read_text(encoding='utf8'))
        for identifier in (True, 0, ['id'], {'id': 7}):
            with self.subTest(identifier=identifier):
                self.rewrite_messages([{**row, 'id': identifier}])
                with self.assertRaises(ProfileError):
                    self.store.prepare(self.export)

    def test_distinct_numeric_ids_keep_the_exact_reference_body(self):
        row = json.loads((self.export / 'messages.jsonl').read_text(encoding='utf8'))
        self.rewrite_messages([{**row, 'id': 7, 'text': '第一条原消息'},
                               {**row, 'id': 8, 'text': '第二条原消息'}])
        request = self.store.prepare(self.export)
        candidate = self.candidate(request)
        candidate['observations'][0]['evidence'][0]['ref'] = 2
        staged = self.store.stage(request['requestId'], candidate)
        evidence = self.store.candidate(staged['candidateId'])['observations'][0]['evidence'][0]
        self.assertEqual(evidence['ref'], 2)
        self.assertEqual(evidence['messageId'], '8')
        self.assertEqual(evidence['text'], '第二条原消息')


if __name__ == '__main__':
    unittest.main()
