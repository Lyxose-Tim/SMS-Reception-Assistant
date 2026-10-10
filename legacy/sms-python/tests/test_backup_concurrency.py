"""Actual native leases, SQLite transactions and backup API; fake data only."""
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest

from reception.engine import Engine
from reception.operations import BackupTimeout, backup_database, restore_database, service_lock, verify_database
from reception.platform_fs import NativePlatform, maintenance_lock

ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT / '.venv' / 'Scripts' / 'python.exe'
KEY = 'owned-synthetic-backup-concurrency-privacy-key'
_BACKUP = """
import json,os,sys,time
from reception.operations import backup_database
start=time.monotonic()
print(json.dumps({'pid':os.getpid(),'enter':start}),flush=True)
try:
    result=backup_database(sys.argv[1],sys.argv[2],float(sys.argv[3]))
    print(json.dumps({'result':result,'start':start,'finish':time.monotonic()}),flush=True)
except (ValueError,RuntimeError,OSError) as exc:
    print(json.dumps({'kind':type(exc).__name__,'error':str(exc),'start':start,'finish':time.monotonic()}),flush=True)
    sys.exit(2)
"""
_LEASE = """
import json,os,sys,time
from reception.platform_fs import maintenance_lock
start=time.monotonic()
try:
    with maintenance_lock(sys.argv[1],exclusive=sys.argv[2]=='ex',timeout_seconds=float(sys.argv[3])) as path:
        print(json.dumps({'pid':os.getpid(),'acquired':True,'canonical':str(path)}),flush=True)
        if sys.argv[4]=='hold':sys.stdin.readline()
except (ValueError,RuntimeError,OSError) as exc:
    print(json.dumps({'pid':os.getpid(),'acquired':False,'kind':type(exc).__name__,'error':str(exc),'elapsed':time.monotonic()-start}),flush=True)
    sys.exit(2)
"""
_WRITER = """
import json,os,sqlite3,sys,time
from pathlib import Path
db=sqlite3.connect(sys.argv[1],timeout=2)
db.execute('PRAGMA foreign_keys=ON')
print(json.dumps({'pid':os.getpid(),'writer_ready':True}),flush=True)
sys.stdin.readline()
generation=0
try:
    while not Path(sys.argv[2]).exists():
        generation+=1
        with db:
            db.execute('UPDATE pair_parent SET generation=?',(generation,))
            db.execute('UPDATE pair_child SET generation=?',(generation,))
            db.execute('INSERT INTO commits(generation) VALUES (?)',(generation,))
        print(json.dumps({'generation':generation,'committed':time.monotonic()}),flush=True)
        time.sleep(float(sys.argv[3]) if len(sys.argv)>3 else .35)
finally:db.close()
"""


@unittest.skipUnless(os.name == 'nt', 'New Windows kernel/SQLite concurrency evidence requires Windows')
class BackupConcurrencyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        supplied = os.environ.get('HARBOR_BACKUP_CONCURRENCY_EVIDENCE_DIR')
        cls.evidence = Path(supplied) if supplied else ROOT / 'artifacts' / 'final-acceptance-runs' / ('bc-' + os.urandom(4).hex())
        cls.evidence.mkdir(parents=True, exist_ok=True)
        runs = ROOT / 'artifacts' / 'final-acceptance-runs'
        run_root = next((p for p in (cls.evidence, *cls.evidence.parents) if p.parent == runs), cls.evidence)
        cls.fixture_parent = run_root / 'bcf'
        cls.fixture_parent.mkdir(exist_ok=True)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='b-', dir=self.fixture_parent)
        self.base = Path(self.temp.name)
        self.db = self.base / 'synthetic.db'
        self.notes = {'id': self.id(), 'kind': 'actual native and SQLite subprocess operations; no API mocks', 'observations': []}
        self.children = []
        self.writer_drains = {}
        engine = Engine(self.db, production=True, seed_demo=False)
        with engine.db:
            engine.db.execute("INSERT INTO settings VALUES ('windows_acceptance_synthetic','true')")
            engine.db.execute('CREATE TABLE pair_parent(id INTEGER PRIMARY KEY,generation INTEGER NOT NULL)')
            engine.db.execute('CREATE TABLE pair_child(id INTEGER PRIMARY KEY REFERENCES pair_parent(id),generation INTEGER NOT NULL)')
            engine.db.execute('CREATE TABLE commits(generation INTEGER PRIMARY KEY)')
            engine.db.executemany('INSERT INTO pair_parent VALUES (?,0)', [(n,) for n in range(128)])
            engine.db.executemany('INSERT INTO pair_child VALUES (?,0)', [(n,) for n in range(128)])
        engine.close()

    def tearDown(self):
        for child in self.children:
            if child.poll() is None:
                if hasattr(child, 'owned_writer_stop'):
                    child.owned_writer_stop.write_text('owned teardown stop', encoding='utf-8')
                if child.stdin and not child.stdin.closed:
                    child.stdin.write('\n')
                    child.stdin.flush()
                child.wait(timeout=15)
            for stream in (child.stdin, child.stdout, child.stderr):
                if stream and not stream.closed:
                    stream.close()
        self.temp.cleanup()
        self.notes['owned_fixture_removed'] = not self.base.exists()
        destination = self.evidence / (hashlib.sha256(self.id().encode()).hexdigest()[:12] + '.json')
        with destination.open('x', encoding='utf-8') as handle:
            json.dump(self.notes, handle, indent=2)

    def env(self):
        values = os.environ.copy()
        for name in tuple(values):
            if name.upper().startswith(('TWILIO_', 'DEMO_')):
                values.pop(name)
        values.update(ENABLE_REAL_SEND='false', PRIVACY_HMAC_KEY=KEY, PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1')
        return values

    def child(self, code, arguments):
        process = subprocess.Popen([str(PYTHON), '-B', '-c', code, *map(str, arguments)],
            cwd=ROOT, env=self.env(), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding='utf-8', creationflags=subprocess.CREATE_NO_WINDOW)
        self.children.append(process)
        first = json.loads(process.stdout.readline())
        self.notes['observations'].append({'actual_child': first})
        if code == _WRITER:
            process.owned_writer_stop = Path(arguments[1])
            receipts = []
            def drain():
                for line in process.stdout:
                    if line.strip():
                        receipts.append(json.loads(line))
            reader = threading.Thread(target=drain, daemon=True)
            reader.start()
            self.writer_drains[process.pid] = (reader, receipts)
        return process, first

    def finish(self, child):
        if child.pid in self.writer_drains:
            if child.stdin and not child.stdin.closed:
                child.stdin.close()
            child.wait(timeout=15)
            reader, receipts = self.writer_drains[child.pid]
            reader.join(timeout=2)
            self.assertFalse(reader.is_alive(), 'Actual writer output must be drained through normal exit')
            rows = list(receipts)
            error = child.stderr.read()
        else:
            output, error = child.communicate(timeout=15)
            rows = [json.loads(line) for line in output.splitlines() if line]
        self.notes['observations'].append({'exit': child.returncode, 'stdout': rows, 'stderr': error})
        return child.returncode, rows

    def lease_attempt(self, kind, timeout=.25):
        result = subprocess.run([str(PYTHON), '-B', '-c', _LEASE, str(self.db), kind, str(timeout), 'once'],
            cwd=ROOT, env=self.env(), capture_output=True, text=True, encoding='utf-8', timeout=5,
            creationflags=subprocess.CREATE_NO_WINDOW)
        value = json.loads(result.stdout)
        self.notes['observations'].append({'exit': result.returncode, **value})
        return value

    def test_invalid_timeout_is_rejected_before_any_native_or_filesystem_creation(self):
        output = self.base / 'must-not-create' / 'backup.db'
        events = []
        def observe(event, arguments):
            if event == 'harbor.native_fs_open':
                events.append(arguments)
        sys.addaudithook(observe)
        for timeout in (True, False, None, '5', 0, -1, float('nan'), float('inf'), 121, 10 ** 400):
            count = len(events)
            with self.assertRaises(ValueError):
                backup_database(self.db, output, timeout)
            self.assertEqual(len(events), count)
        self.assertFalse(output.parent.exists())
        self.assertFalse(Path(str(self.db) + '.maintenance.lock').exists())

    def test_actual_exclusive_sqlite_lock_has_cli_deadline_and_no_partial_publication(self):
        output = self.base / 'timeout.db'
        with closing(sqlite3.connect(self.db)) as writer:
            writer.execute('BEGIN EXCLUSIVE')
            started = time.monotonic()
            result = subprocess.run([str(PYTHON), '-B', str(ROOT / 'manage.py'), 'backup', '--db', str(self.db),
                '--output', str(output), '--timeout-seconds', '.4'], cwd=ROOT, env=self.env(),
                capture_output=True, text=True, encoding='utf-8', timeout=4, creationflags=subprocess.CREATE_NO_WINDOW)
            elapsed = time.monotonic() - started
            self.assertEqual(result.returncode, 2, result.stdout)
            self.assertIn('Backup timed out', result.stderr)
            self.assertGreaterEqual(elapsed, .35)
            self.assertLess(elapsed, 2)
            self.assertFalse(output.exists())
            self.assertFalse(list(self.base.glob('.sqlite-backup-*')))
            writer.rollback()
        backup_database(self.db, output, 2)
        self.assertTrue(verify_database(output))
        with maintenance_lock(self.db, exclusive=True, timeout_seconds=.25):
            pass
        self.notes['observations'].append({'actual_SQLite_exclusive_lock': True, 'elapsed': elapsed,
            'cli_exit': result.returncode, 'stderr': result.stderr, 'retry_after_release': 'verified_backup'})

    def test_exclusive_maintenance_times_out_backup_before_new_output_parent(self):
        output = self.base / 'must-not-create' / 'snapshot.db'
        with maintenance_lock(self.db, exclusive=True):
            child, _ = self.child(_BACKUP, [self.db, output, .25])
            code, records = self.finish(child)
            self.assertEqual(code, 2)
            self.assertEqual(records[-1]['kind'], 'BackupTimeout')
            self.assertLess(records[-1]['finish'] - records[-1]['start'], 1)
            self.assertFalse(output.parent.exists())
        self.assertFalse(list(self.base.glob('.sqlite-backup-*')))

    def test_two_shared_processes_exclude_exclusive_and_protect_the_native_sidecar(self):
        first, one = self.child(_LEASE, [self.db, 'sh', 1, 'hold'])
        second, two = self.child(_LEASE, [self.db, 'sh', 1, 'hold'])
        self.assertTrue(one['acquired'])
        self.assertTrue(two['acquired'])
        self.assertFalse(self.lease_attempt('ex')['acquired'])
        self.assertTrue(self.lease_attempt('sh')['acquired'])
        sidecar = Path(str(self.db) + '.maintenance.lock')
        with self.assertRaises(OSError) as failure:
            sidecar.unlink()
        self.assertEqual(failure.exception.winerror, 32)
        for process in (first, second):
            process.stdin.write('\n')
            process.stdin.flush()
            self.assertEqual(self.finish(process)[0], 0)
        self.assertTrue(self.lease_attempt('ex')['acquired'])

    def test_waiting_backup_succeeds_when_exclusive_lease_releases_before_deadline(self):
        output = self.base / 'after-release.db'
        with maintenance_lock(self.db, exclusive=True):
            child, _ = self.child(_BACKUP, [self.db, output, 2])
            time.sleep(.2)
            self.assertIsNone(child.poll())
            self.assertFalse(output.exists())
        code, records = self.finish(child)
        self.assertEqual(code, 0)
        self.assertGreaterEqual(records[-1]['finish'] - records[-1]['start'], .2)
        self.assertTrue(verify_database(output))

    def test_backup_succeeds_with_service_lease_owned_and_normal_sqlite_writes_continue(self):
        output = self.base / 'while-service-owned.db'
        with service_lock(self.db):
            child, _ = self.child(_BACKUP, [self.db, output, 2])
            self.assertEqual(self.finish(child)[0], 0)
            self.assertTrue(verify_database(output))
            with closing(sqlite3.connect(self.db)) as normal_writer:
                with normal_writer:
                    normal_writer.execute("INSERT INTO settings VALUES ('business-write-during-service-ownership','committed')")
            second = self.base / 'later-while-owned.db'
            child, _ = self.child(_BACKUP, [self.db, second, 2])
            self.assertEqual(self.finish(child)[0], 0)
            with closing(sqlite3.connect(second)) as snapshot:
                self.assertEqual(snapshot.execute("SELECT value FROM settings WHERE key='business-write-during-service-ownership'").fetchone(), ('committed',))
        self.notes['observations'].append({'real_service_kernel_lease_held_through_both_actual_backup_children': True,
            'ordinary_source_write_committed_without_releasing_service_lease': True})

    def test_shared_lease_blocks_actual_retention_cli_without_changing_source(self):
        before = hashlib.sha256(self.db.read_bytes()).hexdigest()
        with maintenance_lock(self.db):
            started = time.monotonic()
            result = subprocess.run([str(PYTHON), '-B', str(ROOT / 'manage.py'), 'maintenance', '--db', str(self.db),
                '--retention-days', '30', '--offline-confirmed'], cwd=ROOT, env=self.env(),
                capture_output=True, text=True, encoding='utf-8', timeout=8, creationflags=subprocess.CREATE_NO_WINDOW)
            elapsed = time.monotonic() - started
            self.assertEqual(result.returncode, 2)
            self.assertIn('Database maintenance lock timed out', result.stderr)
            self.assertGreaterEqual(elapsed, 4.9)
            self.assertLess(elapsed, 7)
            self.assertEqual(hashlib.sha256(self.db.read_bytes()).hexdigest(), before)
        self.notes['observations'].append({'actual_retention_exit': result.returncode,
            'maintenance_EX_wait_seconds': elapsed, 'source_whole_file_hash_unchanged': True,
            'stderr': result.stderr})

    def test_backup_shared_lease_blocks_actual_restore_while_sqlite_copy_waits(self):
        original = self.base / 'selected.db'
        backup_database(self.db, original)
        output = self.base / 'ongoing.db'
        with closing(sqlite3.connect(self.db)) as writer:
            writer.execute('BEGIN EXCLUSIVE')
            child, _ = self.child(_BACKUP, [self.db, output, 9])
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline and not list(self.base.glob('.sqlite-backup-*')):
                time.sleep(.005)
            self.assertTrue(list(self.base.glob('.sqlite-backup-*')), 'Actual backup must have acquired SH and created its temporary destination')
            started = time.monotonic()
            restore = subprocess.run([str(PYTHON), '-B', str(ROOT / 'manage.py'), 'restore', '--db', str(self.db),
                '--backup', str(original), '--offline-confirmed'], cwd=ROOT, env=self.env(),
                capture_output=True, text=True, encoding='utf-8', timeout=8, creationflags=subprocess.CREATE_NO_WINDOW)
            elapsed = time.monotonic() - started
            self.assertEqual(restore.returncode, 2)
            self.assertIn('Database maintenance lock timed out', restore.stderr)
            self.assertGreaterEqual(elapsed, 4.9)
            self.assertLess(elapsed, 7)
            self.assertIsNone(child.poll())
            self.assertFalse(Path(str(self.db) + '.restore-recovery.json').exists())
            self.assertFalse(list(self.base.glob('*.rollback-*')))
            writer.rollback()
        self.assertEqual(self.finish(child)[0], 0)
        self.assertTrue(verify_database(output))
        self.notes['observations'].append({'actual_restore_exit': restore.returncode,
            'maintenance_EX_wait_seconds': elapsed, 'stderr': restore.stderr,
            'backup_SH_still_owned_when_restore_refused': True})

    def test_restore_private_rollback_helper_does_not_reacquire_or_downgrade_exclusive_lease(self):
        selected = self.base / 'selected.db'
        backup_database(self.db, selected)
        with closing(sqlite3.connect(self.db)) as db:
            db.execute("INSERT INTO settings VALUES ('current-owned-marker','rollback-only')")
            db.commit()
        started = time.monotonic()
        result = restore_database(self.db, selected, True)
        self.assertLess(time.monotonic() - started, 2)
        self.assertEqual(result['status'], 'restored_paused')
        with closing(sqlite3.connect(self.db)) as db:
            gates = dict(db.execute("SELECT key,value FROM settings WHERE key IN ('outbox_paused','restore_review_required')"))
        self.assertEqual(gates, {'outbox_paused': 'true', 'restore_review_required': 'true'})
        with closing(sqlite3.connect(result['rollback_backup'])) as db:
            self.assertEqual(db.execute("SELECT value FROM settings WHERE key='current-owned-marker'").fetchone(), ('rollback-only',))
        with maintenance_lock(self.db, exclusive=True, timeout_seconds=.25):
            pass

    def test_waiting_backup_rejects_main_file_identity_changed_by_exclusive_owner(self):
        output = self.base / 'must-not-publish.db'
        replacement = self.base / 'new.db'
        backup_database(self.db, replacement)
        with maintenance_lock(self.db, exclusive=True):
            child, _ = self.child(_BACKUP, [self.db, output, 2])
            time.sleep(.2)
            self.assertIsNone(child.poll())
            NativePlatform.move_no_replace(self.db, self.base / 'retained-original.db')
            NativePlatform.move_no_replace(replacement, self.db)
        code, records = self.finish(child)
        self.assertEqual(code, 2)
        self.assertEqual(records[-1]['kind'], 'ValueError')
        self.assertIn('Database pathname changed', records[-1]['error'])
        self.assertFalse(output.exists())
        self.assertTrue(verify_database(self.db))
        self.notes['observations'].append({'scenario': 'constructed real native identity replacement while waiting; no native failure claim'})

    def test_concurrent_committed_transactions_produce_complete_consistent_snapshot(self):
        with closing(sqlite3.connect(self.db)) as seed:
            seed.execute('CREATE TABLE bulk(id INTEGER PRIMARY KEY,payload BLOB NOT NULL)')
            seed.executemany('INSERT INTO bulk(payload) VALUES (?)', [(b'owned-synthetic-payload-' * 1400,)] * 1600)
            seed.commit()
            self.assertEqual(seed.execute('PRAGMA journal_mode=WAL').fetchone()[0], 'wal')
        stop_file = self.base / 'stop-writer'
        writer, _ = self.child(_WRITER, [self.db, stop_file])
        output = self.base / 'concurrent.db'
        backup, enter = self.child(_BACKUP, [self.db, output, 5])
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and not list(self.base.glob('.sqlite-backup-*')) and backup.poll() is None:
            time.sleep(.001)
        self.assertTrue(list(self.base.glob('.sqlite-backup-*')), 'Actual backup temp must exist before releasing the independent writer')
        writer.stdin.write('go\n')
        writer.stdin.flush()
        try:
            code, records = self.finish(backup)
            self.assertEqual(code, 0, records)
            finish = records[-1]['finish']
            stop_file.write_text('owned stop', encoding='utf-8')
            writer_code, commits = self.finish(writer)
            self.assertEqual(writer_code, 0)
            during = [r for r in commits if 'committed' in r and enter['enter'] <= r['committed'] <= finish]
            self.assertTrue(during, 'At least one real independent transaction must commit during the actual backup operation')
            with closing(sqlite3.connect(output)) as db:
                self.assertEqual(db.execute('PRAGMA integrity_check').fetchall(), [('ok',)])
                self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(), [])
                self.assertEqual(db.execute('SELECT count(*) FROM pair_parent').fetchone(), (128,))
                self.assertEqual(db.execute('SELECT count(*) FROM pair_child').fetchone(), (128,))
                self.assertEqual(db.execute('SELECT count(*) FROM pair_parent p JOIN pair_child c USING(id) WHERE p.generation<>c.generation').fetchone(), (0,))
                generations = db.execute('SELECT DISTINCT generation FROM pair_parent').fetchall()
                self.assertEqual(len(generations), 1)
                generation = generations[0][0]
                self.assertEqual(db.execute('SELECT coalesce(max(generation),0) FROM commits').fetchone()[0], generation)
                self.assertEqual(db.execute('SELECT count(*) FROM bulk').fetchone(), (1600,))
                self.assertEqual(db.execute('PRAGMA journal_mode').fetchone()[0], 'delete')
            self.notes['observations'].append({'actual_commits_during_backup': during,
                'snapshot_generation': generation, 'all_128_pair_rows_match': True, 'bulk_rows': 1600,
                'source_bytes': self.db.stat().st_size, 'backup_uses_SQLite_API': True})
        finally:
            if not stop_file.exists():
                stop_file.write_text('owned stop', encoding='utf-8')
            if writer.poll() is None:
                self.finish(writer)

    def test_existing_destination_is_never_overwritten_even_after_a_busy_failure(self):
        destination = self.base / 'existing.db'
        destination.write_bytes(b'owned no-clobber sentinel')
        with maintenance_lock(self.db, exclusive=True):
            with self.assertRaises(ValueError):
                backup_database(self.db, destination, .25)
        self.assertEqual(destination.read_bytes(), b'owned no-clobber sentinel')
        self.assertFalse(list(self.base.glob('.sqlite-backup-*')))

    def test_high_frequency_commits_complete_consistently_or_abort_with_no_partial_output(self):
        with closing(sqlite3.connect(self.db)) as seed:
            seed.execute('CREATE TABLE bulk(id INTEGER PRIMARY KEY,payload BLOB NOT NULL)')
            seed.executemany('INSERT INTO bulk(payload) VALUES (?)', [(b'owned-high-frequency-data-' * 1400,)] * 1600)
            seed.commit()
            seed.execute('PRAGMA journal_mode=WAL')
        stop_file = self.base / 'stop-fast-writer'
        writer, _ = self.child(_WRITER, [self.db, stop_file, .001])
        output = self.base / 'high-frequency.db'
        backup, enter = self.child(_BACKUP, [self.db, output, .8])
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and not list(self.base.glob('.sqlite-backup-*')) and backup.poll() is None:
            time.sleep(.001)
        self.assertTrue(list(self.base.glob('.sqlite-backup-*')))
        writer.stdin.write('go\n')
        writer.stdin.flush()
        try:
            code, records = self.finish(backup)
            outcome = records[-1]
            elapsed = outcome['finish'] - outcome['start']
            self.assertLess(elapsed, 2)
            stop_file.write_text('owned stop', encoding='utf-8')
            writer_code, commits = self.finish(writer)
            self.assertEqual(writer_code, 0)
            during = [r for r in commits if 'committed' in r and enter['enter'] <= r['committed'] <= outcome['finish']]
            self.assertGreaterEqual(len(during), 3, 'A sustained real transaction stream must overlap the backup operation')
            if code == 0:
                self.assertTrue(verify_database(output))
                with closing(sqlite3.connect(output)) as db:
                    self.assertEqual(db.execute('SELECT count(*) FROM pair_parent p JOIN pair_child c USING(id) WHERE p.generation<>c.generation').fetchone(), (0,))
                    self.assertEqual(db.execute('SELECT count(*) FROM bulk').fetchone(), (1600,))
                branch = 'consistent completed snapshot despite high-frequency external commits'
            else:
                self.assertEqual(code, 2)
                self.assertEqual(outcome['kind'], 'BackupTimeout')
                self.assertFalse(output.exists())
                branch = 'bounded backup timeout under high-frequency commits; incomplete destination not published'
            self.assertFalse(list(self.base.glob('.sqlite-backup-*')))
            with maintenance_lock(self.db, exclusive=True, timeout_seconds=.25):
                pass
            self.notes['observations'].append({'actual_continuous_commits_during_backup': len(during),
                'generations_observed': [r['generation'] for r in during], 'budget_seconds': .8,
                'elapsed': elapsed, 'observed_branch': branch, 'kernel_lease_reacquired': True,
                'controlled_API_or_kernel_injection': False, 'writer_stdout_continuously_drained': True})
        finally:
            if not stop_file.exists():
                stop_file.write_text('owned stop', encoding='utf-8')
            if writer.poll() is None:
                self.finish(writer)


if __name__ == '__main__':
    unittest.main(verbosity=2)
