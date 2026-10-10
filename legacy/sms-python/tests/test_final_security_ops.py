"""Independent final acceptance: owned synthetic files and loopback only.

Native error 32 is observed with a real deny-sharing handle. Missing-name
recovery is a constructed filesystem state and does not reproduce error 1177.
No ACL, account, system setting, or installed dependency is changed here.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, redirect_stderr, redirect_stdout
import ctypes
from ctypes import wintypes as w
import hashlib
import http.client
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from app import ServiceServer
from reception.auth import AuthSessions, hash_password, valid_password_hash, verify_password
from reception.config import ServiceConfig
from reception.engine import Engine
from reception.operations import (RestoreRecoveryRequired, _file_hash,
    _freeze_restored_tasks, _recovery_marker, _write_recovery_marker,
    acknowledge_restore, backup_database, recover_restore, restore_database,
    restore_recovery_pending, service_lock, verify_database)
from reception.platform_fs import NativePlatform, _api

ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT / '.venv' / 'Scripts' / 'python.exe'
PASSWORD = 'Independent synthetic password 987!'
PASSWORD_HASH = hash_password(PASSWORD, salt=b'independent-salt')


def security_bytes(path):
    """Independently read OWNER/GROUP/DACL, including raw SD control flags."""
    adv = ctypes.WinDLL('advapi32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    adv.GetNamedSecurityInfoW.argtypes = [w.LPWSTR, ctypes.c_int, w.DWORD] + [ctypes.POINTER(ctypes.c_void_p)] * 5
    adv.GetNamedSecurityInfoW.restype = w.DWORD
    adv.GetSecurityDescriptorControl.argtypes = [ctypes.c_void_p, ctypes.POINTER(w.WORD), ctypes.POINTER(w.DWORD)]
    adv.GetSecurityDescriptorLength.argtypes = [ctypes.c_void_p]
    adv.GetSecurityDescriptorLength.restype = w.DWORD
    adv.GetLengthSid.argtypes = [ctypes.c_void_p]
    adv.GetLengthSid.restype = w.DWORD
    adv.MakeSelfRelativeSD.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(w.DWORD)]
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    pointers = [ctypes.c_void_p() for _ in range(5)]
    code = adv.GetNamedSecurityInfoW(str(path), 1, 7, *(ctypes.byref(p) for p in pointers))
    if code:
        raise ctypes.WinError(code)
    owner, group, dacl, _, sd = pointers
    try:
        if not all(p.value for p in (owner, group, dacl, sd)):
            raise RuntimeError('Complete OWNER/GROUP/DACL is required')
        control, revision = w.WORD(), w.DWORD()
        if not adv.GetSecurityDescriptorControl(sd, ctypes.byref(control), ctypes.byref(revision)):
            raise ctypes.WinError(ctypes.get_last_error())
        if control.value & 0x8000:
            raw = ctypes.string_at(sd, adv.GetSecurityDescriptorLength(sd))
        else:
            length = w.DWORD()
            adv.MakeSelfRelativeSD(sd, None, ctypes.byref(length))
            buffer = ctypes.create_string_buffer(length.value)
            if not adv.MakeSelfRelativeSD(sd, buffer, ctypes.byref(length)):
                raise ctypes.WinError(ctypes.get_last_error())
            raw = buffer.raw[:length.value]
        dacl_length = ctypes.c_ushort.from_address(dacl.value + 2).value
        return {'descriptor': raw, 'dacl': ctypes.string_at(dacl, dacl_length),
                'owner': ctypes.string_at(owner, adv.GetLengthSid(owner)),
                'group': ctypes.string_at(group, adv.GetLengthSid(group)),
                'control': control.value, 'revision': revision.value}
    finally:
        kernel.LocalFree(sd)


_LOCK_CHILD = """
import json,os,sys
from reception.operations import service_lock
try:
    with service_lock(sys.argv[1]) as path:
        print(json.dumps({'owned_pid':os.getpid(),'canonical':str(path),'acquired':True}),flush=True)
        if sys.argv[2]=='hold': sys.stdin.readline()
except (ValueError,RuntimeError,OSError) as exc:
    print(json.dumps({'owned_pid':os.getpid(),'acquired':False,'kind':type(exc).__name__}),flush=True)
    sys.exit(2)
"""


class FinalAcceptanceBase(unittest.TestCase):
    def setUp(self):
        self.evidence = os.environ.get('HARBOR_FINAL_SECURITY_EVIDENCE_DIR')
        if self.evidence:
            Path(self.evidence).mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix='independent-', dir=self.evidence or None)
        self.base = Path(self.temp.name)
        self.path = self.base / 'synthetic.db'
        self.notes = {'id': self.id(), 'scope': 'owned fake files / loopback; no real SMS', 'cases': []}

    def tearDown(self):
        self.temp.cleanup()
        if self.evidence:
            destination = Path(self.evidence) / (self._testMethodName + '.json')
            # A run directory is exclusive; do not silently overwrite old evidence.
            with destination.open('x', encoding='utf-8') as handle:
                json.dump(self.notes, handle, indent=2)

    def seed(self):
        engine = Engine(self.path, production=True, seed_demo=False)
        with engine.db:
            engine.db.execute("INSERT INTO settings VALUES ('windows_acceptance_synthetic','true')")
        engine.close()

    def digest(self, path):
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()

    def attempt(self, path):
        result = subprocess.run([str(PYTHON), '-B', '-c', _LOCK_CHILD, str(path), 'once'],
            cwd=ROOT, capture_output=True, text=True, encoding='utf-8', timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW)
        value = json.loads(result.stdout)
        self.notes['cases'].append({'path': str(path), 'exit': result.returncode, **value})
        return value

    def recovery_fixture(self):
        self.seed()
        logical = self.base / (self.path.name + '.rollback-independent')
        native = self.base / (self.path.name + '.original-independent')
        prepared = self.base / '.sqlite-restore-independent.db'
        backup_database(self.path, logical)
        backup_database(self.path, prepared)
        with closing(sqlite3.connect(prepared)) as db:
            _freeze_restored_tasks(db)
            db.commit()
        NativePlatform.flush_file(prepared)
        value = {'version': 1, 'database': self.path.name, 'original_hash': _file_hash(self.path),
                 'prepared_hash': _file_hash(prepared), 'original_identity': list(NativePlatform.file_identity(self.path)),
                 'prepared_identity': list(NativePlatform.file_identity(prepared)),
                 'logical_rollback': logical.name, 'native_original': native.name,
                 'prepared': prepared.name, 'sidecars': []}
        _write_recovery_marker(self.path, value)
        return value, logical, native, prepared

    def compare_security(self, before, after, label):
        record = {'kind': label, 'control_before': before['control'], 'control_after': after['control']}
        for component in ('descriptor', 'dacl', 'owner', 'group'):
            record[component + '_equal'] = before[component] == after[component]
            record[component + '_before_sha256'] = hashlib.sha256(before[component]).hexdigest()
            record[component + '_after_sha256'] = hashlib.sha256(after[component]).hexdigest()
            if self.evidence:
                for name, value in (('before', before), ('after', after)):
                    destination = Path(self.evidence) / (self._testMethodName + '-' + name + '-' + component + '.bin')
                    with destination.open('xb') as handle:
                        handle.write(value[component])
        self.notes['cases'].append(record)


@unittest.skipUnless(os.name == 'nt', 'Fresh Windows native acceptance requires Windows')
class FinalNativeSecurityOpsTests(FinalAcceptanceBase):
    def test_cross_process_aliases_and_held_sidecar_share_one_owner(self):
        self.seed()
        junction = self.base / 'owned-junction'
        linked = subprocess.run(['cmd.exe', '/c', 'mklink', '/J', str(junction), str(self.base)],
            capture_output=True, timeout=5)
        self.assertEqual(linked.returncode, 0, linked.stderr.hex())
        before = self.digest(self.path)
        try:
            with service_lock(self.path):
                aliases = [self.path, str(self.path).upper(), self.base / '.' / self.path.name,
                           '\\\\?\\' + str(self.path), junction / self.path.name]
                for alias in aliases:
                    self.assertFalse(self.attempt(alias)['acquired'])
                sidecar = Path(str(self.path) + '.service.lock')
                for change in (lambda: sidecar.unlink(), lambda: sidecar.rename(self.base / 'moved.lock')):
                    with self.assertRaises(OSError) as failure:
                        change()
                    self.assertEqual(failure.exception.winerror, 32)
                with self.assertRaises(OSError) as failure:
                    handle = _api().open(sidecar, access=0xC0000000, share=0)
                    _api().dll.CloseHandle(handle)
                self.assertEqual(failure.exception.winerror, 32)
            self.assertTrue(self.attempt(self.path)['acquired'])
            self.assertEqual(self.digest(self.path), before)
        finally:
            junction.rmdir()

    def test_abrupt_owned_process_exit_releases_kernel_lock_with_stale_sidecar(self):
        self.seed()
        sidecar = Path(str(self.path) + '.service.lock')
        sidecar.write_bytes(b'independent synthetic stale sidecar')
        child = subprocess.Popen([str(PYTHON), '-B', '-c', _LOCK_CHILD, str(self.path), 'hold'],
            cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding='utf-8', creationflags=subprocess.CREATE_NO_WINDOW)
        api = _api()
        api.dll.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
        api.dll.OpenProcess.restype = w.HANDLE
        api.dll.TerminateProcess.argtypes = [w.HANDLE, w.UINT]
        api.dll.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
        handle = None
        try:
            owned = json.loads(child.stdout.readline())
            self.assertTrue(owned['acquired'])
            handle = api.dll.OpenProcess(0x1000 | 0x100000 | 1, False, owned['owned_pid'])
            self.assertTrue(handle)
            self.assertFalse(self.attempt(self.path)['acquired'])
            self.assertTrue(api.dll.TerminateProcess(handle, 97))
            self.assertEqual(api.dll.WaitForSingleObject(handle, 5000), 0)
            child.wait(timeout=5)
            self.assertTrue(self.attempt(self.path)['acquired'])
            self.assertEqual(sidecar.read_bytes(), b'independent synthetic stale sidecar')
            self.notes['cases'].append({'actual_pid': owned['owned_pid'], 'termination': 'owned native handle only',
                'launcher_exit': child.returncode, 'normal_drain_proven': False})
        finally:
            if child.poll() is None:
                child.stdin.write('\n')
                child.stdin.flush()
                child.wait(timeout=5)
            for stream in (child.stdin, child.stdout, child.stderr):
                stream.close()
            if handle:
                api.dll.CloseHandle(handle)

    def test_preexisting_short_parent_alias_blocks_a_second_child(self):
        supplied = os.environ.get('HARBOR_FINAL_SHORT_ALIAS_ROOT')
        if not supplied:
            self.skipTest('No authorized fixture root with an existing short-name alias was supplied')
        root = Path(supplied).resolve(strict=True)
        api = _api()
        buffer = ctypes.create_unicode_buffer(32768)
        self.assertTrue(api.dll.GetShortPathNameW(str(root), buffer, len(buffer)))
        short = Path(buffer.value)
        if str(short).casefold() == str(root).casefold():
            self.skipTest('The authorized fixture root has no existing short-name alias; settings unchanged')
        fixture = tempfile.TemporaryDirectory(prefix='final-owned-', dir=root)
        physical = Path(fixture.name) / 'short-alias-synthetic.db'
        alias = short / Path(fixture.name).name / physical.name
        try:
            with closing(sqlite3.connect(physical)) as db:
                db.execute('CREATE TABLE synthetic_marker(value TEXT)')
                db.execute("INSERT INTO synthetic_marker VALUES ('owned-only')")
                db.commit()
            self.assertEqual(NativePlatform.canonical_path(alias), physical)
            self.assertEqual(NativePlatform.file_identity(alias), NativePlatform.file_identity(physical))
            with service_lock(physical):
                self.assertFalse(self.attempt(alias)['acquired'])
            self.assertTrue(self.attempt(alias)['acquired'])
            self.notes['cases'].append({'existing_parent': str(root), 'existing_short_parent': str(short),
                'same_identity': True, 'settings_changed': False})
        finally:
            self.assertTrue(Path(fixture.name).resolve().is_relative_to(root))
            fixture.cleanup()

    def test_database_and_lock_hardlinks_are_rejected_before_database_changes(self):
        self.seed()
        before = self.digest(self.path)
        linked = self.base / 'main-hardlink.db'
        os.link(self.path, linked)
        try:
            with self.assertRaises(ValueError):
                with service_lock(linked):
                    self.fail('Hard-linked database acquired ownership')
        finally:
            linked.unlink()
        lock = Path(str(self.path) + '.service.lock')
        lock.write_bytes(b'owned sidecar')
        second = self.base / 'sidecar-hardlink'
        os.link(lock, second)
        try:
            with self.assertRaises(ValueError):
                with service_lock(self.path):
                    self.fail('Hard-linked lock acquired ownership')
        finally:
            second.unlink()
        self.assertEqual(self.digest(self.path), before)

    def test_raw_invalid_paths_are_rejected_before_native_io_or_new_directories(self):
        self.seed()
        events = []
        def observer(event, arguments):
            if event == 'harbor.native_fs_open':
                events.append(arguments[0])
        sys.addaudithook(observer)
        before = {str(p.relative_to(self.base)): (p.stat().st_size, p.stat().st_mtime_ns, self.digest(p))
                  for p in self.base.iterdir() if p.is_file()}
        raw = str(self.base / 'must-never-exist')
        names = [raw + '\\LPT¹.db', raw + '\\COM²\\x.db', raw + '\\x.db:secret',
                 raw + '\\trailing.\\x.db', raw + '\\trailing \\x.db', raw + '\\x\x00.db',
                 '\\\\?\\GLOBALROOT\\Device\\HarddiskVolume1\\x.db', 'D:relative.db',
                 '\\\\?\\UNC\\never-contacted.invalid\\share\\x.db', '\\\\.\\NUL']
        for name in names:
            count = len(events)
            with self.assertRaises(ValueError):
                backup_database(self.path, name)
            self.assertEqual(len(events), count)
        self.assertFalse((self.base / 'must-never-exist').exists())
        after = {str(p.relative_to(self.base)): (p.stat().st_size, p.stat().st_mtime_ns, self.digest(p))
                 for p in self.base.iterdir() if p.is_file()}
        self.assertEqual(before, after)
        self.notes['cases'].append({'invalid_inputs': len(names), 'native_opens': len(events), 'tree_unchanged': True})

    def test_backup_has_committed_wal_and_closes_native_handles_before_move(self):
        self.seed()
        output = self.base / 'wal-backup.db'
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute('PRAGMA journal_mode=WAL').fetchone()[0], 'wal')
            db.execute("INSERT INTO settings VALUES ('new-committed-wal-marker','included')")
            db.commit()
            backup_database(self.path, output)
        with closing(sqlite3.connect(output)) as copy:
            self.assertEqual(copy.execute("SELECT value FROM settings WHERE key='new-committed-wal-marker'").fetchone(), ('included',))
        moved = self.base / 'moved-backup.db'
        NativePlatform.move_no_replace(output, moved)
        output.write_bytes(b'owned raced destination')
        with self.assertRaises(OSError):
            NativePlatform.move_no_replace(moved, output)
        self.assertEqual(output.read_bytes(), b'owned raced destination')
        self.assertTrue(verify_database(moved))

    def test_actual_reader_occupancy_blocks_restore_and_preserves_wal(self):
        self.seed()
        backup = self.base / 'old-backup.db'
        with closing(sqlite3.connect(self.path)) as writer:
            writer.execute('PRAGMA journal_mode=WAL')
            backup_database(self.path, backup)
            with closing(sqlite3.connect(self.path)) as reader:
                reader.execute('BEGIN')
                reader.execute('SELECT * FROM settings').fetchall()
                writer.execute("INSERT INTO settings VALUES ('newer-synthetic','kept')")
                writer.commit()
                main_bytes = self.path.read_bytes()
                wal = Path(str(self.path) + '-wal')
                wal_bytes = wal.read_bytes()
                with self.assertRaises(RuntimeError):
                    restore_database(self.path, backup, True)
                self.assertEqual(self.path.read_bytes(), main_bytes)
                self.assertEqual(wal.read_bytes(), wal_bytes)
                self.assertFalse(restore_recovery_pending(self.path))

    def test_actual_restore_keeps_dacl_owner_group_and_pauses_after_acknowledgment(self):
        self.seed()
        backup = self.base / 'before.db'
        backup_database(self.path, backup)
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("INSERT INTO settings VALUES ('current-rollback-marker','only-rollback')")
            db.commit()
        before = security_bytes(self.path)
        result = restore_database(self.path, backup, True)
        after = security_bytes(self.path)
        self.compare_security(before, after, 'real successful ReplaceFileW')
        self.assertEqual(result['status'], 'restored_paused')
        for key in ('dacl', 'owner', 'group'):
            self.assertEqual(before[key], after[key])
        self.assertIn(before['control'] ^ after['control'], (0, 0x400))
        self.assertEqual(before['descriptor'][:2], after['descriptor'][:2])
        self.assertEqual(before['descriptor'][4:], after['descriptor'][4:])
        with closing(sqlite3.connect(result['rollback_backup'])) as db:
            self.assertEqual(db.execute("SELECT value FROM settings WHERE key='current-rollback-marker'").fetchone(), ('only-rollback',))
        self.assertTrue(acknowledge_restore(self.path, True, True)['outbox_remains_paused'])
        with closing(sqlite3.connect(self.path)) as db:
            gates = dict(db.execute("SELECT key,value FROM settings WHERE key IN ('outbox_paused','restore_review_required')"))
        self.assertEqual(gates, {'outbox_paused': 'true', 'restore_review_required': 'false'})
        self.assertFalse(restore_recovery_pending(self.path))
        NativePlatform.move_no_replace(self.path, self.base / 'handles-closed.db')

    def test_real_native_sharing_error_preserves_original_and_recovery_evidence(self):
        payload, logical, native, prepared = self.recovery_fixture()
        before = security_bytes(self.path)
        handle = _api().open(prepared, access=0x80000000, share=1)
        try:
            with self.assertRaises(OSError) as failure:
                NativePlatform.replace_file(self.path, prepared, native)
            self.assertEqual(failure.exception.winerror, 32)
            self.assertEqual(_file_hash(self.path), payload['original_hash'])
            self.assertTrue(logical.exists())
            self.assertTrue(prepared.exists())
            self.assertTrue(restore_recovery_pending(self.path))
        finally:
            _api().dll.CloseHandle(handle)
        result = recover_restore(self.path, True)
        self.assertEqual(result['status'], 'original_database_preserved')
        after = security_bytes(self.path)
        self.compare_security(before, after, 'actual native sharing violation 32; no error mock')
        self.assertEqual(before, after)
        self.assertFalse(restore_recovery_pending(self.path))
        self.assertTrue(logical.exists())

    def test_constructed_missing_name_recovery_retains_original_security_and_rollback(self):
        payload, logical, native, prepared = self.recovery_fixture()
        before = security_bytes(self.path)
        NativePlatform.move_no_replace(self.path, native)
        result = recover_restore(self.path, True)
        self.assertEqual(result['status'], 'original_database_restored')
        self.assertEqual(_file_hash(self.path), payload['original_hash'])
        after = security_bytes(self.path)
        self.compare_security(before, after, 'constructed missing-name state using actual native move; 1177 NOT observed')
        self.assertEqual(before, after)
        self.assertTrue(logical.exists())
        self.assertFalse(native.exists())
        self.assertFalse(prepared.exists())
        self.assertFalse(restore_recovery_pending(self.path))

    def test_unknown_recovery_target_is_preserved_and_marker_blocks_other_operations(self):
        payload, logical, native, prepared = self.recovery_fixture()
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("INSERT INTO settings VALUES ('unexpected-local-change','review')")
            db.commit()
        before = self.path.read_bytes()
        with self.assertRaises(RestoreRecoveryRequired):
            recover_restore(self.path, True)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertTrue(logical.exists())
        self.assertTrue(prepared.exists())
        self.assertTrue(restore_recovery_pending(self.path))
        with self.assertRaises(RuntimeError):
            backup_database(self.path, self.base / 'must-not-publish.db')
        self.assertFalse((self.base / 'must-not-publish.db').exists())

    def test_untrusted_marker_cannot_recreate_missing_database_or_escape_directory(self):
        self.seed()
        self.path.unlink()
        marker = _recovery_marker(self.path)
        for contents in ('not-json', '{"version":1,"prepared":"../../outside.db"}', '\u0000', 'x' * 16385):
            marker.write_text(contents, encoding='utf-8')
            before = marker.read_bytes()
            with self.assertRaises(RestoreRecoveryRequired):
                recover_restore(self.path, True)
            self.assertFalse(self.path.exists())
            self.assertEqual(marker.read_bytes(), before)
            self.assertTrue(restore_recovery_pending(self.path))


class FinalAuthenticationSecurityTests(FinalAcceptanceBase):
    def start_server(self, trusted=()):
        config = ServiceConfig(mode='service', admin_password_hash=PASSWORD_HASH,
            trusted_proxy_cidrs=trusted, auth_token='synthetic-secret-never-networked')
        self.engine = Engine(self.path, production=True, seed_demo=False, send_policy=config.send_policy())
        self.server = ServiceServer(('127.0.0.1', 0), self.engine, config)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.host = '127.0.0.1:' + str(self.server.server_port)

    def tearDown(self):
        if hasattr(self, 'server'):
            self.server.shutdown()
            self.server.server_close()
            self.thread.join(timeout=3)
            self.engine.close()
        super().tearDown()

    def request(self, method, path, payload=None, headers=(), duplicates=()):
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=4)
        data = json.dumps(payload).encode() if payload is not None else None
        try:
            conn.putrequest(method, path, skip_host=True)
            conn.putheader('Host', self.host)
            for key, value in [*headers, *duplicates]:
                conn.putheader(key, value)
            if data is not None:
                conn.putheader('Content-Type', 'application/json')
                conn.putheader('Content-Length', str(len(data)))
            conn.endheaders(data)
            response = conn.getresponse()
            return response.status, dict(response.getheaders()), json.loads(response.read())
        finally:
            conn.close()

    def test_concurrent_untrusted_forwarded_headers_cannot_evade_same_peer_rate_limit(self):
        self.start_server()
        def wrong(index):
            return self.request('POST', '/api/login', {'password': 'wrong'},
                                [('X-Forwarded-For', '192.0.2.' + str(index + 1))])[0]
        with ThreadPoolExecutor(max_workers=10) as pool:
            statuses = list(pool.map(wrong, range(10)))
        self.assertEqual(statuses.count(401), 5)
        self.assertEqual(statuses.count(429), 5)
        self.assertEqual(set(self.server.auth.failures), {'127.0.0.1'})
        self.notes['cases'].append({'concurrent_statuses': statuses, 'same_peer_bucket': True})

    def test_trusted_proxy_rejects_duplicate_chain_missing_and_ambiguous_values(self):
        self.start_server(('127.0.0.1/32',))
        for headers in [[], [('X-Forwarded-For', '192.0.2.1, 192.0.2.2')],
                        [('X-Forwarded-For', '192.0.2.1'), ('X-Forwarded-For', '192.0.2.2')],
                        [('X-Forwarded-For', '[2001:db8::1]')], [('X-Forwarded-For', 'fe80::1%1')]]:
            self.assertEqual(self.request('POST', '/api/login', {'password': PASSWORD}, headers)[0], 400)
        self.assertFalse(self.server.auth.sessions)
        self.assertFalse(self.server.auth.failures)
        self.assertEqual(self.request('POST', '/api/login', {'password': PASSWORD},
                                     [('X-Forwarded-For', '2001:db8::1')])[0], 200)

    def test_real_http_rejects_cookie_ambiguity_csrf_and_redacts_logs(self):
        self.start_server()
        status, headers, value = self.request('POST', '/api/login', {'password': PASSWORD})
        self.assertEqual(status, 200)
        cookie = headers['Set-Cookie'].split(';', 1)[0]
        token = value['token']
        self.assertIn('HttpOnly', headers['Set-Cookie'])
        self.assertIn('SameSite=Strict', headers['Set-Cookie'])
        malformed = [cookie + '; ' + cookie, cookie + '; bad[=x', 'reception_session="unterminated',
                     cookie + '; extra=has space', cookie + ';', cookie + '\x7f']
        captured = io.StringIO()
        with redirect_stdout(captured), redirect_stderr(captured):
            for supplied in malformed:
                self.assertEqual(self.request('GET', '/api/state?synthetic-secret=LOG-LEAK-SENTINEL',
                                             headers=[('Cookie', supplied)])[0], 401)
            self.assertEqual(self.request('GET', '/api/session', headers=[('Cookie', cookie)],
                                         duplicates=[('Cookie', cookie)])[0], 401)
            self.assertEqual(self.request('POST', '/api/outbox/pause', {'paused': False},
                                         [('Cookie', cookie)])[0], 403)
            self.assertEqual(self.request('POST', '/api/outbox/pause', {'paused': False},
                                         [('Cookie', cookie), ('X-Demo-Token', token), ('Origin', 'https://cross-origin.invalid')])[0], 403)
            self.assertEqual(self.request('POST', '/api/outbox/pause', {'paused': True},
                                         [('Cookie', cookie), ('X-Demo-Token', token)])[0], 200)
            self.assertEqual(self.request('POST', '/api/logout', {},
                                         [('Cookie', cookie), ('X-Demo-Token', token)])[0], 200)
            self.assertEqual(self.request('GET', '/api/session', headers=[('Cookie', cookie)])[0], 401)
        for secret in ('LOG-LEAK-SENTINEL', PASSWORD, cookie, token, 'synthetic-secret-never-networked'):
            self.assertNotIn(secret, captured.getvalue())
        self.notes['cases'].append({'malformed_cookies': len(malformed), 'stdout_stderr_bytes': len(captured.getvalue()),
            'csrf_blocked': True, 'logout_invalidates_session': True, 'provider': 'none; worker never started'})

    def test_password_hash_parser_and_unicode_inputs_fail_closed_without_exception(self):
        canonical = PASSWORD_HASH.split('$')
        invalid = [None, 123, PASSWORD_HASH + '$x', PASSWORD_HASH.replace('$16384$', '$999999999$'),
                   '$'.join(canonical[:4] + ['_' * 24, canonical[5]]),
                   '$'.join(canonical[:4] + [canonical[4], '/' * 88]),
                   '\ud800', 'x' * 201]
        for encoded in invalid:
            self.assertFalse(valid_password_hash(encoded))
            self.assertFalse(verify_password(PASSWORD, encoded))
        self.assertTrue(verify_password(PASSWORD, PASSWORD_HASH))
        self.assertFalse(verify_password('\ud800' * 12, PASSWORD_HASH))
        auth = AuthSessions(PASSWORD_HASH)
        auth.sessions['x' * 43] = {'token': 'synthetic', 'expires': 0}
        self.assertIsNone(auth.get('reception_session=' + 'x' * 43))
        self.assertFalse(auth.sessions)


@unittest.skipUnless(os.name == 'nt', 'Desktop input acceptance requires native Windows')
class FinalDesktopInputSecurityTests(FinalAcceptanceBase):
    def setUp(self):
        self.evidence = os.environ.get('HARBOR_FINAL_SECURITY_EVIDENCE_DIR')
        parent = Path(self.evidence) if self.evidence else ROOT / 'artifacts' / 'final-acceptance-runs'
        parent.mkdir(parents=True, exist_ok=True)
        # Keep the app's complete session/log path below Windows' legacy limit;
        # long-path system settings are outside this acceptance authorization.
        runs = ROOT / 'artifacts' / 'final-acceptance-runs'
        run_root = next((p for p in (parent, *parent.parents) if p.parent == runs), runs)
        fixture_parent = run_root / 'isf'
        fixture_parent.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix='di-', dir=fixture_parent)
        self.base = Path(self.temp.name)
        self.path = self.base / 'synthetic.db'
        self.state = self.base / 's'
        self.notes = {'id': self.id(), 'scope': 'public desktop entry, owned synthetic namespace; no browser', 'cases': []}

    def invoke_rejected_output(self, output):
        powershell = shutil.which('pwsh')
        self.assertIsNotNone(powershell, 'Existing project PowerShell host is required; nothing is installed')
        argv = [powershell, '-NoLogo', '-NoProfile', '-File', str(ROOT / 'scripts' / 'desktop.ps1'),
                '-Action', 'Launch', '-StateDirectory', str(self.state), '-Port', '0',
                '-NoBrowser', '-Quiet', '-ResultFile', str(output)]
        result = subprocess.run(argv, cwd=ROOT, capture_output=True,
                                timeout=65, creationflags=subprocess.CREATE_NO_WINDOW)
        self.notes['cases'].append({'argv': argv, 'exit': result.returncode, 'stdout_hex': result.stdout.hex(),
                                    'stderr_hex': result.stderr.hex(), 'state_created': self.state.exists(),
                                    'capture': 'raw bytes; producer encoding not assumed'})
        # Always clean up an unexpectedly started owned app before asserting the
        # precondition. This avoids turning a failed regression into an orphan.
        if (self.state / 'active-instance.json').exists():
            cleanup = subprocess.run([str(PYTHON), '-B', str(ROOT / 'scripts' / 'desktop_launcher.py'),
                'stop', '--state-directory', str(self.state), '--no-browser'], cwd=ROOT,
                capture_output=True, text=True, encoding='utf-8', timeout=65,
                creationflags=subprocess.CREATE_NO_WINDOW)
            self.notes['cases'].append({'unexpected_owned_app_cleanup_exit': cleanup.returncode,
                                        'stdout': cleanup.stdout, 'stderr': cleanup.stderr})
            self.assertEqual(cleanup.returncode, 0, 'Normal cleanup of unexpectedly started owned app failed')
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertFalse(self.state.exists(), 'Invalid output must be rejected before creating or launching a namespace')

    def test_existing_output_is_rejected_before_launch_and_preserved(self):
        sentinel = self.base / 'existing-result.json'
        sentinel.write_bytes(b'{"synthetic":"preserve-existing-output"}')
        self.invoke_rejected_output(sentinel)
        self.assertEqual(sentinel.read_bytes(), b'{"synthetic":"preserve-existing-output"}')

    def test_invalid_output_names_are_rejected_before_launch_or_new_parent(self):
        prefix = str(self.base / 'must-not-create')
        for output in (prefix + '\\CON\\result.json', prefix + '\\result.json:stream',
                       prefix + '\\trailing.\\result.json', '\\\\?\\UNC\\never-contacted.invalid\\share\\result.json'):
            self.invoke_rejected_output(output)
        self.assertFalse((self.base / 'must-not-create').exists())
        existing_parent = self.base / 'existing-owned-parent'
        existing_parent.mkdir()
        self.invoke_rejected_output(str(existing_parent) + '\\CON\\..\\must-not-write.json')
        self.assertFalse((existing_parent / 'must-not-write.json').exists())

    def test_output_through_owned_junction_is_rejected_before_launch_or_write(self):
        target = self.base / 'physical-owned-output'
        target.mkdir()
        junction = self.base / 'owned-junction'
        creation = subprocess.run(['cmd.exe', '/c', 'mklink', '/J', str(junction), str(target)],
                                  capture_output=True, timeout=5)
        self.assertEqual(creation.returncode, 0, creation.stderr.hex())
        try:
            self.invoke_rejected_output(junction / 'must-not-write.json')
            self.assertFalse((target / 'must-not-write.json').exists())
        finally:
            junction.rmdir()

    def test_injected_receipt_failure_after_real_child_start_normally_cleans_up(self):
        specification = importlib.util.spec_from_file_location('independent_desktop_receipt_failure',
                                                               ROOT / 'scripts' / 'desktop_launcher.py')
        controller = importlib.util.module_from_spec(specification)
        specification.loader.exec_module(controller)
        state = controller.resolve_state(self.state)
        original_write = controller.write_json
        injected = []
        def write_with_one_fault(path, value, **kwargs):
            if Path(path).name == 'launch-request.json' and not injected:
                injected.append(str(path))
                raise OSError('controlled synthetic launch-receipt write failure')
            return original_write(path, value, **kwargs)
        with controller.coordinated(state, 30):
            with patch.object(controller, 'write_json', side_effect=write_with_one_fault):
                with self.assertRaisesRegex(OSError, 'controlled synthetic'):
                    controller.launch(state, 0, 30)
        self.assertEqual(len(injected), 1)
        sessions = list((state / 'sessions').iterdir())
        self.assertEqual(len(sessions), 1)
        manifest = controller.read_json(sessions[0] / 'run-manifest.json')
        self.assertEqual(manifest['status'], 'stopped')
        self.assertEqual(manifest['instance_lock'], 'released')
        with controller.lock_database(sessions[0] / 'synthetic-demo.sqlite3'):
            pass
        with self.assertRaises(LookupError):
            controller.process_identity(manifest['pid'])
        self.notes['cases'].append({'scenario_kind': 'deterministic launch-receipt write failure only',
            'real_app_pid': manifest['pid'], 'normal_final_manifest': manifest,
            'real_kernel_lease_reacquired': True, 'force_kill': False, 'natural_failure_claim': False})


if __name__ == '__main__':
    unittest.main(verbosity=2)
