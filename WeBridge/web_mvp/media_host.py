"""Local attachment cache. Never accepts a browser-supplied filesystem path."""
import hashlib
import json
from pathlib import Path
import re
import secrets
import subprocess
import threading

from backend import ROOT, BridgeError


def byte_range(header, size):
    if not header: return 0, size - 1
    match = re.fullmatch(r'bytes=(\d*)-(\d*)', header.strip())
    if not match or not any(match.groups()): raise ValueError('无效的分段请求。')
    left, right = match.groups()
    if not left:
        count = int(right)
        if count <= 0: raise ValueError('无效的分段请求。')
        start, end = max(0, size - count), size - 1
    else:
        start, end = int(left), min(int(right), size - 1) if right else size - 1
    if start < 0 or start > end or start >= size: raise ValueError('分段范围超出附件大小。')
    return start, end


class MediaCache:
    def __init__(self, engine, directory=None):
        self.engine = engine
        self.directory = Path(directory or ROOT/'.runtime/web_mvp/media')
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.entries = {}
        self.by_message = {}

    def message(self, account, group, message_id):
        self.engine.validate(account, group)
        if group not in (self.engine.store.watched(account) or []):
            raise ValueError('请先勾选读取这个群聊。')
        rows = self.engine.store.rows('SELECT payload FROM messages WHERE account=? AND group_id=? AND id=?', (account, group, message_id))
        if not rows: raise ValueError('消息不存在，请刷新群聊。')
        message = json.loads(rows[0]['payload'])
        if message.get('kind') == 'revoke': raise ValueError('消息已经撤回。')
        return message

    def resolve(self, account, group, message_id, part=''):
        message = self.message(account, group, message_id)
        key = (account, group, message_id, part)
        with self.lock:
            asset_id = self.by_message.get(key)
            if asset_id in self.entries and not self.entries[asset_id]['public'].get('previewOnly'):
                return self.entries[asset_id]['public']
            if not message.get('dbName'):
                raise ValueError('消息数据正在升级，请刷新后重试。')
            result = self.engine.adapter.call('media', account=account, groupId=group,
                dbName=message['dbName'], localId=message['localId'], serverId=message['serverId'], part=part)
            if result.get('status') != 'ready': return result
            digest = result.get('digest', '')
            if not re.fullmatch(r'[a-f0-9]{64}', digest): raise BridgeError('附件校验失败。')
            asset_id = secrets.token_urlsafe(24)
            path = self.directory / (asset_id + '.blob')
            try:
                subprocess.run(['docker', 'cp', f'wechat-lab-linux:/tmp/wechat-mvp-media/{digest}.blob', str(path)],
                               check=True, capture_output=True, timeout=45)
                path.chmod(0o600)
                if path.stat().st_size != result['size'] or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                    raise ValueError('附件复制校验失败。')
            except Exception:
                path.unlink(missing_ok=True)
                raise BridgeError('附件缓存读取失败，请重试。')
            # Account and membership may have changed while Docker copied data.
            self.message(account, group, message_id)
            public = {k: result[k] for k in ('status','size','mime','filename','previewOnly','kind')}
            if result.get('reason'):public['reason']=result['reason']
            public['assetId'] = asset_id
            self.entries[asset_id] = {'key': key, 'path': path, 'public': public}
            self.by_message[key] = asset_id
            return public

    def get(self, asset_id, account):
        entry = self.entries.get(asset_id)
        if not entry or entry['key'][0] != account: raise ValueError('附件不存在或账号已变化。')
        self.message(*entry['key'][:3])
        return entry
