"""Regression checks for reading and paging synthesized chat records."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from chat_viewer import GlobalSearchIndex, make_conversation, search_messages
from offline_content import mentions
from offline_view import ConversationView


def write_rows(path, rows):
    path.write_bytes(b'\r\n'.join(json.dumps(row, ensure_ascii=False).encode('utf-8') for row in rows))


class NativeViewTests(unittest.TestCase):
    def test_plain_and_embedded_xml_mentions_keep_structured_identity(self):
        plain = mentions({'local_type': 1, 'atuserlist': 'known'}, 'sender:\n@display 普通文字')
        self.assertEqual(plain, {'ids': ['known'], 'everyone': False, 'status': 'structured',
                                 'displayTokens': ['@display']})
        embedded = mentions({'local_type': 1},
                            'sender:\n<msg><msgsource><atuserlist>known,notify@all</atuserlist></msgsource></msg>')
        self.assertEqual(embedded['ids'], ['known', 'notify@all'])
        self.assertEqual(embedded['status'], 'structured')
        self.assertTrue(embedded['everyone'])
        invalid = mentions({'local_type': 1, 'source': '<broken'}, 'sender:\n@display 普通文字')
        self.assertEqual(invalid['status'], 'invalid')
        self.assertEqual(invalid['displayTokens'], ['@display'])

    def test_nested_images_filters_search_and_concurrent_queries(self):
        with tempfile.TemporaryDirectory(prefix='synthetic-view80-') as temp:
            root = Path(temp)
            (root / 'manifest.json').write_text('{}', encoding='utf-8')
            rows = [
                {'id': 'a', 'kind': 'text', 'text': 'Alpha', 'links': ['https://example.invalid/one']},
                {'id': 'b', 'kind': 'image', 'asset': {'status': 'pending'}},
                {'id': 'c', 'kind': 'record', 'record': {'items': [
                    {'kind': 'image', 'asset': {'status': 'ready', 'previewOnly': True}},
                    {'kind': 'text', 'quote': {'message': {'kind': 'image', 'text': 'Nested Needle',
                                                            'asset': {'status': 'unavailable'}}}}]}},
                {'id': 'd', 'kind': 'emoji'},
                {'id': 'e', 'kind': 'file', 'asset': {'status': 'pending'},
                 'attachmentContent': {'text': 'Document Key'}},
                {'id': 'f', 'kind': 'quote', 'quote': {'message': {'kind': 'text', 'text': 'Beta'}}},
            ]
            path = root / 'messages.jsonl'; write_rows(path, rows)
            before = hashlib.sha256(path.read_bytes()).digest()
            view = ConversationView(root)
            def ids(**kwargs): return [row['id'] for row in view.page(offset=0, page_size=100, **kwargs)['messages']]
            self.assertEqual(ids(kind='business'), ['a', 'b', 'c', 'e', 'f'])
            self.assertEqual(ids(kind='missing_media'), ['b', 'c', 'e'])
            self.assertEqual(ids(kind='missing_images'), ['b', 'c'])
            self.assertEqual(ids(kind='quote'), ['c', 'f'])
            self.assertEqual(ids(query='nested needle'), ['c'])
            self.assertEqual(ids(query='document key'), ['e'])
            self.assertEqual(view.image_counts, {'missing': 2, 'preview': 1})
            self.assertEqual([item['rootMessageId'] for item in view.image_page()['images']], ['b', 'c', 'c'])
            self.assertEqual(view.page_for_message('c')['messages'][0]['id'], 'c')
            tasks = [('alpha', 'a'), ('nested needle', 'c'), ('document key', 'e'), ('beta', 'f')] * 20
            with ThreadPoolExecutor(max_workers=4) as pool:
                results = list(pool.map(lambda item: ids(query=item[0]), tasks))
            self.assertEqual(results, [[item[1]] for item in tasks])
            self.assertEqual(hashlib.sha256(path.read_bytes()).digest(), before)


class ViewerPagingTests(unittest.TestCase):
    def test_fast_older_pages_match_existing_search_for_filters_and_zip(self):
        with tempfile.TemporaryDirectory(prefix='synthetic-page80-') as temp:
            root = Path(temp)
            meta = {'id': 'synthetic', 'name': '合成会话', 'conversationKind': 'group'}
            rows = []
            kinds = ('text', 'file', 'image', 'video', 'link', 'miniprogram')
            for i in range(360):
                row = {'chatId': 'synthetic', 'id': str(i // 2), 'kind': kinds[i % len(kinds)],
                       'text': '关键词 ' + str(i), 'senderId': 'same' if i % 2 else 'other',
                       'senderName': '合成成员', 'time': f'2026-01-{i % 3 + 1:02d}T10:00:00+08:00'}
                if row['kind'] == 'link': row['links'] = ['https://example.invalid/needle']
                if row['kind'] == 'file': row['asset'] = {'filename': '合成附件.txt'}
                if i == 331: row['text'] += '长' * 600000
                rows.append(row)
            messages = root / 'messages.jsonl'; write_rows(messages, rows)
            with messages.open('ab') as stream:
                stream.write(b'\r\nnot-json\r\n' + json.dumps({'chatId': 'other', 'id': 'x'}).encode())
            before_hash = hashlib.sha256(messages.read_bytes()).digest()
            chat = make_conversation(meta, len(rows), '合成资料', directory=root)
            filters = ({'q': '关键词'}, {'category': 'file'}, {'category': 'media'},
                       {'category': 'link', 'q': 'needle'}, {'category': 'miniprogram'},
                       {'q': '关键词', 'senderId': 'same', 'from': '2026-01-01', 'to': '2026-01-03'})
            for options in filters:
                with self.subTest(options=options):
                    initial = search_messages(chat, options, limit=7)
                    cursor, seen = initial['older'], set(row['start'] for row in initial['items'])
                    self.assertIsNotNone(cursor)
                    for _ in range(5):
                        parameters = options | {'before': str(cursor)}
                        expected = search_messages(chat, parameters, limit=7)
                        fast = search_messages(chat, parameters | {'pageOnly': '1'}, limit=7)
                        self.assertEqual(fast['items'], expected['items'])
                        self.assertEqual(fast['older'], expected['older'])
                        self.assertIsNone(fast['total'])
                        positions = {row['start'] for row in fast['items']}
                        self.assertFalse(positions & seen)
                        seen.update(positions); cursor = fast['older']
                        if cursor is None: break
            with self.assertRaises(ValueError):
                search_messages(chat, {'q': '关键词', 'before': str(initial['older'] + 1), 'pageOnly': '1'})
            archive = root / 'synthetic.zip'
            with ZipFile(archive, 'w') as bundle:
                bundle.write(messages, 'messages.jsonl')
            zipped = make_conversation(meta, len(rows), '合成压缩资料', archive=archive)
            initial_zip = search_messages(zipped, {'q': '关键词'}, limit=7)
            parameters = {'q': '关键词', 'before': str(initial_zip['older'])}
            self.assertEqual(search_messages(zipped, parameters | {'pageOnly': '1'}, limit=7),
                             search_messages(zipped, parameters, limit=7))
            self.assertEqual(hashlib.sha256(messages.read_bytes()).digest(), before_hash)


class SearchIndexFailureTests(unittest.TestCase):
    class EmptyCatalog:
        def snapshot(self): return {'chats': []}

    def test_corrupt_index_is_reported_and_releases_windows_file_lock(self):
        with tempfile.TemporaryDirectory(prefix='synthetic-index80-') as temp:
            index = GlobalSearchIndex(self.EmptyCatalog(), temp, None)
            content = b'not a sqlite database'
            index.database.write_bytes(content)
            status = index.status()
            self.assertEqual(status['state'], 'partial')
            self.assertIn('索引无法读取', status['error'])
            with self.assertRaisesRegex(ValueError, '索引无法读取'):
                index.prepare()
            self.assertEqual(index.database.read_bytes(), content)
            index.database.unlink()

    def test_uninitialized_database_only_reports_building_while_owner_holds_lock(self):
        with tempfile.TemporaryDirectory(prefix='synthetic-index80-') as temp:
            index = GlobalSearchIndex(self.EmptyCatalog(), temp, None)
            sqlite3.connect(index.database).close()
            owner = index._lock()
            try:
                self.assertIsNotNone(owner)
                self.assertEqual(index.status()['state'], 'building')
            finally:
                index._unlock(owner)
            self.assertEqual(index.status()['state'], 'partial')

    def test_failed_connection_initialization_closes_connection(self):
        class FailingConnection:
            closed = False
            def execute(self, sql):
                if 'synchronous' in sql: raise sqlite3.DatabaseError('synthetic failure')
            def close(self): self.closed = True
        connection = FailingConnection()
        index = GlobalSearchIndex(self.EmptyCatalog(), '.', None)
        with patch('chat_viewer.sqlite3.connect', return_value=connection):
            with self.assertRaises(sqlite3.DatabaseError):
                with index._connection(): pass
        self.assertTrue(connection.closed)


if __name__ == '__main__':
    unittest.main()
