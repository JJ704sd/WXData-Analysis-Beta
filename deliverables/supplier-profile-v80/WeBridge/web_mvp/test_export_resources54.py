"""Export failures must release snapshot files even while diagnostics retain a traceback."""
import itertools
import os
from pathlib import Path
import tempfile
import traceback
import unittest
from unittest.mock import patch

from offline_desktop import DesktopSession
from test_four_exports53 import CASES, source_fixture
from test_database_adapter import create_shard


class ExportResources54Tests(unittest.TestCase):
    @unittest.skipUnless(os.name == 'nt', 'Windows file sharing regression')
    def test_interrupted_parse_releases_files_and_preserves_previous_review(self):
        for client, mode in CASES:
            with self.subTest(client=client, mode=mode), tempfile.TemporaryDirectory() as folder:
                base = Path(folder)
                config, chat, _ = source_fixture(base, client, mode, count=0)
                if client == 'wechat':
                    create_shard(Path(config['sourceRoot']), relative='message/extra.db', group=chat,
                                 rows=[{'local': 200+i, 'text': 'resource fixture '+str(i)} for i in range(4)])
                session = DesktopSession(base / 'profile', client=client, conversation_mode=mode)
                session.open_source(config)
                previous = session.prepare_review(chat, copy_media=False, load_view=False)
                prior_root = Path(previous['view']['viewRoot'])
                prior_bytes = (prior_root / 'messages.json').read_bytes()
                failure = None

                def interrupt(value):
                    if value.get('stage') == 'parsing' and value.get('processed', 0) >= 1:
                        raise OSError('synthetic interruption while streaming')

                try:
                    with patch('offline_chat.time.monotonic', side_effect=itertools.count()):
                        try:
                            session.prepare_review(chat, copy_media=False, load_view=False, progress=interrupt)
                        except OSError as error:
                            failure = error
                    self.assertIsNotNone(failure)
                    self.assertEqual((prior_root / 'messages.json').read_bytes(), prior_bytes)
                    # A failed export's retained traceback must not lock its snapshots.
                    for source in (base / 'profile' / 'snapshots').rglob('*.db'):
                        moved = source.with_suffix('.released')
                        source.rename(moved)
                        moved.rename(source)
                    session.activate_review(chat)
                    self.assertEqual(session.pending_review['summary']['reviewId'], previous['review']['reviewId'])
                    retried = session.prepare_review(chat, copy_media=False, load_view=False)
                    self.assertEqual((Path(retried['view']['viewRoot']) / 'messages.json').read_bytes(), prior_bytes)
                finally:
                    if failure is not None:
                        traceback.clear_frames(failure.__traceback__)


if __name__ == '__main__':
    unittest.main()
