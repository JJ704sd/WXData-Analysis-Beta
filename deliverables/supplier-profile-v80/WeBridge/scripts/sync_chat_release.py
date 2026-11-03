"""Synchronize generated Skill versions with chat_release.py; --check never writes."""
import argparse
from pathlib import Path
import re

from chat_release import GROUP_VERSION, PRIVATE_VERSION

PROJECT = Path(__file__).resolve().parents[1]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args(argv)
    stale = []
    for name, version in (
        ('local-wechat-archive', GROUP_VERSION),
        ('local-wechat-private-analysis', PRIVATE_VERSION),
        ('local-wecom-private-analysis', PRIVATE_VERSION),
    ):
        path = PROJECT / 'skills' / name / 'SKILL.md'
        body = path.read_text(encoding='utf-8')
        updated, count = re.subn(r'^  version: "[^"]+"$', '  version: "' + version + '"', body, count=1, flags=re.M)
        if count != 1:
            raise ValueError('Missing Skill metadata version: ' + name)
        if body != updated:
            stale.append(name)
            if not args.check:
                path.write_text(updated, encoding='utf-8')
    if args.check and stale:
        print('Stale Skill versions: ' + ', '.join(stale))
        return 1
    print('Skill versions match chat_release.py.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
