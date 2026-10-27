"""Explicit, one-shot filehelper Hook smoke bridge for the pinned local client.

Default: construct/read back the fixed native payload; do not send.
--serve: retain that payload and serve an authenticated loopback bridge. Only an
explicit POST may submit, and a FULL-synchronous disk latch precedes native RPC.
This is an operator test harness, not a general-purpose messaging endpoint.
The Frida agent and script allocations remain resident until the client exits;
this avoids the independently reproduced Frida 17.19 Windows unload crash.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import sys
import threading

from inspect_windows_hook_runtime import ROOT, HASH, CODE_RVAS, loaded_module_path
from analyze_windows_hook_send import Image
from acquire_database_keys import AcquireError

sys.path.insert(0, str(ROOT / 'web_mvp'))
from hook_account_safety import HookAccountSafety, SafetyError

PROTOCOL = 'webridge.windows-hook.v1'
TARGET, TEXT = 'filehelper', 'WB-HOOK-0926'
TEXT_HASH = hashlib.sha256(TEXT.encode('ascii')).hexdigest()
RUNTIME = ROOT / '.runtime/web_mvp-database'
SCRIPT = ROOT / 'execution/windows/native_send_once.js'
PROFILE = {'clientVersion': '4.1.15.13', 'arch': 'x64', 'moduleName': 'weixin.dll', 'moduleSha256': HASH}
CHECKS = frozenset(('pointerPair', 'contentVtable', 'controlVtable', 'weakSelfReference', 'receiver',
                   'content', 'messageType', 'messageLength', 'emptyAtList', 'tokenPair', 'emptyCallbacks', 'vector'))
CONSTRUCTION_STEPS = frozenset(('allocate_wrappers', 'text_factory', 'text_factory_readback',
    'write_inline_fields', 'options_factory', 'options_readback', 'full_payload_readback'))
FAILED_CHECKS = frozenset(('factory_return_mismatch', 'factory_pair_mismatch', 'factory_vtable_mismatch',
    'factory_reference_mismatch', 'message_subtype_mismatch', 'factory_uuid_missing', 'nonempty_or_non_sso_field',
    'options_return_mismatch', 'options_reference_mismatch', 'constructed_payload_mismatch', 'native_exception'))
OWNER_ERROR_CODES = frozenset(('invalid_source', 'missing_poc', 'windows_x64_required', 'owner_unknown',
    'owner_not_unique', 'owner_changed', 'process_unavailable', 'source_changed', 'internal_failure'))


class SmokeError(ValueError):
    """Fixed public code only; native errors may contain private addresses."""
    def __init__(self, code, details=None):
        super().__init__(code)
        self.details = details or {}


def public_failure(error, stage):
    if isinstance(error, SmokeError):
        code, details = str(error), error.details
    elif isinstance(error, AcquireError) and error.code in OWNER_ERROR_CODES:
        code, details = error.code, {}
    else:
        code, details = 'smoke_failed', {}
    return {'ok': False, 'issueCode': code, 'retryAllowed': False, 'stage': stage,
            'errorType': type(error).__name__, **details}


def bounded_call(function, timeout=8):
    completed = threading.Event()
    output = []
    def run():
        try:
            output.append((True, function()))
        except BaseException:
            output.append((False, None))
        finally:
            completed.set()
    threading.Thread(target=run, daemon=True).start()
    if not completed.wait(timeout):
        raise SmokeError('native_timeout')
    if not output[0][0]:
        raise SmokeError('native_rpc_failed')
    return output[0][1]


def read_source(runtime_dir=RUNTIME):
    try:
        config = json.loads((Path(runtime_dir) / 'database-config.json').read_text(encoding='utf-8'))['config']
        self_id = config.get('selfId')
        if not isinstance(self_id, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,200}', self_id):
            raise ValueError()
        source = Path(config['sourceRoot'])
        if not source.is_absolute():
            raise ValueError()
        source = source.resolve(strict=True)
        # Bind the selected account directory, including its optional install suffix.
        # NativeSession still verifies the unique owning process and pinned module.
        account_folder = re.escape(self_id) + r'(?:_[0-9a-fA-F]{4})?'
        if (not source.is_dir() or source.name.casefold() != 'db_storage' or
                re.fullmatch(account_folder, source.parent.name) is None):
            raise ValueError()
        return {'selfId': self_id, 'sourceRoot': os.path.normcase(str(source))}
    except Exception:
        raise SmokeError('selected_account_changed') from None


class NativeSession:
    """Own one agent and payload; loading a module never starts this class."""
    def __init__(self, script_path=SCRIPT, runtime_dir=RUNTIME, code_rvas=CODE_RVAS):
        self.script_path = Path(script_path)
        self.runtime_dir = Path(runtime_dir)
        self.code_rvas = dict(code_rvas)
        self.session = None
        self.script = None
        self.detached = False
        self.invalidated = False
        self.keeper = None
        self.keeper_pinned = False
        self.main_pinned = False
        self.native_call_started = False
        self._ended = threading.Event()
        self._attach_pending = False
        self.nonce = secrets.token_urlsafe(32)
        self.source = read_source(self.runtime_dir)
        from acquire_database_keys import inventory, WindowsDependencies
        self.inventory = inventory
        self.owners = WindowsDependencies()  # Only .owner(), never .scan().
        _, _, files = inventory(self.source['sourceRoot'])
        self.owner = self.owners.owner(files)
        self.module_path = loaded_module_path(self.owner[0])
        raw = self.module_path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != HASH:
            raise SmokeError('module_changed')
        image = Image(self.module_path)
        expected_code = {name: image.data[image.offset(rva):image.offset(rva)+32].hex()
                         for name, rva in self.code_rvas.items()}
        self.binding = {**PROFILE, **self.source, 'pid': self.owner[0],
                        'processStarted': str(self.owner[1]), 'instanceId': secrets.token_hex(16)}
        program = self.script_path.read_text(encoding='utf-8')
        self.script_hash = hashlib.sha256(program.encode('utf-8')).hexdigest()
        # Independent metadata recheck immediately before submit. No payload,
        # user strings, memory addresses or arbitrary memory reads are returned.
        program += '\nconst hostChecks=' + json.dumps(expected_code) + ';\nconst hostRvas=' + json.dumps(self.code_rvas) + ';\n' + r'''
rpc.exports.inspectidentity = function () {
  const m = Process.getModuleByName('Weixin.dll');
  const matches = {};
  for (const [name, rva] of Object.entries(hostRvas)) {
    const bytes = Array.from(new Uint8Array(m.base.add(rva).readByteArray(32)), b => b.toString(16).padStart(2, '0')).join('');
    matches[name] = bytes === hostChecks[name];
  }
  return {pid:Process.id, arch:Process.arch, modulePath:m.path, codeMatches:matches};
};
'''
        sys.path.insert(0, str(ROOT / '.runtime/hook-tools'))
        try:
            import frida
        except ImportError:
            raise SmokeError('frida_missing') from None
        if self.owners.owner(files) != self.owner:
            raise SmokeError('owner_changed')
        try:
            self._attach_pending = True
            def attach_once():
                try:
                    # Keep a late result even if the bounded caller times out.
                    # Dropping it would implicitly detach and unload the agent.
                    self.session = frida.attach(self.owner[0])
                    self.session.on('detached', self._detached)
                finally:
                    self._attach_pending = False
            bounded_call(attach_once)
            # Even metadata-only load/RPC/detach crashed the independent Windows
            # fixture. Pin an inert realm before loading the working RPC script.
            # Eternalizing the working script now would disable its RPC routing.
            self._ensure_keeper()
            self.script = self.session.create_script(program)
            # Deliberately do not print Frida log/error messages or exception repr.
            bounded_call(self.script.load)
            profile = {**PROFILE, 'moduleName': 'Weixin.dll', 'modulePath': str(self.module_path), 'expectedCode': expected_code}
            result = self._rpc('configure', {'profile': profile, 'binding': self.binding, 'nonce': self.nonce})
            if result.get('state') != 'configured' or result.get('sendCalled') is not False:
                raise SmokeError('configuration_failed')
            self.revalidate()
        except BaseException:
            if not self.close():
                self.wait_for_client_exit()
            raise

    def _detached(self, *unused):
        self.detached = True
        self._ended.set()

    def _ensure_keeper(self):
        if self.keeper_pinned:
            return
        if self.keeper is not None:
            raise SmokeError('agent_residency_unconfirmed')
        try:
            self.keeper = self.session.create_script('// Inert WeBridge residency keeper. No RPC, hooks, timers or native calls.')
            bounded_call(self.keeper.load)
            bounded_call(self.keeper.eternalize)
            self.keeper_pinned = True
        except BaseException:
            self.invalidated = True
            raise SmokeError('agent_residency_unconfirmed') from None

    def _rpc(self, method, payload):
        if self.invalidated or self.detached:
            raise SmokeError('native_session_unavailable')
        try:
            result = bounded_call(lambda: getattr(self.script.exports_sync, method)(payload))
            if not isinstance(result, dict):
                raise SmokeError('native_response_invalid')
            return result
        except BaseException:
            self.invalidated = True
            raise

    def revalidate(self):
        if self.detached or self.invalidated or read_source(self.runtime_dir) != self.source:
            raise SmokeError('binding_changed')
        _, _, files = self.inventory(self.source['sourceRoot'])
        if self.owners.owner(files) != self.owner:
            raise SmokeError('owner_changed')
        if loaded_module_path(self.owner[0]) != self.module_path or hashlib.sha256(self.module_path.read_bytes()).hexdigest() != HASH:
            raise SmokeError('module_changed')
        if hashlib.sha256(self.script_path.read_text(encoding='utf-8').encode('utf-8')).hexdigest() != self.script_hash:
            raise SmokeError('native_script_changed')
        result = self._rpc('inspectidentity', {})
        if (result.get('pid') != self.owner[0] or result.get('arch') != 'x64' or
                os.path.normcase(str(result.get('modulePath', ''))) != os.path.normcase(str(self.module_path)) or
                result.get('codeMatches') != {key: True for key in self.code_rvas} or
                self.owners.owner(files) != self.owner):
            self.invalidated = True
            raise SmokeError('runtime_identity_changed')

    def construct(self):
        self.revalidate()
        self._ensure_keeper()
        self.native_call_started = True  # Set before RPC, including unknown outcomes.
        result = self._rpc('construct', {'nonce': self.nonce})
        checks = result.get('checks')
        if (result.get('state') != 'constructed' or result.get('sendCalled') is not False or
                result.get('submissionAttempted') is not False or not isinstance(checks, dict) or
                set(checks) != CHECKS or any(value is not True for value in checks.values()) or
                not isinstance(result.get('constructionId'), str)):
            self.invalidated = True
            details = {}
            for key, allowed in (('constructionStep', CONSTRUCTION_STEPS), ('failedCheck', FAILED_CHECKS)):
                if result.get(key) in allowed:
                    details[key] = result[key]
            details.update(nativeAllocationRetained=True, nativeFunctionsCalled=True, sendCalled=False)
            raise SmokeError('native_construction_failed', details)
        self.construction_id = result['constructionId']
        return {'ok': True, 'state': 'constructed', 'checks': checks, 'sendCalled': False,
                'nativeFunctionsCalled': True, 'nativeAllocationRetained': True,
                'agentResident': True, 'retentionPolicy': 'until_client_exit'}

    def submit(self):
        return self._rpc('submit', {'nonce': self.nonce, 'constructionId': self.construction_id,
            'binding': self.binding, 'targetId': TARGET, 'text': TEXT, 'confirmed': True})

    def close(self):
        if getattr(self, '_attach_pending', False):
            return False
        if self.session is None or self.detached:
            self.session = None
            return True
        if not self.keeper_pinned:
            return False
        if self.script is not None and not self.main_pinned:
            try:
                # No more RPC after this: Frida removes an eternalized script
                # from its session RPC map. Its roots/native wrappers survive.
                bounded_call(self.script.eternalize)
                self.main_pinned = True
            except BaseException:
                self.invalidated = True
                return False
        try:
            bounded_call(self.session.detach, timeout=2)
        except BaseException:
            pass  # Keeper + primary realm are resident even if host detach times out.
        self.session = None
        return True

    def wait_for_client_exit(self):
        # Do not let a failed pin fall through to interpreter teardown, which
        # implicitly detaches the session. The user may close Weixin normally.
        print(json.dumps({'ok': False, 'state': 'waiting_for_client_exit',
            'issueCode': 'agent_residency_unconfirmed', 'retryAllowed': False}), flush=True)
        while not self.detached:
            # An attach that eventually failed created no session to preserve.
            if not self._attach_pending and self.session is None:
                return
            try:
                self._ended.wait(1)
            except KeyboardInterrupt:
                continue

    def lifecycle_report(self):
        return {'reason': 'frida_17_19_windows_unload_crash_reproduced_in_fixture',
                'keeperPinned': self.keeper_pinned, 'mainScriptPinned': self.main_pinned,
                'hostDetached': self.session is None, 'nativeCallStarted': self.native_call_started,
                'retentionPolicy': 'until_client_exit', 'automaticRetry': False}


class SmokeBridge:
    def __init__(self, native, directory):
        self.native = native
        self.binding = dict(native.binding)
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / 'hook-smoke-attempt.sqlite'
        self.lock = threading.Lock()
        with closing(self.connect()) as database:
            database.execute('CREATE TABLE IF NOT EXISTS attempt (slot INTEGER PRIMARY KEY CHECK(slot=1), '
                             'request_hash TEXT NOT NULL, response TEXT NOT NULL)')
            database.commit()
        self.safety = HookAccountSafety(self.directory)

    def connect(self):
        database = sqlite3.connect(self.path, timeout=2)
        database.execute('PRAGMA synchronous=FULL')
        return database

    def status(self):
        with closing(self.connect()) as database:
            consumed = database.execute('SELECT 1 FROM attempt WHERE slot=1').fetchone() is not None
        safety = self.safety.status(self.binding)
        return {'protocol': PROTOCOL, 'ready': not consumed and not self.native.detached and not self.native.invalidated
                and not safety['paused'] and safety['retryAt'] is None,
                'instanceId': self.binding['instanceId'], 'binding': self.binding,
                'capabilities': {'sendText': True, 'idempotency': True},
                'scope': {'targetId': TARGET, 'text': TEXT, 'maximumSubmissions': 1},
                'attemptConsumed': consumed, 'delivered': False, 'safety': safety}

    def response(self, payload, status, attempted):
        return {'protocol': PROTOCOL, 'requestId': payload.get('requestId'), 'textHash': payload.get('textHash'),
                'targetId': payload.get('targetId'), 'binding': self.binding, 'status': status,
                'submissionAttempted': attempted, 'delivered': False, 'retryAllowed': False}

    def send(self, payload):
        required = {'protocol', 'requestId', 'draftId', 'textHash', 'text', 'targetId', 'expectedBinding'}
        if (not isinstance(payload, dict) or set(payload) != required or payload.get('protocol') != PROTOCOL or
                payload.get('targetId') != TARGET or payload.get('text') != TEXT or payload.get('textHash') != TEXT_HASH or
                not isinstance(payload.get('requestId'), str) or not re.fullmatch(r'[a-f0-9]{32}', payload['requestId']) or
                payload.get('draftId') != payload['requestId'] or payload.get('expectedBinding') != self.binding):
            raise SmokeError('request_outside_fixed_scope')
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        with self.lock:
            with closing(self.connect()) as database:
                database.execute('BEGIN IMMEDIATE')
                existing = database.execute('SELECT request_hash,response FROM attempt WHERE slot=1').fetchone()
                if existing:
                    return json.loads(existing[1]) if existing[0] == digest else self.response(payload, 'not_submitted', False)
                try:
                    self.native.revalidate()
                except BaseException:
                    return self.response(payload, 'not_submitted', False)
                try:
                    self.safety.admit(self.binding,payload['requestId'],TARGET,TEXT_HASH)
                except SafetyError as error:
                    denied = {**self.response(payload,'not_submitted',False),'issueCode':error.code}
                    database.execute('INSERT INTO attempt VALUES(1,?,?)',(digest,json.dumps(denied)))
                    database.commit()
                    return denied
                response = self.response(payload,'unknown',True)
                try:
                    database.execute('INSERT INTO attempt VALUES(1,?,?)', (digest, json.dumps(response)))
                    database.commit()  # Durable before native submit; never deleted/reset by this tool.
                except BaseException:
                    self.safety.fail_before_submission(self.binding,payload['requestId'])
                    raise
                completed = False
                try:
                    try:
                        result = self.native.submit()
                        if (result.get('state') == 'submitted_unconfirmed' and result.get('submissionAttempted') is True and
                                result.get('nativeReturned') is True):
                            response = self.response(payload,'submitted',True)
                    except BaseException:
                        pass  # Unknown includes timeout or post-dispatch disconnect. No replay.
                    database.execute('UPDATE attempt SET response=? WHERE slot=1', (json.dumps(response),))
                    database.commit()
                    completed = True
                    return response
                finally:
                    self.safety.finish(self.binding,payload['requestId'],response['status'] if completed else 'unknown')


def handler_type(bridge, token, port, max_request_bytes=8192):
    class Handler(BaseHTTPRequestHandler):
        server_version = 'WeBridgeHookSmoke'
        sys_version = ''

        def log_message(self, *unused):
            pass

        def setup(self):
            super().setup()
            self.connection.settimeout(3)

        def reply(self, status, value):
            body = json.dumps(value, separators=(',', ':')).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Connection', 'close')
            self.end_headers()
            self.wfile.write(body)
            self.close_connection = True

        def authorized(self):
            # API is server-to-server. A browser Origin is never necessary.
            if (self.headers.get_all('Host') != [f'127.0.0.1:{port}'] or self.headers.get_all('Origin') or
                    len(self.headers.get_all('Authorization', [])) != 1 or
                    not hmac.compare_digest(self.headers.get('Authorization', ''), 'Bearer ' + token)):
                self.reply(403, {'ok': False, 'issueCode': 'unauthorized'})
                return False
            return True

        def do_GET(self):
            if not self.authorized():
                return
            if self.path != '/v1/status':
                self.reply(404, {'ok': False})
                return
            self.reply(200, bridge.status())

        def do_POST(self):
            if not self.authorized():
                return
            if self.path != '/v1/send-text':
                self.reply(404, {'ok': False})
                return
            try:
                lengths = self.headers.get_all('Content-Length', [])
                if (len(lengths) != 1 or not re.fullmatch(r'[0-9]{1,5}', lengths[0]) or
                        not 1 <= int(lengths[0]) <= max_request_bytes or self.headers.get_all('Transfer-Encoding') or
                        self.headers.get_content_type() != 'application/json'):
                    raise SmokeError('invalid_request')
                raw = self.rfile.read(int(lengths[0]))
                if len(raw) != int(lengths[0]):
                    raise SmokeError('invalid_request')
                result = bridge.send(json.loads(raw.decode('utf-8')))
                self.reply(200, result)
            except (ValueError, UnicodeError):
                self.reply(400, {'ok': False, 'issueCode': 'invalid_request'})
            except Exception:
                self.reply(500, {'ok': False, 'issueCode': 'outcome_unknown'})
    return Handler


def write_config(directory, token, port):
    # Unique private files avoid replacing credentials of another live instance.
    suffix = secrets.token_hex(8)
    token_name, profile_name = f'hook-smoke-token-{suffix}', f'hook-smoke-profiles-{suffix}.json'
    (directory / token_name).write_text(token, encoding='utf-8')
    (directory / profile_name).write_text(json.dumps([PROFILE]), encoding='utf-8')
    config = {'endpoint': f'http://127.0.0.1:{port}', 'tokenFile': token_name, 'profilesFile': profile_name}
    temporary = directory / f'.hook-config-{suffix}.tmp'
    with temporary.open('x', encoding='utf-8') as stream:
        stream.write(json.dumps(config)); stream.flush(); os.fsync(stream.fileno())
    os.replace(temporary, directory / 'hook-config.json')


def save_construction_evidence(result):
    path = ROOT / '.runtime/hook-native-construction.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    # Caller passes an allowlisted result, never the raw native response.
    temporary = path.with_name('.hook-native-construction-' + secrets.token_hex(8) + '.tmp')
    with temporary.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, indent=2); stream.flush(); os.fsync(stream.fileno())
    os.replace(temporary, path)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--construct-only', action='store_true', help='default; native constructors, no send')
    mode.add_argument('--serve', action='store_true', help='retain constructed payload; explicit authenticated POST required')
    args = parser.parse_args(argv)
    native = None
    server = None
    stage = 'initializing'
    try:
        native = NativeSession()
        stage = 'constructing'
        result = native.construct()
        stage = 'saving_evidence'
        save_construction_evidence(result)
        if not args.serve:
            print(json.dumps(result))
            return 0
        stage = 'preparing_bridge'
        bridge = SmokeBridge(native, RUNTIME)
        if not bridge.status()['ready']:
            raise SmokeError('attempt_already_consumed')
        token, port = secrets.token_urlsafe(32), 8789
        stage = 'binding'
        server = ThreadingHTTPServer(('127.0.0.1', port), handler_type(bridge, token, port))
        server.daemon_threads = True
        stage = 'writing_config'
        write_config(RUNTIME, token, port)  # Only after all construct checks and successful bind.
        print(json.dumps({'ok': True, 'state': 'ready', 'endpoint': f'http://127.0.0.1:{port}',
                          'sendCalled': False, 'maximumSubmissions': 1}), flush=True)
        stage = 'serving'
        server.serve_forever(poll_interval=0.25)
        return 0
    except KeyboardInterrupt:
        return 0
    except BaseException as error:
        failure = public_failure(error, stage)
        if failure['issueCode'] == 'native_construction_failed':
            save_construction_evidence(failure)
        print(json.dumps(failure))
        return 1
    finally:
        if server is not None:
            server.server_close()
        if native is not None:
            if not native.close():
                native.wait_for_client_exit()
            try:
                path = ROOT / '.runtime/hook-native-lifecycle.json'
                with path.open('w', encoding='utf-8') as stream:
                    json.dump(native.lifecycle_report(), stream, indent=2)
                    stream.flush(); os.fsync(stream.fileno())
            except OSError:
                pass


if __name__ == '__main__':
    raise SystemExit(main())
