"""Synthetic harness tests. Never load Frida or operate a Weixin process."""
import http.client
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import sys

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location('hook_smoke_test_module', SCRIPTS / 'run_windows_hook_smoke.py')
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)


class SourceBindingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def configure(self, self_id, folder):
        source = self.directory / folder / 'db_storage'
        source.mkdir(parents=True, exist_ok=True)
        (self.directory / 'database-config.json').write_text(json.dumps({
            'config': {'selfId': self_id, 'sourceRoot': str(source)}}), encoding='utf-8')
        return source

    def test_selected_account_is_not_pinned_to_the_smoke_fixture(self):
        for self_id, folder in (('wxid_fixture_one', 'wxid_fixture_one_559e'),
                                ('wxid_fixture_two', 'wxid_fixture_two_a1b2'),
                                ('wxid_fixture_three', 'wxid_fixture_three')):
            with self.subTest(account=self_id):
                source = self.configure(self_id, folder)
                self.assertEqual(smoke.read_source(self.directory), {
                    'selfId': self_id, 'sourceRoot': os.path.normcase(str(source.resolve()))})

    def test_different_account_directory_is_rejected(self):
        self.configure('wxid_fixture_one', 'wxid_fixture_two_559e')
        with self.assertRaisesRegex(smoke.SmokeError, '^selected_account_changed$'):
            smoke.read_source(self.directory)

    def test_missing_identity_or_non_database_source_is_rejected(self):
        for self_id, folder in (('', 'wxid_fixture_one_559e'),
                                ('../wxid_fixture_one', 'wxid_fixture_one_559e')):
            with self.subTest(account=self_id):
                self.configure(self_id, folder)
                with self.assertRaisesRegex(smoke.SmokeError, '^selected_account_changed$'):
                    smoke.read_source(self.directory)
        source = self.configure('wxid_fixture_one', 'wxid_fixture_one_559e')
        (self.directory / 'database-config.json').write_text(json.dumps({
            'config': {'selfId': 'wxid_fixture_one', 'sourceRoot': str(source.parent)}}), encoding='utf-8')
        with self.assertRaisesRegex(smoke.SmokeError, '^selected_account_changed$'):
            smoke.read_source(self.directory)


class FakeNative:
    def __init__(self):
        self.binding = {**smoke.PROFILE, 'pid': 123, 'processStarted': '134000000000000000',
            'selfId': 'wxid_synthetic', 'sourceRoot': 'd:\\synthetic\\db_storage', 'instanceId': 'synthetic-instance'}
        self.detached = self.invalidated = False
        self.submissions = 0
        self.validation_error = False
        self.submit_error = False
        self.on_submit = None

    def revalidate(self):
        if self.validation_error:
            raise smoke.SmokeError('owner_changed')

    def submit(self):
        self.submissions += 1
        if self.on_submit:
            self.on_submit()
        if self.submit_error:
            raise smoke.SmokeError('native_timeout')
        return {'state': 'submitted_unconfirmed', 'submissionAttempted': True, 'nativeReturned': True}


class SmokeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)
        self.native = FakeNative()
        self.bridge = smoke.SmokeBridge(self.native, self.directory)

    def tearDown(self):
        self.temporary.cleanup()

    def request(self):
        return {'protocol': smoke.PROTOCOL, 'requestId': 'a'*32, 'draftId': 'a'*32,
            'textHash': smoke.TEXT_HASH, 'text': smoke.TEXT, 'targetId': smoke.TARGET,
            'expectedBinding': self.native.binding}

    def test_smoke_respects_shared_account_pause_before_native_submission(self):
        from hook_account_safety import HookAccountSafety
        safety=HookAccountSafety(self.directory)
        safety.pause(self.native.binding,safety.status(self.native.binding)['version'])
        result=self.bridge.send(self.request())
        self.assertEqual((result['status'],result['issueCode']),('not_submitted','account_paused'))
        self.assertEqual(self.native.submissions,0)

    def test_smoke_unknown_pauses_general_text_bridge_on_same_account(self):
        from hook_account_safety import HookAccountSafety, SafetyError
        self.native.submit_error=True
        result=self.bridge.send(self.request())
        self.assertEqual(result['status'],'unknown')
        safety=HookAccountSafety(self.directory)
        self.assertTrue(safety.status(self.native.binding)['paused'])
        with self.assertRaisesRegex(SafetyError,'account_paused'):
            safety.admit(self.native.binding,'b'*32,'another-target','another-hash')

    def test_smoke_budget_is_shared_with_an_independent_sender(self):
        from hook_account_safety import HookAccountSafety
        safety=HookAccountSafety(self.directory)
        safety.admit(self.native.binding,'b'*32,'another-target','another-hash')
        safety.finish(self.native.binding,'b'*32,'submitted')
        result=self.bridge.send(self.request())
        self.assertEqual((result['status'],result['issueCode']),('not_submitted','account_rate_limited'))
        self.assertEqual(self.native.submissions,0)

    def test_legacy_smoke_unknown_initializes_account_pause(self):
        self.native.submit_error=True
        self.bridge.send(self.request())
        self.bridge.safety.path.unlink()
        restarted=smoke.SmokeBridge(FakeNative(),self.directory)
        self.assertTrue(restarted.safety.status(self.native.binding)['paused'])

    def test_old_smoke_unknown_blocks_general_bridge_before_diagnostic_restart(self):
        from hook_account_safety import HookAccountSafety
        self.native.submit_error=True
        self.bridge.send(self.request())
        self.bridge.safety.path.unlink()
        safety=HookAccountSafety(self.directory)
        self.assertTrue(safety.status(self.native.binding)['paused'])

    def test_success_is_submitted_without_delivery_claim(self):
        result = self.bridge.send(self.request())
        self.assertEqual(result['status'], 'submitted')
        self.assertFalse(result['delivered'])
        self.assertEqual(self.native.submissions, 1)
        self.assertFalse(self.bridge.status()['ready'])

    def test_identical_request_replays_result_without_native_call(self):
        expected = self.bridge.send(self.request())
        self.assertEqual(self.bridge.send(self.request()), expected)
        self.assertEqual(self.native.submissions, 1)

    def test_new_request_after_once_is_not_submitted(self):
        self.bridge.send(self.request())
        another = {**self.request(), 'requestId': 'b'*32, 'draftId': 'b'*32}
        self.assertEqual(self.bridge.send(another)['status'], 'not_submitted')
        self.assertEqual(self.native.submissions, 1)

    def test_unknown_outcome_survives_restart_and_cannot_retry(self):
        self.native.submit_error = True
        self.assertEqual(self.bridge.send(self.request())['status'], 'unknown')
        native = FakeNative()
        restarted = smoke.SmokeBridge(native, self.directory)
        self.assertEqual(restarted.send(self.request())['status'], 'unknown')
        self.assertEqual(native.submissions, 0)
        self.assertFalse(restarted.status()['ready'])

    def test_disk_latch_exists_before_native_dispatch(self):
        def check():
            other = FakeNative()
            restarted = smoke.SmokeBridge(other, self.directory)
            self.assertEqual(restarted.send(self.request())['status'], 'unknown')
            self.assertEqual(other.submissions, 0)
        self.native.on_submit = check
        self.assertEqual(self.bridge.send(self.request())['status'], 'submitted')

    def test_changed_owner_never_reaches_native(self):
        self.native.validation_error = True
        result = self.bridge.send(self.request())
        self.assertEqual(result['status'], 'not_submitted')
        self.assertFalse(result['submissionAttempted'])
        self.assertEqual(self.native.submissions, 0)

    def test_scope_rejects_other_target_text_hash_and_binding(self):
        for change in ({'targetId': 'another'}, {'text': 'another'}, {'textHash': 'f'*64},
                       {'expectedBinding': {**self.native.binding, 'pid': 124}}, {'draftId': 'b'*32}):
            with self.subTest(change=change):
                with self.assertRaises(smoke.SmokeError):
                    self.bridge.send({**self.request(), **change})
        self.assertEqual(self.native.submissions, 0)

    def test_construct_requires_every_check(self):
        native = smoke.NativeSession.__new__(smoke.NativeSession)
        native.revalidate = lambda: None
        native._ensure_keeper = lambda: None
        native.nonce = 'synthetic'
        native.invalidated = False
        native._rpc = lambda *args: {'state': 'constructed', 'sendCalled': False,
            'submissionAttempted': False, 'constructionId': 'id', 'checks': {'receiver': True}}
        with self.assertRaises(smoke.SmokeError):
            native.construct()
        self.assertTrue(native.invalidated)

    def test_construct_error_only_exposes_allowlisted_fields(self):
        native = smoke.NativeSession.__new__(smoke.NativeSession)
        native.revalidate = lambda: None
        native._ensure_keeper = lambda: None
        native.nonce = 'synthetic'
        native.invalidated = False
        native._rpc = lambda *args: {'state': 'construction_failed', 'constructionStep': 'text_factory',
            'failedCheck': 'private memory at 0x12345', 'constructionId': 'secret'}
        with self.assertRaises(smoke.SmokeError) as caught:
            native.construct()
        self.assertEqual(caught.exception.details['constructionStep'], 'text_factory')
        self.assertNotIn('failedCheck', caught.exception.details)
        self.assertNotIn('constructionId', caught.exception.details)

    def test_rpc_wait_is_bounded_without_waiting_for_worker_exit(self):
        unblock = threading.Event()
        try:
            with self.assertRaisesRegex(smoke.SmokeError, 'native_timeout'):
                smoke.bounded_call(lambda: unblock.wait(2), timeout=0.01)
        finally:
            unblock.set()

    def test_owner_errors_only_expose_fixed_code(self):
        error = smoke.AcquireError('owner_not_unique')
        error.args = ('private process or path details',)
        result = smoke.public_failure(error, 'initializing')
        self.assertEqual(result['issueCode'], 'owner_not_unique')
        self.assertNotIn('private', json.dumps(result))
        generic = smoke.public_failure(OSError('secret address or token'), 'initializing')
        self.assertEqual(generic['issueCode'], 'smoke_failed')
        self.assertNotIn('secret', json.dumps(generic))

    def test_native_close_pins_main_before_detach(self):
        events = []
        native = smoke.NativeSession.__new__(smoke.NativeSession)
        native.native_call_started = True
        native.keeper_pinned = True
        native.main_pinned = False
        native.detached = False
        native.session = type('Session', (), {'detach': lambda _: events.append('detach')})()
        native.script = type('Script', (), {'eternalize': lambda _: events.append('pin_main')})()
        native.close()
        self.assertEqual(events, ['pin_main', 'detach'])
        self.assertTrue(native.main_pinned)

    def test_native_close_does_not_detach_when_pin_fails(self):
        events = []
        native = smoke.NativeSession.__new__(smoke.NativeSession)
        native.native_call_started = True
        native.keeper_pinned = True
        native.main_pinned = False
        native.detached = False
        native.session = type('Session', (), {'detach': lambda _: events.append('detach')})()
        def fail(_):
            raise RuntimeError('synthetic failure')
        native.script = type('Script', (), {'eternalize': fail})()
        native.close()
        self.assertEqual(events, [])
        self.assertIsNotNone(native.session)

    def test_keeper_is_inert_and_pinned_without_touching_working_rpc(self):
        events = []
        keeper = type('Keeper', (), {'load': lambda _: events.append('load_keeper'),
                                     'eternalize': lambda _: events.append('pin_keeper')})()
        def create(_, source):
            self.assertTrue(source.startswith('//'))
            events.append('create_keeper')
            return keeper
        native = smoke.NativeSession.__new__(smoke.NativeSession)
        native.keeper_pinned = False
        native.keeper = None
        native.session = type('Session', (), {'create_script': create})()
        native._ensure_keeper()
        native._ensure_keeper()
        self.assertEqual(events, ['create_keeper', 'load_keeper', 'pin_keeper'])
        self.assertTrue(native.keeper_pinned)

    def test_failed_keeper_blocks_construct_before_native_rpc(self):
        native = smoke.NativeSession.__new__(smoke.NativeSession)
        native.native_call_started = False
        native.revalidate = lambda: None
        def fail():
            raise smoke.SmokeError('agent_residency_unconfirmed')
        native._ensure_keeper = fail
        with patch.object(smoke.NativeSession, '_rpc') as rpc:
            with self.assertRaises(smoke.SmokeError):
                native.construct()
            rpc.assert_not_called()
        self.assertFalse(native.native_call_started)

    def test_metadata_only_session_cannot_unload_without_keeper(self):
        events = []
        native = smoke.NativeSession.__new__(smoke.NativeSession)
        native.native_call_started = False
        native.keeper_pinned = False
        native.main_pinned = False
        native.detached = False
        native.session = type('Session', (), {'detach': lambda _: events.append('detach')})()
        self.assertFalse(native.close())
        self.assertEqual(events, [])

    def test_timed_out_attach_cannot_fall_through_to_host_exit(self):
        native = smoke.NativeSession.__new__(smoke.NativeSession)
        native._attach_pending = True
        native.session = None
        self.assertFalse(native.close())
        # A later failed attach leaves no session and can safely finish.
        native._attach_pending = False
        native.detached = False
        self.assertTrue(native.close())

    def test_config_shape_loads_through_production_sender(self):
        from web_mvp.windows_hook_sender import WindowsHookSender
        smoke.write_config(self.directory, 'A'*43, 8789)
        sender = WindowsHookSender.from_config(self.directory, self.directory / 'hook-config.json')
        self.assertIsNone(sender.configuration_error)
        self.assertEqual(sender.profiles, (smoke.PROFILE,))

    def test_http_auth_origin_host_and_scope_guards(self):
        token = 'A'*43
        server = smoke.ThreadingHTTPServer(('127.0.0.1', 0), smoke.BaseHTTPRequestHandler)
        server.daemon_threads = True
        port = server.server_port
        server.RequestHandlerClass = smoke.handler_type(self.bridge, token, port)
        worker = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.01}, daemon=True)
        worker.start()
        def request(method, path, headers, payload=None):
            connection = http.client.HTTPConnection('127.0.0.1', port, timeout=2)
            try:
                body = None if payload is None else json.dumps(payload)
                connection.request(method, path, body=body, headers=headers)
                response = connection.getresponse()
                return response.status, json.loads(response.read())
            finally:
                connection.close()
        good = {'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'}
        try:
            for headers in ({}, {**good, 'Origin': 'http://127.0.0.1:8787'}, {**good, 'Host': 'evil.invalid'}):
                self.assertEqual(request('POST', '/v1/send-text', headers, self.request())[0], 403)
            self.assertEqual(request('GET', '/v1/status', good)[0], 200)
            self.assertEqual(request('POST', '/v1/send-text', good, {**self.request(), 'text': 'other'})[0], 400)
            self.assertEqual(self.native.submissions, 0)
            self.assertEqual(request('POST', '/v1/send-text', good, self.request())[1]['status'], 'submitted')
            self.assertEqual(self.native.submissions, 1)
        finally:
            server.shutdown(); server.server_close(); worker.join(timeout=2)


if __name__ == '__main__':
    unittest.main()
