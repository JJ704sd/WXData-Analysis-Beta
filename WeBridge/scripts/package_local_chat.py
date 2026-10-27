"""Allowlisted local distribution: EXE, guide, build metadata and licenses only."""
import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path
import shutil
import sys
import zipfile

from local_chat_distribution import copy_public_guide, write_quick_start, complete_metadata, write_wecom_diagnostic_launcher, verify_group_desktop
from chat_release import GROUP_VERSION


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True, help='New package directory, must not exist')
    parser.add_argument('--desktop', action='store_true', help='Package the one-click native desktop application')
    parser.add_argument('--binary', type=Path, help='Use a specific already-built executable')
    parser.add_argument('--shareable', action='store_true', help='Include public guide and launcher; omit private historical reports')
    args = parser.parse_args()
    project = Path(__file__).resolve().parents[1]
    dependencies = project / '.runtime/offline-build-deps'
    source = project / ('build/local-chat-desktop/local-chat-desktop.exe' if args.desktop else 'build/local-chat/local-chat.exe')
    if args.binary:
        source = args.binary.resolve()
    output = args.output.resolve()
    archive = output.parent / (output.name + '.zip')
    if output.exists() or archive.exists():
        raise SystemExit('Package directory or archive exists; choose a new path.')
    if not source.is_file():
        raise SystemExit('Build ' + source.name + ' first.')
    if args.shareable and not args.desktop:
        raise SystemExit('--shareable requires the native desktop binary.')
    if args.shareable:
        verify_group_desktop(source, GROUP_VERSION)
    output.mkdir(parents=True)
    binary_name = 'local-chat-desktop.exe' if args.shareable else ('本地聊天解析.exe' if args.desktop else 'local-chat.exe')
    shutil.copyfile(source, output / binary_name)
    if args.shareable:
        copy_public_guide(project, output, 'desktop')
        write_quick_start(output, 'desktop')
        (output / 'Start.cmd').write_bytes(b'@echo off\r\nsetlocal\r\nstart "" "%~dp0local-chat-desktop.exe" --client wechat --data-dir "%LOCALAPPDATA%\\LocalWechatArchive"\r\n')
        (output / 'Start-WeCom.cmd').write_bytes(b'@echo off\r\nsetlocal\r\nstart "" "%~dp0local-chat-desktop.exe" --client wecom --data-dir "%LOCALAPPDATA%\\LocalWeComArchive"\r\n')
        write_wecom_diagnostic_launcher(output, binary_name)
    elif args.desktop:
        shutil.copyfile(project / 'LOCAL_CHAT_DESKTOP.md', output / 'README.md')
        reference = (project / 'LOCAL_CHAT.md').read_text(encoding='utf-8')
        (output / 'LOCAL_CHAT.md').write_text(reference.replace('(LOCAL_CHAT_DESKTOP.md)', '(README.md)'), encoding='utf-8')
    else:
        shutil.copyfile(project / 'LOCAL_CHAT.md', output / 'README.md')
    if not args.shareable:
        (output / 'execution').mkdir()
        shutil.copyfile(project / 'execution/offline-chat-parser-2026-10-16.md',
                        output / 'execution/offline-chat-parser-2026-10-16.md')
    if args.desktop and not args.shareable:
        shutil.copyfile(project / 'execution/offline-chat-desktop-2026-10-16.md',
                        output / 'execution/offline-chat-desktop-2026-10-16.md')
        shutil.copyfile(project / 'execution/offline-chat-media-v2-2026-10-16.md',
                        output / 'execution/offline-chat-media-v2-2026-10-16.md')
        shutil.copyfile(project / 'execution/offline-chat-business-v3-2026-10-16.md',
                        output / 'execution/offline-chat-business-v3-2026-10-16.md')
        report = project / 'execution/offline-chat-portable-v4-2026-10-16.md'
        if report.is_file():
            shutil.copyfile(report, output / 'execution' / report.name)
        report = project / 'execution/offline-chat-short-launch-v5-2026-10-16.md'
        if report.is_file():
            shutil.copyfile(report, output / 'execution' / report.name)
    licenses = output / 'licenses'
    licenses.mkdir()
    shutil.copyfile(Path(sys.base_prefix) / 'LICENSE.txt', licenses / 'Python-LICENSE.txt')
    if args.desktop:
        shutil.copyfile(project / 'scripts/licenses/Tcl-8.6.15-license.terms', licenses / 'Tcl-8.6.15-license.terms')
        shutil.copyfile(Path(sys.base_prefix) / 'tcl/tk8.6/license.terms', licenses / 'Tk-8.6-license.terms')
    versions = {}
    names = {'cryptography', 'zstandard', 'cffi', 'pycparser', 'pyinstaller', 'pypdf', 'xlrd', 'olefile', 'msoffcrypto-tool'}
    if args.desktop:
        names.add('pillow')
    for distribution in importlib.metadata.distributions(path=[str(dependencies), *sys.path]):
        name = distribution.metadata['Name'].lower()
        if name not in names:
            continue
        versions[name] = distribution.version
        for file in distribution.files or []:
            listed = str(file).replace('\\', '/')
            modern_license = '/licenses/' in listed
            legacy_license = name in ('pypdf', 'xlrd', 'olefile', 'msoffcrypto-tool') and Path(listed).name.upper() in ('LICENSE', 'LICENSE.TXT', 'COPYING')
            if not modern_license and not legacy_license:
                continue
            path = Path(distribution.locate_file(file))
            if path.is_file():
                target = licenses / name / (Path(listed.split('licenses/', 1)[1]) if modern_license else Path(listed).name)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, target)
    metadata = {'platform': 'Windows x64', 'interface': 'desktop' if args.desktop else 'cli',
                'python': sys.version.split()[0], 'dependencies': versions,
                'containsChatData': False, 'containsKeys': False,
                'files': [{'path': path.relative_to(output).as_posix(), 'bytes': path.stat().st_size,
                           'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
                          for path in sorted(output.rglob('*')) if path.is_file()]}
    if args.shareable:
        complete_metadata(output, metadata)
    else:
        (output / 'package.json').write_text(json.dumps(metadata, indent=2), encoding='utf-8')
    with zipfile.ZipFile(archive, 'x', zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(output.rglob('*')):
            if path.is_file():
                bundle.write(path, output.name + '/' + path.relative_to(output).as_posix())
    print(json.dumps({'directory': str(output), 'archive': str(archive), 'bytes': archive.stat().st_size,
                      'sha256': hashlib.sha256(archive.read_bytes()).hexdigest(), 'dependencies': versions}))


if __name__ == '__main__':
    main()
