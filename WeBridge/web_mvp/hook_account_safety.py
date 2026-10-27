"""Durable local account budgets shared by every Windows Hook submission.

These are operator limits, not platform-approved safe rates. Account keys derive
from the selected identity; no account prefix, sample or directory is admitted
by a fixed identity list. The native bridge and workbench share this ledger.
"""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import time

DEFAULT_LIMITS = {'minimumIntervalSeconds': 5, 'perMinute': 6,
                  'per24Hours': 100, 'duplicateWindowSeconds': 30}
LIMIT_RANGES = {'minimumIntervalSeconds': (1, 3600), 'perMinute': (1, 60),
                'per24Hours': (1, 1000), 'duplicateWindowSeconds': (5, 3600)}


class SafetyError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def account_key(source):
    identity = source.get('selfId') if isinstance(source, dict) else None
    if (not isinstance(identity, str) or not identity or len(identity) > 256 or
            any(ord(char) < 32 or ord(char) == 127 for char in identity)):
        raise SafetyError('source_changed')
    return hashlib.sha256(identity.encode('utf-8')).hexdigest()


def content_key(target, text_hash):
    return hashlib.sha256(json.dumps([target, text_hash], separators=(',', ':')).encode()).hexdigest()


class HookAccountSafety:
    def __init__(self, directory, *, clock=None):
        self.path = Path(directory) / 'hook-account-safety.sqlite'
        self.clock = clock or time.time
        with closing(self._db()) as db, db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS accounts (
                    account_key TEXT PRIMARY KEY, version INTEGER NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS submissions (
                    id TEXT PRIMARY KEY, account_key TEXT NOT NULL, content_key TEXT NOT NULL,
                    created REAL NOT NULL, status TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS safety_account_time ON submissions(account_key,created);
            ''')
        self._import_native_legacy()

    def _import_native_legacy(self):
        # Read both prior native entry points before any new submission, even if
        # that particular bridge is never restarted after upgrading.
        legacy=[]
        for name,table in (('hook-bridge-attempts.sqlite','attempts'),('hook-smoke-attempt.sqlite','attempt')):
            path=self.path.parent/name
            if not path.is_file():continue
            with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=10)) as db:
                responses=db.execute(f'SELECT response FROM {table}').fetchall()
            for raw, in responses:
                response=json.loads(raw)
                if response.get('status')=='unknown':
                    legacy.append({'id':response['requestId'],'status':'unknown',
                        'request':json.dumps({**response['binding'],'targetId':response['targetId']}),
                        'text_hash':response['textHash'],'result':'{}','expires':self.clock()+120})
        self.import_legacy(legacy)

    def _db(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA synchronous=FULL')
        return db

    @staticmethod
    def _account(db, key):
        db.execute('INSERT OR IGNORE INTO accounts VALUES(?,?,?)', (key, 0, json.dumps(
            {'limits': DEFAULT_LIMITS, 'paused': False, 'reasonCode': '', 'blockedRequestId': ''})))
        row = db.execute('SELECT * FROM accounts WHERE account_key=?', (key,)).fetchone()
        return row['version'], json.loads(row['payload'])

    def _view(self, db, key, version, state):
        now = self.clock()
        times = [row[0] for row in db.execute(
            "SELECT created FROM submissions WHERE account_key=? AND status!='cancelled' AND created>? ORDER BY created",
            (key, now-86400))]
        minute = [stamp for stamp in times if stamp > now-60]
        limits, retry = state['limits'], now
        if times: retry = max(retry, times[-1]+limits['minimumIntervalSeconds'])
        if len(minute) >= limits['perMinute']: retry = max(retry, minute[-limits['perMinute']]+60)
        if len(times) >= limits['per24Hours']: retry = max(retry, times[-limits['per24Hours']]+86400)
        unknown = db.execute("SELECT count(*) FROM submissions WHERE account_key=? AND status IN ('unknown','attempted')", (key,)).fetchone()[0]
        return {'version': version, **state, 'unresolvedCount': unknown, 'usage': {'minute': len(minute), 'last24Hours': len(times)},
                'retryAt': retry if retry > now else None}

    def status(self, source):
        key = account_key(source)
        with closing(self._db()) as db, db:
            version, state = self._account(db, key)
            return self._view(db, key, version, state)

    def configure(self, source, limits, version):
        if (not isinstance(limits, dict) or set(limits) != set(DEFAULT_LIMITS) or
                any(type(limits[name]) is not int or not low <= limits[name] <= high
                    for name, (low, high) in LIMIT_RANGES.items())):
            raise SafetyError('invalid_safety_limits')
        key = account_key(source)
        with closing(self._db()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            current, state = self._account(db, key)
            if type(version) is not int or current != version: raise SafetyError('safety_version_changed')
            state['limits'] = dict(limits)
            db.execute('UPDATE accounts SET version=?,payload=? WHERE account_key=?',
                       (current+1, json.dumps(state), key))
        return self.status(source)

    @staticmethod
    def _pause(db, key, version, state, request_id, reason='outcome_unknown'):
        if not state['paused']:
            state.update(paused=True, reasonCode=reason, blockedRequestId=request_id)
            db.execute('UPDATE accounts SET version=?,payload=? WHERE account_key=?',
                       (version+1, json.dumps(state), key))

    def _check(self, db, key, request_id, content, expected_version=None):
        version, state = self._account(db, key)
        previous = db.execute('SELECT * FROM submissions WHERE id=?', (request_id,)).fetchone()
        if previous and (previous['account_key'] != key or previous['content_key'] != content):
            return 'idempotency_conflict'
        unfinished = db.execute("SELECT id FROM submissions WHERE account_key=? AND "
            "(status='unknown' OR (status='attempted' AND id!=?)) LIMIT 1", (key, request_id)).fetchone()
        if unfinished:
            self._pause(db, key, version, state, unfinished['id'])
            return 'account_paused'
        if state['paused']: return 'account_paused'
        if expected_version is not None and (type(expected_version) is not int or expected_version != version):
            return 'safety_policy_changed'
        if previous: return None if previous['status']=='attempted' else 'request_consumed'
        view = self._view(db, key, version, state)
        if view['retryAt'] is not None: return 'account_rate_limited'
        duplicate = db.execute("SELECT 1 FROM submissions WHERE account_key=? AND content_key=? "
            "AND status!='cancelled' AND created>? LIMIT 1", (key, content,
            self.clock()-state['limits']['duplicateWindowSeconds'])).fetchone()
        return 'account_rate_limited' if duplicate else None

    def check(self, source, request_id, target, text_hash, *, expected_version=None):
        key, content = account_key(source), content_key(target, text_hash)
        with closing(self._db()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            error = self._check(db, key, request_id, content, expected_version)
        if error: raise SafetyError(error)

    def admit(self, source, request_id, target, text_hash, *, expected_version=None):
        key, content = account_key(source), content_key(target, text_hash)
        with closing(self._db()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            error = self._check(db, key, request_id, content, expected_version)
            if not error:
                db.execute('INSERT OR IGNORE INTO submissions VALUES(?,?,?,?,?)',
                           (request_id, key, content, self.clock(), 'attempted'))
        if error: raise SafetyError(error)

    def finish(self, source, request_id, status):
        key = account_key(source)
        with closing(self._db()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            final = 'cancelled' if status == 'expired' else status
            db.execute("UPDATE submissions SET status=CASE WHEN status IN ('unknown','acknowledged_unknown') "
                       "AND ?!='unknown' THEN status ELSE ? END WHERE id=? AND account_key=?",
                       (final, final, request_id, key))
            if status == 'unknown':
                version, state = self._account(db, key)
                self._pause(db, key, version, state, request_id)

    def fail_before_submission(self, source, request_id):
        """The caller could not persist its latch and has not dispatched native work."""
        key=account_key(source)
        with closing(self._db()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            db.execute("UPDATE submissions SET status='not_submitted' WHERE id=? AND account_key=? AND status='attempted'",
                       (request_id,key))
            version,state=self._account(db,key)
            self._pause(db,key,version,state,request_id,reason='journal_unavailable')

    def import_legacy(self, rows):
        with closing(self._db()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            for row in rows:
                source = json.loads(row['request'])
                key = account_key(source)
                result = json.loads(row['result'])
                stamp = result.get('submittedAtEpoch', row['expires']-120)
                status = 'unknown' if row['status']=='attempted' else row['status']
                inserted = db.execute('INSERT OR IGNORE INTO submissions VALUES(?,?,?,?,?)',
                    (row['id'], key, content_key(source['targetId'], row['text_hash']), stamp, status)).rowcount
                if inserted and status == 'unknown':
                    version, state = self._account(db, key)
                    self._pause(db, key, version, state, row['id'])

    def recover_pending(self):
        """Caller holds exclusive submission ownership after a process interruption."""
        with closing(self._db()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            rows = db.execute("SELECT id,account_key FROM submissions WHERE status='attempted'").fetchall()
            for row in rows:
                db.execute("UPDATE submissions SET status='unknown' WHERE id=?", (row['id'],))
                version, state = self._account(db, row['account_key'])
                self._pause(db,row['account_key'],version,state,row['id'])

    def pause(self, source, version):
        key = account_key(source)
        with closing(self._db()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            current, state = self._account(db, key)
            if type(version) is not int or current != version: raise SafetyError('safety_version_changed')
            if not state['paused']: state.update(paused=True,reasonCode='manual_pause',blockedRequestId='')
            db.execute('UPDATE accounts SET version=?,payload=? WHERE account_key=?',
                       (current+1,json.dumps(state),key))
        return self.status(source)

    def resume(self, source, version, acknowledged):
        if acknowledged is not True: raise SafetyError('safety_acknowledgement_required')
        key = account_key(source)
        with closing(self._db()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            current, state = self._account(db, key)
            if type(version) is not int or current != version: raise SafetyError('safety_version_changed')
            if not state['paused']: raise SafetyError('account_not_paused')
            # Acknowledgement permits new requests; original requests remain consumed.
            db.execute("UPDATE submissions SET status='acknowledged_unknown' WHERE account_key=? "
                       "AND status IN ('unknown','attempted')", (key,))
            state.update(paused=False, reasonCode='', blockedRequestId='')
            db.execute('UPDATE accounts SET version=?,payload=? WHERE account_key=?',
                       (current+1, json.dumps(state), key))
        return self.status(source)
