"""Synthetic offline public-API coverage; no real clients or account keys."""
from contextlib import closing
from datetime import datetime
import hashlib
import json
import re
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from database_snapshot import prepare_snapshot
from offline_chat import BEIJING, LocalArchive, date_range, discover, workbench_source
from offline_content import interpret
from test_database_adapter import create_metadata, create_shard, GROUP, SELF, PEER, STAMP


class OfflineChatTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='offline-chat-test-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.source = self.base / 'source'
        self.source.mkdir()
        create_metadata(self.source)
        self.destination = self.base / 'export'

    def archive(self, rows=None, own=SELF, config=None):
        create_shard(self.source, rows=rows)
        return LocalArchive(self.source, own, config)

    def read(self):
        return [json.loads(line) for line in (self.destination / 'messages.jsonl').read_text(encoding='utf-8').splitlines()]

    def test_group_request_suffix_uses_exact_name_first_and_unique_fallback(self):
        archive = self.archive()
        group = archive.state['groups'][0]
        name = group['name']
        self.assertEqual(archive.select_chat(chat_name=name + '群')['id'], group['id'])
        self.assertEqual(archive.select_chat(chat_name=name + '群聊')['id'], group['id'])
        exact = {**group, 'id': 'suffix-exact@chatroom', 'name': name + '群'}
        archive.state['groups'].append(exact)
        self.assertEqual(archive.select_chat(chat_name=name + '群')['id'], exact['id'])
        archive.state['groups'].append({**group, 'id': 'duplicate@chatroom'})
        with self.assertRaises(ValueError):
            archive.select_chat(chat_name=name + '群聊')
        with self.assertRaises(ValueError):
            archive.select_chat(chat_name=name[:-1])

    def test_export_json_and_markdown_preserve_rows_nested_media_and_sources(self):
        from test_windows_media import PNG
        account = self.base / 'selected-account'
        source = account / 'db_storage'
        source.mkdir(parents=True)
        image = account / 'resource/cache.png'
        image.parent.mkdir()
        image.write_bytes(PNG)
        digest = hashlib.md5(PNG).hexdigest()
        nested = '<recordinfo><datalist><dataitem datatype="2"><sourcename>内嵌发送人</sourcename><sourcetime>2026-10-16 10:00</sourcetime><fullmd5>' + 'a' * 32 + '</fullmd5></dataitem></datalist></recordinfo>'
        record = '<msg><appmsg><type>19</type><recorditem><recordinfo><datalist><dataitem datatype="17"><recordxml>' + nested + '</recordxml></dataitem></datalist></recordinfo></recorditem></appmsg></msg>'
        quote = '<msg><appmsg><type>57</type><title>核对引用</title><refermsg><type>1</type><displayname>引用发送人</displayname><content>引用正文</content></refermsg></appmsg></msg>'
        archive = self.archive([
            {'local': 1, 'text': '第一行\n第二行 ![伪图片](https://example.org/untrusted.png) <script>alert(1)</script> http://[bad'},
            {'local': 2, 'type': 3, 'text': '<msg><img md5="' + digest + '"/></msg>'},
            {'local': 3, 'type': 3, 'text': '<msg><img md5="' + 'b' * 32 + '"/></msg>'},
            {'local': 4, 'type': 49, 'text': record},
            {'local': 5, 'type': 49, 'text': quote},
            {'local': 6, 'type': 47, 'text': '<msg><emoji/></msg>'}], config={'sourceRoot': str(source)})
        report = archive.export(self.destination, chat_id=GROUP, copy_media=True)
        rows = self.read()
        self.assertEqual(json.loads((self.destination / 'messages.json').read_text(encoding='utf-8')), rows)
        self.assertEqual(len(rows), 6)
        markdown = (self.destination / 'messages.md').read_text(encoding='utf-8')
        self.assertIn('第一行\n第二行', markdown)
        rendered_markup = re.sub(r'(?ms)^(`{3,})[^\n]*\n.*?^\1$', '', markdown)
        self.assertNotIn('<script>', rendered_markup)
        self.assertNotIn('![伪图片](https://example.org/untrusted.png)', rendered_markup)
        self.assertIn('内嵌发送人', markdown)
        self.assertIn('2026-10-16 10:00', markdown)
        self.assertIn('part=0.0', markdown)
        self.assertIn('引用正文', markdown)
        self.assertIn('引用发送人', markdown)
        self.assertIn('原件未取得', markdown)
        self.assertIn('![' , markdown)
        self.assertIn('](' + rows[1]['asset']['path'] + ')', markdown)
        for row in rows:
            self.assertIn(row['id'], markdown)
        self.assertEqual(report['messageExports'], {'jsonl': 'messages.jsonl', 'json': 'messages.json', 'md': 'messages.md'})
        self.assertTrue({'messages.json', 'messages.md'} <= {item['path'] for item in report['artifacts']})

    def test_empty_date_range_is_valid_in_all_message_formats(self):
        archive = self.archive([{'text': 'old record'}])
        report = archive.export(self.destination, chat_id=GROUP, start='2099-01-01')
        self.assertEqual(report['exportedRows'], 0)
        self.assertEqual(json.loads((self.destination / 'messages.json').read_text(encoding='utf-8')), [])
        self.assertEqual(self.read(), [])
        self.assertIn('共导出 0 条源消息', (self.destination / 'messages.md').read_text(encoding='utf-8'))

    def test_media_summary_separates_images_files_emoji_and_counts_quoted_occurrences(self):
        from test_windows_media import PNG
        source = self.base / 'account/db_storage'
        source.mkdir(parents=True)
        image = source.parent / 'resource/original.png'
        image.parent.mkdir()
        image.write_bytes(PNG)
        thumb = source.parent / 'cache/2023-11/Message' / hashlib.md5(GROUP.encode()).hexdigest() / 'Thumb'
        thumb.mkdir(parents=True)
        (thumb / f'2_{STAMP}_thumb.jpg').write_bytes(PNG)
        data = b'%PDF-1.7 synthetic attachment'
        file = source.parent / 'msg/file/2023-11/example.pdf'
        file.parent.mkdir(parents=True)
        file.write_bytes(data)
        nested = '<recordinfo><datalist><dataitem datatype="2"><fullmd5>' + 'c' * 32 + '</fullmd5></dataitem></datalist></recordinfo>'
        record = '<msg><appmsg><type>19</type><recorditem><recordinfo><datalist><dataitem datatype="17"><recordxml>' + nested + '</recordxml></dataitem></datalist></recordinfo></recorditem></appmsg></msg>'
        quote = '<msg><appmsg><type>57</type><refermsg><type>0</type><svrid>10</svrid><fromusr>' + SELF + '</fromusr><content/></refermsg></appmsg></msg>'
        archive = self.archive([
            {'local': 1, 'server': 10, 'sender': 1, 'type': 3, 'text': '<msg><img md5="' + hashlib.md5(PNG).hexdigest() + '"/></msg>'},
            {'local': 2, 'type': 3, 'text': '<msg><img md5="' + 'b' * 32 + '"/></msg>'},
            {'local': 3, 'type': 49, 'text': record},
            {'local': 4, 'type': 49, 'text': quote},
            {'local': 5, 'type': 49, 'text': '<msg><appmsg><type>6</type><title>example.pdf</title><md5>' + hashlib.md5(data).hexdigest() + '</md5></appmsg></msg>'},
            {'local': 6, 'type': 47, 'text': '<msg><emoji/></msg>'}], config={'sourceRoot': str(source)})
        report = archive.export(self.destination, chat_id=GROUP, copy_media=True)
        summary = report['mediaSummary']
        self.assertEqual(summary['scope'], 'ordinary_forward_quote_references')
        self.assertFalse(summary['deduplicated'])
        self.assertEqual(summary['images'], {'total': 4, 'original': 2, 'thumbnail': 1, 'localPreview': 0, 'missing': 1, 'notLoaded': 0})
        self.assertEqual(summary['attachments'], {'total': 1, 'ready': 1, 'previewOnly': 0, 'missing': 0, 'notLoaded': 0})
        self.assertEqual(summary['attachmentTypes'], {'.pdf': {'total': 1, 'ready': 1, 'previewOnly': 0, 'missing': 0, 'notLoaded': 0}})
        self.assertEqual(summary['emoji'], {'total': 1, 'ready': 0, 'previewOnly': 0, 'missing': 1, 'notLoaded': 0})
        self.assertEqual(summary['images']['total'], report['imageReferences'])
        self.assertIn('图片引用 4 处：原图 2，缩略图 1，本地预览 0，缺失图片 1，未加载 0', report['mediaSummaryText'])
        self.assertIn('附件引用 1 处', report['mediaSummaryText'])
        self.assertIn('表情引用 1 处', report['mediaSummaryText'])
        self.assertIn('不是去重后的文件张数', report['mediaSummaryText'])
        self.assertEqual(json.loads((self.destination / 'manifest.json').read_text(encoding='utf-8'))['mediaSummary'], summary)
        unloaded = archive.export(self.base / 'without-media', chat_id=GROUP)
        self.assertEqual(unloaded['mediaSummary']['images'], {'total': 4, 'original': 0, 'thumbnail': 0, 'localPreview': 0, 'missing': 0, 'notLoaded': 4})
        self.assertEqual(unloaded['mediaSummary']['attachments']['notLoaded'], 1)
        self.assertEqual(unloaded['mediaSummary']['emoji']['notLoaded'], 1)

    def test_completion_report_presents_all_three_formats_and_exact_attachment_types(self):
        archive = self.archive([
            {'local': i, 'type': 49, 'text': '<msg><appmsg><type>6</type><title>' + name + '</title></appmsg></msg>'}
            for i, name in enumerate(('a.csv', 'b.CSV', 'example.pdf', 'no-extension'), 1)])
        report = archive.export(self.destination, chat_id=GROUP)
        reporting = report['reporting']
        content = Path(reporting['markdownFile']).read_text(encoding='utf-8')
        self.assertEqual(content, reporting['replyMarkdown'])
        self.assertIn(report['mediaSummaryText'], content)
        self.assertIn('附件类型（按引用）：.csv 2处、.pdf 1处、未识别扩展名 1处', content)
        self.assertIn('未取得图片当前没有可显示的原件', content)
        self.assertIn('缩略图与本地预览的原图均未取得，分别计数', content)
        self.assertIn('未加载尚未核查', content)
        types = report['mediaSummary']['attachmentTypes']
        self.assertEqual(types['.csv']['total'], 2)
        self.assertEqual(types['.csv']['notLoaded'], 2)
        self.assertEqual(sum(item['total'] for item in types.values()), report['mediaSummary']['attachments']['total'])
        shown = reporting['presentation']
        self.assertEqual(Path(shown['cwd']), self.destination)
        for relative, label in (('messages.jsonl', 'JSONL'), ('messages.json', 'JSON'), ('messages.md', 'MD')):
            self.assertIn(str(self.destination / relative), shown['files'])
            self.assertIn('[' + label + '](<' + (self.destination / relative).as_posix() + '>)', content)
        self.assertTrue(all(Path(path).is_file() for path in shown['files']))
        self.assertIn('解析结果.md', {item['path'] for item in report['artifacts']})

    def test_streams_all_shards_without_ui_limit_and_retains_provenance(self):
        rows = [{'local': i, 'time': STAMP + i, 'server': i, 'text': f'row {i}'} for i in range(1, 2302)]
        archive = self.archive(rows)
        # Reconfigure after adding a shard, as a changed snapshot must invalidate.
        create_shard(self.source, 'message/message_1.db', rows=[rows[0], {'local': 5, 'time': STAMP + 2400, 'text': 'last'}])
        archive = LocalArchive(self.source, SELF)
        result = archive.export(self.destination, chat_id=GROUP)
        data = self.read()
        self.assertEqual(result['exportedRows'], 2303)
        self.assertFalse(result['truncatedByLimit'])
        self.assertEqual(data[0]['timestamp'], data[1]['timestamp'])
        self.assertNotEqual(data[0]['id'], data[1]['id'])
        self.assertEqual(data[-1]['text'], 'last')
        self.assertTrue(all(row['rawColumns'] for row in data))

    def test_sender_and_structured_mentions_distinct_from_text(self):
        archive = self.archive([
            {'local': 1, 'sender': 1, 'text': '@合成联系人 纯文本', 'source': '<msgsource/>'},
            {'local': 2, 'sender': 2, 'text': 'hello', 'source': f'<msgsource><atuserlist>{SELF},notify@all</atuserlist></msgsource>'}])
        archive.export(self.destination, chat_id=GROUP)
        data = self.read()
        self.assertEqual(data[0]['direction'], 'outgoing')
        self.assertEqual(data[0]['mentions']['ids'], [])
        self.assertTrue(data[0]['mentions']['displayTokens'])
        self.assertEqual(data[1]['senderId'], PEER)
        self.assertEqual(data[1]['direction'], 'incoming')
        self.assertTrue(data[1]['mentions']['self'])
        self.assertTrue(data[1]['mentions']['everyone'])

    def test_local_media_search_recovers_exact_image_from_other_cache_location(self):
        from test_windows_media import PNG
        account = self.base / 'account'
        source = account / 'db_storage'
        source.mkdir(parents=True)
        image = account / 'business/emoticon/Persist/ab/opaque-cache'
        image.parent.mkdir(parents=True)
        image.write_bytes(PNG)
        digest = hashlib.md5(PNG).hexdigest()
        archive = self.archive([{'type': 3, 'text': '<msg><img md5="' + digest + '"/></msg>'}],
                               config={'sourceRoot': str(source)})
        report = archive.export(self.destination, chat_id=GROUP, copy_media=True)
        asset = self.read()[0]['asset']
        self.assertEqual(asset['status'], 'ready')
        self.assertFalse(asset['previewOnly'])
        self.assertEqual((self.destination / asset['path']).read_bytes(), PNG)
        self.assertEqual(report['localMediaSearch']['recoveredReferences'], 1)
        self.assertEqual((self.destination / 'media-recovery.jsonl').read_text(encoding='utf-8'), '')

    def test_relocated_v2_image_selects_key_by_content_without_rewriting_key_file(self):
        import struct
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        from cryptography.hazmat.primitives.padding import PKCS7
        from test_windows_media import PNG
        source = self.base / 'new-machine/selected-account/db_storage'
        source.mkdir(parents=True)
        key = b'0123456789abcdef'
        head, tail = PNG[:32], PNG[32:]
        padder = PKCS7(128).padder()
        padded = padder.update(head) + padder.finalize()
        cipher = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
        encoded = (b'\x07\x08V2\x08\x07' + struct.pack('<II', len(head), len(tail)) + b'\0'
                   + cipher.update(padded) + cipher.finalize() + bytes(value ^ 73 for value in tail))
        cached = source.parent / 'resource/cached.dat'
        cached.parent.mkdir()
        cached.write_bytes(encoded)
        key_file = self.base / 'existing-image-keys.json'
        key_file.write_text(json.dumps({'keys': [
            {'sourceRoot': str(source), 'aes_hex': ('22' * 16)},
            {'sourceRoot': str(self.base / 'old-machine/account/db_storage'), 'aes_hex': key.hex()}]}))
        before = key_file.read_bytes()
        archive = self.archive([{'type': 3, 'text': '<msg><img md5="' + hashlib.md5(PNG).hexdigest() + '"/></msg>'}],
                               config={'sourceRoot': str(source), 'imageKeyFile': str(key_file)})
        report = archive.export(self.destination, chat_id=GROUP, copy_media=True)
        asset = self.read()[0]['asset']
        self.assertEqual(asset['status'], 'ready')
        self.assertFalse(asset['previewOnly'])
        self.assertEqual((self.destination / asset['path']).read_bytes(), PNG)
        self.assertEqual(report['localMediaSearch']['decodedContainers'], 1)
        self.assertEqual(key_file.read_bytes(), before)
        self.assertEqual(cached.read_bytes(), encoded)

    def test_local_gap_report_and_filter_include_nested_images(self):
        from offline_view import ConversationView
        account = self.base / 'account'
        source = account / 'db_storage'
        source.mkdir(parents=True)
        digest = hashlib.md5(b'missing-image').hexdigest()
        nested = '<recordinfo><datalist><dataitem datatype="2"><fullmd5>' + digest + '</fullmd5></dataitem></datalist></recordinfo>'
        record = '<msg><appmsg><type>19</type><recorditem><recordinfo><datalist><dataitem datatype="17"><recordxml>' + nested + '</recordxml></dataitem></datalist></recordinfo></recorditem></appmsg></msg>'
        archive = self.archive([{'local': 1, 'type': 3, 'text': '<msg><img md5="' + digest + '"/></msg>'},
                                {'local': 2, 'type': 49, 'text': record}], config={'sourceRoot': str(source)})
        archive.export(self.destination, chat_id=GROUP, copy_media=True)
        gaps = [json.loads(line) for line in (self.destination / 'media-recovery.jsonl').read_text(encoding='utf-8').splitlines()]
        self.assertEqual(len(gaps), 2)
        self.assertEqual(gaps[1]['part'], '0.0')
        self.assertTrue(all(row['localCheck']['result'] == 'not_found_in_supported_local_media' for row in gaps))
        self.assertEqual(ConversationView(self.destination).page(kind='missing_media')['total'], 2)

    def test_business_reading_keeps_context_quote_and_system_but_folds_emoji(self):
        from offline_view import ConversationView
        quote = '<msg><appmsg><type>57</type><title>请按此版本确认</title><refermsg><displayname>合成来源</displayname><type>1</type><content>合同金额已更新</content></refermsg></appmsg></msg>'
        archive = self.archive([
            {'local': 1, 'text': '请核对业务附件'},
            {'local': 2, 'type': 47, 'text': '<msg><emoji md5="' + 'a' * 32 + '" aeskey="' + 'b' * 32 + '"/></msg>'},
            {'local': 3, 'type': 49, 'text': quote},
            {'local': 4, 'type': 10000, 'text': '撤回了一条消息'}])
        report = archive.export(self.destination, chat_id=GROUP)
        reading = (self.destination / '业务阅读.txt').read_text(encoding='utf-8-sig')
        self.assertEqual(report['exportedRows'], 4)
        self.assertEqual(report['businessRows'], 3)
        self.assertIn('请核对业务附件', reading)
        self.assertIn('合同金额已更新', reading)
        self.assertIn('撤回了一条消息', reading)
        self.assertNotIn('aeskey', reading)
        self.assertEqual(ConversationView(self.destination).page(kind='business')['total'], 3)

    def test_empty_quote_links_unique_same_chat_source_with_media(self):
        from test_windows_media import PNG
        account = self.base / 'account'
        source = account / 'db_storage'
        source.mkdir(parents=True)
        path = account / 'resource/opaque-cache'
        path.parent.mkdir()
        path.write_bytes(PNG)
        quote = '<msg><appmsg><type>57</type><title>请核对这张图</title><refermsg><type>0</type><svrid>10</svrid><fromusr>' + SELF + '</fromusr><content/></refermsg></appmsg></msg>'
        archive = self.archive([
            {'local': 1, 'server': 10, 'sender': 1, 'type': 3, 'text': '<msg><img md5="' + hashlib.md5(PNG).hexdigest() + '"/></msg>'},
            {'local': 2, 'server': 20, 'sender': 2, 'type': 49, 'text': quote}], config={'sourceRoot': str(source)})
        report = archive.export(self.destination, chat_id=GROUP, copy_media=True)
        first, second = self.read()
        linked = second['quote']
        self.assertEqual(linked['resolutionStatus'], 'linked_local_source')
        self.assertEqual(linked['sourceMessageId'], first['id'])
        self.assertEqual(linked['message']['kind'], 'image')
        self.assertEqual(linked['message']['asset']['digest'], first['asset']['digest'])
        self.assertEqual(report['mediaStatuses'], {'ready': 1})
        self.assertEqual(report['localMediaSearch']['recoveredReferences'], 1)

    def test_quote_source_rejects_conflicts_duplicates_absent_and_cyclic_ids(self):
        def quote(server, sender=SELF):
            return '<msg><appmsg><type>57</type><title>合成回复</title><refermsg><type>0</type><svrid>' + str(server) + '</svrid><fromusr>' + sender + '</fromusr><content/></refermsg></appmsg></msg>'
        archive = self.archive([
            {'local': 1, 'server': 10, 'sender': 1, 'text': '已确认业务原文'},
            {'local': 2, 'server': 30, 'sender': 1, 'text': '重复编号 A'},
            {'local': 3, 'server': 30, 'sender': 1, 'text': '重复编号 B'},
            {'local': 4, 'server': 40, 'type': 49, 'text': quote(10, PEER)},
            {'local': 5, 'server': 50, 'type': 49, 'text': quote(30)},
            {'local': 6, 'server': 60, 'type': 49, 'text': quote(99)},
            {'local': 7, 'server': 70, 'type': 49, 'text': quote(70)}])
        archive.export(self.destination, chat_id=GROUP)
        statuses = [row['quote']['resolutionStatus'] for row in self.read() if row.get('quote')]
        self.assertEqual(statuses, ['source_sender_conflict', 'ambiguous_source', 'source_not_found', 'cyclic_source'])

    def test_self_identity_unknown_is_null_not_incoming(self):
        archive = self.archive(own='')
        archive.export(self.destination, chat_id=GROUP)
        self.assertIsNone(self.read()[0]['isSelf'])
        self.assertEqual(self.read()[0]['direction'], 'unknown')

    def test_unknown_binary_keeps_original_bytes_and_declares_decode_gap(self):
        archive = self.archive([{'text': b'\xff\xfe\x00', 'type': 999}])
        report = archive.export(self.destination, chat_id=GROUP)
        row = self.read()[0]
        self.assertEqual(row['decodeStatus'], 'utf8_decode_error')
        self.assertEqual(row['rawColumns']['message_content']['data'], '//4A')
        self.assertEqual(report['decodeStatuses'], {'utf8_decode_error': 1})

    def test_zstd_and_corrupt_compress_fallback_keep_raw_fields(self):
        import zstandard
        archive = self.archive([
            {'local': 1, 'text': zstandard.ZstdCompressor().compress('压缩正文'.encode())},
            {'local': 2, 'compressed': b'\xff\xfe', 'text': '明文备用'}])
        archive.export(self.destination, chat_id=GROUP)
        first, second = self.read()
        self.assertEqual(first['text'], '压缩正文')
        self.assertEqual(second['text'], '明文备用')
        self.assertEqual(second['decodeAttempts'][0]['status'], 'utf8_decode_error')

    def test_dates_are_beijing_and_limit_is_explicit(self):
        day = int(datetime(2026, 10, 16, tzinfo=BEIJING).timestamp())
        archive = self.archive([{'local': i + 1, 'time': t} for i, t in enumerate([day - 1, day, day + 86399, day + 86400])])
        result = archive.export(self.destination, chat_id=GROUP, start='2026-10-16', end='2026-10-16', limit=1)
        self.assertTrue(result['truncatedByLimit'])
        self.assertEqual(self.read()[0]['timestamp'], day)
        for start, end in [('2026-10-17', '2026-10-16'), ('2026-1-1', None)]:
            with self.assertRaises(ValueError):
                date_range(start, end)

    def test_duplicate_group_name_requires_id(self):
        archive = self.archive()
        archive.state['groups'][1]['name'] = archive.state['groups'][0]['name']
        with self.assertRaisesRegex(ValueError, '名称重复'):
            archive.select_chat(chat_name=archive.state['groups'][0]['name'])

    def test_output_cannot_overwrite_or_write_source(self):
        archive = self.archive()
        for destination in (self.source / 'export', self.source):
            with self.assertRaises(ValueError):
                archive.export(destination, chat_id=GROUP)
        self.destination.mkdir()
        (self.destination / 'keep.txt').write_text('keep')
        with self.assertRaises(ValueError):
            archive.export(self.destination, chat_id=GROUP)
        self.assertEqual((self.destination / 'keep.txt').read_text(), 'keep')

    def test_failed_export_is_not_published_and_stage_is_cleaned(self):
        archive = self.archive()
        with patch.object(archive, '_message', side_effect=ValueError('invalid row')):
            with self.assertRaises(ValueError):
                archive.export(self.destination, chat_id=GROUP)
        self.assertFalse(self.destination.exists())
        self.assertFalse(list(self.base.glob('.offline-export-*')))

    def test_html_and_csv_do_not_execute_message_payloads(self):
        archive = self.archive([{'local': 1, 'text': '<script>alert(1)</script>'},
                                {'local': 2, 'text': '=HYPERLINK("evil")'}])
        archive.export(self.destination, chat_id=GROUP)
        page = (self.destination / 'chat.html').read_text(encoding='utf-8')
        self.assertNotIn('<script>', page)
        self.assertIn('&lt;script&gt;', page)
        self.assertIn("default-src 'none'", page)
        self.assertIn("'=HYPERLINK", (self.destination / 'messages.csv').read_text(encoding='utf-8-sig'))

    def test_media_copy_is_bound_to_file_md5(self):
        account = self.base / 'account'
        source = account / 'db_storage'
        source.mkdir(parents=True)
        folder = account / 'msg/file' / datetime.fromtimestamp(STAMP, BEIJING).strftime('%Y-%m')
        folder.mkdir(parents=True)
        data = b'%PDF-1.7 synthetic'
        (folder / 'sample.pdf').write_bytes(data)
        md5 = hashlib.md5(data).hexdigest()
        xml = f'<msg><appmsg><title>sample.pdf</title><type>6</type><md5>{md5}</md5></appmsg></msg>'
        archive = self.archive([{'type': 49, 'text': xml}], config={'sourceRoot': str(source)})
        # Empty indexes still contain the observed schema. Missing tables are gaps.
        (self.source / 'hardlink').mkdir()
        with closing(sqlite3.connect(self.source / 'hardlink/hardlink.db')) as db, db:
            db.execute('CREATE TABLE file_hardlink_info_v4(md5,file_name,dir1,dir2)')
            db.execute('CREATE TABLE dir2id(username)')
        archive = LocalArchive(self.source, SELF, {'sourceRoot': str(source)})
        report = archive.export(self.destination, chat_id=GROUP, copy_media=True)
        asset = self.read()[0]['asset']
        self.assertEqual(report['mediaStatuses'], {'ready': 1})
        self.assertEqual((self.destination / asset['path']).read_bytes(), data)
        # A changed cache does not pass the expected digest.
        (folder / 'sample.pdf').write_bytes(b'wrong')
        archive.export(self.base / 'second', chat_id=GROUP, copy_media=True)
        row = json.loads((self.base / 'second/messages.jsonl').read_text(encoding='utf-8').strip())
        self.assertNotIn('path', row['asset'])

    def test_voice_copy_and_optional_snapshot_inventory(self):
        archive = self.archive([{'type': 34, 'local': 4, 'server': 7, 'text': '<msg><voicemsg voicelength="123"/></msg>'}])
        voice_path = self.source / 'message/media_0.db'
        with closing(sqlite3.connect(voice_path)) as db, db:
            db.execute('CREATE TABLE Name2Id(user_name)')
            db.execute('INSERT INTO Name2Id VALUES(?)', (GROUP,))
            db.execute('CREATE TABLE VoiceInfo(chat_name_id,local_id,svr_id,voice_data)')
            db.execute('INSERT INTO VoiceInfo VALUES(1,4,7,?)', (b'\x02#!SILK_V3 synthetic',))
        default = prepare_snapshot(self.source, self.base / 'snapshots')
        self.assertFalse((default['root'] / 'message/media_0.db').exists())
        full = prepare_snapshot(self.source, self.base / 'snapshots', include_voice=True)
        self.assertTrue((full['root'] / 'message/media_0.db').exists())
        archive = LocalArchive(full['root'], SELF)
        archive.export(self.destination, chat_id=GROUP, copy_media=True)
        row = self.read()[0]
        self.assertTrue(row['asset']['path'].endswith('.silk'))
        self.assertFalse(row['asset']['playable'])

    def test_discovery_reads_only_headers_and_never_guesses_self_id(self):
        source = self.base / 'xwechat_files' / 'directory-label' / 'db_storage'
        source.mkdir(parents=True)
        create_shard(source)
        result = discover([source.parent.parent])
        self.assertEqual(len(result['accounts']), 1)
        self.assertEqual(result['accounts'][0]['identityStatus'], 'directory_label_only')
        self.assertFalse(result['accounts'][0]['databases'][0]['encrypted'])

    def test_record_nested_media_link_and_quote(self):
        nested = '<recordinfo><datalist><dataitem datatype="5"><sourcename>甲</sourcename><weburl>https://example.org/</weburl></dataitem></datalist></recordinfo>'
        body = '<msg><appmsg><type>19</type><recorditem><![CDATA[' + nested + ']]></recorditem></appmsg></msg>'
        parsed = interpret(body, (19 << 32) | 49)
        self.assertEqual(parsed['record']['items'][0]['links'], ['https://example.org/'])
        quote = interpret('<msg><appmsg><type>57</type><title>回复</title><refermsg><type>1</type><displayname>甲</displayname><content>原文</content></refermsg></appmsg></msg>', 49)
        self.assertEqual(quote['quote']['message']['text'], '原文')

    def test_dtd_and_unknown_types_are_not_false_success(self):
        parsed = interpret('<!DOCTYPE msg [<!ENTITY x "xx">]><msg>&x;</msg>', 3)
        self.assertNotEqual(parsed['parseStatus'], 'ok')
        self.assertEqual(interpret('<msg/>', 987)['parseStatus'], 'unknown_type')

    def test_orphan_tables_report_coverage_gap(self):
        archive = self.archive()
        path = self.source / 'message/message_0.db'
        with closing(sqlite3.connect(path)) as db, db:
            db.execute('CREATE TABLE Msg_' + 'a' * 32 + '(local_id)')
        archive = LocalArchive(self.source)
        self.assertEqual(len(archive.groups()['coverage']['unmappedMessageTables']), 1)

    def test_workbench_reuses_snapshot_only_and_rejects_foreign_snapshot(self):
        root = self.base / 'runtime'
        root.mkdir()
        config = root / 'database-config.json'
        config.write_text(json.dumps({'snapshotRoot': str(self.source), 'config': {}}))
        with self.assertRaises(ValueError):
            workbench_source(config)


if __name__ == '__main__':
    unittest.main()
