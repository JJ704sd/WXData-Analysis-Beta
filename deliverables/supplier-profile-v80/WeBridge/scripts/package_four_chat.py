"""Package all four products from one explicitly selected desktop/profile build."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import zipfile

from chat_release import GROUP_VERSION, PRIVATE_VERSION, release_matrix
from local_chat_distribution import verify_group_desktop
from package_private_chat import validate_executable
from sync_chat_release import main as check_versions


def verify_package(archive, *, client, mode, version):
    with zipfile.ZipFile(archive) as bundle:
        if bundle.testzip() is not None:
            raise ValueError('Package CRC verification failed: ' + archive.name)
        runtime = json.loads(bundle.read('runtime.json'))
        if (runtime.get('client') != client or runtime.get('conversationMode') != mode
                or runtime.get('version') != version):
            raise ValueError('Package product binding mismatch: ' + archive.name)
        manifest = json.loads(bundle.read('package.json'))
        for row in manifest['files']:
            if hashlib.sha256(bundle.read(row['path'])).hexdigest() != row['sha256']:
                raise ValueError('Package file digest mismatch: ' + archive.name)
    return {'client': client, 'conversationMode': mode, 'version': version,
            'archive': archive.name, 'sha256': hashlib.sha256(archive.read_bytes()).hexdigest()}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--desktop-exe', required=True, type=Path)
    parser.add_argument('--profile-cli-exe', required=True, type=Path)
    parser.add_argument('--output-root', required=True, type=Path)
    args = parser.parse_args(argv)
    desktop = args.desktop_exe.resolve()
    profile = args.profile_cli_exe.resolve()
    if check_versions(['--check']):
        raise SystemExit('Synchronize Skill versions before packaging.')
    verify_group_desktop(desktop, GROUP_VERSION)
    validate_executable(desktop, 'desktop', PRIVATE_VERSION)
    validate_executable(profile, 'profile', PRIVATE_VERSION)
    output = args.output_root.resolve()
    if output.exists():
        raise SystemExit('Choose a new output directory; existing packages are preserved.')
    scripts = Path(__file__).resolve().parent
    output.mkdir(parents=True)

    def run(name, *arguments):
        subprocess.run([sys.executable, str(scripts / name), *map(str, arguments)], check=True)

    runtime = output / 'desktop'
    run('package_local_chat.py', '--desktop', '--shareable', '--binary', desktop, '--output', runtime)
    products = []
    for client, label in (('wechat', 'WeChat'), ('wecom', 'WeCom')):
        directory = output / (label + '-Archive-Skill-' + GROUP_VERSION + '-Windows-x64')
        run('package_local_chat_skill.py', '--shareable', '--client', client,
            '--desktop-package', runtime, '--output', directory)
        products.append(verify_package(Path(str(directory) + '.zip'), client=client, mode='group', version=GROUP_VERSION))
    private_output = output / 'private'
    run('package_private_chat.py', '--desktop-exe', desktop, '--profile-cli-exe', profile,
        '--output-root', private_output, '--version', PRIVATE_VERSION)
    for client in ('wechat', 'wecom'):
        archive = private_output / ('local-' + client + '-private-analysis-' + PRIVATE_VERSION + '-windows-x64.zip')
        row = verify_package(archive, client=client, mode='direct', version=PRIVATE_VERSION)
        row['archive'] = archive.relative_to(output).as_posix()
        products.append(row)
    result = {**release_matrix(), 'products': products, 'containsChatData': False, 'containsKeys': False}
    (output / 'release-matrix.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
