"""Native process/path/maintenance ownership; local synthetic files only."""
from contextlib import closing
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from reception.auth import hash_password
from reception.operations import (backup_database, canonical_database_path, restore_database,
                                  run_retention, service_lock)


@unittest.skipUnless(os.name == 'nt' or sys.platform.startswith('linux'), 'Native process ownership requires Windows NTFS or Linux')
class ProcessLockTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.path = self.base / 'service.db'
        with closing(sqlite3.connect(self.path)) as db:
            db.execute('CREATE TABLE disposable(value TEXT)')
            db.commit()

    def tearDown(self):
        self.temp.cleanup()

    def test_path_aliases_share_one_lock_and_yield_canonical_database(self):
        child = self.base / 'sub'
        child.mkdir()
        directory_alias = self.base / 'directory-alias'
        with service_lock(self.path) as canonical:
            self.assertEqual(canonical, self.path.resolve())
            aliases = [child / '..' / self.path.name]
            if os.name == 'nt':
                aliases.append(Path(str(self.path).swapcase()))
            for alias in aliases:
                with self.subTest(alias=str(alias)), self.assertRaises(RuntimeError):
                    with service_lock(alias):
                        self.fail('A path alias bypassed the instance lock')
            with self.subTest(capability='directory_symlink'):
                try:
                    directory_alias.symlink_to(self.base, target_is_directory=True)
                except OSError as exc:
                    if os.name == 'nt' and exc.winerror == 1314:
                        self.skipTest('Directory-symlink privilege is absent; no system or privilege setting was changed')
                    raise
                try:
                    with self.assertRaises(RuntimeError):
                        with service_lock(directory_alias / self.path.name):
                            self.fail('A parent-directory alias bypassed the instance lock')
                finally:
                    directory_alias.unlink()
            if os.name == 'nt':
                junction = self.base / 'directory-junction'
                from test_windows_lifecycle import capture_owned_junction_creation
                result = capture_owned_junction_creation(self.id(), junction, self.base)
                self.assertEqual(result.returncode, 0, 'Could not create the owned NTFS junction fixture')
                try:
                    with self.assertRaises(RuntimeError):
                        with service_lock(junction / self.path.name):
                            self.fail('A parent-junction alias bypassed the instance lock')
                finally:
                    junction.rmdir()

    def test_database_and_lock_symlinks_and_database_hardlinks_are_rejected(self):
        alias = self.base / 'database-alias.db'
        with self.subTest(capability='file_symlink'):
            try:
                alias.symlink_to(self.path)
            except OSError as exc:
                if os.name == 'nt' and exc.winerror == 1314:
                    self.skipTest('File-symlink privilege is absent; no system or privilege setting was changed')
                raise
            try:
                with self.assertRaises(ValueError):
                    canonical_database_path(alias)
            finally:
                alias.unlink()
        os.link(self.path, alias)
        with self.assertRaises(ValueError):
            with service_lock(self.path):
                self.fail('Database hard links must be rejected')
        alias.unlink()
        lock_path = Path(str(self.path) + '.service.lock')
        with self.subTest(capability='lock_file_symlink'):
            try:
                lock_path.symlink_to(self.path)
            except OSError as exc:
                if os.name == 'nt' and exc.winerror == 1314:
                    self.skipTest('Lock-file symlink privilege is absent; no system or privilege setting was changed')
                raise
            try:
                with self.assertRaises((OSError, ValueError)):
                    with service_lock(self.path):
                        self.fail('A symbolic lock file must be rejected')
            finally:
                lock_path.unlink()
        if os.name == 'nt':
            # Leaf junctions are real reparse points and need no new privilege.
            for link in (alias, lock_path):
                from test_windows_lifecycle import capture_owned_junction_creation
                result = capture_owned_junction_creation(self.id(), link, self.base)
                self.assertEqual(result.returncode, 0)
                try:
                    with self.assertRaises((OSError, ValueError)):
                        if link == alias:
                            canonical_database_path(alias)
                        else:
                            with service_lock(self.path):
                                self.fail('A leaf reparse lock must be rejected')
                finally:
                    link.rmdir()

    def test_second_process_cannot_start_service_on_owned_database(self):
        env = dict(os.environ)
        for name in list(env):
            if name.startswith('TWILIO_'):
                env.pop(name)
        env.update(APP_MODE='service', ENABLE_REAL_SEND='false',
                   ADMIN_PASSWORD_HASH=hash_password('Synthetic process-test password!'),
                   ADMIN_ORIGIN='', PRIVACY_HMAC_KEY='', TRUSTED_PROXY_CIDRS='',
                   RECIPIENT_POLICY='test_allowlist', TEST_RECIPIENT_ALLOWLIST='')
        root = Path(__file__).resolve().parents[1]
        with service_lock(self.path):
            result = subprocess.run([sys.executable, str(root / 'app.py'), '--db', str(self.path), '--port', '0'],
                                    cwd=root, env=env, capture_output=True, text=True, timeout=5)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('lock is active', result.stderr)

    def test_active_service_lock_refuses_restore_and_retention(self):
        backup = self.base / 'before.db'
        backup_database(self.path, backup)
        with service_lock(self.path):
            with self.assertRaises(RuntimeError):
                restore_database(self.path, backup, True)
            with self.assertRaises(RuntimeError):
                run_retention(self.path, 'synthetic-key-' + 'x' * 32, 30, True)


if __name__ == '__main__':
    unittest.main()
