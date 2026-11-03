"""One user profile per client; bounded legacy discovery, no account probing."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile

RESET_MARKER = '.profile-reset.json'
CACHE_ENTRIES = ('settings.local.json', 'keys', 'requests', 'reviews', 'snapshots', 'views')


def reset_requested(directory):
    return os.path.lexists(Path(directory) / RESET_MARKER)


def _cache_entry(path, root):
    if _linked(path) or not path.resolve().is_relative_to(root) or path.resolve() == root:
        raise ValueError('缓存目录包含越界路径或链接，未清理；请检查程序数据目录。')
    if path.is_dir():
        for current, directories, files in os.walk(path, followlinks=False):
            for name in directories + files:
                child = Path(current) / name
                if _linked(child) or not child.resolve().is_relative_to(root):
                    raise ValueError('缓存中包含外部链接，未清理；请先检查对应缓存目录。')


def clear_profile_cache(directory, client, *, source_root=''):
    """Clear one client's tool cache, retain exports and block automatic legacy import."""
    if client not in ('wechat', 'wecom'):
        raise ValueError('不支持的客户端。')
    original = Path(directory).absolute()
    if any(_linked(path) for path in (original, *original.parents)):
        raise ValueError('程序数据目录包含链接，未清理；请使用独立的普通目录。')
    root = original.resolve()
    expected = 'LocalWeComArchive' if client == 'wecom' else 'LocalWechatArchive'
    if (root == Path(root.anchor) or root == Path.home().resolve()
            or (root.name.casefold() in ('localwecomarchive', 'localwechatarchive') and root.name.casefold() != expected.casefold())):
        raise ValueError('程序数据目录不适合清理，或属于另一客户端，未执行。')
    if any((root / name).exists() for name in ('message.db', 'user.db', 'contact.db', 'db_storage', 'Data')):
        raise ValueError('程序数据目录中存在微信原始数据库，未清理；请先检查目录设置。')
    paths = [root / name for name in CACHE_ENTRIES]
    if source_root:
        source = Path(source_root).resolve()
        account = source.parent if source.name in ('db_storage', 'Data') else source
        if any(path.resolve().is_relative_to(account) or account.is_relative_to(path.resolve()) for path in paths):
            raise ValueError('原始数据与待清理缓存目录重叠，未清理；请先将数据源独立存放。')
    for path in paths:
        _cache_entry(path, root)
    marker = root / RESET_MARKER
    if _linked(marker) or marker.is_dir():
        raise ValueError('配置重置标记异常，未清理；请检查程序数据目录。')
    if marker.is_file():
        try:
            previous = json.loads(marker.read_text(encoding='utf8')) if marker.stat().st_size < 1024 else {}
        except (ValueError, UnicodeError):
            previous = {}
        if isinstance(previous, dict) and previous.get('client', client) != client:
            raise ValueError('此程序数据目录属于另一客户端，未清理。')
    root.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.reset-', dir=root)
    try:
        with os.fdopen(fd, 'w', encoding='utf8') as stream:
            json.dump({'version': 1, 'client': client}, stream)
        os.replace(temporary, marker)
    finally:
        Path(temporary).unlink(missing_ok=True)
    removed, remaining = [], []
    for path in paths:
        if not os.path.lexists(path):
            continue
        try:
            _cache_entry(path, root)
            shutil.rmtree(path) if path.is_dir() else path.unlink()
            removed.append(path.name)
        except (OSError, ValueError):
            remaining.append(path.name)
    remaining = [path.name for path in paths if os.path.lexists(path)]
    label = '企微' if client == 'wecom' else '个微'
    message = (f'已清除{label}工具账号缓存；原始聊天和已导出文件保留。请重新选择账号并配置。'
               if not remaining else '部分缓存未清理：' + '、'.join(remaining) + '。请关闭其他解析窗口后重试；尚未完成清理。')
    return {'ok': not remaining, 'status': 'needs_source', 'removed': removed,
            'remaining': remaining, 'message': message}


class ClientBindingError(ValueError):
    """A client-bound Skill must never fall back to another client/profile."""


def resolve_client(requested=None):
    if requested not in (None, 'wechat', 'wecom'):
        raise ClientBindingError('客户端类型必须为 wechat 或 wecom。')
    fallback = requested or 'wechat'
    if not getattr(sys, 'frozen', False):
        return fallback
    exe = Path(sys.executable).resolve()
    root = exe.parent.parent if exe.parent.name.lower() == 'bin' else exe.parent
    manifest = root/'runtime.json'
    required = bool(re.fullmatch(r'local-chat-desktop-\d+\.\d+\.\d+\.exe', exe.name, re.I))
    if not manifest.is_file():
        if required:
            raise ClientBindingError('Skill缺少runtime.json，请完整解压或重新导入，不能单独复制bin内EXE。')
        return fallback
    try:
        if manifest.stat().st_size > 65536:
            raise ValueError('oversized manifest')
        value = json.loads(manifest.read_text(encoding='utf-8-sig'))
        relative = value.get('exeRelativePath') if isinstance(value, dict) else None
        target = (root/relative).resolve() if isinstance(relative, str) else None
    except (OSError, ValueError):
        if required:
            raise ClientBindingError('Skill运行清单无效，请完整解压或重新导入。') from None
        return fallback
    if target != exe or not target.is_relative_to(root):
        if required:
            raise ClientBindingError('Skill运行清单与当前EXE不匹配，请使用同一完整包。')
        return fallback
    bound = value.get('client')
    if bound is None and not required:
        return fallback  # Older generic runtimes remain compatible with --client.
    if bound is None and value.get('clientBinding') == 'selectable':
        bound = fallback
    if bound not in ('wechat', 'wecom'):
        raise ClientBindingError('Skill未声明有效客户端，请使用对应个微或企微完整包。')
    if requested and requested != bound:
        raise ClientBindingError('启动客户端与Skill绑定不一致，请使用对应客户端的Skill。')
    try:
        with exe.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    except OSError:
        raise ClientBindingError('无法校验Skill程序，请检查文件读取权限。') from None
    if digest != value.get('exeSha256'):
        raise ClientBindingError('Skill程序校验不一致，请重新解压完整包，不要混用不同版本文件。')
    return bound


def default_directory(client):
    if client not in ('wechat', 'wecom'):
        raise ValueError('Unsupported client')
    root = Path(os.environ.get('LOCALAPPDATA') or Path.home()/'AppData/Local')
    return (root / ('LocalWeComArchive' if client == 'wecom' else 'LocalWechatArchive')).resolve()


def application_directory():
    return (Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False)
            else Path(__file__).resolve().parents[1]/'.runtime/local-chat-desktop')


def _linked(path):
    return path.is_symlink() or path.is_junction()


def legacy_settings(client):
    """Only named old profiles beside/near the EXE or current workspace, never disk-wide."""
    app = application_directory()
    roots = list(dict.fromkeys((app, app.parent, Path.cwd().resolve())))
    roots += [root/'build' for root in list(roots)]
    name = 'local-chat-data-wecom' if client == 'wecom' else 'local-chat-data'
    found = set()
    for root in roots:
        try:
            if not root.is_dir() or _linked(root):
                continue
            directories = [root]
            # Bound filesystem work even in a large Downloads/project directory.
            with os.scandir(root) as entries:
                for index, entry in enumerate(entries):
                    if index >= 256:
                        break
                    if entry.is_dir(follow_symlinks=False) and not _linked(Path(entry.path)):
                        directories.append(Path(entry.path))
            for directory in directories:
                profile = directory/name
                settings = profile/'settings.local.json'
                if not _linked(profile) and not _linked(settings) and settings.is_file():
                    found.add(settings.resolve())
        except OSError:
            continue
    return sorted(found, key=lambda path: str(path).lower())[:64]


def read_legacy(path, client):
    path = Path(path).resolve()
    if path.name != 'settings.local.json' or path.stat().st_size > 65536:
        raise ValueError('请选择旧程序目录下的 settings.local.json，文件上限64 KiB。')
    value = json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(value, dict) or value.get('version') != 1:
        raise ValueError('旧配置的格式或版本不支持。')
    if value.get('client', 'wechat') != client:
        raise ValueError('旧配置属于另一种客户端，请使用对应的个微或企微入口。')
    config = value.get('source')
    if not isinstance(config, dict) or not isinstance(config.get('sourceRoot'), str) or not config['sourceRoot'].strip():
        raise ValueError('旧配置缺少已确认的数据目录。')
    config = dict(config)
    for name in ('sourceRoot', 'keyFile', 'imageKeyFile'):
        reference = config.get(name)
        if reference:
            if not isinstance(reference, str):
                raise ValueError('旧配置中的数据与密钥必须为文件路径。')
            target = Path(reference).expanduser()
            config[name] = str((target if target.is_absolute() else path.parent/target).resolve())
    for name in ('includeMedia', 'includeVoice'):
        if name in config and not isinstance(config[name], bool):
            raise ValueError('旧配置中的媒体选项格式不正确。')
    if 'selfId' in config and not isinstance(config['selfId'], str):
        raise ValueError('旧配置中的本人ID格式不正确。')
    return value, config


def copy_key_references(config, directory):
    """Copy only explicitly referenced keys; leave chat data and old files in place."""
    config = dict(config)
    for name in ('keyFile', 'imageKeyFile'):
        if not config.get(name):
            continue
        source = Path(config[name])
        if source.stat().st_size > 1024*1024:
            raise ValueError('旧配置引用的密钥文件超过1 MiB，请在手动页确认文件。')
        data = source.read_bytes()
        if len(data) > 1024*1024:
            raise ValueError('旧配置引用的密钥文件超过1 MiB。')
        folder = directory/'keys'
        if _linked(folder):
            raise ValueError('配置密钥目录不能为链接目录。')
        folder.mkdir(parents=True, exist_ok=True)
        target = folder/('imported-'+hashlib.sha256(data).hexdigest()+'.json')
        if target.exists():
            if _linked(target) or target.read_bytes() != data:
                raise ValueError('迁移密钥文件发生冲突，请保留旧配置并检查目标目录。')
        else:
            try:
                _atomic_create(target, data)
            except FileExistsError:
                if _linked(target) or target.read_bytes() != data:
                    raise ValueError('迁移密钥文件发生冲突。')
        config[name] = str(target)
    return config


def _atomic_create(path, data):
    """Publish a complete file without replacing a concurrently created profile."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.profile-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if os.name == 'nt':
            os.rename(temporary, path)  # Windows rename refuses an existing destination.
        else:
            os.link(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def publish_settings(path, value):
    _atomic_create(path, json.dumps(value, ensure_ascii=False, indent=2).encode('utf8'))
