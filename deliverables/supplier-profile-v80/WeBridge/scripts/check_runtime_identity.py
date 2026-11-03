"""Reject embedded account identities in runtime source without exposing their values."""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIRS = ('scripts', 'web_mvp', 'execution/windows')
SOURCE_SUFFIXES = {'.py', '.js', '.ps1'}
EXCLUDED_DIRS = {'__pycache__', 'diagnostics', 'vendor'}


def runtime_sources(root):
    for relative in SOURCE_DIRS:
        top = root / relative
        if not top.is_dir():
            continue
        for folder, directories, names in os.walk(top):
            directories[:] = sorted(name for name in directories if name not in EXCLUDED_DIRS)
            for name in sorted(names):
                path = Path(folder) / name
                if (path.suffix not in SOURCE_SUFFIXES or name.startswith('test_') or
                        path.relative_to(root).as_posix() == 'web_mvp/demo_backend.py'):
                    continue
                yield path


IDENTITY = re.compile(
    r'\bwxid_[A-Za-z0-9_]+\b|\b[0-9]{5,}@(?:im\.)?chatroom\b|'
    r'\b(?:fixture|synthetic|sample|example)[-_](?:self|account)(?:[-_][A-Za-z0-9]+)*\b', re.I)
JS_TOKENS = re.compile(r'//[^\r\n]*|/\*[\s\S]*?\*/|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|`(?:\\.|[^`\\])*`')
PS_TOKENS = re.compile(r'\#[^\r\n]*|<\#[\s\S]*?\#>|"(?:`.|[^"`])*"|\'(?:\'\'|[^\'])*\'')


def literals(path, source):
    if path.suffix == '.py':
        tree = ast.parse(source)
        docstrings = set()
        for block in ast.walk(tree):
            if isinstance(block, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                if block.body and isinstance(block.body[0], ast.Expr):
                    value = block.body[0].value
                    if isinstance(value, ast.Constant) and isinstance(value.value, str):
                        docstrings.add(id(value))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
                yield node.lineno, node.value
    else:
        pattern = JS_TOKENS if path.suffix == '.js' else PS_TOKENS
        for token in pattern.finditer(source):
            value = token.group()
            if value.startswith(('//', '/*', '#', '<#')):
                continue
            yield source.count('\n', 0, token.start()) + 1, value


def scan(root):
    paths = list(runtime_sources(root))
    issues = []
    for path in paths:
        relative = path.relative_to(root).as_posix()
        try:
            source = path.read_text(encoding='utf-8-sig')
            lines = {line for line, value in literals(path, source) if IDENTITY.search(value)}
            issues.extend({'path': relative, 'line': line, 'code': 'embedded_wechat_identity'}
                          for line in sorted(lines))
        except SyntaxError as error:
            issues.append({'path': relative, 'line': error.lineno or 1, 'code': 'invalid_python_source'})
        except (OSError, UnicodeError):
            issues.append({'path': relative, 'line': 1, 'code': 'source_unreadable'})
    if not paths:
        issues.append({'path': '.', 'line': 1, 'code': 'no_runtime_sources'})
    return {'ok': not issues, 'scannedFiles': len(paths), 'issues': issues}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    args = parser.parse_args(argv)
    report = scan(args.root.resolve())
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
