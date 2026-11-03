"""Synthetic end-to-end checks for parsed-cache viewing and review boundaries."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import Request, urlopen
from unittest.mock import patch

from offline_chat import ArchiveError
from offline_chat_viewer import EmbeddedChatViewer
from offline_desktop import DesktopSession
from offline_review import REVIEW_CHECKS, inventory
from test_database_adapter import create_metadata, create_shard, GROUP, SELF
from test_private_chat_integration import direct_source
from test_wecom_archive import fixture


def json_get(base, path):
    with urlopen(base + path, timeout=10) as response:
        return json.load(response)


class ChatViewerIntegrationTests(unittest.TestCase):
    def test_simultaneous_windows_keep_their_own_search_results(self):
        from test_offline_multiselect import multi_group_fixture
        with tempfile.TemporaryDirectory(prefix='synthetic-chat-viewer-concurrent-') as temp:
            base = Path(temp)
            first, second = multi_group_fixture(base / 'source', 'wecom')
            session = DesktopSession(base / 'profile', client='wecom', conversation_mode='group')
            session.open_source({'sourceRoot': str(base / 'source'), 'selfId': '11', 'includeMedia': False})
            parsed = [session.prepare_review(chat, copy_media=False) for chat in (first, second)]
            viewers = [EmbeddedChatViewer(session.directory), EmbeddedChatViewer(session.directory)]
            try:
                hits = []
                for viewer, chat, item in zip(viewers, (first, second), parsed):
                    viewer.open_chat(item['view']['viewRoot'], chat_id=chat, client='wecom', kind='group', open_browser=False)
                    viewer.server.global_index.prepare()
                    deadline = time.monotonic() + 10
                    while time.monotonic() < deadline and viewer.server.global_index.status()['state'] == 'building':
                        time.sleep(.02)
                    results = viewer.server.global_index.query({'q': '企微消息'})
                    self.assertEqual(results['total'], 1)
                    hits.append(results['items'][0])
                hit = hits[0]
                chat, _ = viewers[0].server.global_index.resolve(hit['recordId'], hit['chatKey'], hit['fingerprint'])
                self.assertEqual(chat.chat_id, first)
                primary = viewers[0].server.global_index.directory
                self.assertNotEqual(primary, viewers[1].server.global_index.directory)
                viewers[0].close()
                reopened = EmbeddedChatViewer(session.directory)
                try:
                    reopened.open_chat(parsed[0]['view']['viewRoot'], chat_id=first, client='wecom',
                                       kind='group', open_browser=False)
                    self.assertEqual(reopened.server.global_index.directory, primary)
                    other = hits[1]
                    chat, _ = viewers[1].server.global_index.resolve(other['recordId'], other['chatKey'], other['fingerprint'])
                    self.assertEqual(chat.chat_id, second)
                finally:
                    reopened.close()
            finally:
                for viewer in viewers:
                    viewer.close()

    def test_desktop_button_opens_selected_cache_in_all_four_modes(self):
        project = Path(__file__).resolve().parents[1]
        code = r'''
import json, sys, time, tkinter as tk
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
from urllib.request import urlopen
from local_chat_app import ChatApp
from offline_desktop import DesktopSession
profile, client, mode, chat = sys.argv[1:]
root = tk.Tk(); root.withdraw()
session = DesktopSession(Path(profile), client=client, conversation_mode=mode)
app = ChatApp(root, session, open_chat=chat)
assert str(app.chat_viewer_button.cget('state')) == 'disabled'
deadline = time.monotonic() + 25
while time.monotonic() < deadline:
    root.update()
    if chat in app.parsed_results and not app.busy and not app.batch_running and not app.read_busy:
        break
    time.sleep(.01)
assert app.active_chat == chat and app.parsed_chat == chat
assert str(app.chat_viewer_button.cget('state')) == 'normal'
assert app.chat_viewer_button.winfo_manager() == 'pack'
with patch('offline_chat_viewer.webbrowser.open', return_value=True) as browser:
    app.chat_viewer_button.invoke(); root.update()
    assert browser.call_count == 1
    url = browser.call_args.args[0]
    split = urlsplit(url); key = parse_qs(split.query)['chat'][0]
    with urlopen(f'{split.scheme}://{split.netloc}/api/conversations', timeout=5) as response:
        chats = json.load(response)['chats']
    selected = next(item for item in chats if item['key'] == key)
    assert selected['client'] == client and selected['kind'] == mode and selected['preview']
assert session.review_status(chat) == '待复核' and not session._exports
root.destroy()
assert app.chat_viewer is None
'''
        for client in ('wechat', 'wecom'):
            for kind in ('group', 'direct'):
                with self.subTest(client=client, kind=kind), tempfile.TemporaryDirectory(prefix='synthetic-chat-viewer-ui-') as temp:
                    base = Path(temp)
                    if kind == 'direct':
                        source, chat, own = direct_source(base, client)
                    elif client == 'wechat':
                        source = base / 'source'; source.mkdir()
                        create_metadata(source); create_shard(source)
                        chat, own = GROUP, SELF
                    else:
                        source = base / 'source'; fixture(source)
                        chat, own = 'R:synthetic', '11'
                    profile = base / 'profile'; profile.mkdir()
                    settings = {'version': 1, 'client': client, 'source':
                                {'sourceRoot': str(source), 'selfId': own, 'includeMedia': False}}
                    (profile / 'settings.local.json').write_text(json.dumps(settings), encoding='utf-8')
                    environment = os.environ.copy()
                    environment['PYTHONPATH'] = os.pathsep.join((str(project / 'web_mvp'), str(project / 'scripts'),
                                                                  environment.get('PYTHONPATH', '')))
                    completed = subprocess.run([sys.executable, '-X', 'utf8', '-c', code, str(profile), client, kind, chat],
                                               env=environment, capture_output=True, timeout=40)
                    self.assertEqual(completed.returncode, 0, completed.stderr.decode('utf-8', errors='replace'))

    def test_four_parsers_open_exact_unreviewed_cache_without_publishing(self):
        for client in ('wechat', 'wecom'):
            for kind in ('group', 'direct'):
                with self.subTest(client=client, kind=kind), tempfile.TemporaryDirectory(prefix='synthetic-chat-viewer-') as temp:
                    base = Path(temp)
                    if kind == 'direct':
                        source, chat, own = direct_source(base, client)
                    elif client == 'wechat':
                        source = base / 'source'
                        source.mkdir()
                        create_metadata(source)
                        create_shard(source)
                        chat, own = GROUP, SELF
                    else:
                        source = base / 'source'
                        fixture(source)
                        chat, own = 'R:synthetic', '11'
                    session = DesktopSession(base / 'profile', client=client, conversation_mode=kind)
                    session.open_source({'sourceRoot': str(source), 'selfId': own, 'includeMedia': False})
                    parsed = session.prepare_review(chat, copy_media=False)
                    root = Path(parsed['view']['viewRoot'])
                    before = inventory(root)
                    viewer = EmbeddedChatViewer(session.directory)
                    self.addCleanup(viewer.close)
                    with patch('offline_chat_viewer.webbrowser.open', return_value=True) as browser:
                        opened = viewer.open_chat(root, chat_id=chat, client=client, kind=kind)
                    browser.assert_called_once_with(opened['url'], new=2)
                    url = urlsplit(opened['url'])
                    base_url = f'{url.scheme}://{url.netloc}'
                    key = parse_qs(url.query)['chat'][0]
                    chats = json_get(base_url, '/api/conversations')['chats']
                    selected = next(item for item in chats if item['key'] == key)
                    self.assertEqual((selected['client'], selected['kind'], selected['preview']), (client, kind, True))
                    self.assertEqual(selected['reviewStatus'], '以解析工具为准')
                    rows = json_get(base_url, '/api/messages?chat=' + key)['messages']
                    self.assertTrue(rows)
                    self.assertEqual(session.review_status(chat), '待复核')
                    self.assertFalse((root / 'review.json').exists())
                    self.assertFalse(session._exports)
                    self.assertEqual(inventory(root), before)
                    with self.assertRaises(ArchiveError):
                        session.confirm_export(parsed['review']['reviewId'], {}, chat=chat)
                    with self.assertRaises(ValueError):
                        viewer.open_chat(root, chat_id='different-id', client=client, kind=kind, open_browser=False)
                    result = session.confirm_export(parsed['review']['reviewId'],
                              {check: True for check in REVIEW_CHECKS}, 'synthetic test only', chat=chat)
                    self.assertTrue((Path(result['output']) / 'review.json').is_file())
                    viewer.close()

    def test_reused_server_search_and_exact_selection(self):
        from test_offline_multiselect import multi_group_fixture
        with tempfile.TemporaryDirectory(prefix='synthetic-chat-viewer-') as temp:
            base = Path(temp)
            first, second = multi_group_fixture(base / 'source', 'wecom')
            session = DesktopSession(base / 'profile', client='wecom', conversation_mode='group')
            session.open_source({'sourceRoot': str(base / 'source'), 'selfId': '11', 'includeMedia': False})
            views = [session.prepare_review(chat, copy_media=False) for chat in (first, second)]
            before = [inventory(Path(view['view']['viewRoot'])) for view in views]
            viewer = EmbeddedChatViewer(session.directory)
            self.addCleanup(viewer.close)
            urls = [viewer.open_chat(view['view']['viewRoot'], chat_id=chat, client='wecom', kind='group',
                                     open_browser=False)['url'] for chat, view in zip((first, second), views)]
            parsed_urls = [urlsplit(url) for url in urls]
            self.assertEqual(parsed_urls[0].netloc, parsed_urls[1].netloc)
            keys = [parse_qs(url.query)['chat'][0] for url in parsed_urls]
            self.assertEqual(len(set(keys)), 2)
            base_url = f'{parsed_urls[0].scheme}://{parsed_urls[0].netloc}'
            self.assertEqual({c['key'] for c in json_get(base_url, '/api/conversations')['chats']}, set(keys))
            for key, chat in zip(keys, (first, second)):
                rows = json_get(base_url, '/api/messages?chat=' + key)['messages']
                self.assertTrue(rows)
                params = urlencode({'chat': key, 'start': rows[0]['start'], 'end': rows[0]['end']})
                self.assertEqual(json_get(base_url, '/api/record?' + params)['chatId'], chat)
            with urlopen(Request(base_url + '/api/global/prepare', data=b'{}', method='POST',
                    headers={'Origin': base_url, 'Content-Type': 'application/json'}), timeout=10) as response:
                json.load(response)
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                status = json_get(base_url, '/api/global/status')
                if status['state'] in ('ready', 'partial'):
                    break
                time.sleep(.02)
            self.assertEqual(status['ready'], 2)
            found = json_get(base_url, '/api/global/search?q=%E4%BC%81%E5%BE%AE%E6%B6%88%E6%81%AF')
            self.assertEqual({item['chatKey'] for item in found['items']}, set(keys))
            self.assertEqual([inventory(Path(view['view']['viewRoot'])) for view in views], before)
            viewer.close()
            self.assertTrue((session.directory / 'views' / 'chat-viewer' / 'global-search-index-wecom-group').is_dir())
            self.assertTrue(session.clear_account_cache()['ok'])
            self.assertFalse((session.directory / 'views' / 'chat-viewer').exists())
            self.assertTrue((base / 'source' / 'message.db').is_file())


if __name__ == '__main__':
    unittest.main()
