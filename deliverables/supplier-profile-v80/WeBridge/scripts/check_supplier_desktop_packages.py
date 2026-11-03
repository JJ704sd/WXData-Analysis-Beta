"""Run each released Windows EXE through the synthetic supplier desktop workflow."""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import sqlite3
import subprocess
import sys
import zipfile

PROJECT = Path(__file__).resolve().parents[1]
for path in (PROJECT / 'web_mvp', PROJECT / 'scripts', PROJECT.parent / 'poc',
             PROJECT / '.runtime' / 'offline-build-deps'):
    sys.path.insert(0, str(path))

from offline_desktop import DesktopSession
from offline_review import REVIEW_CHECKS
from test_database_adapter import create_shard
from test_four_exports53 import source_fixture
from test_wecom_archive import pb

QUOTE = '供应商产品报价30元（合成）'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fixture(base, client, mode):
    config, chat, _ = source_fixture(base, client, mode, count=0)
    source = Path(config['sourceRoot'])
    if client == 'wechat':
        create_shard(source, relative='message/message_2.db', group=chat,
                     rows=[{'local': 1000, 'sender': 2, 'time': 1700000020, 'text': QUOTE}])
    else:
        with closing(sqlite3.connect(source / 'user.db')) as db, db:
            if not db.execute('SELECT 1 FROM user_table WHERE id=22').fetchone():
                db.execute('INSERT INTO user_table VALUES(22,?,?)', ('合成供应商', '合成供应商'))
        content = pb(1, pb(1, 0) + pb(2, pb(1, QUOTE)))
        with closing(sqlite3.connect(source / 'message.db')) as db, db:
            db.execute('INSERT INTO message_table VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                       (1000, 1010, 1000, 22, chat, 2, 1700000020, 0, content, b'', b''))
    config['includeMedia'] = False
    session = DesktopSession(base / 'profile', client=client, conversation_mode=mode)
    session.open_source(config)
    review = session.prepare_review(chat, copy_media=False, load_view=False)
    exported = session.confirm_export(review['review']['reviewId'], {key: True for key in REVIEW_CHECKS}, chat=chat)
    session.remember_selection([chat], active=chat)
    rows = [json.loads(line) for line in (Path(exported['output']) / 'messages.jsonl').read_text(encoding='utf-8').splitlines()]
    quotation = [row for row in rows if row.get('text') == QUOTE]
    if len(quotation) != 1:
        raise ValueError('合成报价消息没有唯一出现在正式导出中。')
    return {'client': client, 'mode': mode, 'chatId': chat, 'senderId': quotation[0]['senderId'],
            'messageId': quotation[0]['id'], 'reports': str(base / 'reports'), 'start': '2023-11-15',
            'end': '2023-11-15', 'supplierLabel': '合成供应商'}


def check_product(packages, product, base):
    archive = packages / product['archive']
    if digest(archive) != product['sha256']:
        raise ValueError('程序包 SHA-256 不匹配。')
    package = base / 'package'
    package.mkdir()
    with zipfile.ZipFile(archive) as bundle:
        if bundle.testzip() is not None:
            raise ValueError('程序包 ZIP 校验失败。')
        for info in bundle.infolist():
            name = PurePosixPath(info.filename)
            if name.is_absolute() or '..' in name.parts or '\\' in info.filename:
                raise ValueError('程序包路径无效。')
        runtime = json.loads(bundle.read('runtime.json'))
        for field in ('client', 'conversationMode', 'version'):
            if runtime[field] != product[field]:
                raise ValueError('程序包身份不匹配。')
        bundle.extractall(package)
    exe = package / runtime['exeRelativePath']
    if digest(exe) != runtime['exeSha256']:
        raise ValueError('程序包 EXE 摘要不匹配。')
    task = fixture(base, product['client'], product['conversationMode'])
    task_path, receipt = base / 'desktop-task.json', base / 'desktop-result.json'
    task_path.write_text(json.dumps(task, ensure_ascii=False), encoding='utf-8')
    temp = base / 'temp'
    temp.mkdir()
    environment = os.environ.copy()
    for key in ('PYTHONPATH', 'PYTHONHOME', 'TCL_LIBRARY', 'TK_LIBRARY'):
        environment.pop(key, None)
    for key in ('TEMP', 'TMP', 'LOCALAPPDATA'):
        environment[key] = str(temp)
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = 0
    command = [str(exe), '--client', task['client'], '--conversation-mode', task['mode'],
               '--data-dir', str(base / 'profile'), '--check-supplier-desktop', str(task_path),
               '--result-json', str(receipt)]
    process = subprocess.Popen(command, env=environment, startupinfo=startup,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        status = process.wait(timeout=90)
    except subprocess.TimeoutExpired:
        subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        process.wait(timeout=10)
        raise RuntimeError('桌面合成检查超时。')
    result = json.loads(receipt.read_text(encoding='utf-8')) if receipt.exists() else {}
    if status or not result.get('ok'):
        raise ValueError(f'桌面流程失败 (exit={status}): {result.get("error", "没有回执")}')
    reports = list((base / 'reports').iterdir())
    if len(reports) != 1:
        raise ValueError('桌面流程未生成唯一报告目录。')
    folder = reports[0]
    report = json.loads((folder / 'report.json').read_text(encoding='utf-8'))
    evidence = [json.loads(row) for row in (folder / 'evidence.jsonl').read_text(encoding='utf-8').splitlines()]
    rows = [row for row in evidence if row['messageId'] == task['messageId']]
    relation = 'selected_group_speaker' if task['mode'] == 'group' else 'selected_direct_peer'
    if len(rows) != 1 or rows[0]['senderId'] != task['senderId'] or rows[0]['supplierRelation'] != relation:
        raise ValueError('输出证据归属错误。')
    row = rows[0]
    prices = next(item for item in report['dimensions'] if item['label'] == '报价与费用')
    if row['evidenceId'] not in prices['evidenceIds'] or not row['sources'][0]['reviewId'] or not row['sources'][0]['line']:
        raise ValueError('报价线索或复核出处缺失。')
    for item in json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))['files']:
        if digest(folder / item['path']) != item['sha256']:
            raise ValueError('报告校验清单不匹配。')
    return {**product, 'ok': True, 'exeSha256': runtime['exeSha256'], 'steps': result['steps'],
            'selectedRelation': relation, 'attributedMessages': result['attributedMessages'],
            'quoteReviewAndLine': True, 'reportManifestVerified': True,
            'nativeFolderPickerStubbed': True, 'browserLaunchStubbed': True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--packages', required=True, type=Path)
    parser.add_argument('--work-root', required=True, type=Path)
    parser.add_argument('--report', required=True, type=Path)
    args = parser.parse_args()
    if sys.platform != 'win32':
        raise SystemExit('此检查需要 Windows 桌面会话。')
    products = json.loads((args.packages / 'release-matrix.json').read_text(encoding='utf-8'))['products']
    if len(products) != 4 or {(p['client'], p['conversationMode']) for p in products} != {
            ('wechat', 'group'), ('wecom', 'group'), ('wechat', 'direct'), ('wecom', 'direct')}:
        raise SystemExit('需要四类程序包。')
    args.work_root.mkdir(parents=True, exist_ok=False)
    results = []
    for product in products:
        base = (args.work_root / (product['client'] + '-' + product['conversationMode'])).resolve()
        base.mkdir()
        try:
            results.append(check_product(args.packages.resolve(), product, base))
            print(product['client'], product['conversationMode'], 'OK', flush=True)
        except Exception as error:
            results.append({**product, 'ok': False, 'error': str(error)})
            print(product['client'], product['conversationMode'], 'FAILED:', str(error), flush=True)
    report = {'schemaVersion': 1, 'checkedAt': datetime.now(timezone.utc).isoformat(),
              'ok': all(item['ok'] for item in results), 'syntheticOnly': True,
              'method': 'each_unpacked_exe_drives_real_tk_widgets_with_isolated_reviewed_synthetic_profile',
              'realAccountTested': False, 'nativeFolderPickerTested': False,
              'browserLaunchTested': False, 'products': results}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
