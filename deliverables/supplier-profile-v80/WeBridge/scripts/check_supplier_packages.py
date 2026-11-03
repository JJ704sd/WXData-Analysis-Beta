"""Verify all four packaged supplier services using isolated synthetic exports."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tempfile
import zipfile

PROJECT = Path(__file__).resolve().parents[1]
for path in (PROJECT / 'web_mvp', PROJECT / 'scripts', PROJECT.parent / 'poc'):
    sys.path.insert(0, str(path))

from test_daily_reports import formal_export, message
from test_supplier_profile import direct_export


def run_exe(exe, args, env):
    process = subprocess.Popen([str(exe), *args], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        return process.wait(timeout=60)
    except subprocess.TimeoutExpired:
        if os.name == 'nt':
            subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        else:
            process.kill()
        process.wait(timeout=10)
        raise RuntimeError('程序包测试超时。')


def check_product(base, product, temporary):
    archive = base / product['archive']
    archive_bytes = archive.read_bytes()
    if hashlib.sha256(archive_bytes).hexdigest() != product['sha256']:
        raise ValueError('程序包摘要不匹配。')
    unpack = temporary / 'package'
    unpack.mkdir()
    with zipfile.ZipFile(archive) as bundle:
        if bundle.testzip() is not None:
            raise ValueError('程序包 ZIP 校验失败。')
        for info in bundle.infolist():
            name = PurePosixPath(info.filename)
            if name.is_absolute() or '..' in name.parts or '\\' in info.filename:
                raise ValueError('程序包路径无效。')
        runtime = json.loads(bundle.read('runtime.json'))
        if any(runtime.get(field) != product[field] for field in ('client', 'conversationMode', 'version')):
            raise ValueError('程序包客户端、会话类型或版本不匹配。')
        if '供应商画像材料-v81.md' not in bundle.namelist():
            raise ValueError('程序包缺少供应商画像说明。')
        skill = bundle.read('SKILL.md').decode('utf-8')
        if '(供应商画像材料-v81.md)' not in skill:
            raise ValueError('程序包内说明链接无效。')
        package = json.loads(bundle.read('package.json'))
        for item in package['files']:
            raw = bundle.read(item['path'])
            if len(raw) != item['bytes'] or hashlib.sha256(raw).hexdigest() != item['sha256']:
                raise ValueError('程序包内文件摘要不匹配。')
        bundle.extractall(unpack)
    exe = unpack / runtime['exeRelativePath']
    if hashlib.sha256(exe.read_bytes()).hexdigest() != runtime['exeSha256']:
        raise ValueError('程序包内 EXE 摘要不匹配。')
    client, mode = product['client'], product['conversationMode']
    if mode == 'group':
        source = formal_export(temporary, 'reviewed', [
            message('supplier', '产品报价 30 元'), message('other', '其他成员的物流', senderId='other-supplier'),
            message('own', '我方确认', own=True), message('unknown-time', '订单待核实', time=None)],
            client=client, mode=mode)
        chat, speakers, relation = 'group-conversation', {'group-conversation': ['synthetic-peer']}, 'selected_group_speaker'
    else:
        source = direct_export(temporary, 'reviewed', [
            message('supplier', '产品报价 30 元'), message('own', '我方确认', own=True),
            message('other', '其他来源的物流', senderId='third-party'),
            message('unknown-time', '订单待核实', time=None)], client=client)
        chat, speakers, relation = 'direct-conversation', {}, 'selected_direct_peer'
    task = {'client': client, 'exportRoots': [str(source)], 'chatIds': [chat],
            'groupSpeakers': speakers, 'supplierLabel': '合成供应商',
            'start': '2026-10-27', 'end': '2026-10-27', 'output': str(temporary / 'report')}
    task_path = temporary / 'task.json'
    task_path.write_text(json.dumps(task, ensure_ascii=False), encoding='utf-8')
    receipt = temporary / 'result.json'
    environment = os.environ.copy()
    for key in ('PYTHONPATH', 'PYTHONHOME', 'TCL_LIBRARY', 'TK_LIBRARY'):
        environment.pop(key, None)
    environment['TEMP'] = environment['TMP'] = str(temporary)
    code = run_exe(exe, ['--client', client, '--conversation-mode', mode,
                         '--data-dir', str(temporary / 'profile'), '--supplier-analysis-json', str(task_path),
                         '--result-json', str(receipt)], environment)
    if code != 0 or not receipt.is_file():
        raise ValueError('程序包画像任务未生成成功回执。')
    result = json.loads(receipt.read_text(encoding='utf-8'))
    report = json.loads((temporary / 'report/report.json').read_text(encoding='utf-8'))
    rows = [json.loads(line) for line in (temporary / 'report/evidence.jsonl').read_text(encoding='utf-8').splitlines()]
    by_id = {row['messageId']: row for row in rows}
    if (not result['ok'] or report['version'] != 'v81' or report['overview']['datedMessages'] != 3
            or report['overview']['attributedMessages'] != 1 or report['overview']['undatedMessages'] != 1
            or by_id['supplier']['supplierRelation'] != relation
            or by_id['other']['supplierRelation'] in {'selected_group_speaker', 'selected_direct_peer'}):
        raise ValueError('画像范围、对象归属或时间统计错误。')
    quote = next(item for item in report['dimensions'] if item['label'] == '报价与费用')
    if quote['evidenceIds'] != [by_id['supplier']['evidenceId']]:
        raise ValueError('报价线索的证据引用错误。')
    if not all(row['sources'][0]['reviewId'] and row['sources'][0]['line'] for row in rows):
        raise ValueError('画像材料缺少导出复核依据。')
    page = (temporary / 'report/report.html').read_text(encoding='utf-8')
    if 'id="' + by_id['supplier']['evidenceId'] + '"' not in page:
        raise ValueError('报告无法定位原文证据。')
    for item in json.loads((temporary / 'report/manifest.json').read_text(encoding='utf-8'))['files']:
        raw = (temporary / 'report' / item['path']).read_bytes()
        if len(raw) != item['bytes'] or hashlib.sha256(raw).hexdigest() != item['sha256']:
            raise ValueError('画像材料校验清单不匹配。')
    task['client'] = 'wecom' if client == 'wechat' else 'wechat'
    task['output'] = str(temporary / 'wrong-client-report')
    task_path.write_text(json.dumps(task, ensure_ascii=False), encoding='utf-8')
    negative = temporary / 'negative.json'
    code = run_exe(exe, ['--client', client, '--conversation-mode', mode,
                         '--data-dir', str(temporary / 'profile'), '--supplier-analysis-json', str(task_path),
                         '--result-json', str(negative)], environment)
    if code != 2 or not negative.is_file() or json.loads(negative.read_text(encoding='utf-8'))['ok']:
        raise ValueError('程序包未拒绝客户端不一致的任务。')
    if (temporary / 'wrong-client-report').exists():
        raise ValueError('客户端不一致时不应发布画像材料。')
    return {'client': client, 'conversationMode': mode, 'version': product['version'],
            'archive': product['archive'], 'sha256': product['sha256'], 'ok': True,
            'datedMessages': 3, 'attributedMessages': 1, 'undatedMessages': 1,
            'sourceReviewAndEvidence': True, 'incorrectClientRejected': True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--packages', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    matrix = json.loads((args.packages / 'release-matrix.json').read_text(encoding='utf-8'))
    products = matrix['products']
    if {(row['client'], row['conversationMode']) for row in products} != {
            ('wechat', 'group'), ('wecom', 'group'), ('wechat', 'direct'), ('wecom', 'direct')} or len(products) != 4:
        raise SystemExit('程序包清单不完整。')
    results = []
    with tempfile.TemporaryDirectory(prefix='synthetic-supplier-packages-') as folder:
        for index, product in enumerate(products):
            target = Path(folder) / str(index)
            target.mkdir()
            try:
                results.append(check_product(args.packages, product, target))
                print(product['client'], product['conversationMode'], 'OK', flush=True)
            except Exception as error:
                results.append({'client': product['client'], 'conversationMode': product['conversationMode'],
                                'ok': False, 'error': str(error)})
                print(product['client'], product['conversationMode'], 'FAILED:', str(error), flush=True)
    report = {'schemaVersion': 1, 'checkedAt': datetime.now(timezone.utc).isoformat(),
              'ok': all(item['ok'] for item in results), 'syntheticOnly': True,
              'method': 'unpack_each_windows_zip_and_run_its_supplier_analysis_json',
              'realAccountTested': False, 'interactiveGuiTested': False, 'products': results}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
