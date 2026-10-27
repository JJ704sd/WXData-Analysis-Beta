"""Explicit recovery from the selected account's uniquely verified live owner.

This is not an offline cipher attack. Only authenticated candidate keys leave
the bounded worker, and only file references/statuses leave this module.
"""
from datetime import datetime
import json
import multiprocessing
import os
from pathlib import Path
import sys
import uuid

from offline_chat import ArchiveError

NOTES = {
    'wechat_not_running': '未检测到本机微信进程，请先手动登录本人微信，再点“一键自动配置”；无需寻找密钥文件。',
    'source_not_active': '所选数据未被当前微信持有，请登录对应账号或选择当前账号目录；离线副本需使用已有密钥。',
    'owner_unknown': '无法确认所选数据库的进程归属；离线副本请提供已有密钥。',
    'owner_not_unique': '所选数据没有唯一对应的已登录微信进程；请选本机当前账号的原数据目录，或提供已有密钥。',
    'owner_changed': '微信进程归属已变化，未保存密钥。',
    'process_unavailable': '系统不允许只读访问所选微信进程，请使用有读取权限的本机用户。',
    'windows_x64_required': '取钥需要 Windows x64 和对应64位微信进程。',
    'missing_poc': '本程序缺少取钥组件，请使用完整的新便携包。',
    'key_match_incomplete': '没有找到通过全部所选数据库校验的密钥；当前客户端或内存布局可能不受支持。',
    'scan_timeout': '本次只读取钥已超时，未生成不完整结果。',
    'scan_budget_exceeded': '达到只读取钥的资源上限，未生成不完整结果。',
    'source_changed': '取钥期间数据库发生变化，未生成不完整结果。',
    'source_not_encrypted': '所选数据已是明文，或存在不支持的文件头；明文副本可直接加载，无需恢复密钥。',
}


def _worker(source, include_media, timeout, budget, pipe, include_voice=False):
    with open(os.devnull, 'w') as sink:
        for descriptor in (1, 2):
            try:
                os.dup2(sink.fileno(), descriptor)
            except OSError:
                pass  # Windowed EXEs may have no console file descriptors.
        sys.stdout = sys.stderr = sink
        try:
            from acquire_database_keys import collect_keys, WindowsDependencies, AcquireError
            result = collect_keys(source, WindowsDependencies(), timeout=timeout,
                                  max_scan_bytes=budget, include_media=include_media, include_voice=include_voice)
            image_keys, image_code = [], 'not_recovered'
            try:
                from acquire_image_key import scan
                image_keys = scan(source, timeout=min(timeout, 20), budget=min(budget, 256 * 1024 * 1024))
                image_code = 'verified_local_samples'
            except AcquireError as error:
                image_code = error.code
            except Exception:
                image_code = 'scan_failed'
            pipe.send({'ok': True, **result, 'imageKeys': image_keys, 'imageRecovery': image_code})
        except Exception as error:
            code = getattr(error, 'code', 'scan_failed')
            pipe.send({'ok': False, 'code': code})
        finally:
            pipe.close()


def _collect(source, include_media, timeout, budget, include_voice=False):
    context = multiprocessing.get_context('spawn')
    incoming, outgoing = context.Pipe(duplex=False)
    worker = context.Process(target=_worker, args=(str(source), include_media, timeout, budget, outgoing, include_voice))
    try:
        worker.start()
        outgoing.close()
        if not incoming.poll(timeout + min(timeout, 20) + 5):
            raise ArchiveError(NOTES['scan_timeout'])
        result = incoming.recv()
        if not isinstance(result, dict) or not result.get('ok'):
            code = result.get('code') if isinstance(result, dict) else ''
            raise ArchiveError(NOTES.get(code, '未能从本机微信恢复已校验密钥，请选择已有密钥文件。'))
        return result
    finally:
        incoming.close()
        outgoing.close()
        if worker.pid is not None:
            worker.join(1)
            if worker.is_alive():
                worker.terminate()  # Only this app's worker, never WeChat.
                worker.join(5)
            worker.close()


def recover_key_file(source_root, directory, *, allow_process_read=False, include_media=True,
                     include_voice=False, timeout=30, budget_mib=4096):
    if allow_process_read is not True:
        raise ArchiveError('恢复密钥需要用户明确选择只读访问本机当前微信进程。')
    if not isinstance(timeout, int) or not 5 <= timeout <= 90 or not isinstance(budget_mib, int) or not 64 <= budget_mib <= 4096:
        raise ArchiveError('取钥时间或读取预算超出支持范围。')
    from acquire_database_keys import inventory, read_first_pages, matching_keys, AcquireError
    source = Path(source_root).resolve()
    if (source / 'db_storage').is_dir():
        source = (source / 'db_storage').resolve()
    directory = Path(directory).resolve()
    account = source.parent if source.name == 'db_storage' else source
    if directory == account or directory.is_relative_to(account):
        raise ArchiveError('密钥和程序数据不能写入微信账号目录。')
    try:
        _, databases, _ = inventory(source, include_media, include_voice)
        pages = read_first_pages(databases)
        result = _collect(source, include_media, timeout, budget_mib * 1024 * 1024,
                          **({'include_voice': True} if include_voice else {}))
        # Re-authenticate after IPC and before writing a new private file.
        _, current_databases, _ = inventory(source, include_media, include_voice)
        current_pages = read_first_pages(current_databases)
        if current_databases != databases or set(current_pages) != set(pages):
            raise AcquireError('source_changed')
        keys = matching_keys(current_pages, [(row['key_hex'], row['salt_hex']) for row in result['keys']])
    except AcquireError as error:
        raise ArchiveError(NOTES.get(error.code, '所选数据或取钥结果未通过校验。')) from None
    folder = directory / 'keys'
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    if folder.resolve().parent != directory:
        raise ArchiveError('本地密钥目录存在不受支持的重定向。')
    path = folder / ('keys-' + datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:8] + '.json')
    value = {'keys': keys}
    if result.get('imageKeys'):
        value['imageKeys'] = result['imageKeys']
    with path.open('x', encoding='utf-8') as handle:
        json.dump(value, handle, ensure_ascii=True, separators=(',', ':'))
    return {'keyFile': str(path), 'databaseCount': len(databases), 'matchedCount': len(current_pages),
            'plaintextCount': len(databases) - len(current_pages),
            'imageRecovery': result.get('imageRecovery', 'not_recovered')}
