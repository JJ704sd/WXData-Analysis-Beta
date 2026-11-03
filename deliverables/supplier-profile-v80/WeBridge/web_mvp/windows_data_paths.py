"""Bounded current-user path hints; no disk-wide search or process access."""
import os
from pathlib import Path


def path_hints():
    documents = [Path.home() / 'Documents']
    custom = []
    if os.name == 'nt':
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                    r'Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders') as key:
                value, _ = winreg.QueryValueEx(key, 'Personal')
                if isinstance(value, str) and value:
                    documents.insert(0, Path(os.path.expandvars(value)))
        except OSError:
            pass
        for relative in (r'Software\Tencent\WeChat', r'Software\Tencent\Weixin'):
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, relative) as key:
                    for field in ('FileSavePath', 'DataPath'):
                        try:
                            value, _ = winreg.QueryValueEx(key, field)
                            if isinstance(value, str) and value and value != 'MyDocument:':
                                path = Path(os.path.expandvars(value)).expanduser()
                                if path.is_absolute():
                                    custom.append(path)
                        except OSError:
                            continue
            except OSError:
                continue
    roots = [folder / name for folder in documents for name in ('xwechat_files', 'WeChat Files')]
    for folder in custom:
        roots.extend((folder, folder / 'xwechat_files', folder / 'WeChat Files'))
    return list(dict.fromkeys(roots))[:16]


def wecom_path_hints():
    roots = [Path(value) / 'Tencent' / 'WXWork' / 'Data'
             for name in ('APPDATA', 'LOCALAPPDATA') if (value := os.environ.get(name))]
    roots.extend(path.parent / 'WXWork' for path in path_hints())
    return list(dict.fromkeys(roots))[:16]
