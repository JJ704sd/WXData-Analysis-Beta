"""Open parsed reading caches in the bundled local viewer, without exporting them."""
from dataclasses import replace
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import RLock, Thread
import uuid
import webbrowser

import chat_viewer


class PreviewCatalog(chat_viewer.Catalog):
    """Add explicitly selected session caches to the ordinary export catalog."""

    def __init__(self, directory):
        self.bindings = {}
        # The existing views directory is owned by the tool and covered by its
        # per-client cache reset; source exports stay outside this directory.
        self.state_directory = Path(directory).resolve() / 'views' / 'chat-viewer'
        super().__init__(default_root=self.state_directory / 'no-automatic-exports',
                         config=self.state_directory / 'sources.json')

    def refresh(self):
        with self.lock:
            super().refresh()
            for key, chat in list(self.chats.items()):
                binding = self.bindings.get(str(chat.directory)) if chat.directory else None
                if binding is None:
                    continue
                if (chat.chat_id != binding['chat'] or chat.kind != binding['kind'] or
                        chat.client not in ('unknown', binding['client'])):
                    self.chats.pop(key, None)
                    self.issues.append('解析缓存的会话或平台已变化，请返回工具重新解析。')
                    continue
                self.chats[key] = replace(chat, source='解析预览', client=binding['client'],
                                          preview=True, review_status='以解析工具为准')

    def register_preview(self, root, *, chat_id, client, kind, review_directory):
        if client not in ('wechat', 'wecom') or kind not in ('group', 'direct'):
            raise ValueError('当前客户端或会话类型无效')
        root = Path(root).resolve(strict=True)
        review_directory = Path(review_directory).resolve(strict=True)
        if root.parent != review_directory or not root.is_dir():
            raise ValueError('解析缓存不属于当前工具，请重新解析')
        found = chat_viewer.discover_directory(root, '解析预览')
        if (len(found) != 1 or found[0].chat_id != chat_id or found[0].kind != kind or
                found[0].client not in ('unknown', client)):
            raise ValueError('解析缓存与当前会话不一致，请重新解析')
        binding = {'chat': chat_id, 'client': client, 'kind': kind}
        with self.lock:
            self.bindings[str(root)] = binding
            self.initial_paths = list(self.bindings)
            self.refresh()
            for chat in self.chats.values():
                if chat.directory == root and chat.chat_id == chat_id and chat.preview:
                    return chat.key
        raise ValueError('无法读取当前解析缓存，请重新解析')


class EmbeddedChatViewer:
    """Serve the viewer inside the desktop process using only the standard library."""

    def __init__(self, directory):
        self.directory = Path(directory).resolve()
        self.catalog = PreviewCatalog(self.directory)
        self.server = None
        self.thread = None
        self.lock = RLock()
        self.index_owner = None
        self.index_scope = None

    def _claim_index(self, client, kind):
        """Reuse the primary index only when another window is not using it."""
        primary = self.catalog.state_directory / f'global-search-index-{client}-{kind}'
        owner = chat_viewer.GlobalSearchIndex(self.catalog, primary, chat_viewer.open_conversation_file)
        owner.lock_path = primary / 'viewer-owner.lock'
        handle = owner._lock()
        if handle is not None:
            return primary, handle
        return self.catalog.state_directory / (f'global-search-index-{client}-{kind}-' + uuid.uuid4().hex), None

    def open_chat(self, root, *, chat_id, client, kind, open_browser=True):
        with self.lock:
            if self.index_scope is not None and self.index_scope != (client, kind):
                raise ValueError('查看窗口与当前客户端或会话类型不一致，请重新打开')
            key = self.catalog.register_preview(root, chat_id=chat_id, client=client,
                                                kind=kind, review_directory=self.directory / 'reviews')
            if self.server is None:
                server = ThreadingHTTPServer(('127.0.0.1', 0), chat_viewer.Handler)
                owner = None
                try:
                    index_directory, owner = self._claim_index(client, kind)
                    server.daemon_threads = True
                    server.catalog = self.catalog
                    server.global_index = chat_viewer.GlobalSearchIndex(
                        self.catalog, index_directory, chat_viewer.open_conversation_file)
                    thread = Thread(target=server.serve_forever, kwargs={'poll_interval': 0.1},
                                    daemon=True, name='local-chat-viewer')
                    thread.start()
                except Exception:
                    server.server_close()
                    chat_viewer.GlobalSearchIndex._unlock(owner)
                    raise
                self.server, self.thread, self.index_owner, self.index_scope = server, thread, owner, (client, kind)
            url = f'http://127.0.0.1:{self.server.server_port}/?chat={key}'
        return {'url': url, 'opened': webbrowser.open(url, new=2) if open_browser else False}

    def close(self):
        with self.lock:
            server, self.server = self.server, None
            if server is not None:
                worker, owner = server.global_index.thread, self.index_owner
                try:
                    server.global_index.stop()
                    server.shutdown()
                    server.server_close()
                finally:
                    self.index_owner = self.index_scope = self.thread = None
                    if worker is not None and worker.is_alive() and owner is not None:
                        def unlock_after_worker():
                            worker.join()
                            chat_viewer.GlobalSearchIndex._unlock(owner)
                        Thread(target=unlock_after_worker, daemon=True, name='viewer-index-unlock').start()
                    else:
                        chat_viewer.GlobalSearchIndex._unlock(owner)
