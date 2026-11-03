"""Build or check the standard-library viewer bundled in the desktop executable."""
import argparse
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]
SOURCE = PROJECT / 'web_mvp' / 'chat_viewer_src'
DEFAULT_OUTPUT = PROJECT / 'web_mvp' / 'chat_viewer.py'


def content():
    index = (SOURCE / 'global_search.py').read_text(encoding='utf-8')
    app = (SOURCE / 'app.py').read_text(encoding='utf-8')
    future = 'from __future__ import annotations\n'
    module_import = 'from global_search import GlobalSearchIndex, searchable_fields, source_fingerprint\n'
    if index.count(future) != 1 or app.count(future) != 1 or app.count(module_import) != 1:
        raise ValueError('Viewer sources no longer match the bundler contract')
    assets = {name: (SOURCE / name).read_text(encoding='utf-8')
              for name in ('index.html', 'app.js', 'style.css')}
    return ('#!/usr/bin/env python3\n# -*- coding: utf-8 -*-\n'
            '# Local chat archive viewer; generated from the source directory.\n'
            'from __future__ import annotations\n\n'
            f'_BUNDLED_ASSETS = {assets!r}\n\n'
            + index.replace(future, '', 1) + '\n\n'
            + app.replace(future, '', 1).replace(module_import, '', 1))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    expected = content()
    if args.check:
        if not args.output.is_file() or args.output.read_text(encoding='utf-8') != expected:
            raise SystemExit('Bundled chat viewer is stale; run scripts/build_chat_viewer.py')
        print('Bundled chat viewer matches its sources.')
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(expected, encoding='utf-8', newline='\n')
        print(f'Built {args.output}')


if __name__ == '__main__':
    main()
