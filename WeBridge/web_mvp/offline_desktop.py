"""Local desktop workflow. GUI-independent so startup/export can be verified.

Settings contain file references, never key material. Client/Hook tools are not
called. Snapshots stay in the data directory; group exports may use a selected folder.
"""
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import tempfile
import uuid

from database_adapter import DatabaseError
from database_snapshot import SnapshotError, prepare_snapshot
from offline_chat import ArchiveError, IntegrityScopeError, LocalArchive, discover, workbench_source
from offline_view import ConversationView
from offline_accounts import same_account_source
from offline_progress import ReviewProgress
from offline_direct import conversations as mode_conversations, select as select_conversation, validate_conversation_mode

CONTENT_FIELDS = ('supplementRoot', 'documentPasswordFile', 'ocrEnabled', 'ocrImages')
CONFIG_FIELDS = ('sourceRoot', 'keyFile', 'imageKeyFile', 'selfId', 'includeMedia', 'includeVoice', *CONTENT_FIELDS)


def safe_issue(error):
    if isinstance(error, (ArchiveError, SnapshotError, DatabaseError)):
        return str(error)
    if isinstance(error, PermissionError):
        return '文件访问被系统拒绝，请使用拥有该副本读取权限的本机用户运行。'
    return '无法读取本地数据，请检查数据目录、密钥文件和文件访问权限。'


class DesktopSession:
    def __init__(self, data_directory, workbench=None, *, client='wechat', conversation_mode='group', scan_roots=None):
        if client not in ('wechat', 'wecom'):
            raise ArchiveError('客户端类型必须为 wechat 或 wecom。')
        validate_conversation_mode(conversation_mode)
        self.client = client
        self.conversation_mode = conversation_mode
        self.scan_roots = scan_roots
        self._requested_directory = Path(data_directory).absolute()
        self.directory = Path(data_directory).resolve()
        self.settings_path = self.directory / 'settings.local.json'
        self.workbench = Path(workbench) if workbench else None
        self.settings = self._settings()
        self.archive = None
        self.groups = []
        self.last_export = None
        self.source_status = ''
        self.views = {}
        self.pending_review = None
        self._reviews = {}
        self._published_reviews = {}
        self._exports = {}
        self.last_batch_root = None
        self._live_account_names = {}
        roots = self.settings.get('groupExportRoots', [])
        self._progress = ReviewProgress(self.directory, self.client, conversation_mode,
            export_roots=[root for root in roots if isinstance(root, str) and Path(root).is_absolute()]
            if isinstance(roots, list) else [])
        self._progress_ui = {'selected': [], 'active': None}
        self.restored_results = {}

    def _refresh_conversations(self, archive=None):
        """Refresh the mode-scoped list exposed to the desktop caller."""
        source = archive or self.archive
        self.groups = mode_conversations(source, self.conversation_mode) if source else []
        self.conversations = self.groups
        return self.groups

    def _select_conversation(self, *, chat_id=None, chat_name=None):
        if self.archive is None:
            raise ArchiveError('请先加载数据。')
        return select_conversation(self.archive, chat_id=chat_id, chat_name=chat_name,
                                   mode=self.conversation_mode)

    def _assert_conversation(self, chat):
        return self._select_conversation(chat_id=chat)

    def select_chat(self, chat_id=None, chat_name=None):
        """Public mode-aware selector used by the desktop UI and CLI."""
        return self._select_conversation(chat_id=chat_id, chat_name=chat_name)

    def _settings(self):
        from offline_profiles import reset_requested
        if reset_requested(self.directory):
            return {}
        try:
            if self.settings_path.stat().st_size > 65536:
                return {}
            value = json.loads(self.settings_path.read_text(encoding='utf-8-sig'))
            return value if isinstance(value, dict) and value.get('version') == 1 else {}
        except (OSError, ValueError):
            return {}

    def _save(self, config=None, last_chat=None, *, export_settings=None):
        value = dict(self.settings)
        if export_settings is not None:
            value.update(export_settings)
        value['version'] = 1
        value['client'] = self.client
        if config is not None:
            previous = value.get('source', {}).get('sourceRoot')
            if previous and not same_account_source(previous, config.get('sourceRoot'), self.client):
                value.pop('lastChat', None)
                value.pop('lastDirectChat', None)
            value['source'] = {name: config[name] for name in CONFIG_FIELDS if name in config}
        if last_chat is not None:
            value['lastDirectChat' if self.conversation_mode == 'direct' else 'lastChat'] = last_chat
        self.directory.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix='.settings-', dir=self.directory)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                json.dump(value, stream, ensure_ascii=False, indent=2)
            os.replace(temporary, self.settings_path)
            if config is not None:
                from offline_profiles import RESET_MARKER
                (self.directory / RESET_MARKER).unlink(missing_ok=True)
        finally:
            if Path(temporary).exists():
                Path(temporary).unlink()
        self.settings = value

    @property
    def export_directory(self):
        value = self.settings.get('groupExportDirectory') if self.conversation_mode == 'group' else None
        return Path(value) if isinstance(value, str) and value else self.directory / 'exports'

    def _validate_export_directory(self, folder):
        original = Path(folder).expanduser().absolute()
        if any(p.is_symlink() or p.is_junction() for p in (original, *original.parents)):
            raise ArchiveError('导出位置不能经过链接，请选择实际文件夹。')
        path = original.resolve()
        default = self.directory / 'exports'
        if path != default and (path == self.directory or path.is_relative_to(self.directory)):
            raise ArchiveError('请选择程序缓存目录以外的导出位置。')
        config = self.archive.media_config if self.archive else self.settings.get('source', {})
        if config.get('sourceRoot'):
            source = Path(config['sourceRoot']).resolve()
            account = source.parent if source.name in ('Data', 'db_storage') else source
            if path == account or path.is_relative_to(account):
                raise ArchiveError('导出位置不能位于聊天数据源或账号目录内。')
        return path

    def set_export_directory(self, folder):
        if self.conversation_mode != 'group':
            raise ArchiveError('此设置仅用于群聊导出。')
        path = self._validate_export_directory(folder)
        if not path.is_dir():
            raise ArchiveError('导出位置不存在，请选择已有的本机文件夹。')
        roots = list(self._progress.export_roots)
        if path != self.directory / 'exports' and str(path) not in roots:
            roots.append(str(path))
        self._save(export_settings={'groupExportDirectory': str(path), 'groupExportRoots': roots})
        self._progress.export_roots = roots
        return path

    def set_content_options(self, options, passwords=None):
        if self.archive is None:
            raise ArchiveError('请先选择并加载账号。')
        config = dict(self.archive.media_config)
        for key in CONTENT_FIELDS:
            if key in options: config[key] = options[key]
        for key in ('supplementRoot', 'documentPasswordFile'):
            value = config.get(key)
            if value:
                path = Path(value).expanduser().absolute()
                if not (path.is_dir() if key == 'supplementRoot' else path.is_file()):
                    raise ArchiveError('补充原件目录或密码文件不存在。')
                config[key] = str(path)
        if config.get('documentPasswordFile'):
            from offline_documents import read_passwords
            try: read_passwords(config['documentPasswordFile'])
            except (OSError, ValueError): raise ArchiveError('密码文件须为 schemaVersion=1、以文件SHA256为键的 passwords 对象。')
        self._save(config)
        if passwords is not None: config['_documentPasswords'] = dict(passwords)
        self.archive.media_config = config

    def _outside_source(self, config):
        source = Path(config['sourceRoot']).expanduser().resolve()
        account = source.parent if source.name in ('db_storage', 'Data') else source
        if self.directory == account or self.directory.is_relative_to(account):
            raise ArchiveError('程序数据目录不能位于微信账号目录内，请将程序移到其他位置运行。')

    def open_source(self, config, *, save=True, reset_progress=True):
        if self.settings.get('source') and self.settings.get('client', 'wechat') != self.client:
            raise ArchiveError('当前程序配置属于另一种客户端，请使用各自的启动入口和独立数据目录。')
        config = {name: config[name] for name in CONFIG_FIELDS if name in config}
        if not isinstance(config.get('sourceRoot'), str) or not config['sourceRoot'].strip():
            raise ArchiveError('请选择一个本机账号的数据目录或已经解密的副本目录。')
        config['sourceRoot'] = str(Path(config['sourceRoot']).expanduser().resolve())
        self._outside_source(config)
        snapshot_factory, archive_factory = prepare_snapshot, LocalArchive
        if self.client == 'wecom':
            from wecom_snapshot import prepare_snapshot as snapshot_factory
            from wecom_archive import WeComArchive as archive_factory
        result = snapshot_factory(config['sourceRoot'], self.directory / 'snapshots',
            config.get('keyFile') or None, include_media=config.get('includeMedia', False),
            include_voice=config.get('includeVoice', False))
        archive = archive_factory(result['root'], config.get('selfId', ''), config)
        groups = mode_conversations(archive, self.conversation_mode)
        # Persist only a source that really passed decryption and schema checks.
        self._commit_source(archive, groups, (lambda: self._save(config)) if save else None,
                            reset_progress=reset_progress)
        self.source_status = '已创建并验证新的本地副本'
        return self.ready_info()

    def _commit_source(self, archive, groups, settings_writer=None, *, reset_progress=True):
        """Install a validated source only after its settings and cache transition succeed."""
        if self.archive and same_account_source(self.archive.media_config.get('sourceRoot'), archive.media_config.get('sourceRoot'), self.client):
            archive.media_config['_documentPasswords'] = dict(self.archive.media_config.get('_documentPasswords', {}))
        previous_settings = self.settings
        previous_file = self.settings_path.read_bytes() if settings_writer and self.settings_path.is_file() else None
        previous_inventory = self._progress._snapshot, self._progress._files
        try:
            if self.conversation_mode == 'group':
                self._progress.warm_snapshot(archive)
            if settings_writer:
                settings_writer()
            # clear() runs before discard_review mutates the active in-memory results.
            self.discard_review(persist=reset_progress)
        except FileExistsError:
            self._progress._snapshot, self._progress._files = previous_inventory
            # A concurrent first-use migration owns its new configuration file.
            raise
        except (OSError, ValueError, DatabaseError):
            self._progress._snapshot, self._progress._files = previous_inventory
            self.settings = previous_settings
            if settings_writer:
                try:
                    current_file = self.settings_path.read_bytes() if self.settings_path.is_file() else None
                    if current_file != previous_file:
                        if previous_file is None:
                            self.settings_path.unlink(missing_ok=True)
                        else:
                            fd, temporary = tempfile.mkstemp(prefix='.settings-restore-', dir=self.directory)
                            try:
                                with os.fdopen(fd, 'wb') as stream:
                                    stream.write(previous_file)
                                    stream.flush()
                                    os.fsync(stream.fileno())
                                os.replace(temporary, self.settings_path)
                            finally:
                                Path(temporary).unlink(missing_ok=True)
                except OSError as error:
                    raise ArchiveError('新数据源未切换，当前解析与复核仍保留；账号设置未能恢复，'
                                       '请在关闭或重启前到账号设置重新确认原数据源。') from error
            raise
        self.archive, self.groups, self.conversations = archive, groups, groups
        self.views.clear()

    def bootstrap(self, *, resume=False):
        from offline_profiles import reset_requested
        if reset_requested(self.directory):
            label = '企业微信' if self.client == 'wecom' else '微信'
            return {'status': 'needs_source', 'message': f'首次使用：请重新选择本人{label}账号并配置；旧配置不会自动导入。'}
        saved = self.settings.get('source')
        if isinstance(saved, dict) and saved.get('sourceRoot'):
            if self.settings.get('client', 'wechat') != self.client:
                return {'status': 'needs_source', 'message': '当前程序配置属于另一种客户端，请使用对应入口和独立数据目录。'}
            resume_issue = ''
            try:
                restored = self._progress.restore(saved) if resume else None
                if restored:
                    self.archive, self._reviews = restored['archive'], restored['reviews']
                    self._published_reviews = restored.get('published', {})
                    self._exports = restored.get('exports', {})
                    self.last_batch_root = (restored.get('batchRoots') or [None])[-1]
                    self._refresh_conversations(self.archive)
                    ui = restored['ui'] if isinstance(restored['ui'], dict) else {}
                    available = set(self._reviews) | set(self._published_reviews)
                    known_chats = {group['id'] for group in self.groups}
                    self._progress_ui = {'selected': [chat for chat in ui.get('selected', []) if chat in known_chats],
                                         'active': ui.get('active') if ui.get('active') in available else next(iter(available), None)}
                    self.restored_results = {}
                    for chat, pending in self._reviews.items():
                        self.restored_results[chat] = {'chat': pending['summary']['chat'], 'review': pending['summary'],
                            'report': json.loads((pending['root']/'manifest.json').read_text(encoding='utf8')),
                            'view': {'viewRoot': str(pending['root'])}}
                    for chat, pending in self._published_reviews.items():
                        output = self._exports[chat]
                        self.restored_results[chat] = {'chat': pending['summary']['chat'],
                            'review': pending.get('approval', pending['summary']),
                            'report': json.loads((output/'manifest.json').read_text(encoding='utf8')),
                            'view': {'viewRoot': str(pending['root'])},
                            'output': str(output), 'state': 'exported'}
                    label = '私聊' if self.conversation_mode == 'direct' else '群'
                    self.source_status = (f'已恢复 {len(self._reviews)} 个{label}的解析与复核进度，'
                                          f'已恢复 {len(self._published_reviews)} 个已完成导出；'
                                          '沿用上次副本，新消息请点击刷新')
                    if restored['rejected']:
                        self.source_status += f'；{restored["rejected"]} 项未通过校验，需重新检查'
                    return {**self.ready_info(), 'restoredReviews': len(self._reviews),
                            'restoredPublished': len(self._published_reviews),
                            'resumeSelection': self._progress_ui['selected'],
                            'resumeActiveChat': self._progress_ui['active'],
                            'restoredResults': self.restored_results}
            except (OSError, ValueError, DatabaseError) as error:
                resume_issue = safe_issue(error)
            try:
                value = self.open_source(saved, reset_progress=resume)
                return {**value, 'notice': resume_issue} if resume_issue else value
            except (OSError, ValueError, DatabaseError) as error:
                # Keep the selected source. Never silently switch accounts.
                return {'status': 'needs_source', 'message': safe_issue(error), 'config': saved}
        if self.settings_path.exists() or self.settings_path.is_symlink():
            return {'status': 'needs_source', 'message': '用户配置已存在但无法读取有效数据源；已保留原文件，请在账号设置中修复，或导入旧版配置。'}
        from offline_profiles import default_directory, legacy_settings, read_legacy
        if self.directory == default_directory(self.client):
            paths = legacy_settings(self.client)
            candidates = []
            for path in paths:
                try:
                    value, config = read_legacy(path, self.client)
                    candidates.append((path.stat().st_mtime_ns, path, config))
                except (OSError, ValueError):
                    continue
            if candidates:
                source = candidates[0][2]['sourceRoot']
                if any(not same_account_source(source, config['sourceRoot'], self.client) for _, _, config in candidates[1:]):
                    return {'status': 'needs_source', 'message': '发现多个不同账号的旧配置，未自动选择。请在手动页点击“导入旧版配置”，选择要继续使用的账号配置。'}
                last_error = None
                for _, path, config in sorted(candidates, key=lambda item: item[0], reverse=True)[:8]:
                    try:
                        return self.import_settings(path, overwrite=False)
                    except (OSError, ValueError, DatabaseError) as error:
                        last_error = error
                return {'status': 'needs_source', 'message': '已找到旧配置但校验未通过，旧文件已保留。'+safe_issue(last_error),
                        'config': config}
            if paths:
                return {'status': 'needs_source', 'message': '发现旧配置但格式、客户端或路径不符合要求，已保留旧文件。请在手动页导入正确的旧版配置，或修复数据和密钥路径。'}
        if self.client == 'wechat' and self.workbench and self.workbench.is_file():
            try:
                return self.open_workbench(self.workbench)
            except (OSError, ValueError, KeyError, TypeError, DatabaseError) as error:
                return {'status': 'needs_source', 'message': safe_issue(error)}
        label = '企业微信' if self.client == 'wecom' else '微信'
        return {'status': 'needs_source', 'message': f'首次使用：登录本人{label}后点击“一键自动配置”；已有数据与密钥也可在手动页选择。'}

    def clear_account_cache(self):
        from offline_profiles import clear_profile_cache
        if self.settings and self.settings.get('client', 'wechat') != self.client:
            raise ArchiveError('此数据目录属于另一客户端，未清理；请使用对应客户端的入口。')
        config = self.archive.media_config if self.archive else self.settings.get('source', {})
        try:
            result = clear_profile_cache(self._requested_directory, self.client,
                                         source_root=config.get('sourceRoot', ''))
        except ValueError as error:
            raise ArchiveError(str(error)) from None
        self.settings = {}
        self.archive = None
        self.groups = []
        self.source_status = ''
        self.workbench = None
        self.views.clear()
        self.discard_review()
        self._live_account_names.clear()
        return result

    def import_settings(self, path, *, overwrite=True):
        """Validate before publishing; automatic migration never overwrites a user profile."""
        from offline_profiles import read_legacy, copy_key_references, publish_settings
        from offline_startup import record
        value, config = read_legacy(path, self.client)
        if not overwrite and self.settings_path.exists():
            self.settings = self._settings()
            return self.bootstrap()
        probe = DesktopSession(self.directory, client=self.client, conversation_mode=self.conversation_mode)
        probe.open_source(config, save=False, reset_progress=False)
        config = copy_key_references(config, self.directory)
        value = {**value, 'version': 1, 'client': self.client,
                 'source': {name: config[name] for name in CONFIG_FIELDS if name in config}}
        def write_settings():
            if overwrite:
                probe.settings = value
                probe._save(config)
            else:
                publish_settings(self.settings_path, value)
            self.settings = value
        try:
            self._commit_source(probe.archive, probe.groups, write_settings)
        except FileExistsError:
            if overwrite:
                raise
            self.settings = self._settings()
            return self.bootstrap()
        self.archive.media_config.update(config)
        self.source_status = '已迁移并验证旧配置，后续桌面版与 Skill 共用此账号配置'
        record('profile_migration_completed', client=self.client, copiedKeyReferences=sum(bool(config.get(k)) for k in ('keyFile', 'imageKeyFile')))
        return self.ready_info()

    def open_workbench(self, path):
        snapshot, config = workbench_source(path)
        try:
            return self.open_source(config)
        except (OSError, ValueError, DatabaseError) as error:
            # Fallback is an explicit older-data state, not a successful refresh.
            self._outside_source(config)
            value = prepare_snapshot(snapshot, self.directory / 'snapshots',
                include_media=config.get('includeMedia', False))
            archive = LocalArchive(value['root'], config.get('selfId', ''), config)
            groups = mode_conversations(archive, self.conversation_mode)
            self._commit_source(archive, groups)
            self.source_status = '已加载工作台既有副本，未取得新的客户端数据'
            return {**self.ready_info(), 'notice': safe_issue(error)}

    def ready_info(self):
        info = self.archive.adapter.get_source_info()
        config = self.archive.media_config
        profile = self._account_names({'accounts': [{'sourceRoot': config['sourceRoot']}]})['accounts'][0]
        return {'status': 'ready', 'groups': self.groups, 'conversations': self.groups,
                'conversationMode': self.conversation_mode, 'message': self.source_status,
                'snapshotTime': info['createdAt'], 'coverage': self.archive.coverage(), 'accountProfile': profile}

    def discard_review(self, chat=None, *, persist=True):
        if chat is None:
            if persist:
                self._progress.clear()
            self._reviews.clear()
            self._published_reviews.clear()
            self._exports.clear()
            self.restored_results.clear()
            self._progress_ui = {'selected': [], 'active': None}
            self.last_batch_root = None
        else:
            self._reviews.pop(chat, None)
            self._published_reviews.pop(chat, None)
            self._exports.pop(chat, None)
            self.restored_results.pop(chat, None)
            self._checkpoint()
        self.pending_review = None
        self.last_export = None

    def _checkpoint(self, reviews=None):
        if self.archive is not None:
            value = dict(self._reviews if reviews is None else reviews)
            value.update(self._published_reviews)
            self._progress.save(self.archive, value, self._progress_ui)

    def _checkpoint_with_published(self, reviews, published):
        if self.archive is not None:
            value = dict(reviews)
            value.update(published)
            self._progress.save(self.archive, value, self._progress_ui)

    def remember_selection(self, chats, active=None, *, persist=True):
        known = {group['id'] for group in self.groups}
        self._progress_ui = {'selected': list(dict.fromkeys(chat for chat in chats if chat in known)),
                             'active': active if active in known else None}
        if persist:
            self._checkpoint()

    def activate_review(self, chat):
        """Switch reading targets without losing other groups' unconfirmed batches."""
        self.pending_review = self._reviews.get(chat)
        self.last_export = self._exports.get(chat)

    def prepare_review(self, chat, start=None, end=None, copy_media=True, *, page_size=100,
                       load_view=True, progress=None):
        """Parse into a private reading cache; no final export or human conclusion."""
        from offline_review import inventory, REVIEW_CHECKS
        if self.archive is None:
            raise ArchiveError('请先加载数据。')
        selected = self._assert_conversation(chat)
        review_id = uuid.uuid4().hex
        output = self.directory / 'reviews' / review_id
        report = self.archive.export(output, chat_id=chat, start=start, end=end, copy_media=copy_media,
                                     **({'progress': progress} if progress is not None else {}))
        source = self.archive.media_config.get('sourceRoot', '')
        account = Path(source).parent.name if Path(source).name in ('Data', 'db_storage') else Path(source).name
        summary = {'version': 1, 'reviewId': review_id, 'client': self.client,
                   'conversationMode': self.conversation_mode,
                   'accountDirectory': account, 'selfId': self.archive.state['self_id'],
                   'identityBasis': report['coverage']['identityBasis'],
                   'sourceFingerprint': hashlib.sha256(str(Path(source).resolve()).encode()).hexdigest(),
                   'snapshotTime': self.archive.adapter.get_source_info()['createdAt'],
                   'chat': report['chat'], 'startDate': start, 'endDate': end,
                   'copyMediaRequested': copy_media, 'exportedRows': report['exportedRows'],
                   'kinds': report['kinds'], 'parseStatuses': report['parseStatuses'],
                   'recordIssues': report.get('recordIssues', {}),
                   'decodeStatuses': report['decodeStatuses'], 'mediaSummary': report['mediaSummary'],
                   'coverage': report['coverage'], 'warnings': report['warnings'],
                   'limitations': report['limitations'],
                   'peerId': selected.get('peerId') if self.conversation_mode == 'direct' else None,
                   'relationship': selected.get('relationship', 'unknown') if self.conversation_mode == 'direct' else None}
        def report_progress(stage):
            if progress is not None:
                progress({'stage': stage, 'processed': report['exportedRows'],
                          'chatId': chat, 'chatName': selected['name']})

        new_view = None
        view = {'viewRoot': str(output)}
        if load_view:
            report_progress('indexing')
            new_view = ConversationView(output)
            view = new_view.page(kind='business', page_size=page_size)
        new_pending = {'archive': self.archive, 'root': output,
                       'files': inventory(output), 'summary': summary}
        projected = dict(self._reviews)
        projected[chat] = new_pending
        projected_published = dict(self._published_reviews)
        projected_published.pop(chat, None)
        # Persist the new parse while the old in-memory review remains active.
        # A parse or checkpoint failure therefore leaves the previous review
        # and its export state available for retry.
        # Save the last-contact setting before the checkpoint so a settings
        # failure cannot leave memory switched to an unpersisted review.
        report_progress('saving')
        self._save(last_chat=chat)
        self._checkpoint_with_published(projected, projected_published)
        self._reviews = projected
        self._published_reviews = projected_published
        self._exports.pop(chat, None)
        self.last_export = None
        if new_view is None:
            self.views.pop(chat, None)
        else:
            self.views[chat] = new_view
        self.pending_review = new_pending
        self.restored_results.pop(chat, None)
        return {'chat': selected, 'report': report, 'view': view,
                'review': summary, 'reviewChecks': dict(REVIEW_CHECKS), 'state': 'awaiting_review'}

    def confirm_export(self, review_id, checks, note='', *, chat=None):
        """Export an unchanged reviewed batch; never parse a newer result here."""
        self.approve_review(review_id, checks, note, chat=chat)
        pending = self.pending_review
        output = self._validate_export_directory(self.export_directory) / (datetime.now().strftime('%Y%m%d-%H%M%S-%f') + '-' + review_id[:8])
        return self._publish_pending(pending, output)

    def approve_review(self, review_id, checks, note='', *, chat=None):
        from offline_review import approve_review
        pending = self.pending_review
        if (not pending or pending['archive'] is not self.archive or
                pending['summary']['reviewId'] != review_id or
                (chat is not None and pending['summary']['chat']['id'] != chat)):
            label = '私聊联系人' if self.conversation_mode == 'direct' else '群聊'
            raise ArchiveError(f'复核目标已失效，请重新解析当前账号和{label}。')
        self.archive._check()
        approval = approve_review(pending, checks, note)
        self._checkpoint({**self._reviews, pending['summary']['chat']['id']: {**pending, 'approval': approval}})
        pending['approval'] = approval
        return {'state': 'reviewed', 'review': pending['approval'], 'chat': pending['summary']['chat']}

    def review_status(self, chat):
        if chat in self._exports:
            return '已导出'
        pending = self._reviews.get(chat)
        return ('已复核' if pending.get('approval') else '待复核') if pending else '未解析'

    def approve_reviews(self, review_ids, checks, note=''):
        """Stage every approval before saving any; bind confirmation to exact previews."""
        from offline_review import approve_review, validated_approval
        if (not isinstance(review_ids, dict) or not review_ids or
                any(not isinstance(chat, str) or not isinstance(rid, str) for chat, rid in review_ids.items())):
            label = '私聊' if self.conversation_mode == 'direct' else '群聊'
            raise ArchiveError(f'请先勾选已解析的待复核{label}。')
        if self.archive is None:
            raise ArchiveError('请先加载数据。')
        self.archive._check()
        staged, already = [], []
        for chat, rid in review_ids.items():
            pending = self._reviews.get(chat)
            if (not pending or pending['archive'] is not self.archive or
                    pending['summary']['chat']['id'] != chat or pending['summary']['reviewId'] != rid):
                label = '私聊' if self.conversation_mode == 'direct' else '群聊'
                raise ArchiveError(f'所选{label}的复核目标已失效，请重新解析并复核；本次未保存新的复核。')
            if pending.get('approval'):
                validated_approval(pending)
                already.append(chat)
            else:
                staged.append((pending, approve_review(pending, checks, note)))
        batch_id = uuid.uuid4().hex
        confirmed_ids = [pending['summary']['chat']['id'] for pending, _ in staged]
        reviews = []
        for pending, approval in staged:
            approval.update(confirmationBasis='explicit_batch_review_checklist',
                            batchReview={'id': batch_id, 'chatIds': confirmed_ids, 'count': len(staged)})
            reviews.append({'state': 'reviewed', 'review': approval, 'chat': pending['summary']['chat']})
        projected = dict(self._reviews)
        projected.update({pending['summary']['chat']['id']: {**pending, 'approval': approval} for pending, approval in staged})
        self._checkpoint(projected)
        for pending, approval in staged:
            pending['approval'] = approval
        return {'state': 'reviewed', 'reviews': reviews, 'alreadyReviewed': already, 'reviewBatchId': batch_id}

    def _publish_pending(self, pending, output):
        from offline_review import publish_review
        self.archive._check()
        reference = self._progress.export_reference(output)
        if any(pending.get(key) != reference.get(key) for key in ('exportTarget', 'exportRoot')):
            projected = dict(self._reviews)
            revised = {key: value for key, value in pending.items() if key not in ('exportTarget', 'exportRoot')}
            revised.update(reference)
            projected[pending['summary']['chat']['id']] = revised
            self._checkpoint(projected)
            pending.pop('exportRoot', None)
            pending.update(reference)
        result = publish_review(pending, output)
        self.last_export = output
        selected = pending['summary']['chat']['id']
        self._exports[selected] = output
        self._reviews.pop(selected, None)
        self._published_reviews[selected] = {**pending, 'exportTarget': pending['exportTarget']}
        if self.pending_review is pending:
            self.pending_review = None
        try:
            self._checkpoint()
        except ArchiveError:
            # The pre-publish intent remains recoverable; restart revalidates the
            # formal target before marking this export completed.
            pass
        return {'output': str(output), 'report': result, 'humanReview': result['humanReview'],
                'mediaSummary': result['mediaSummary'], 'mediaSummaryText': result['mediaSummaryText'],
                'reporting': result['reporting'],
                'exportFiles': {kind: str(output / name) for kind, name in result['messageExports'].items()}}

    @staticmethod
    def _batch_group(root, exported):
        report = exported['report']
        files = {}
        for kind, value in exported.get('exportFiles', {}).items():
            path = Path(value)
            try:
                files[kind] = path.relative_to(root).as_posix()
            except ValueError:
                files[kind] = path.name
        return {'chat': report['chat'], 'rows': report['exportedRows'],
                'reviewId': exported['humanReview']['reviewId'],
                'directory': Path(exported['output']).relative_to(root).as_posix(),
                'files': files}

    def save_export_copy(self, chat):
        """Save another reviewed copy without changing the original export checkpoint."""
        from offline_review import publish_review
        pending = self._published_reviews.get(chat)
        if self.conversation_mode != 'group' or pending is None:
            raise ArchiveError('请先完成人工复核并导出当前群，再另存副本。')
        with self.archive.integrity_scope():
            self._progress.validate_published(pending, self.archive)
            base = self._validate_export_directory(self.export_directory)
            output = base / (datetime.now().strftime('%Y%m%d-%H%M%S-%f') + '-' + self.client + '-copy-' + pending['summary']['reviewId'][:8])
            report = publish_review(pending, output)
        return {'output': str(output), 'report': report,
                'exportFiles': {kind: str(output / name) for kind, name in report['messageExports'].items()}}

    @staticmethod
    def _batch_readme(label, index):
        lines = ['# ' + label + '导出清单', '',
                 f'本次导出 {len(index["groups"])} 个{label}，失败 {len(index["failed"])} 个，已导出跳过 {len(index.get("alreadyExported", []))} 个。', '']
        if index.get('analysis'):
            lines += ['跨群业务分析索引：[' + index['analysis']['readmeFile'] + '](' +
                      index['analysis']['readmeFile'] + ')', '']
        for group in index['groups']:
            lines += [group['chat']['name'] + ' · ' + group['chat']['id'] + ' · ' + str(group['rows']) + ' 条',
                      ' / '.join('[' + kind.upper() + '](' + path + ')' for kind, path in group['files'].items()), '']
        for item in index['failed']:
            lines += ['未完成：' + item['chatName'] + ' · ' + item['chatId'] + ' · ' + item['error'], '']
        for item in index.get('alreadyExported', []):
            lines += ['已导出保留原结果：' + item['chatId'] + ' · ' + item['output'], '']
        for item in index.get('metadataErrors', []):
            lines += ['清单未完成：' + item['file'] + ' · ' + item['error'], '']
        return '\n'.join(lines)

    @staticmethod
    def _metadata_issue(error):
        if isinstance(error, OSError):
            return '清单写入失败，请检查输出目录空间与写入权限后重试。'
        return safe_issue(error)

    def _write_batch_metadata(self, root, index):
        from offline_review import atomic_write_json, atomic_write_text
        metadata_errors = list(index.get('metadataErrors', []))
        batch_ok = False
        try:
            atomic_write_json(root / 'batch.json', index)
            batch_ok = True
        except Exception as error:
            metadata_errors.append({'file': 'batch.json', 'error': self._metadata_issue(error)})
        try:
            atomic_write_text(root / '导出清单.md', self._batch_readme(
                '私聊' if self.conversation_mode == 'direct' else '群聊',
                {**index, 'metadataErrors': metadata_errors}))
        except Exception as error:
            metadata_errors.append({'file': '导出清单.md', 'error': self._metadata_issue(error)})
        if metadata_errors and batch_ok:
            final = {**index, 'state': 'partial', 'metadataComplete': False,
                     'metadataErrors': metadata_errors}
            try:
                atomic_write_json(root / 'batch.json', final)
            except Exception as error:
                metadata_errors.append({'file': 'batch.json', 'error': self._metadata_issue(error)})
        return metadata_errors

    def _build_batch_analysis(self, root, index):
        """Derived analysis failures never invalidate already published bodies."""
        from offline_analysis import build_batch
        analysed = [group for group in index['groups']
                    if (root / group['directory'] / 'analysis' / 'index.json').is_file()]
        if not analysed:
            return
        try:
            index['analysis'] = build_batch(root, analysed)
        except (OSError, ValueError, KeyError) as error:
            issue = ('跨群分析材料未生成，请检查输出目录空间与写入权限后修复。'
                     if isinstance(error, OSError) else safe_issue(error))
            index['analysisError'] = issue
            index['metadataErrors'].append({'file': 'batch-analysis', 'error': issue})
            index['metadataComplete'] = False
            index['state'] = 'partial'

    def _checked_batch(self, operation):
        """Reuse nested snapshot checks only until this batch's final full check."""
        if self.archive is None:
            raise ArchiveError('请先加载数据。')
        try:
            with self.archive.integrity_scope():
                result = operation()
        except IntegrityScopeError as error:
            issue = {'file': 'source_snapshot', 'error': str(error)}
            result['state'], result['metadataComplete'] = 'partial', False
            result['metadataErrors'].append(issue)
            if result.get('output'):
                root = Path(result['output'])
                try:
                    index = json.loads((root / 'batch.json').read_text(encoding='utf8'))
                except (OSError, ValueError) as index_error:
                    result['metadataErrors'].append({'file': 'batch.json', 'error': self._metadata_issue(index_error)})
                else:
                    index.update(state='partial', metadataComplete=False,
                                 metadataErrors=list(result['metadataErrors']))
                    result['metadataErrors'] = self._write_batch_metadata(root, index)
        return result

    def export_reviewed(self, chats, *, progress=None):
        completed_events = []
        def relay(event):
            if event['stage'] == 'completed':
                completed_events.append(event)
            elif progress is not None:
                progress(event)
        result = self._checked_batch(lambda: self._export_reviewed(chats, progress=relay))
        if (progress is not None and completed_events and
                not any(item['file'] == 'source_snapshot' for item in result['metadataErrors'])):
            progress(completed_events[-1])
        return result

    def _export_reviewed(self, chats, *, progress=None):
        """Preflight every selected batch, then publish each once with a batch index."""
        from offline_review import validated_approval
        if not isinstance(chats, (list, tuple)) or not chats or any(not isinstance(chat, str) for chat in chats):
            label = '私聊' if self.conversation_mode == 'direct' else '群聊'
            raise ArchiveError(f'请先勾选需要导出的{label}。')
        chats = list(dict.fromkeys(chats))
        if self.archive is None:
            raise ArchiveError('请先加载数据。')
        total = sum(chat not in self._exports for chat in chats)
        completed = 0
        def report_progress(stage, chat=None):
            if progress is not None:
                event = {'stage': stage, 'completed': completed, 'total': total}
                if chat is not None:
                    event.update(chatId=chat['id'], chatName=chat['name'])
                progress(event)
        report_progress('validating')
        self.archive._check()
        selected = []
        already = []
        for chat in chats:
            if chat in self._exports:
                already.append({'chatId': chat, 'output': str(self._exports[chat])})
                continue
            pending = self._reviews.get(chat)
            if not pending or pending['archive'] is not self.archive:
                label = '私聊' if self.conversation_mode == 'direct' else '群聊'
                raise ArchiveError(f'所选{label}中有未解析或已失效的会话，请先解析并逐项复核。')
            validated_approval(pending)
            selected.append(pending)
        result = {'version': 1, 'state': 'completed', 'output': None, 'exports': [], 'failed': [],
                  'alreadyExported': already, 'selectedChatIds': chats,
                  'metadataErrors': [], 'metadataComplete': True}
        if not selected:
            report_progress('completed')
            return result
        base = self._validate_export_directory(self.export_directory)
        root = base/(datetime.now().strftime('%Y%m%d-%H%M%S-%f')+'-'+self.client+'-batch-'+uuid.uuid4().hex[:8])
        try:
            root.mkdir(parents=True)
        except OSError:
            raise ArchiveError('无法写入所选导出位置，请检查目录是否可用、磁盘空间和写入权限；复核结果仍保留。') from None
        # Save the whole plan before the first copy. Unstarted groups must still
        # belong to this batch when an interrupted run is restored and repaired.
        planned = []
        projected = dict(self._reviews)
        for number, pending in enumerate(selected, 1):
            summary = pending['summary']
            destination = root / (f'{number:02d}-' + summary['reviewId'][:8])
            reference = self._progress.export_reference(destination)
            revised = {key: value for key, value in pending.items() if key not in ('exportTarget', 'exportRoot')}
            projected[summary['chat']['id']] = {**revised, **reference}
            planned.append((pending, destination, reference))
        self._checkpoint(projected)
        for pending, destination, reference in planned:
            pending.pop('exportRoot', None)
            pending.update(reference)
        self.last_batch_root = root
        result['output'] = str(root)
        for pending, destination, _ in planned:
            summary = pending['summary']
            report_progress('exporting', summary['chat'])
            try:
                exported = self._publish_pending(pending, destination)
            except Exception as error:
                from offline_startup import failure
                failure(error,'group_export_failed')
                result['failed'].append({'chatId': summary['chat']['id'], 'chatName': summary['chat']['name'],
                    'error': ('当前群未能写入导出位置，请检查目录、空间与写入权限；复核结果保留。'
                              if isinstance(error, OSError) else safe_issue(error))})
                report_progress('failed', summary['chat'])
            else:
                result['exports'].append(exported)
                completed += 1
                report_progress('saved', summary['chat'])
        result['state'] = 'partial' if result['failed'] else 'completed'
        index = {'version': 1, 'state': result['state'], 'selectedChatIds': chats, 'alreadyExported': already,
                 'failed': result['failed'], 'groups': [{'chat': item['report']['chat'],
                    'rows': item['report']['exportedRows'], 'reviewId': item['humanReview']['reviewId'],
                    'directory': Path(item['output']).relative_to(root).as_posix(),
                    'files': {kind: Path(path).relative_to(root).as_posix() for kind, path in item['exportFiles'].items()}}
                    for item in result['exports']]}
        index['metadataErrors'] = []
        index['metadataComplete'] = True
        report_progress('analysis')
        self._build_batch_analysis(root, index)
        for key in ('analysis', 'analysisError'):
            if key in index:
                result[key] = index[key]
        metadata_errors = self._write_batch_metadata(root, index)
        result['metadataErrors'] = metadata_errors
        result['metadataComplete'] = not metadata_errors
        if metadata_errors:
            result['state'] = 'partial'
        report_progress('completed')
        return result

    def repair_export_metadata(self, batch_root):
        return self._checked_batch(lambda: self._repair_export_metadata(batch_root))

    def _repair_export_metadata(self, batch_root):
        """Rebuild metadata for verified exports and retain unfinished retries."""
        from offline_review import validated_approval
        requested = Path(batch_root).absolute()
        bases = [self.directory / 'exports', *(Path(p) for p in self._progress.export_roots)]
        if any(path.is_symlink() or path.is_junction()
               for path in (requested, *requested.parents)):
            raise ArchiveError('导出批次路径不能包含链接。')
        root = requested.resolve()
        if not any(root != base.resolve() and root.is_relative_to(base.resolve()) for base in bases) or not root.is_dir():
            raise ArchiveError('导出批次不属于已选择的导出位置。')
        if self.archive is None:
            raise ArchiveError('请先加载数据。')
        self.archive._check()
        entries = []
        for pending in self._published_reviews.values():
            target = self._progress.export_path(pending)
            if target.parent == root:
                entries.append(pending)
        unfinished = []
        for pending in self._reviews.values():
            relative = pending.get('exportTarget')
            if not isinstance(relative, str):
                continue
            try:
                target = self._progress.export_path(pending)
            except (OSError, ValueError):
                continue
            if target.parent != root:
                continue
            chat = pending['summary']['chat']
            error = '此前导出未完成，请从原导出入口重试。'
            try:
                if target.exists():
                    raise ArchiveError('导出目标存在但未通过正式完成校验。')
                validated_approval(pending)
            except Exception as validation_error:
                error = safe_issue(validation_error)
                unfinished.append({'chatId': chat['id'], 'chatName': chat['name'],
                                   'error': error})
            else:
                unfinished.append({'chatId': chat['id'], 'chatName': chat['name'],
                                   'error': error})
        if not entries and not unfinished:
            raise ArchiveError('当前批次没有可验证的已发布会话。')
        groups, exports, metadata_errors = [], [], []
        for pending in entries:
            chat = pending['summary']['chat']
            try:
                checked, target, report = self._progress.validate_published(pending, self.archive)
                message_exports = {}
                group_directory = target.relative_to(root)
                for kind, value in report.get('messageExports', {}).items():
                    path = Path(value)
                    if path.is_absolute():
                        link = path.relative_to(root)
                    else:
                        if path.is_absolute() or '..' in path.parts:
                            raise ArchiveError('导出清单引用越界。')
                        resolved = (target / path).resolve()
                        if not resolved.is_relative_to(target) or not resolved.is_file():
                            raise ArchiveError('导出清单引用文件缺失。')
                        link = group_directory / path
                    message_exports[kind] = link.as_posix()
                groups.append({'chat': report['chat'], 'rows': report['exportedRows'],
                               'reviewId': report['humanReview']['reviewId'],
                               'directory': target.relative_to(root).as_posix(),
                               'files': message_exports})
                exports.append({'chatId': chat['id'], 'output': str(target),
                                'reviewId': report['humanReview']['reviewId']})
            except Exception as error:
                metadata_errors.append({'file': str(pending.get('exportTarget', '')),
                                        'error': safe_issue(error)})
        failed = unfinished[:]
        chats = [item['chat']['id'] for item in groups] + [item['chatId'] for item in failed]
        index = {'version': 1, 'state': 'partial' if (metadata_errors or failed) else 'completed',
                 'selectedChatIds': chats, 'alreadyExported': [], 'failed': failed,
                 'groups': groups, 'metadataErrors': metadata_errors,
                 'metadataComplete': not metadata_errors}
        self._build_batch_analysis(root, index)
        written_errors = self._write_batch_metadata(root, index)
        metadata_errors = written_errors
        result = {'version': 1, 'state': 'partial' if (metadata_errors or failed) else 'completed',
                  'output': str(root), 'exports': exports, 'failed': [],
                  'alreadyExported': [], 'metadataErrors': metadata_errors,
                  'metadataComplete': not metadata_errors}
        result['failed'] = failed
        for key in ('analysis', 'analysisError'):
            if key in index:
                result[key] = index[key]
        return result

    def scan(self):
        if self.client == 'wecom':
            from wecom_snapshot import discover as discover_wecom
            return discover_wecom(self.scan_roots)
        return discover(self.scan_roots)

    def _account_names(self, discovery):
        from offline_account_profiles import account_profiles
        config = self.archive.media_config if self.archive else self.settings.get('source', {})
        paths = [Path(config['keyFile'])] if config.get('keyFile') else []
        folder = self.directory / 'keys'
        if folder.is_dir() and not folder.is_symlink() and folder.resolve().parent == self.directory:
            from itertools import islice
            paths += [path for path in islice(folder.glob('*.json'), 16) if not path.is_symlink()]
        return account_profiles(discovery, self.client, key_files=list(dict.fromkeys(paths))[:16],
                                configured_source=config.get('sourceRoot', ''), configured_self_id=config.get('selfId', ''))

    def scan_accounts(self, *, live_names=False):
        """Fresh ownership evidence plus display-only local account names."""
        from offline_accounts import identify_accounts
        from live_account_profiles import complete_live_profiles, PROFILE_FIELDS
        from offline_account_profiles import COMPANY_FIELDS
        scan = self._account_names(identify_accounts(self.scan(), self.client))
        if live_names:
            scan = complete_live_profiles(scan, self.client, allow_process_read=True)
        valid = {}
        for row in scan['accounts']:
            identities = row.get('processIdentities', [])
            if row.get('processState') != 'active' or len(identities) != 1:
                continue
            key = (str(Path(row['sourceRoot']).resolve()), identities[0]['pid'], identities[0]['created'])
            if row.get('profileBasis', '').startswith('live_owner:') and row.get('profileStatus') == 'matched':
                valid[key] = {field: row[field] for field in PROFILE_FIELDS if field in row}
            elif not live_names and key in self._live_account_names:
                cached = self._live_account_names[key]
                if not row.get('accountNickname'):
                    row.update(cached)
                elif (self.client == 'wecom' and row.get('accountId') == cached.get('accountId')
                      and not (row.get('companyName') or row.get('companyShortName'))
                      and (not row.get('companyId') or row['companyId'] == cached.get('companyId'))):
                    row.update({field: cached[field] for field in COMPANY_FIELDS if field in cached})
                valid[key] = cached
        self._live_account_names = valid
        return scan

    def automatic_setup(self, config=None, *, allow_process_read=False):
        """Explicit one-click setup; retain old settings format and source binding."""
        if self.settings.get('source') and self.settings.get('client', 'wechat') != self.client:
            return {'status': 'needs_source', 'message': '当前配置属于另一种客户端，请使用其独立启动入口和数据目录。'}
        saved = self.settings.get('source', {})
        supplied = {name: config[name] for name in CONFIG_FIELDS if name in (config or {})}
        source = supplied.get('sourceRoot') or saved.get('sourceRoot')
        if not source:
            discovered = self.scan()
            accounts = discovered['accounts']
            if len(accounts) != 1:
                return {'status': 'needs_source', 'accounts': accounts,
                        'message': '发现多个账号，请先从账号列表选定一个。' if accounts else
                                   '未发现受支持的微信数据目录，请登录本人微信后重试，或手动选择自定义目录。'}
            source = accounts[0]['sourceRoot']
        def normalized(value):
            path = Path(value).expanduser().resolve()
            if self.client == 'wecom':
                from wecom_snapshot import data_root
                return data_root(path)
            return path / 'db_storage' if (path / 'db_storage').is_dir() else path
        source = normalized(source)
        same_source = bool(saved.get('sourceRoot')) and normalized(saved['sourceRoot']) == source
        value = {**(saved if same_source else {}), **supplied, 'sourceRoot': str(source)}
        value.setdefault('includeMedia', True)
        value.setdefault('includeVoice', False)
        if not same_source and not supplied.get('selfId'):
            value['selfId'] = ''
        self._outside_source(value)
        # Reuse only explicit references and this application's own saved keys.
        # Never search other directories for private JSON files.
        attempts = [value]
        if value.get('keyFile'):
            # A plaintext copy does not require a stale key-file reference.
            # Snapshot validation still rejects encrypted or damaged databases.
            attempts.append({**value, 'keyFile': ''})
        folder = self.directory / 'keys'
        if folder.is_dir() and not folder.is_symlink() and folder.resolve() == folder:
            candidates = sorted(folder.glob('*.json'), key=lambda path: path.name, reverse=True)[:16]
            for path in candidates:
                try:
                    if (path.is_file() and not path.is_symlink() and path.resolve().parent == folder
                            and path.stat().st_size <= 65536):
                        attempts.append({**value, 'keyFile': str(path), 'imageKeyFile': ''})
                except OSError:
                    continue
        last_error = None
        for attempt in attempts:
            try:
                info = self.open_source(attempt)
                return {**info, 'setupMode': 'reused_or_plaintext',
                        'notice': '已自动加载并校验数据，无需选择密钥文件；后续复用本机设置。'}
            except OSError as error:
                import errno
                message = ('本地副本未写完，请检查工具目录所在磁盘空间后重试。'
                           if error.errno == errno.ENOSPC else safe_issue(error))
                return {'status': 'needs_source', 'config': value, 'message': message}
            except ArchiveError as error:
                # Session/settings errors cannot be fixed by reading process keys.
                return {'status': 'needs_source', 'config': value, 'message': safe_issue(error)}
            except SnapshotError as error:
                if error.code == 'snapshot_io':
                    return {'status': 'needs_source', 'config': value, 'message': safe_issue(error)}
                last_error = error
            except (ValueError, DatabaseError) as error:
                last_error = error
        if allow_process_read is not True:
            return {'status': 'needs_source', 'config': value,
                    'message': safe_issue(last_error)}
        if self.client == 'wecom':
            from wecom_key_recovery import recover_key_file
        else:
            from portable_key_recovery import recover_key_file
        try:
            recovery_options = {'allow_process_read': True, 'include_media': value['includeMedia']}
            if self.client == 'wechat':
                recovery_options['include_voice'] = value.get('includeVoice', False)
            result = recover_key_file(source, self.directory, **recovery_options)
            value['keyFile'] = result['keyFile']
            if result['imageRecovery'] == 'verified_local_samples':
                value['imageKeyFile'] = ''
            info = self.open_source(value)
        except (OSError, ValueError, DatabaseError) as error:
            return {'status': 'needs_source', 'message': safe_issue(error), 'config': value}
        if self.client == 'wecom':
            return {**info, 'setupMode': 'recovered_live', 'imageRecovery': result['imageRecovery'],
                    'notice': '企微数据库密钥已自动恢复并通过副本校验；已保存独立配置。媒体按本机缓存摘要匹配，不需要个微图片密钥。'}
        return {**info, 'setupMode': 'recovered_live', 'imageRecovery': result['imageRecovery'],
                'notice': '数据库密钥已自动恢复并通过副本校验，已记住本机设置。' +
                          ('图片密钥也已验证。' if result['imageRecovery'] == 'verified_local_samples' else
                           '图片密钥未恢复，图片仍按可用、缩略图或缺失显示。')}

    def review_view(self, chat):
        """Open a saved review for reading without parsing or changing settings."""
        pending = self._reviews.get(chat) or self._published_reviews.get(chat)
        if pending is None:
            raise ArchiveError('当前群尚无可阅读的解析结果。')
        view = self.views.get(chat)
        if view is None:
            view = ConversationView(pending['root'])
            # A reparse may finish while the reader builds an older index.
            # Keep that reader's result separate from the newly saved review.
            current = self._reviews.get(chat) or self._published_reviews.get(chat)
            if current is pending:
                self.views[chat] = view
        return view

    def preview(self, chat, offset=None, kind=None, *, page_size=100, query=''):
        if self.archive is None:
            raise ArchiveError('请先加载数据。')
        self._assert_conversation(chat)
        self.archive._check()
        if chat not in self.views:
            if chat in self._reviews or chat in self._published_reviews:
                self.review_view(chat)
            else:
                suffix = hashlib.sha256(chat.encode()).hexdigest()[:8]
                output = self.directory / 'views' / (datetime.now().strftime('%Y%m%d-%H%M%S-%f') + '-' + suffix)
                self.archive.export(output, chat_id=chat, copy_media=True)
                self.views[chat] = ConversationView(output)
        result = self.views[chat].page(offset, kind, page_size=page_size, query=query)
        self._save(last_chat=chat)
        return result

    def export(self, chat, start=None, end=None, copy_media=True):
        if self.archive is None:
            raise ArchiveError('请先加载数据。')
        self._assert_conversation(chat)
        suffix = hashlib.sha256(chat.encode()).hexdigest()[:8]
        output = self.directory / 'exports' / (datetime.now().strftime('%Y%m%d-%H%M%S-%f') + '-' + suffix)
        result = self.archive.export(output, chat_id=chat, start=start, end=end, copy_media=copy_media)
        self._save(last_chat=chat)
        self.last_export = output
        return {'output': str(output), 'report': result,
                'mediaSummary': result['mediaSummary'], 'mediaSummaryText': result['mediaSummaryText'],
                'reporting': result['reporting'],
                'exportFiles': {kind: str(output / name) for kind, name in result['messageExports'].items()}}

    def parse_and_open(self, *, chat_id=None, chat_name=None, start=None, end=None, copy_media=True, page_size=100):
        if self.archive is None:
            raise ArchiveError('请先选择本机数据和匹配密钥。')
        chat = self.select_chat(chat_id=chat_id, chat_name=chat_name)
        result = self.export(chat['id'], start=start, end=end, copy_media=copy_media)
        self.views[chat['id']] = ConversationView(result['output'])
        return {**result, 'chat': chat, 'view': self.preview(chat['id'], kind='business', page_size=page_size)}
