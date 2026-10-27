"""Roaming/legacy discovery uses synthetic layouts and substituted owner evidence."""
from pathlib import Path
import os
import tempfile
import time
import unittest
from unittest.mock import patch

from wecom_snapshot import discover
from offline_accounts import identify_accounts
from test_wecom_archive import fixture


class WeComDiscovery68Tests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory(prefix='synthetic-discovery68-');self.addCleanup(temp.cleanup)
        self.base=Path(temp.name).resolve()
        self.documents=self.base/'Documents';self.documents.mkdir()
        self.roaming=self.base/'Roaming';self.local=self.base/'Local'
        self.boundary=patch.dict(os.environ,{'APPDATA':str(self.roaming),'LOCALAPPDATA':str(self.local)})
        self.boundary.start();self.addCleanup(self.boundary.stop)
        self.hints=patch('windows_data_paths.path_hints',return_value=[self.documents/'xwechat_files'])
        self.hints.start();self.addCleanup(self.hints.stop)

    def source(self,root,label):
        account=root/label;account.mkdir(parents=True);fixture(account/'Data')
        return account

    def test_default_finds_roaming_without_documents_account(self):
        account=self.source(self.roaming/'Tencent/WXWork/Data','selected')
        self.assertEqual([Path(a['sourceRoot']) for a in discover()['accounts']],[account])

    def test_default_finds_localappdata_alternative(self):
        account=self.source(self.local/'Tencent/WXWork/Data','selected')
        self.assertIn(account,[Path(a['sourceRoot']) for a in discover()['accounts']])

    def test_same_label_old_copy_is_kept_and_live_source_wins_only_by_ownership(self):
        old=self.source(self.documents/'WXWork','same-label')
        live=self.source(self.roaming/'Tencent/WXWork/Data','same-label')
        when=time.time()-400*86400;os.utime(old/'Data/message.db',(when,when))
        scan=identify_accounts(discover(),'wecom',ownership=lambda paths,_:[(91,300)] if paths[0].parent==live/'Data' else [])
        self.assertEqual(len(scan['accounts']),2)
        self.assertEqual(scan['recommendedSourceRoot'],str(live))
        self.assertEqual(scan['accounts'][0]['processState'],'active')
        self.assertTrue(scan['accounts'][1]['stale'])
        self.assertGreater(scan['accounts'][0]['messageDbModifiedNs'],scan['accounts'][1]['messageDbModifiedNs'])

    def test_explicit_account_and_data_aliases_are_deduplicated(self):
        account=self.source(self.roaming/'Tencent/WXWork/Data','selected')
        rows=discover([account/'Data',account,account.parent])['accounts']
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['directoryLabel'],'selected')
        self.assertEqual(Path(rows[0]['sourceRoot']),account)

    def test_two_live_accounts_never_recommend_by_size_or_age(self):
        first=self.source(self.roaming/'Tencent/WXWork/Data','first')
        self.source(self.roaming/'Tencent/WXWork/Data','second')
        scan=identify_accounts(discover(),'wecom',ownership=lambda paths,_:[(91 if paths[0].parent==first/'Data' else 92,300)])
        self.assertEqual(scan['activeAccountCount'],2)
        self.assertIsNone(scan['recommendedSourceRoot'])


if __name__=='__main__':unittest.main()
