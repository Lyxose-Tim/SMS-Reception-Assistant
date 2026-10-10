"""Actual project-venv Windows app children and kernel handles; no SMS traffic."""
from contextlib import closing
import ctypes
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timezone
import secrets

from reception.auth import hash_password
from reception.engine import Engine
from reception.platform_fs import NativePlatform, _api


ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT / '.venv' / 'Scripts' / 'python.exe'
PASSWORD = 'Synthetic native lifecycle password!'
PASSWORD_HASH = hash_password(PASSWORD, salt=b'0123456789abcdef')


def record_native_evidence(name, value):
    evidence_root = Path(os.environ['HARBOR_WINDOWS_NATIVE_EVIDENCE_DIR']) if os.environ.get('HARBOR_WINDOWS_NATIVE_EVIDENCE_DIR') else ROOT / 'artifacts' / 'windows-alignment-runs'
    destination = evidence_root / (
        name + '-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '-' + secrets.token_hex(4))
    destination.mkdir(parents=True)
    (destination / (name + '.json')).write_text(json.dumps(value, indent=2), encoding='utf-8')
    return destination


def capture_owned_junction_creation(parent_test_id, junction, target):
    argv = ['cmd.exe', '/c', 'mklink', '/J', str(junction), str(target)]
    # cmd emits its native console/OEM bytes even when Python uses UTF-8.
    # Preserve them without a text decoder; reader errors must not be hidden.
    result = subprocess.run(argv, capture_output=True, timeout=5)
    record_native_evidence('native-mklink-capture', {
        'parent_test_id': parent_test_id, 'argv': argv, 'returncode': result.returncode,
        'stdout_hex': result.stdout.hex(), 'stderr_hex': result.stderr.hex(),
        'encoding_metadata': {'capture_mode': 'binary', 'producer': 'cmd.exe mklink /J',
                              'text_decoding': 'none', 'code_page': 'not inferred; native console/OEM bytes retained'},
        'fixture_scope': 'only test-owned local NTFS junction and target'})
    return result


def environment():
    env = dict(os.environ)
    for name in list(env):
        if name.startswith('TWILIO_'):
            env.pop(name)
    env.update(APP_MODE='service', ENABLE_REAL_SEND='false', ADMIN_PASSWORD_HASH=PASSWORD_HASH,
               ADMIN_ORIGIN='', PRIVACY_HMAC_KEY='', TRUSTED_PROXY_CIDRS='',
               RECIPIENT_POLICY='test_allowlist', TEST_RECIPIENT_ALLOWLIST='',
               TEST_SEND_APPROVAL_RECORDED='false', INBOUND_SERVICE_APPROVAL_RECORDED='false',
               DEMO_INTERNAL_QA='0', PYTHONIOENCODING='utf-8')
    return env


class OwnedApp:
    """A handle is obtained only from this child's own stdout + run manifest."""
    def __init__(self, database, run_dir, port=0, command=None, app_mode='service'):
        self.database, self.run_dir = database, run_dir
        self.stdout, self.stderr = [], []
        self.manifest = None
        self.handle = None
        arguments = command or [str(PYTHON), str(ROOT / 'app.py')]
        self.argv = [*arguments, '--db', str(database), '--port', str(port), '--run-dir', str(run_dir)]
        self.scenario_label = ('direct_project_venv_app_cli' if command is None
                               else 'controlled_service_worker_Thread.start_failure_only')
        self.shutdown_kind = 'not_requested'
        self.close_error = None
        child_environment = environment()
        child_environment['APP_MODE'] = app_mode
        if app_mode == 'demo':
            # Demo has no service manifest; its direct CLI still shares the DB lease.
            self.argv = [*arguments, '--db', str(database), '--port', str(port)]
        self.app_mode = app_mode
        self.process = subprocess.Popen(self.argv, cwd=ROOT, env=child_environment,
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding='utf-8', creationflags=subprocess.CREATE_NO_WINDOW)
        self.readers = []
        for stream, destination in ((self.process.stdout, self.stdout), (self.process.stderr, self.stderr)):
            def collect(src=stream, out=destination):
                for line in src:
                    out.append(line)
            thread = threading.Thread(target=collect, daemon=True)
            thread.start()
            self.readers.append(thread)

    def ready(self, timeout=10):
        deadline = time.monotonic() + timeout
        manifest_path = self.run_dir / 'run-manifest.json'
        while time.monotonic() < deadline:
            if manifest_path.exists():
                try:
                    record = json.loads(manifest_path.read_text(encoding='utf-8'))
                except (OSError, ValueError):
                    record = None
                if record and record['status'] == 'running':
                    own_output = ''.join(self.stdout)
                    match = re.search(r'loopback port (\d+) — pid (\d+)', own_output)
                    if match and int(match.group(2)) == record['pid'] and int(match.group(1)) == record['port']:
                        self.manifest = record
                        self._own_actual_process(record['pid'])
                        return record
            if self.process.poll() is not None:
                raise AssertionError('Owned app did not start: ' + ''.join(self.stderr))
            time.sleep(0.05)
        raise AssertionError('Owned app readiness timed out')

    def _own_actual_process(self, pid):
        from ctypes import wintypes as w
        api = _api()
        api.dll.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
        api.dll.OpenProcess.restype = w.HANDLE
        api.dll.GetProcessTimes.argtypes = [w.HANDLE] + [ctypes.POINTER(w.FILETIME)] * 4
        api.dll.GetProcessTimes.restype = w.BOOL
        api.dll.TerminateProcess.argtypes = [w.HANDLE, w.UINT]
        api.dll.TerminateProcess.restype = w.BOOL
        api.dll.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
        api.dll.WaitForSingleObject.restype = w.DWORD
        self.handle = api.dll.OpenProcess(0x1000 | 0x100000 | 1, False, pid)
        if not self.handle:
            raise api.error()
        times = [w.FILETIME() for _ in range(4)]
        if not api.dll.GetProcessTimes(self.handle, *(ctypes.byref(value) for value in times)):
            raise api.error()
        self.os_creation_time = (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime

    def stop(self):
        if self.process.poll() is None and self.manifest:
            self.shutdown_kind = 'run_id_pid_bound_stop_request'
            (self.run_dir / 'stop-request.json').write_text(json.dumps({
                'run_id': self.manifest['run_id'], 'pid': self.manifest['pid']}), encoding='utf-8')
        self.process.wait(timeout=10)
        for reader in self.readers:
            reader.join(timeout=1)

    def terminate_owned_actual_process(self):
        if self.handle is None:
            raise AssertionError('No verified owned app handle is available')
        api = _api()
        self.shutdown_kind = 'test_owned_actual_handle_TerminateProcess'
        if not api.dll.TerminateProcess(self.handle, 97):
            raise api.error()
        if api.dll.WaitForSingleObject(self.handle, 5000) != 0:
            raise AssertionError('Owned app termination timed out')
        self.process.wait(timeout=10)

    def close(self):
        try:
            if self.process.poll() is None:
                if self.manifest:
                    try:
                        self.stop()
                    except subprocess.TimeoutExpired:
                        self.terminate_owned_actual_process()
                else:
                    own_ready = re.search(r'loopback port (\d+) — pid (\d+)', ''.join(self.stdout))
                    if own_ready:
                        # Venv redirectors can have a different PID. Obtain a
                        # handle only for the actual child identified by our pipe.
                        self._own_actual_process(int(own_ready.group(2)))
                        self.terminate_owned_actual_process()
                    else:
                        self.shutdown_kind = 'test_owned_Popen_launcher_termination_before_readiness'
                        self.process.terminate()
                        self.process.wait(timeout=5)
            for reader in self.readers:
                reader.join(timeout=1)
        finally:
            if self.handle is not None:
                _api().dll.CloseHandle(self.handle)
                self.handle = None
            self.process.stdout.close()
            self.process.stderr.close()

    def evidence(self):
        final_manifest = None
        manifest_error = None
        manifest_path = self.run_dir / 'run-manifest.json'
        if manifest_path.exists():
            try:
                final_manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
            except (OSError, ValueError) as exc:
                manifest_error = type(exc).__name__ + ': ' + str(exc)
        record = self.manifest or final_manifest
        return {
            'scenario_label': self.scenario_label, 'argv': self.argv,
            'app_mode': self.app_mode,
            'working_directory': str(ROOT), 'launcher_pid': self.process.pid,
            'actual_app_pid': record.get('pid') if record else None,
            'actual_app_pid_source': ('readiness_stdout_and_run_manifest' if self.manifest
                                      else 'final_run_manifest' if final_manifest else 'unknown_no_manifest'),
            'actual_app_os_creation_filetime': getattr(self, 'os_creation_time', None),
            'ready_manifest': self.manifest, 'final_manifest': final_manifest,
            'manifest_read_error': manifest_error, 'returncode': self.process.returncode,
            'stdout': ''.join(self.stdout), 'stderr': ''.join(self.stderr),
            'shutdown_kind': self.shutdown_kind, 'close_error': self.close_error,
            'real_send_enabled': False,
            'note': 'Actual child PID can differ from the project-venv launcher PID; absent manifests are not guessed.'}


@unittest.skipUnless(os.name == 'nt', 'Windows-native venv/Win32 lifecycle acceptance')
class WindowsLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(PYTHON.is_file(), 'Acceptance requires the project .venv interpreter')
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.path = self.base / 'Native service acceptance.sqlite3'
        self.apps = []

    def tearDown(self):
        errors = []
        for app in reversed(self.apps):
            try:
                app.close()
            except BaseException as exc:
                app.close_error = type(exc).__name__ + ': ' + str(exc)
                errors.append(exc)
        if self.apps:
            record_native_evidence('native-lifecycle', {
                'parent_test_id': self.id(), 'collected_after_owned_children_close': True,
                'fixtures': [app.evidence() for app in self.apps],
                'kernel_and_filesystem_mocks': False,
                'thread_start_injection_only': any('Thread.start_failure' in app.scenario_label for app in self.apps)})
        self.temp.cleanup()
        if errors:
            raise errors[0]

    def start(self, path=None, port=0):
        app = OwnedApp(path or self.path, self.base / ('run-' + str(len(self.apps))), port)
        self.apps.append(app)
        return app

    def denied_second(self, path):
        second = self.start(path=path)
        second.scenario_label = 'actual_second_instance_kernel_lock_rejection'
        second.process.wait(timeout=8)
        for reader in second.readers:
            reader.join(timeout=1)
        self.assertNotEqual(second.process.returncode, 0)
        self.assertIn('lock is active', ''.join(second.stderr))

    def test_real_first_instance_login_second_instance_rejection_and_normal_release(self):
        first = self.start()
        record = first.ready()
        self.assertEqual(Path(record['python_executable']), PYTHON)
        self.assertFalse(record['real_outbound'])
        self.assertEqual(record['instance_lock'], 'held')
        connection = http.client.HTTPConnection('127.0.0.1', record['port'], timeout=3)
        try:
            connection.request('POST', '/api/login', body=json.dumps({'password': PASSWORD}),
                               headers={'Content-Type': 'application/json'})
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            cookie = response.getheader('Set-Cookie').split(';', 1)[0]
            self.assertTrue(json.loads(response.read())['authenticated'])
            connection.request('GET', '/api/state', headers={'Cookie': cookie})
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            self.assertFalse(json.loads(response.read())['runtime']['real_outbound'])
        finally:
            connection.close()
        before = hashlib.sha256(self.path.read_bytes()).hexdigest()
        self.denied_second(self.path)
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).hexdigest(), before)
        first.stop()
        self.assertEqual(first.process.returncode, 0)
        stopped = json.loads((first.run_dir / 'run-manifest.json').read_text(encoding='utf-8'))
        self.assertEqual(stopped['status'], 'stopped')
        next_app = self.start()
        next_app.ready()
        next_app.stop()

    def test_abnormal_owned_process_exit_releases_kernel_lock(self):
        first = self.start()
        first.ready()
        first.terminate_owned_actual_process()
        deadline = time.monotonic() + 5
        while True:
            try:
                with NativePlatform.lock_database(self.path):
                    break
            except RuntimeError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.05)
        second = self.start()
        second.ready()
        second.stop()

    def test_demo_child_cannot_write_active_service_database_and_normal_stop_releases_lock(self):
        first = self.start()
        first.ready()
        def fingerprints():
            with closing(sqlite3.connect(self.path.as_uri() + '?mode=ro', uri=True)) as db:
                tables = db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
                content = {name: db.execute('SELECT * FROM "' + name.replace('"', '""') + '"').fetchall() for (name,) in tables}
            return hashlib.sha256(self.path.read_bytes()).hexdigest(), hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()
        before = fingerprints()
        demo = OwnedApp(self.path, self.base / 'demo-rejected', app_mode='demo')
        demo.scenario_label = 'actual_demo_writer_rejected_by_active_service_kernel_lease'
        self.apps.append(demo)
        demo.process.wait(timeout=8)
        for reader in demo.readers:
            reader.join(timeout=1)
        self.assertNotEqual(demo.process.returncode, 0)
        self.assertIn('lock is active', ''.join(demo.stderr))
        self.assertEqual(fingerprints(), before)
        first.stop()
        self.assertEqual(first.process.returncode, 0)
        with NativePlatform.lock_database(self.path):
            pass

    def test_initialization_failure_releases_lock_and_closes_database(self):
        with closing(socket.socket()) as busy:
            busy.bind(('127.0.0.1', 0))
            busy.listen()
            failed = self.start(port=busy.getsockname()[1])
            failed.scenario_label = 'actual_socket_bind_initialization_failure'
            failed.process.wait(timeout=8)
            self.assertNotEqual(failed.process.returncode, 0)
        with NativePlatform.lock_database(self.path):
            pass
        second = self.start()
        second.ready()
        second.stop()

    def test_worker_thread_start_failure_closes_port_database_and_instance_lock(self):
        with closing(socket.socket()) as reserve:
            reserve.bind(('127.0.0.1', 0))
            port = reserve.getsockname()[1]
        # Only this negative case injects Thread.start failure. All filesystem,
        # kernel locking, Engine, server binding and cleanup remain real.
        script = ("import runpy,sys,threading; from unittest.mock import patch; "
                  "sys.argv=['app.py']+sys.argv[1:]; "
                  "original=threading.Thread.start; "
                  "fault=lambda t: (_ for _ in ()).throw(RuntimeError('synthetic worker start failed')) if t.name=='service-worker' else original(t); "
                  "guard=patch.object(threading.Thread,'start',fault); guard.start(); "
                  "runpy.run_path('app.py',run_name='__main__')")
        failed = OwnedApp(self.path, self.base / 'worker-start-failed', port,
                          command=[str(PYTHON), '-c', script])
        self.apps.append(failed)
        failed.process.wait(timeout=8)
        self.assertNotEqual(failed.process.returncode, 0)
        with NativePlatform.lock_database(self.path), NativePlatform.exclusive_database_probe(self.path):
            pass
        with closing(socket.socket()) as rebinding:
            rebinding.bind(('127.0.0.1', port))
        next_app = self.start(port=port)
        next_app.ready()
        next_app.stop()

    def test_restore_marker_refuses_start_before_engine_changes_database(self):
        with closing(sqlite3.connect(self.path)) as db:
            db.execute('CREATE TABLE sentinel(value TEXT)')
            db.commit()
        before = hashlib.sha256(self.path.read_bytes()).hexdigest()
        marker = Path(str(self.path) + '.restore-recovery.json')
        marker.write_text('{"synthetic": "pending"}', encoding='utf-8')
        failed = self.start()
        failed.scenario_label = 'actual_restore_marker_startup_rejection_before_engine'
        failed.process.wait(timeout=8)
        self.assertNotEqual(failed.process.returncode, 0)
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).hexdigest(), before)
        with NativePlatform.lock_database(self.path):
            pass

    def test_synthetic_acceptance_marker_blocks_live_configuration_without_starting_live_cli(self):
        from app import _validate_service_database
        engine = Engine(self.path, production=True, seed_demo=False)
        try:
            self.assertEqual(engine.db.execute('SELECT count(*) FROM messages').fetchone()[0], 0)
            with engine.db:
                engine.db.execute("INSERT INTO settings VALUES ('windows_acceptance_synthetic','true')")
            _validate_service_database(engine, False)
            with self.assertRaisesRegex(ValueError, 'Synthetic'):
                _validate_service_database(engine, True)
        finally:
            engine.close()

    def test_wrong_run_stop_request_is_ignored_and_owned_request_stops(self):
        app = self.start()
        record = app.ready()
        (app.run_dir / 'stop-request.json').write_text(json.dumps({'run_id': 'wrong-run', 'pid': record['pid']}), encoding='utf-8')
        time.sleep(0.3)
        self.assertIsNone(app.process.poll())
        app.stop()
        self.assertEqual(app.process.returncode, 0)

    def test_case_and_parent_relative_aliases_cannot_start_second_instance(self):
        first = self.start()
        first.ready()
        sub = self.base / 'sub'
        sub.mkdir()
        for alias in (Path(str(self.path).swapcase()), sub / '..' / self.path.name):
            self.denied_second(alias)
        first.stop()

    def test_existing_8_3_alias_uses_same_kernel_sidecar(self):
        first = self.start()
        first.ready()
        buffer = ctypes.create_unicode_buffer(32768)
        result = _api().dll.GetShortPathNameW(str(self.path), buffer, len(buffer))
        self.assertTrue(result)
        alias = Path(buffer.value)
        if str(alias).casefold() == str(self.path).casefold():
            self.skipTest('This NTFS volume does not provide an existing 8.3 alias; no system setting was changed')
        self.denied_second(alias)
        first.stop()

    def test_held_sidecar_cannot_be_deleted_renamed_or_opened_without_sharing(self):
        with closing(sqlite3.connect(self.path)):
            pass
        with NativePlatform.lock_database(self.path):
            sidecar = Path(str(self.path) + '.service.lock')
            with self.assertRaises(OSError):
                sidecar.unlink()
            with self.assertRaises(OSError):
                sidecar.rename(self.base / 'moved.lock')
            with self.assertRaises(OSError):
                handle = _api().open(sidecar, access=0xC0000000, share=0)
                _api().dll.CloseHandle(handle)

    def test_invalid_creation_paths_are_rejected_before_native_open_or_directory_creation(self):
        events = []
        active = [True]
        def observe(event, args):
            if active[0] and event == 'harbor.native_fs_open':
                events.append(args)
        sys.addaudithook(observe)
        before = sorted(str(path.relative_to(self.base)) for path in self.base.rglob('*'))
        before_metadata = (self.base.stat().st_mtime_ns, self.base.stat().st_ctime_ns)
        rejected = []
        malformed = [r'\\synthetic.invalid\share\child\snapshot.db',
                     r'\\?\UNC\synthetic.invalid\share\snapshot.db', r'\\.\NUL',
                     r'D:drive-relative\snapshot.db', str(self.base / 'snapshot.db') + ':stream',
                     str(self.base / 'folder.') + r'\snapshot.db', str(self.base / 'folder ') + r'\snapshot.db',
                     str(self.base / 'NUL.db')]
        try:
            for path in malformed:
                with self.subTest(path_kind=malformed.index(path)):
                    try:
                        NativePlatform.validate_creation_path(path)
                    except ValueError as exc:
                        rejected.append({'input': path, 'reason': str(exc)})
                    else:
                        self.fail('An invalid creation path was accepted')
        finally:
            active[0] = False
        self.assertEqual(events, [])
        self.assertEqual(sorted(str(path.relative_to(self.base)) for path in self.base.rglob('*')), before)
        self.assertEqual((self.base.stat().st_mtime_ns, self.base.stat().st_ctime_ns), before_metadata)
        record_native_evidence('native-invalid-path-audit', {
            'scenario': 'actual lexical rejection before native file access; no native API mocks',
            'rejected_inputs': rejected, 'native_open_events': events,
            'target_paths_unchanged': True, 'target_directory_mtime_ctime_unchanged': True,
            'network_share_or_drive_created': False, 'real_network_or_messaging': 'none requested or configured'})

    def test_native_path_audit_checks_drive_and_stored_local_junction_target_before_access(self):
        target = self.base / 'physical'
        target.mkdir()
        database = target / 'audit.sqlite3'
        with closing(sqlite3.connect(database)):
            pass
        junction = self.base / 'local-junction'
        created = capture_owned_junction_creation(self.id(), junction, target)
        self.assertEqual(created.returncode, 0)
        events = []
        active = [True]
        def observe(event, args):
            if active[0] and event.startswith('harbor.native_'):
                events.append({'event': event, 'args': list(args)})
        sys.addaudithook(observe)
        try:
            self.assertEqual(NativePlatform.canonical_path(junction / database.name), database.resolve())
        finally:
            active[0] = False
            junction.rmdir()
        first_open = next(index for index, record in enumerate(events) if record['event'] == 'harbor.native_fs_open')
        self.assertTrue(any(record['event'] == 'harbor.native_drive_checked' and record['args'][1] == 3 for record in events[:first_open]))
        reads = [index for index, record in enumerate(events) if record['event'] == 'harbor.native_reparse_target_read']
        self.assertTrue(reads)
        read = reads[0]
        validated = next(index for index in range(read + 1, len(events)) if events[index]['event'] == 'harbor.native_reparse_target_validated')
        checked = next(index for index in range(validated + 1, len(events)) if events[index]['event'] == 'harbor.native_drive_checked')
        opened = next(index for index in range(checked + 1, len(events)) if events[index]['event'] == 'harbor.native_fs_open')
        self.assertLess(read, validated)
        self.assertLess(validated, checked)
        self.assertLess(checked, opened)
        self.assertTrue(all(record['args'][1] is True for record in events if record['event'] == 'harbor.native_fs_open'))
        record_native_evidence('native-path-audit', {
            'scenario': 'real local NTFS junction; native kernel metadata, no kernel/FS mocks',
            'events': events, 'drive_checked_before_first_open': True,
            'stored_target_validated_before_target_drive_and_open': True,
            'all_component_opens_nofollow': True,
            'mapped_drive_case': 'blocked: no approved existing mapped drive; none was created',
            'unc_reparse_target_case': 'blocked: no approved existing UNC-tag fixture or symlink privilege; no share was created',
            'unknown_reparse_tag_case': 'blocked: no approved existing unknown-tag fixture; no filter or system fixture was installed',
            'real_network_or_messaging': 'none requested or configured'})


if __name__ == '__main__':
    unittest.main()
