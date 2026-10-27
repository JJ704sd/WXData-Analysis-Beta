"""All fixtures are invented SQLite records, never real WeChat conversations."""
from contextlib import closing
from datetime import datetime
import hashlib
import importlib.util
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

from database_adapter import DatabaseAdapter, DatabaseError


GROUP = 'synthetic@chatroom'
MIXED = 'synthetic@im.chatroom'
ENTERPRISE = 'synthetic@openim'
SELF = 'wxid_synthetic_self'
PEER = 'wxid_synthetic_peer'
STAMP = 1700000000


def field(number, value):
    raw = value.encode() if isinstance(value, str) else value
    size = len(raw)
    encoded = bytearray()
    while size >= 128:
        encoded.append((size & 127) | 128); size >>= 7
    encoded.append(size)
    return bytes([number * 8 + 2]) + bytes(encoded) + raw


def membership():
    return b''.join(field(1, field(1, name) + field(2, label)) for name, label in (
        (SELF, '合成本人'), (PEER, '合成群昵称'), (ENTERPRISE, '合成企业成员')))


def create_metadata(root, *, session_name='SessionTable', member_data=None):
    (root / 'contact').mkdir(parents=True)
    with closing(sqlite3.connect(root / 'contact/contact.db')) as db:
        db.execute('CREATE TABLE contact(username,remark,nick_name,alias,delete_flag)')
        db.executemany('INSERT INTO contact VALUES (?,?,?,?,?)', [
            (GROUP, '合成普通群', '', '', 0),
            (MIXED.encode(), '', '合成混合群'.encode(), '', 0),
            (SELF, '合成本人', '', '', 0), (PEER.encode(), '合成联系人'.encode(), '', '', 0),
            (ENTERPRISE, '', '合成企业联系人', '', 0),
            ('filehelper', '文件传输助手（合成）', '', '', 0),
            ('deleted@chatroom', '已删除', '', '', 1),
        ])
        db.execute('CREATE TABLE chat_room(username,owner,ext_buffer)')
        raw = membership() if member_data is None else member_data
        db.executemany('INSERT INTO chat_room VALUES (?,?,?)', [(GROUP, SELF.encode(), raw), (MIXED, PEER, raw)])
        db.commit()
    (root / 'session').mkdir()
    with closing(sqlite3.connect(root / 'session/session.db')) as db:
        db.execute('CREATE TABLE ' + session_name + '(username,sort_timestamp)')
        db.executemany('INSERT INTO ' + session_name + ' VALUES (?,?)', [(name, STAMP) for name in (GROUP, MIXED, ENTERPRISE, 'filehelper')])
        db.commit()


def create_shard(root, relative='message/message_0.db', *, blob_names=False, rows=None, group=GROUP):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    table = 'Msg_' + hashlib.md5(group.encode()).hexdigest()
    with closing(sqlite3.connect(path)) as db:
        db.execute('CREATE TABLE Name2Id(user_name)')
        names = (SELF, PEER, ENTERPRISE, group)
        db.executemany('INSERT INTO Name2Id VALUES (?)', [(name.encode() if blob_names else name,) for name in names])
        db.execute('CREATE TABLE "' + table + '"(local_id INTEGER,server_id INTEGER,local_type INTEGER,create_time INTEGER,real_sender_id INTEGER,message_content,compress_content,source,atuserlist)')
        for row in rows if rows is not None else [{'text': '合成消息'}]:
            db.execute('INSERT INTO "' + table + '" VALUES (?,?,?,?,?,?,?,?,?)', (
                row.get('local', 1), row.get('server', 0), row.get('type', 1), row.get('time', STAMP),
                row.get('sender', 2), row.get('text', '合成消息'), row.get('compressed'),
                row.get('source'), row.get('atuserlist')))
        db.commit()
    return path


class DatabaseAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='webridge-synthetic-adapter-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        create_metadata(self.root)

    def adapter(self, **kwargs):
        return DatabaseAdapter(self.root, **kwargs)

    def messages(self, adapter, group=GROUP):
        return adapter.call('messages', account=adapter.auth()['sourceId'], groupId=group)

    def test_expanded_message_history_limit(self):
        create_shard(self.root,rows=[{'local':i+1,'time':STAMP+i,'text':'history '+str(i)} for i in range(510)])
        adapter=self.adapter();params={'account':adapter.auth()['sourceId'],'groupId':GROUP}
        self.assertEqual(len(adapter.call('messages',**params)['messages']),200)
        expanded=adapter.call('messages',**params,limit=500)['messages']
        self.assertEqual(len(expanded),500)
        self.assertEqual(expanded[0]['text'],'history 10')
        self.assertEqual(expanded[-1]['text'],'history 509')
        self.assertEqual(len(adapter.call('messages',**params,limit=2000)['messages']),510)
        with self.assertRaises(DatabaseError):adapter.call('messages',**params,limit=2001)

    def test_unconfigured_and_explicit_read_only_actions(self):
        adapter = DatabaseAdapter()
        self.assertEqual(adapter.auth()['status'], 'unconfigured')
        self.assertTrue(adapter.read_only)
        for action in ('send', 'media', 'login', 'logout'):
            with self.assertRaises(DatabaseError) as caught:
                adapter.call(action)
            self.assertEqual(caught.exception.code, 'read_only_source')
        for method in (adapter.login, adapter.logout):
            with self.assertRaises(DatabaseError):
                method()
        with self.assertRaises(DatabaseError) as caught:
            adapter.call('groups', account='any')
        self.assertEqual(caught.exception.code, 'unconfigured')

    def test_groups_names_mixed_enterprise_and_filehelper_are_preserved(self):
        create_shard(self.root)
        adapter = self.adapter()
        state = adapter.auth()
        self.assertEqual(state['status'], 'snapshot_ready')
        self.assertEqual(state['selfId'], '')
        groups = {row['id']: row for row in adapter.call('groups', account=state['sourceId'])['groups']}
        self.assertEqual(groups[GROUP]['name'], '合成普通群')
        self.assertEqual(groups[MIXED]['conversationKind'], 'group')
        self.assertEqual(groups[MIXED]['name'], '合成混合群')
        self.assertEqual(groups[ENTERPRISE]['conversationKind'], 'direct')
        self.assertIn('filehelper', groups)
        self.assertNotIn('deleted@chatroom', groups)

    def test_member_mapping_and_owner_follow_upstream_shape(self):
        create_shard(self.root)
        adapter = self.adapter(self_id=SELF)
        result = adapter.call('group_info', account=adapter.auth()['sourceId'], groupId=MIXED)
        members = {row['id']: row for row in result['members']}
        self.assertEqual(members[PEER]['name'], '合成群昵称')
        self.assertEqual(members[ENTERPRISE]['kind'], '企业微信')
        self.assertEqual(result['ownerName'], '合成联系人')
        self.assertEqual(result['memberCount'], 3)
        self.assertTrue(result['membersAvailable'])

    def test_unknown_member_schema_does_not_infer_members_from_history(self):
        create_shard(self.root)
        with closing(sqlite3.connect(self.root / 'contact/contact.db')) as db:
            db.execute('UPDATE chat_room SET ext_buffer=?', (b'\x0a\xff',)); db.commit()
        adapter = self.adapter()
        result = adapter.call('members', account=adapter.auth()['sourceId'], groupId=GROUP)
        self.assertEqual(result['members'], [])
        self.assertFalse(result['membersAvailable'])
        self.assertEqual(result['warnings'][0]['code'], 'members_schema_unsupported')

    def test_multishard_local_ids_are_distinct_with_text_and_blob_senders(self):
        create_shard(self.root, rows=[{'text': '分片零', 'sender': 1}])
        create_shard(self.root, 'message/message_1.db', blob_names=True, rows=[{'text': '分片一', 'sender': 3}])
        create_shard(self.root, 'biz_message/biz_message_0.db', rows=[{'text': '企业分片'}])
        adapter = self.adapter(self_id=SELF, source_id='synthetic-source')
        result = self.messages(adapter)['messages']
        self.assertEqual({row['text'] for row in result}, {'分片零', '分片一', '企业分片'})
        self.assertEqual(len({row['id'] for row in result}), 3)
        self.assertTrue(all(row['id'].startswith('synthetic-source|') for row in result))
        self.assertTrue(next(row for row in result if row['text'] == '分片零')['isSelf'])
        self.assertEqual(next(row for row in result if row['text'] == '分片一')['senderId'], ENTERPRISE)
        self.assertEqual(next(row for row in result if row['text'] == '企业分片')['senderName'], '合成联系人')

    def test_self_is_never_guessed_and_visible_at_text_is_not_structured_mention(self):
        create_shard(self.root, rows=[{'text': '@合成本人 请回复', 'sender': 1}])
        row = self.messages(self.adapter())['messages'][0]
        self.assertFalse(row['isSelf']); self.assertFalse(row['isSelfKnown'])
        self.assertFalse(row['mentionSelf']); self.assertEqual(row['mentionStatus'], 'self_unknown')
        row = self.messages(self.adapter(self_id=SELF))['messages'][0]
        self.assertTrue(row['isSelf']); self.assertTrue(row['isSelfKnown'])
        self.assertFalse(row['mentionSelf'])
        self.assertEqual(row['mentionStatus'], 'unknown')

    def test_empty_at_column_does_not_mask_structured_source(self):
        create_shard(self.root, rows=[{'source': '<msgsource><atuserlist>' + SELF + ',notify@all</atuserlist></msgsource>', 'atuserlist': ''}])
        row = self.messages(self.adapter(self_id=SELF))['messages'][0]
        self.assertTrue(row['mentionSelf']); self.assertTrue(row['mentionEveryone'])
        self.assertEqual(row['mentionStatus'], 'structured')

    def test_malformed_mention_metadata_is_reported_without_inference(self):
        create_shard(self.root, rows=[{'source': b'\xff', 'atuserlist': SELF}])
        result = self.messages(self.adapter(self_id=SELF))
        self.assertFalse(result['messages'][0]['mentionSelf'])
        self.assertEqual(result['messages'][0]['mentionStatus'], 'invalid')
        self.assertIn('mention_metadata_invalid', [row['code'] for row in result['warnings']])

    def test_positive_server_ids_deduplicate_only_identical_native_contents(self):
        create_shard(self.root, rows=[{'server': 99, 'text': '相同消息'}])
        create_shard(self.root, 'message/message_1.db', rows=[{'server': 99, 'text': '相同消息'}])
        create_shard(self.root, 'message/message_2.db', rows=[{'server': 99, 'text': '不同消息'}])
        result = self.messages(self.adapter())['messages']
        self.assertEqual(len(result), 2)
        self.assertEqual({row['text'] for row in result}, {'相同消息', '不同消息'})

    def test_rich_media_with_equal_labels_is_not_incorrectly_deduplicated(self):
        create_shard(self.root, rows=[{'server': 99, 'type': 3, 'text': '<msg><img length="123" aeskey="synthetic-secret"/></msg>'}])
        create_shard(self.root, 'message/message_1.db', rows=[{'server': 99, 'type': 3, 'text': '<msg><img length="456"/></msg>'}])
        result = self.messages(self.adapter())['messages']
        self.assertEqual(len(result), 2)
        self.assertEqual({row['media']['size'] for row in result}, {123, 456})
        self.assertNotIn('synthetic-secret', str(result))
        self.assertFalse(any('_contentHash' in row for row in result))

    def test_revoke_removes_original_content_and_media(self):
        create_shard(self.root, rows=[
            {'server': 50, 'type': 3, 'text': '<msg><img length="123"/></msg>'},
            {'local': 2, 'server': 51, 'type': 10002, 'time': STAMP + 1,
             'text': '<sysmsg><revokemsg><newmsgid>50</newmsgid><replacemsg>合成撤回</replacemsg></revokemsg></sysmsg>'}])
        result = self.messages(self.adapter())['messages']
        original = next(row for row in result if row['serverId'] == '50')
        self.assertEqual(original['kind'], 'revoke'); self.assertEqual(original['text'], '合成撤回')
        self.assertNotIn('media', original)

    def test_missing_compression_dependency_preserves_a_typed_placeholder(self):
        create_shard(self.root, rows=[{'compressed': b'\x28\xb5\x2f\xfd' + b'fixture'}])
        with patch.dict('sys.modules', {'zstandard': None}):
            result = self.messages(self.adapter())
        row = result['messages'][0]
        self.assertEqual(row['decodeStatus'], 'missing_zstandard')
        self.assertEqual(row['kind'], 'unsupported')
        self.assertEqual(result['warnings'][0]['messageId'], row['id'])

    @unittest.skipUnless(importlib.util.find_spec('zstandard'), 'Optional zstandard unavailable')
    def test_real_zstd_frames_in_both_content_columns_and_truncation(self):
        import zstandard
        compressed = zstandard.ZstdCompressor().compress('合成压缩正文'.encode())
        create_shard(self.root, rows=[{'text': compressed}, {'local': 2, 'compressed': compressed},
                                    {'local': 3, 'compressed': b'\x28\xb5\x2f\xfd'}])
        rows = {row['localId']: row for row in self.messages(self.adapter())['messages']}
        self.assertEqual(rows[1]['text'], '合成压缩正文')
        self.assertEqual(rows[2]['text'], '合成压缩正文')
        self.assertEqual(rows[3]['decodeStatus'], 'zstd_decode_error')

    @unittest.skipUnless(importlib.util.find_spec('zstandard'), 'Optional zstandard unavailable')
    def test_compressed_expansion_is_bounded(self):
        import zstandard
        from database_adapter import MAX_TEXT
        compressed = zstandard.ZstdCompressor().compress(b'A' * (MAX_TEXT + 1))
        create_shard(self.root, rows=[{'compressed': compressed}])
        result = self.messages(self.adapter())
        self.assertEqual(result['messages'][0]['decodeStatus'], 'content_too_large')

    def test_bad_utf8_retains_message_with_warning(self):
        create_shard(self.root, rows=[{'text': b'hello\xffworld'}])
        result = self.messages(self.adapter())
        self.assertEqual(result['messages'][0]['decodeStatus'], 'utf8_decode_error')
        self.assertNotIn('helloworld', result['messages'][0]['text'])

    def test_every_read_preserves_files_bytes_and_closes_windows_handles(self):
        create_shard(self.root)
        files = sorted(self.root.rglob('*'))
        before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in files if path.is_file()}
        adapter = self.adapter()
        for action in ('groups', 'members', 'group_info', 'messages'):
            adapter.call(action, account=adapter.auth()['sourceId'], groupId=GROUP)
        self.assertEqual(sorted(self.root.rglob('*')), files)
        for path, (data, mtime) in before.items():
            self.assertEqual(path.read_bytes(), data); self.assertEqual(path.stat().st_mtime_ns, mtime)
            moved = path.with_suffix('.moved'); path.rename(moved); moved.rename(path)

    def test_unmerged_wal_rejected_and_empty_wal_accepted(self):
        path = create_shard(self.root)
        wal = Path(str(path) + '-wal'); wal.write_bytes(b'nonempty synthetic WAL')
        with self.assertRaises(DatabaseError) as caught:
            self.adapter()
        self.assertEqual(caught.exception.code, 'unmerged_journal')
        wal.write_bytes(b'')
        self.assertEqual(self.adapter().auth()['status'], 'snapshot_ready')

    def test_failed_reconfigure_preserves_old_source_and_namespace_guards(self):
        create_shard(self.root)
        adapter = self.adapter(source_id='old-source')
        other = self.root / 'other'; other.mkdir()
        (other / 'message_0.db').write_bytes(b'encrypted synthetic bytes')
        with self.assertRaises(DatabaseError):
            adapter.configure(other, source_id='new-source')
        self.assertEqual(adapter.auth()['sourceId'], 'old-source')
        self.assertEqual(len(self.messages(adapter)['messages']), 1)
        with self.assertRaises(DatabaseError) as caught:
            adapter.call('groups', account='new-source')
        self.assertEqual(caught.exception.code, 'source_changed')

    def test_unrelated_sqlite_schema_is_not_reported_as_ready(self):
        with closing(sqlite3.connect(self.root / 'message_0.db')) as db:
            db.execute('CREATE TABLE unrelated(value TEXT)'); db.commit()
        with self.assertRaises(DatabaseError) as caught:
            self.adapter()
        self.assertEqual(caught.exception.code, 'message_schema_unsupported')

    def test_changed_source_is_unavailable_until_reconfigured(self):
        path = create_shard(self.root)
        adapter = self.adapter()
        before = adapter.get_source_info()
        stat = path.stat(); os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1000000000))
        self.assertEqual(adapter.auth()['status'], 'unavailable')
        with self.assertRaises(DatabaseError) as caught:
            self.messages(adapter)
        self.assertEqual(caught.exception.code, 'snapshot_changed')
        adapter.configure(self.root)
        after = adapter.get_source_info()
        self.assertEqual(after['id'], before['id']); self.assertNotEqual(after['revision'], before['revision'])

    def test_added_message_shard_invalidates_previous_revision(self):
        create_shard(self.root)
        adapter = self.adapter()
        create_shard(self.root, 'message/message_1.db', rows=[{'text': '新增分片'}])
        self.assertEqual(adapter.get_source_info()['issueCode'], 'snapshot_changed')
        with self.assertRaises(DatabaseError):
            self.messages(adapter)
        adapter.configure(self.root)
        self.assertEqual(len(self.messages(adapter)['messages']), 2)

    def test_source_info_has_iso_dates_and_no_mutable_alias(self):
        create_shard(self.root)
        supplied = {'revision': 'synthetic-v1', 'warnings': [], 'detail': {'fixture': True}}
        adapter = self.adapter(source_info=supplied)
        supplied['detail']['fixture'] = False
        info = adapter.get_source_info()
        self.assertTrue(info['detail']['fixture'])
        info['detail']['fixture'] = False
        self.assertTrue(adapter.get_source_info()['detail']['fixture'])
        for key in ('createdAt', 'modifiedAt', 'readAt'):
            self.assertIsNotNone(datetime.fromisoformat(info[key]).tzinfo)

    def test_legacy_session_table_name_and_no_recursive_cross_account_scan(self):
        create_shard(self.root)
        with closing(sqlite3.connect(self.root / 'session/session.db')) as db:
            db.execute('ALTER TABLE SessionTable RENAME TO Session'); db.commit()
        nested = self.root / 'other-account'; nested.mkdir()
        create_shard(nested, rows=[{'text': '不应跨账号读取'}])
        adapter = self.adapter()
        self.assertIn('filehelper', {row['id'] for row in adapter.call('groups', account=adapter.auth()['sourceId'])['groups']})
        self.assertEqual([row['text'] for row in self.messages(adapter)['messages']], ['合成消息'])

    def test_read_and_reconfigure_cannot_mix_sources(self):
        create_shard(self.root)
        other = self.root / 'second'; other.mkdir()
        create_metadata(other); create_shard(other, rows=[{'text': '第二来源'}])
        adapter = self.adapter(source_id='first')
        entered, release = threading.Event(), threading.Event()
        original = adapter._messages
        results, errors = [], []
        def blocked(state, group, **options):
            entered.set()
            if not release.wait(5):
                raise RuntimeError('synthetic test release missing')
            return original(state, group, **options)
        def read():
            try:
                results.append(adapter.call('messages', account='first', groupId=GROUP))
            except Exception as exc:
                errors.append(exc)
        def configure():
            try:
                adapter.configure(other, source_id='second')
            except Exception as exc:
                errors.append(exc)
        with patch.object(adapter, '_messages', side_effect=blocked):
            reader = threading.Thread(target=read); reader.start()
            try:
                self.assertTrue(entered.wait(5))
                switch = threading.Thread(target=configure); switch.start()
            finally:
                release.set(); reader.join(5)
            switch.join(5)
        self.assertFalse(reader.is_alive()); self.assertFalse(switch.is_alive()); self.assertEqual(errors, [])
        self.assertEqual(results[0]['messages'][0]['sourceId'], 'first')
        self.assertEqual(self.messages(adapter)['messages'][0]['text'], '第二来源')


if __name__ == '__main__':
    unittest.main()
