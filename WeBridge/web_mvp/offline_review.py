"""Publish the exact locally parsed batch after an explicit human review."""
from datetime import datetime
import json
import os
from pathlib import Path
import shutil
import tempfile

from database_snapshot import _hash_file
from offline_chat import ArchiveError, BEIJING, write_json, publish_directory
from offline_reporting import completion_report, REPORT_FILE

REVIEW_CHECKS = {
    'accountGroup': '已核对当前账号、群名称与群 ID',
    'rangeCount': '已核对日期范围、记录条数与本地数据范围',
    'content': '已抽查正文、发送人、引用、转发及附件解析状态',
    'gaps': '已检查缺失媒体、未知类型及解析限制，接受已列明的缺口',
}


def _atomic_write(path, writer):
    """Write metadata beside its destination and publish it atomically."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        fd, temporary = tempfile.mkstemp(prefix='.' + path.name + '-', dir=path.parent)
        with os.fdopen(fd, 'w', encoding='utf8') as stream:
            writer(stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary:
            Path(temporary).unlink(missing_ok=True)


def atomic_write_json(path, value):
    _atomic_write(path, lambda stream: json.dump(value, stream, ensure_ascii=False, indent=2))


def atomic_write_text(path, value):
    _atomic_write(path, lambda stream: stream.write(value))


def inventory(root):
    root = Path(root)
    result = []
    for path in sorted(root.rglob('*')):
        if path.is_symlink() or path.is_junction():
            raise ArchiveError('解析结果包含链接，请重新解析。')
        if path.relative_to(root).parts[0] == 'opened':
            # Checked viewer copies are derived from assets, not reviewed source files.
            continue
        if path.is_file():
            result.append({'path': path.relative_to(root).as_posix(),
                           'bytes': path.stat().st_size, 'sha256': _hash_file(path)})
    return result


def approve_review(pending, checks, note=''):
    if not isinstance(checks, dict) or set(checks) != set(REVIEW_CHECKS) or any(value is not True for value in checks.values()):
        raise ArchiveError('请由人工完成全部复核检查后再确认导出。')
    if not isinstance(note, str) or len(note) > 4000:
        raise ArchiveError('复核备注须为不超过4000字的文本。')
    if inventory(pending['root']) != pending['files']:
        raise ArchiveError('待复核文件已变化，请重新解析并复核。')
    return {**pending['summary'], 'status': 'confirmed',
            'confirmedAt': datetime.now(BEIJING).isoformat(timespec='seconds'),
            'confirmationBasis': 'explicit_review_checklist', 'checks': dict(checks), 'note': note,
            'reviewedArtifacts': pending['files']}


def validated_approval(pending):
    review = pending.get('approval')
    checks = review.get('checks', {}) if isinstance(review, dict) else {}
    if (not isinstance(review, dict) or review.get('reviewId') != pending['summary']['reviewId'] or
            review.get('chat') != pending['summary']['chat'] or review.get('status') != 'confirmed' or
            review.get('reviewedArtifacts') != pending['files'] or
            not isinstance(checks, dict) or set(checks) != set(REVIEW_CHECKS) or
            any(value is not True for value in checks.values())):
        raise ArchiveError('所选群尚未完成人工复核，请逐群复核后再一键导出。')
    if inventory(pending['root']) != pending['files']:
        pending.pop('approval', None)
        raise ArchiveError('已复核文件已变化，请重新解析并复核。')
    return review


def publish_review(pending, destination, checks=None, note=''):
    review = approve_review(pending, checks, note) if checks is not None else validated_approval(pending)
    root, destination = pending['root'], Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise ArchiveError('导出目录已存在，请使用新的目录。')
    stage = Path(tempfile.mkdtemp(prefix='.review-export-', dir=destination.parent))
    try:
        shutil.copytree(root, stage, dirs_exist_ok=True,
                        ignore=lambda folder, names: ['opened'] if Path(folder) == root and 'opened' in names else [])
        copied_files = inventory(stage)
        if copied_files != pending['files']:
            raise ArchiveError('复制期间解析结果发生变化，请重新解析并复核。')
        report = json.loads((stage / 'manifest.json').read_text(encoding='utf8'))
        write_json(stage / 'review.json', review)
        report['humanReview'] = {'status': 'confirmed', 'reviewId': review['reviewId'], 'file': 'review.json'}
        report['reporting'] = completion_report(destination, report)
        (stage / REPORT_FILE).write_text(report['reporting']['replyMarkdown'], encoding='utf8')
        # The copy was just hashed against the reviewed inventory. Only these
        # small metadata files have changed since; do not reread every media
        # attachment merely to repeat the same hashes in the manifest.
        changed = {'review.json', REPORT_FILE}
        artifacts = [item for item in copied_files
                     if item['path'] not in changed | {'manifest.json'}]
        for name in changed:
            path = stage / name
            artifacts.append({'path': name, 'bytes': path.stat().st_size,
                              'sha256': _hash_file(path)})
        report['artifacts'] = sorted(artifacts, key=lambda item: Path(item['path']))
        (stage / 'manifest.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf8')
        if destination.exists():
            raise ArchiveError('导出目录在确认期间已创建，未覆盖。')
        publish_directory(stage, destination)
        return report
    finally:
        if stage.exists() and stage.resolve().parent == destination.parent.resolve() and stage.name.startswith('.review-export-'):
            shutil.rmtree(stage)
