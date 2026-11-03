"""Update or verify the explicit source inventory and its SHA-256 list."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath

from chat_release import DAILY_REPORT_VERSION, GROUP_RELEASE, GROUP_VERSION, PRIVATE_VERSION

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / 'source-manifest.json'
SUMS = ROOT / 'SHA256SUMS.txt'


def checked_path(relative):
    if not isinstance(relative, str) or '\\' in relative:
        raise ValueError('源文件路径必须使用相对正斜杠格式。')
    path = PurePosixPath(relative)
    if path.is_absolute() or not path.parts or any(part in ('', '.', '..') for part in path.parts):
        raise ValueError('源文件路径无效。')
    target = ROOT.joinpath(*path.parts)
    if any(item.is_symlink() or item.is_junction() for item in (target, *target.parents) if item != ROOT.parent):
        raise ValueError('源文件路径不能包含链接。')
    if not target.is_file():
        raise ValueError('清单中的源文件不存在：' + relative)
    return target


def inventory(paths):
    entries = []
    for relative in sorted(paths):
        raw = checked_path(relative).read_bytes()
        entries.append({'path': relative, 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()})
    return entries


def checksum_text(entries, manifest_bytes):
    values = [(entry['path'], entry['sha256']) for entry in entries]
    values.append(('source-manifest.json', hashlib.sha256(manifest_bytes).hexdigest()))
    return ''.join(digest + '  ' + name + '\n' for name, digest in sorted(values))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--update', action='store_true', help='重算已列出的文件')
    parser.add_argument('--add', action='append', default=[], help='明确纳入的新源文件，可重复指定')
    args = parser.parse_args(argv)
    if args.add and not args.update:
        parser.error('--add 必须与 --update 一起使用。')
    try:
        manifest = json.loads(MANIFEST.read_text(encoding='utf-8-sig'))
        entries = manifest['files']
        if not isinstance(entries, list) or any(not isinstance(entry, dict) for entry in entries):
            raise ValueError('源文件清单格式无效。')
        listed = [entry['path'] for entry in entries]
        if len(listed) != len(set(listed)) or any(name in {'source-manifest.json', 'SHA256SUMS.txt'} for name in listed):
            raise ValueError('源文件清单存在重复项或自引用。')
        paths = set(listed)
        for relative in args.add:
            if relative in {'source-manifest.json', 'SHA256SUMS.txt'}:
                raise ValueError('不能将校验清单自身加入文件清单。')
            checked_path(relative)
            paths.add(relative)
        actual = inventory(paths)
        expected_metadata = {'release': GROUP_RELEASE, 'groupSkillVersion': GROUP_VERSION,
                             'privateVersion': PRIVATE_VERSION, 'dailyReportVersion': DAILY_REPORT_VERSION}
        if args.update:
            manifest.update(expected_metadata)
            manifest['scope'] = 'four-assistants-full-source-with-supplier-profile'
            manifest['baseSnapshotCommit'] = '64f4542647024f763914867fc97853fb39f88157'
            manifest['files'] = actual
            manifest_bytes = (json.dumps(manifest, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
            MANIFEST.write_bytes(manifest_bytes)
            SUMS.write_text(checksum_text(actual, manifest_bytes), encoding='utf-8', newline='\n')
        else:
            if entries != actual or any(manifest.get(key) != value for key, value in expected_metadata.items()):
                raise ValueError('源码内容、大小、摘要或版本与清单不一致。')
            if SUMS.read_text(encoding='utf-8') != checksum_text(actual, MANIFEST.read_bytes()):
                raise ValueError('SHA256SUMS.txt 与源码清单不一致。')
        print(json.dumps({'ok': True, 'files': len(actual), 'release': manifest['release']}, ensure_ascii=False))
        return 0
    except (OSError, UnicodeError, KeyError, TypeError, ValueError) as error:
        print(json.dumps({'ok': False, 'error': str(error)}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
