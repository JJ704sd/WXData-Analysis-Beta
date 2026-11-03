"""Owner detection for SQLite shared memory uses synthetic files and OS seams."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from offline_accounts import identify_accounts, account_process_hint, diagnostic_report
import json
import test_group_key_recovery43 as recovery_fixture
import wecom_key_recovery


class SharedMemoryOwnership67Tests(unittest.TestCase):
    def test_shm_owner_confirms_account_without_guessing_from_process_presence(self):
        with tempfile.TemporaryDirectory(prefix='synthetic-owner67-') as folder:
            root=Path(folder).resolve();db=root/'message.db';db.write_bytes(b'synthetic')
            shm=root/'message.db-shm';shm.write_bytes(b'synthetic')
            scan={'accounts':[{'sourceRoot':str(root),'directoryLabel':'selected','databases':[{'path':'message.db'}]}]}
            result=identify_accounts(scan,'wecom',ownership=lambda paths,_: [(91,300)] if shm in paths else [])
            self.assertEqual(result['accounts'][0]['processState'],'active')
            self.assertEqual(result['accounts'][0]['processIds'],[91])

    def test_unchecked_shm_link_prevents_active_claim(self):
        with tempfile.TemporaryDirectory(prefix='synthetic-owner67-') as folder:
            root=Path(folder).resolve();(root/'message.db').write_bytes(b'synthetic')
            with patch.object(Path,'is_symlink',lambda p: p.name.endswith('-shm')):
                (root/'message.db-shm').write_bytes(b'synthetic')
                result=identify_accounts({'accounts':[{'sourceRoot':str(root),'databases':[{'path':'message.db'}]}]},
                    'wecom',ownership=lambda *_:[(91,300)])
            self.assertEqual(result['accounts'][0]['processState'],'unavailable')


class SharedMemoryRecovery67Tests(unittest.TestCase):
    setUp = recovery_fixture.WeComDatabaseKeyRecoveryTests.setUp
    fake_owner = recovery_fixture.WeComDatabaseKeyRecoveryTests.fake_owner
    def test_recovery_uses_message_wal_and_shm_before_reading_selected_owner(self):
        wal=self.source/'message.db-wal';shm=self.source/'message.db-shm'
        wal.write_bytes(b'fixture');shm.write_bytes(b'fixture')
        context,kernel=self.fake_owner([b'A'*16,b'B'*16])
        with context, patch('acquire_database_keys.WindowsDependencies._file_owners',
            side_effect=lambda paths: [(101,123)] if shm in paths else []) as owners:
            result=wecom_key_recovery.recover_key_file(self.source,self.base/'profile',allow_process_read=True)
        self.assertTrue(Path(result['keyFile']).is_file())
        self.assertEqual(owners.call_args_list[0].args[0],[self.source/'message.db',wal,shm])
        kernel.OpenProcess.assert_called_once_with(0x410,False,101)


class ProcessDiagnostic67Tests(unittest.TestCase):
    def test_client_presence_does_not_authenticate_an_account(self):
        with tempfile.TemporaryDirectory(prefix='synthetic-owner67-') as folder:
            root=Path(folder).resolve();(root/'message.db').write_bytes(b'synthetic')
            discovery={'accounts':[{'sourceRoot':str(root),'directoryLabel':'private-account',
                'databases':[{'path':'message.db'}]}]}
            with patch('offline_accounts._windows_owners',return_value=[]), patch('offline_accounts.client_processes',
                return_value=[{'pid':91,'metadataReadable':True},{'pid':92,'metadataReadable':False}]):
                scan=identify_accounts(discovery,'wecom')
            report=diagnostic_report(scan,'wecom')
            self.assertEqual((report['clientProcessCount'],report['processMetadataDenied']),(2,1))
            self.assertEqual(report['accounts'][0]['processState'],'inactive')
            self.assertIsNone(scan['recommendedSourceRoot'])
            self.assertIn('检测到 2 个企微进程',report['accounts'][0]['hint'])
            self.assertNotIn('private-account',json.dumps(report))
            self.assertNotIn(str(root),json.dumps(report))

    def test_unreadable_probe_is_not_reported_as_client_absence(self):
        with patch('offline_accounts.client_processes',side_effect=PermissionError('private-path')):
            scan=identify_accounts({'accounts':[]},'wecom')
        self.assertIsNone(scan['clientProcessCount'])
        hint=account_process_hint({'processState':'inactive'},scan,'wecom')
        self.assertIn('检测也未完成',hint)
        self.assertNotIn('未发现企微',hint)

    def test_cli_diagnostic_does_not_load_source_recover_keys_or_create_profile(self):
        from local_chat_app import main
        with tempfile.TemporaryDirectory(prefix='synthetic-owner67-') as folder:
            root=Path(folder);out=root/'report.json';profile=root/'not-created'
            with patch('wecom_snapshot.discover',return_value={'accounts':[]}), patch('offline_accounts.client_processes',return_value=[]), \
                patch('wecom_key_recovery._recover_key') as recover, patch('wecom_snapshot.prepare_snapshot') as load:
                self.assertEqual(main(['--client','wecom','--data-dir',str(profile),'--diagnose-accounts',str(out)]),0)
            report=json.loads(out.read_text(encoding='utf8'))
            self.assertEqual(report['clientProcessCount'],0)
            self.assertFalse(profile.exists());recover.assert_not_called();load.assert_not_called()


if __name__=='__main__': unittest.main()
