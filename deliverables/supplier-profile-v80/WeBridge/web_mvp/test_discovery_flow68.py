"""Targeted launch-root propagation and restored-selection confirmation."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch

from offline_desktop import DesktopSession
from offline_launcher import launch
from offline_accounts import account_scan_hint
from test_wecom_archive import fixture
import test_group_ui42 as ui_fixture
from test_offline_multiselect import multi_group_fixture


class DiscoveryFlow68Tests(unittest.TestCase):
    def test_session_scan_uses_explicit_root_without_persisting_it(self):
        with tempfile.TemporaryDirectory(prefix='synthetic-flow68-') as folder:
            base=Path(folder).resolve();root=base/'指定目录';root.mkdir();fixture(root/'account')
            session=DesktopSession(base/'profile',client='wecom',scan_roots=[str(root)])
            self.assertEqual(len(session.scan()['accounts']),1)
            self.assertFalse(session.settings_path.exists())

    def test_desktop_request_passes_unicode_scan_root_to_launched_process(self):
        with tempfile.TemporaryDirectory(prefix='synthetic-flow68-') as folder:
            base=Path(folder).resolve();requests=base/'profile/requests';requests.mkdir(parents=True)
            root=base/'指定目录';task=requests/'test.desktop.json';out=requests/'launch.json'
            task.write_text(json.dumps({'version':1,'client':'wecom','roots':[str(root)]}),encoding='utf8')
            with patch('offline_launcher.subprocess.Popen') as popen:
                self.assertEqual(launch(task,out,base/'profile','wecom'),0)
            popen.assert_called_once()
            result=json.loads(out.read_text(encoding='utf8'));raw=Path(result['shortcut']).read_bytes()
            self.assertIn('--scan-root'.encode('utf-16-le'),raw)
            self.assertIn(str(root).encode('utf-16-le'),raw)

    def test_scan_states_have_distinct_next_actions(self):
        self.assertIn('未发现本地账号目录',account_scan_hint({'accounts':[]}))
        self.assertIn('已有密钥',account_scan_hint({'accounts':[{}],'activeAccountCount':0}))
        self.assertIn('选择要解析的账号',account_scan_hint({'accounts':[{},{}],'activeAccountCount':2}))


class RestoredSelection68Tests(unittest.TestCase):
    setUp=ui_fixture.GroupUI42Tests.setUp
    tearDown=ui_fixture.GroupUI42Tests.tearDown
    app=ui_fixture.GroupUI42Tests.app

    def test_restored_picks_stay_idle_and_cancel_does_not_start_old_scope(self):
        app=self.app('wecom');source=self.base/'source';chats=multi_group_fixture(source,'wecom')
        app.session.open_source({'sourceRoot':str(source)})
        app.session.remember_selection(chats,None)
        app.ready(app.session.bootstrap(resume=True))
        self.assertEqual(app.selected_chats,set(chats))
        self.assertTrue(app.restored_selection_pending)
        self.assertFalse(app.batch_running)
        self.assertIn('尚未开始解析',app.status.get())
        with patch('local_chat_app.messagebox.askyesno',return_value=False) as confirm:
            app.choose_search_result()
        confirm.assert_called_once();self.assertFalse(app.batch_running)
        self.assertEqual(app.batch_queue,[])
        with patch('local_chat_app.messagebox.askyesno',return_value=True) as confirm,patch.object(app,'parse_next'):
            app.choose_search_result()
        self.assertIn('2 个会话',confirm.call_args.args[1])
        self.assertEqual(app.batch_queue,chats)
        self.assertFalse(app.restored_selection_pending)


if __name__=='__main__':unittest.main()
