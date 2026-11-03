"""Generate a supplier evidence bundle from an explicit local JSON task."""
import argparse
import json
from pathlib import Path
import sys

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / 'web_mvp'))

from offline_chat import ArchiveError
from offline_supplier import build_supplier_report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--task', required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        task = json.loads(args.task.read_text(encoding='utf-8-sig'))
        result = build_supplier_report(task['exportRoots'], client=task['client'],
            chat_ids=task['chatIds'], start=task['start'], end=task['end'],
            output=task['output'], supplier_label=task['supplierLabel'],
            group_speakers=task.get('groupSpeakers'))
    except (ArchiveError, OSError, KeyError, TypeError, ValueError) as error:
        print(json.dumps({'ok': False, 'error': str(error) if isinstance(error, ArchiveError)
                          else '任务文件、输入材料或输出位置无效，请核对后重试。'}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
