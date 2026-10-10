"""Offline privacy maintenance and verified SQLite snapshots.

Customer content is deleted; keyed opt-out suppression and hashed event IDs are
kept for safety. These hashes are pseudonymous records, not anonymous data or
encryption. No operation uploads data or communicates with Twilio.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import re
import sqlite3
import stat
import sys
import tempfile
import time
import uuid
from contextlib import closing, contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path


def _stamp(value=None):
    return (value or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def privacy_hash(key, value):
    if not isinstance(key, str) or len(key) < 32:
        raise ValueError("PRIVACY_HMAC_KEY must contain at least 32 characters")
    return hmac.new(key.encode("utf-8"), str(value).encode("utf-8"), hashlib.sha256).hexdigest()


def event_hash(event_id):
    return hashlib.sha256(str(event_id).encode("utf-8")).hexdigest()


def ensure_privacy_schema(db):
    # Separate statements avoid executescript's implicit transaction commit.
    db.execute("CREATE TABLE IF NOT EXISTS privacy_suppressions(phone_hash TEXT PRIMARY KEY, reason TEXT NOT NULL, created_at TEXT NOT NULL)")
    db.execute("CREATE TABLE IF NOT EXISTS privacy_dedup(event_hash TEXT PRIMARY KEY, created_at TEXT NOT NULL)")
    db.execute("CREATE TABLE IF NOT EXISTS privacy_actions(id INTEGER PRIMARY KEY AUTOINCREMENT, action TEXT NOT NULL, counts TEXT NOT NULL, created_at TEXT NOT NULL)")


def suppression_for(db, key, phone):
    if not key:
        return ""
    ensure_privacy_schema(db)
    row = db.execute("SELECT reason FROM privacy_suppressions WHERE phone_hash=?", (privacy_hash(key, phone),)).fetchone()
    return row[0] if row else ""


def event_was_deleted(db, key, event_id):
    # Plain SHA256 is suitable for high-entropy provider/event IDs; phones use HMAC.
    ensure_privacy_schema(db)
    return db.execute("SELECT 1 FROM privacy_dedup WHERE event_hash=?", (event_hash(event_id),)).fetchone() is not None


def clear_suppression(db, key, phone, include_carrier=False):
    """Only a trusted, fresh platform restoration may call this helper."""
    reason = suppression_for(db, key, phone)
    if not reason:
        return
    if "carrier" in reason and not include_carrier:
        db.execute("UPDATE privacy_suppressions SET reason='carrier_opt_out' WHERE phone_hash=?", (privacy_hash(key, phone),))
    else:
        db.execute("DELETE FROM privacy_suppressions WHERE phone_hash=?", (privacy_hash(key, phone),))


@contextmanager
def service_lock(db_path):
    """Use the same exclusive lock in the service and offline commands.

    This detects this application's running instance, not arbitrary external
    SQLite readers. Operators must stop other database tools before restoration.
    """
    _require_supported_platform()
    if os.name == 'nt':
        from .platform_fs import lock_database
        with lock_database(db_path) as database:
            yield database
        return
    try:
        import fcntl
    except ImportError:
        raise RuntimeError("Offline database operations require POSIX flock; this platform has not been verified") from None
    database = canonical_database_path(db_path, allow_create=True)
    path = Path(str(database) + ".service.lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o600)
    try:
        metadata = os.fstat(fd)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise ValueError("Service lock must be a regular single-link file")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("Service/database lock is active; stop the service before this operation") from None
        current = path.lstat()
        if (not stat.S_ISREG(current.st_mode) or current.st_nlink != 1 or
                (current.st_dev, current.st_ino) != (metadata.st_dev, metadata.st_ino)):
            raise ValueError("Service lock pathname changed while acquiring ownership")
        os.fchmod(fd, 0o600)
        # Recheck after the lock was obtained; callers use exactly this path.
        canonical_database_path(database, allow_create=True)
        yield database
    finally:
        os.close(fd)


def _require_linux():
    if not sys.platform.startswith('linux'):
        raise RuntimeError("Database service locking and offline maintenance require the supported Linux deployment path")


def _require_supported_platform():
    if os.name != 'nt' and not sys.platform.startswith('linux'):
        raise RuntimeError('Database operations require a supported Windows or Linux local filesystem')


def canonical_database_path(value, allow_create=False):
    """Normal pathname aliases share a lock; database hard links are unsupported.

    The containing directory must be private and trusted. This advisory guard
    cannot coordinate arbitrary external SQLite tools or hostile file replacement.
    """
    if os.name == 'nt':
        from .platform_fs import canonical_path
        return canonical_path(value, allow_create=allow_create)
    original = Path(os.path.abspath(value))
    if original.is_symlink():
        raise ValueError("Database must not be a symlink")
    path = original.parent.resolve() / original.name
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        if allow_create:
            return path
        raise ValueError("Database must be an existing regular single-link file") from None
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        raise ValueError("Database must be a regular single-link file, without symlink or hard-link aliases")
    return path


def _existing_file(value):
    if os.name == 'nt':
        return canonical_database_path(value)
    path = Path(value).absolute()
    if path.is_symlink() or not path.is_file():
        raise ValueError("Database/backup must be an existing regular file, not a symlink")
    return path.parent.resolve() / path.name


def _read_connection(path, timeout=5):
    return sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=timeout)


def verify_database(path):
    path = _existing_file(path)
    with closing(_read_connection(path)) as db:
        if db.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
            raise ValueError("SQLite integrity verification failed")
        if db.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise ValueError("SQLite foreign-key verification failed")
    return True


def _fsync_directory(path):
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _flush_file(path):
    if os.name == 'nt':
        from .platform_fs import flush_file
        flush_file(path)
    else:
        with Path(path).open('rb') as handle:
            os.fsync(handle.fileno())


def _recovery_marker(db_path):
    return Path(str(db_path) + '.restore-recovery.json')


def restore_recovery_pending(db_path):
    """Do not parse or recover an untrusted marker during service startup."""
    path = canonical_database_path(db_path, allow_create=True)
    return os.path.lexists(_recovery_marker(path))


def _refuse_pending_recovery(db_path):
    if restore_recovery_pending(db_path):
        raise RuntimeError('Restore recovery is pending; keep the service stopped and use recover-restore')


class BackupTimeout(RuntimeError):
    """A backup exceeded its application-controlled wait/work budget."""


def _backup_deadline(timeout_seconds):
    if (type(timeout_seconds) not in (int, float) or not 0 < timeout_seconds <= 120 or
            not math.isfinite(timeout_seconds)):
        raise ValueError('Backup timeout must be a finite number greater than zero and at most 120 seconds')
    return time.monotonic() + timeout_seconds


def _backup_remaining(deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise BackupTimeout('Backup timed out; no destination was published')
    return remaining


def _backup_paths(db_path, output):
    """Validate complete input names before leases, mkdir or SQLite access."""
    _require_supported_platform()
    if os.name == 'nt':
        # Reject the complete raw name before Path metadata or any creation.
        # The native validator also proves the existing local ancestor and
        # validates all pending path components before any directory is made.
        # Existing local parent aliases are resolved after stored-target checks;
        # creation of a missing tail through a reparse alias is rejected.
        from .platform_fs import validate_creation_path, validate_lexical_path
        validate_lexical_path(db_path)
        validate_lexical_path(output)
        target = validate_creation_path(output)
    else:
        target = Path(output).absolute()
    source = canonical_database_path(db_path)
    if source == target or target.exists() or target.is_symlink():
        raise ValueError("Backup destination must be a new file distinct from the database")
    return source, target


def _verify_backup_before_publish(path, deadline):
    _backup_remaining(deadline)
    with closing(_read_connection(_existing_file(path), timeout=0)) as db:
        db.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
        try:
            if db.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
                raise ValueError('SQLite integrity verification failed')
            if db.execute('PRAGMA foreign_key_check').fetchone() is not None:
                raise ValueError('SQLite foreign-key verification failed')
        except sqlite3.OperationalError:
            _backup_remaining(deadline)
            raise RuntimeError('Backup verification could not complete; no destination was published') from None
    _backup_remaining(deadline)


def _backup_database_owned(source, target, deadline):
    """Private helper: caller already owns the matching maintenance SH or EX.

    Restore rollback uses its existing EX lease; this helper never reacquires
    or downgrades it and is not exposed as a bypass flag in the public API.
    """
    _backup_remaining(deadline)
    _refuse_pending_recovery(source)
    if source == target or os.path.lexists(target):
        raise ValueError('Backup destination must be a new file distinct from the database')
    target.parent.mkdir(parents=True, exist_ok=True)
    if os.name == 'nt':
        target = canonical_database_path(target, allow_create=True)
    fd, temp_name = tempfile.mkstemp(prefix=".sqlite-backup-", suffix=".db", dir=target.parent)
    os.close(fd)
    temp = Path(temp_name)
    try:
        _backup_remaining(deadline)
        # SQLite backup_step retries BUSY/LOCKED independently of the connect
        # timeout. Progress sees every step, including retries and DONE, and
        # raises to abort/finish the native backup before either handle closes.
        with closing(_read_connection(source, timeout=0)) as src, closing(sqlite3.connect(temp, timeout=0)) as dst:
            try:
                src.backup(dst, pages=64, progress=lambda *_: _backup_remaining(deadline), sleep=.05)
                _backup_remaining(deadline)
                dst.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
                mode = dst.execute('PRAGMA journal_mode=DELETE').fetchone()
                if not mode or mode[0].lower() != 'delete':
                    raise RuntimeError('Backup could not be made self-contained; no destination was published')
                dst.commit()
            except sqlite3.OperationalError:
                _backup_remaining(deadline)
                raise RuntimeError('Backup could not obtain a stable SQLite snapshot; no destination was published') from None
        _verify_backup_before_publish(temp, deadline)
        _backup_remaining(deadline)
        _flush_file(temp)
        _backup_remaining(deadline)
        # No-clobber installation also avoids overwriting a raced destination.
        if os.name == 'nt':
            from .platform_fs import move_no_replace
            move_no_replace(temp, target)
        else:
            os.link(temp, target)
            temp.unlink()
            _fsync_directory(target.parent)
    finally:
        # A canceled/failed snapshot never leaks its incomplete main or sidecars.
        for suffix in ('-wal', '-shm', '-journal', ''):
            Path(str(temp) + suffix).unlink(missing_ok=True)
    return {"status": "verified_backup", "path": str(target)}


def backup_database(db_path, output, timeout_seconds=5):
    """Online consistent SQLite snapshot, bounded retries, no-clobber publish.

    Normal business writes continue. A separate native maintenance shared lease
    excludes restoration/privacy maintenance through verification and publish.
    The budget bounds application-controlled waits and SQLite work; ordinary
    filesystem/flush system calls are not hard real-time interruptible.
    """
    deadline = _backup_deadline(timeout_seconds)
    source, target = _backup_paths(db_path, output)
    from .platform_fs import MaintenanceLockTimeout, maintenance_lock
    try:
        with maintenance_lock(source, timeout_seconds=_backup_remaining(deadline)) as source:
            _backup_remaining(deadline)
            # Marker check occurs only after the coordinated lease was granted.
            return _backup_database_owned(source, target, deadline)
    except MaintenanceLockTimeout:
        raise BackupTimeout('Backup timed out waiting for offline maintenance; no destination was published') from None


def _tables(db):
    return {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _columns(db, table):
    if table not in _tables(db):
        return set()
    return {r[1] for r in db.execute(f"PRAGMA table_info({table})")}


def _freeze_restored_tasks(db):
    # A valid SQLite snapshot may lack application tables. Preserve the review
    # barrier even when Engine later initializes the remaining empty schema.
    db.execute("CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    tables = _tables(db)
    db.execute("INSERT INTO settings(key,value) VALUES ('outbox_paused','true') ON CONFLICT(key) DO UPDATE SET value=excluded.value")
    db.execute("INSERT INTO settings(key,value) VALUES ('restore_review_required','true') ON CONFLICT(key) DO UPDATE SET value=excluded.value")
    counts = {"queued_canceled": 0, "submitting_uncertain": 0, "inbox_frozen": 0}
    if "outbox" in tables:
        for old_status, new_status, code, counter in (("queued", "canceled", "RESTORE_REQUIRES_REVIEW", "queued_canceled"), ("submitting", "uncertain", "RESTORED_SUBMISSION_UNKNOWN", "submitting_uncertain")):
            rows = db.execute("SELECT message_id FROM outbox WHERE status=?", (old_status,)).fetchall()
            counts[counter] = len(rows)
            db.execute("UPDATE outbox SET status=?,error_code=?,updated_at=? WHERE status=?", (new_status, code, _stamp(), old_status))
            for row in rows:
                db.execute("UPDATE messages SET status=?,error_code=? WHERE id=?", (new_status, code, row[0]))
    # A backup may predate provider processing; never replay its pending intake.
    columns = _columns(db, "provider_inbox")
    if "status" in columns:
        counts["inbox_frozen"] = db.execute("UPDATE provider_inbox SET status='rejected',error_code='RESTORE_REQUIRES_REVIEW' WHERE status IN ('pending','queued','processing','verifying','verification_failed')").rowcount
    return counts


def _merge_safety_metadata(current, restored):
    """Preserve current STOP/dedup hashes across an older snapshot restoration.

    The conservative union may retain an older opt-out despite a later START;
    that is preferable to accidentally re-enabling sends and requires review.
    No customer phone, message, raw SID or appointment is copied.
    """
    ensure_privacy_schema(restored)
    restored.execute("CREATE TABLE IF NOT EXISTS inbox_tombstones(sid_hash TEXT PRIMARY KEY,created_at TEXT NOT NULL)")
    counts = {"suppression_hashes_merged": 0, "event_hashes_merged": 0, "inbox_hashes_merged": 0,
              "quota_reservations_merged": 0, "quota_attempts_merged": 0}
    tables = _tables(current)
    for table, key_field, counter in (("privacy_suppressions", "phone_hash", "suppression_hashes_merged"), ("privacy_dedup", "event_hash", "event_hashes_merged"), ("inbox_tombstones", "sid_hash", "inbox_hashes_merged")):
        if table not in tables:
            continue
        fields = key_field + (",reason" if table == "privacy_suppressions" else "") + ",created_at"
        for row in current.execute(f"SELECT {fields} FROM {table}"):
            key, created_at = row[0], row[-1]
            try:
                parsed = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                valid_time = parsed.tzinfo is not None
            except (AttributeError, TypeError, ValueError):
                valid_time = False
            if not isinstance(key, str) or not re.fullmatch(r"[0-9a-f]{64}", key) or not valid_time:
                raise RuntimeError("Safety metadata could not be verified; restoration was not installed")
            if table == "privacy_suppressions":
                reason = row[1]
                if reason not in {"local_opt_out", "carrier_opt_out", "local_and_carrier_opt_out"}:
                    raise RuntimeError("Safety metadata could not be verified; restoration was not installed")
                existing = restored.execute("SELECT reason,created_at FROM privacy_suppressions WHERE phone_hash=?", (key,)).fetchone()
                if existing and existing[0] != reason:
                    reason = "local_and_carrier_opt_out"
                # Normalize timestamps before taking a chronological maximum.
                # Keeping an older backup STOP timestamp would let an unseen
                # delayed START between two STOPs clear the newer restriction.
                created_at = _stamp(parsed)
                if existing:
                    try:
                        existing_time = datetime.fromisoformat(existing[1].replace("Z", "+00:00"))
                        if existing_time.tzinfo is None:
                            raise ValueError()
                    except (AttributeError, TypeError, ValueError):
                        raise RuntimeError("Safety metadata could not be verified; restoration was not installed") from None
                    created_at = _stamp(max(parsed, existing_time))
                restored.execute("INSERT INTO privacy_suppressions(phone_hash,reason,created_at) VALUES (?,?,?) ON CONFLICT(phone_hash) DO UPDATE SET reason=excluded.reason,created_at=excluded.created_at", (key, reason, created_at))
            else:
                restored.execute(f"INSERT OR IGNORE INTO {table}({key_field},created_at) VALUES (?,?)", (key, created_at))
            counts[counter] += 1
    # Preserve replay protection for newer events that have not yet undergone
    # privacy deletion. Copy hashes only, never their raw IDs or content.
    if "events" in tables:
        for row in current.execute("SELECT event_id FROM events"):
            if not isinstance(row[0], str) or not 0 < len(row[0]) <= 160:
                raise RuntimeError("Safety metadata could not be verified; restoration was not installed")
            changed = restored.execute("INSERT OR IGNORE INTO privacy_dedup(event_hash,created_at) VALUES (?,?)", (event_hash(row[0]), _stamp())).rowcount
            counts["event_hashes_merged"] += changed
    if "provider_inbox" in tables:
        for row in current.execute("SELECT sid FROM provider_inbox"):
            if not isinstance(row[0], str) or not re.fullmatch(r"SM[0-9a-fA-F]{32}", row[0]):
                raise RuntimeError("Safety metadata could not be verified; restoration was not installed")
            changed = restored.execute("INSERT OR IGNORE INTO inbox_tombstones(sid_hash,created_at) VALUES (?,?)", (event_hash(row[0]), _stamp())).rowcount
            counts["inbox_hashes_merged"] += changed
            changed = restored.execute("INSERT OR IGNORE INTO privacy_dedup(event_hash,created_at) VALUES (?,?)", (event_hash("twilio:" + row[0]), _stamp())).rowcount
            counts["event_hashes_merged"] += changed
    for table, ddl, fields, counter in (
        ("outbox_quota_reservations", "reservation_key TEXT PRIMARY KEY,day_utc TEXT NOT NULL,segments INTEGER NOT NULL,created_at TEXT NOT NULL", "reservation_key,day_utc,segments,created_at", "quota_reservations_merged"),
        ("outbox_quota_attempts", "attempt_key TEXT PRIMARY KEY,started_at TEXT NOT NULL", "attempt_key,started_at", "quota_attempts_merged")):
        if table not in tables:
            continue
        restored.execute(f"CREATE TABLE IF NOT EXISTS {table}({ddl})")
        for row in current.execute(f"SELECT {fields} FROM {table}"):
            key, timestamp = row[0], row[-1]
            try:
                valid_time = datetime.fromisoformat(timestamp.replace("Z", "+00:00")).tzinfo is not None
            except (AttributeError, TypeError, ValueError):
                valid_time = False
            if not isinstance(key, str) or not re.fullmatch(r"[0-9a-f]{64}", key) or not valid_time:
                raise RuntimeError("Safety metadata could not be verified; restoration was not installed")
            if table == "outbox_quota_reservations":
                try:
                    valid_day = datetime.strptime(row[1], "%Y-%m-%d").strftime("%Y-%m-%d") == row[1]
                except (ValueError, TypeError):
                    valid_day = False
                if not valid_day or type(row[2]) is not int or not 1 <= row[2] <= 10000:
                    raise RuntimeError("Safety metadata could not be verified; restoration was not installed")
                existing = restored.execute("SELECT day_utc FROM outbox_quota_reservations WHERE reservation_key=?", (key,)).fetchone()
                if existing and existing[0] != row[1]:
                    raise RuntimeError("Safety metadata could not be verified; restoration was not installed")
                # A conflicting same reservation keeps the larger segment debit.
                restored.execute(f"INSERT INTO {table}({fields}) VALUES (?,?,?,?) ON CONFLICT(reservation_key) DO UPDATE SET segments=max(segments,excluded.segments)", row)
            else:
                restored.execute(f"INSERT INTO {table}({fields}) VALUES (?,?) ON CONFLICT(attempt_key) DO UPDATE SET started_at=max(started_at,excluded.started_at)", row)
            counts[counter] += 1
    return counts


class RestoreRecoveryRequired(RuntimeError):
    """An incomplete filesystem restore must never initialize a new database."""


def _file_hash(path):
    digest = hashlib.sha256()
    with _existing_file(path).open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _write_recovery_marker(target, payload):
    marker = _recovery_marker(target)
    descriptor = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
        json.dump(payload, handle, sort_keys=True)
        handle.flush()
    _flush_file(marker)


def _read_recovery_marker(target):
    marker = _existing_file(_recovery_marker(target))
    if marker.stat().st_size > 16384:
        raise RestoreRecoveryRequired('Restore recovery metadata is invalid; evidence has been preserved')
    try:
        payload = json.loads(marker.read_text(encoding='utf-8'))
        required = {'version', 'database', 'original_hash', 'prepared_hash', 'original_identity', 'prepared_identity',
                    'logical_rollback', 'native_original', 'prepared', 'sidecars'}
        if not isinstance(payload, dict) or set(payload) != required or payload['version'] != 1 or payload['database'] != target.name:
            raise ValueError()
        for field in ('original_hash', 'prepared_hash'):
            if not isinstance(payload[field], str) or not re.fullmatch('[0-9a-f]{64}', payload[field]):
                raise ValueError()
        for field in ('original_identity', 'prepared_identity'):
            if not isinstance(payload[field], list) or len(payload[field]) not in (2, 3) or any(type(n) is not int for n in payload[field]):
                raise ValueError()
        for field in ('logical_rollback', 'native_original', 'prepared'):
            name = payload[field]
            if not isinstance(name, str) or Path(name).name != name or any(c in name for c in '/\\:') or name in {'', '.', '..', target.name}:
                raise ValueError()
        if not payload['logical_rollback'].startswith(target.name + '.rollback-') or not payload['native_original'].startswith(target.name + '.original-'):
            raise ValueError()
        if not payload['prepared'].startswith('.sqlite-restore-') or not payload['prepared'].endswith('.db'):
            raise ValueError()
        if not isinstance(payload['sidecars'], list) or len(payload['sidecars']) > 3:
            raise ValueError()
        for item in payload['sidecars']:
            if not isinstance(item, dict) or set(item) != {'source', 'saved'} or item['source'] not in {target.name + s for s in ('-wal', '-shm', '-journal')}:
                raise ValueError()
            if not isinstance(item['saved'], str) or Path(item['saved']).name != item['saved'] or any(c in item['saved'] for c in '/\\:'):
                raise ValueError()
            if not item['saved'].startswith(item['source'] + '.before-restore-'):
                raise ValueError()
        return payload
    except (ValueError, TypeError, KeyError):
        raise RestoreRecoveryRequired('Restore recovery metadata is invalid; evidence has been preserved') from None


def _restore_probe(target):
    from .platform_fs import exclusive_database_probe
    try:
        with exclusive_database_probe(target):
            pass
    except OSError:
        raise RuntimeError('Database has open handles; stop the service and all database tools') from None


def _checkpoint_for_replacement(target):
    # A probe cannot inventory later arbitrary external readers. The shared
    # application lock and explicit stopped-tools condition remain required.
    _restore_probe(target)
    with closing(sqlite3.connect(target, timeout=0)) as current:
        try:
            current.execute('BEGIN EXCLUSIVE')
            current.rollback()
            checkpoint = current.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()
            if checkpoint and checkpoint[0] != 0:
                raise RuntimeError('Database WAL checkpoint is busy; original sidecars were preserved')
            mode = current.execute('PRAGMA journal_mode=DELETE').fetchone()
            if not mode or mode[0].lower() != 'delete':
                raise RuntimeError('Database journal could not be made self-contained')
        except sqlite3.OperationalError:
            raise RuntimeError('Database is busy; stop the service and all database tools') from None
    for suffix in ('-wal', '-journal'):
        sidecar = Path(str(target) + suffix)
        if os.path.lexists(sidecar) and _existing_file(sidecar).stat().st_size:
            raise RuntimeError('Nonempty database sidecar was preserved; offline review is required')
    _flush_file(target)


def _matching_file(path, expected_hash, expected_identity=None):
    from .platform_fs import file_identity
    if not os.path.lexists(path):
        return False
    if _file_hash(path) != expected_hash:
        return False
    return expected_identity is None or list(file_identity(path)) == expected_identity


def _finish_recovery(target, payload, status):
    # Only delete the known, unchanged prepared file. Rollback/native originals
    # and quarantined sidecars are retained as explicit operator-owned evidence.
    prepared = target.parent / payload['prepared']
    if _matching_file(prepared, payload['prepared_hash'], payload['prepared_identity']):
        prepared.unlink()
    _recovery_marker(target).unlink()
    return {'status': status, 'rollback_backup': str(target.parent / payload['logical_rollback']),
            'native_original_backup': str(target.parent / payload['native_original'])}


def _recover_restore_locked(target):
    from .platform_fs import move_no_replace
    payload = _read_recovery_marker(target)
    if _matching_file(target, payload['original_hash'], payload['original_identity']):
        verify_database(target)
        return _finish_recovery(target, payload, 'original_database_preserved')
    if _matching_file(target, payload['prepared_hash'], payload['prepared_identity']):
        verify_database(target)
        with closing(_read_connection(target)) as db:
            gates = dict(db.execute("SELECT key,value FROM settings WHERE key IN ('outbox_paused','restore_review_required')"))
        if gates != {'outbox_paused': 'true', 'restore_review_required': 'true'}:
            raise RestoreRecoveryRequired('Restored database lacks safety gates; recovery evidence was preserved')
        _flush_file(target)
        return {**_finish_recovery(target, payload, 'restored_paused'), 'manual_safety_review_required': True}
    native = target.parent / payload['native_original']
    if not os.path.lexists(target) and _matching_file(native, payload['original_hash'], payload['original_identity']):
        move_no_replace(native, target)
        verify_database(target)
        _flush_file(target)
        return _finish_recovery(target, payload, 'original_database_restored')
    raise RestoreRecoveryRequired('Restore outcome is unrecognized; original/rollback files and marker were preserved; keep the service stopped')


def recover_restore(db_path, offline_confirmed=False):
    _require_supported_platform()
    if os.name != 'nt':
        raise ValueError('Native filesystem restore recovery applies only to Windows')
    if offline_confirmed is not True:
        raise ValueError('Explicit offline confirmation is required for restore recovery')
    target = canonical_database_path(db_path, allow_create=True)
    with _offline_ownership(target) as target:
        if not restore_recovery_pending(target):
            raise ValueError('No pending filesystem restore recovery exists')
        if target.exists():
            _restore_probe(target)
        return _recover_restore_locked(target)


def _restore_windows_locked(target, source):
    from .platform_fs import file_identity, move_no_replace, replace_file
    _refuse_pending_recovery(target)
    _restore_probe(target)
    token = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '-' + uuid.uuid4().hex[:8]
    rollback = target.with_name(target.name + '.rollback-' + token)
    native_original = target.with_name(target.name + '.original-' + token)
    descriptor, temp_name = tempfile.mkstemp(prefix='.sqlite-restore-', suffix='.db', dir=target.parent)
    os.close(descriptor)
    prepared = Path(temp_name)
    marker_created = False
    try:
        with closing(_read_connection(source)) as src, closing(_read_connection(target)) as current, closing(sqlite3.connect(prepared)) as dst:
            src.backup(dst)
            safety_counts = _merge_safety_metadata(current, dst)
            counts = _freeze_restored_tasks(dst)
            dst.commit()
            if dst.execute('PRAGMA journal_mode=DELETE').fetchone()[0].lower() != 'delete':
                raise RuntimeError('Prepared restore is not a self-contained database')
        verify_database(prepared)
        _flush_file(prepared)
        _backup_database_owned(target, rollback, _backup_deadline(5))
        _checkpoint_for_replacement(target)
        sidecars = []
        for suffix in ('-wal', '-shm', '-journal'):
            sidecar = Path(str(target) + suffix)
            if os.path.lexists(sidecar):
                _existing_file(sidecar)
                sidecars.append({'source': sidecar.name, 'saved': sidecar.name + '.before-restore-' + token})
        payload = {'version': 1, 'database': target.name, 'original_hash': _file_hash(target),
                   'prepared_hash': _file_hash(prepared), 'original_identity': list(file_identity(target)),
                   'prepared_identity': list(file_identity(prepared)), 'logical_rollback': rollback.name,
                   'native_original': native_original.name, 'prepared': prepared.name, 'sidecars': sidecars}
        _write_recovery_marker(target, payload)
        marker_created = True
        for item in sidecars:
            move_no_replace(target.parent / item['source'], target.parent / item['saved'])
        # Last native open-handle check before publishing. Every own SQLite
        # connection and probe is closed; a false result is not assumed harmless.
        _restore_probe(target)
        try:
            replace_file(target, prepared, native_original)
        except OSError:
            recovered = _recover_restore_locked(target)
            if recovered['status'] == 'restored_paused':
                return {**recovered, **counts, **safety_counts, 'native_error_recovered': True}
            raise RuntimeError('Restore replacement failed; original database is verified and preserved; rollback backup retained') from None
        result = _recover_restore_locked(target)
        return {**result, **counts, **safety_counts}
    finally:
        # Never erase recovery evidence after a partial replacement/crash.
        if not marker_created and not os.path.lexists(_recovery_marker(target)) and prepared.exists():
            prepared.unlink()


def restore_database(db_path, backup, offline_confirmed=False):
    if offline_confirmed is not True:
        raise ValueError("Explicit offline confirmation is required after stopping the service and all database tools")
    source = _existing_file(backup)
    target = canonical_database_path(db_path)
    if source == target:
        raise ValueError("Restore source and destination must be different files")
    verify_database(source)
    with _offline_ownership(target) as target:
        _refuse_pending_recovery(target)
        if os.name == 'nt':
            return _restore_windows_locked(target, source)
        # Refuse an external writer. Other external readers must be stopped by
        # the operator; SQLite/WAL cannot reliably inventory all readers.
        probe = sqlite3.connect(target, timeout=0)
        try:
            probe.execute("BEGIN EXCLUSIVE")
            probe.rollback()
        except sqlite3.OperationalError:
            raise RuntimeError("Database is busy; stop all processes using it before restoration") from None
        finally:
            probe.close()
        rollback = target.with_name(target.name + ".rollback-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"))
        _backup_database_owned(target, rollback, _backup_deadline(5))
        fd, temp_name = tempfile.mkstemp(prefix=".sqlite-restore-", suffix=".db", dir=target.parent)
        os.close(fd)
        temp = Path(temp_name)
        try:
            with closing(_read_connection(source)) as src, closing(_read_connection(target)) as current, closing(sqlite3.connect(temp)) as dst:
                src.backup(dst)
                safety_counts = _merge_safety_metadata(current, dst)
                counts = _freeze_restored_tasks(dst)
                dst.commit()
            verify_database(temp)
            with temp.open("rb") as handle:
                os.fsync(handle.fileno())
            # The service and its workers must be stopped. Remove old journal
            # sidecars so they cannot be applied to the restored main database.
            for suffix in ("-wal", "-shm", "-journal"):
                Path(str(target) + suffix).unlink(missing_ok=True)
            os.replace(temp, target)
            _fsync_directory(target.parent)
        finally:
            temp.unlink(missing_ok=True)
    return {"status": "restored_paused", "rollback_backup": str(rollback), "manual_safety_review_required": True, **counts, **safety_counts}


def acknowledge_restore(db_path, offline_confirmed=False, review_confirmed=False):
    if offline_confirmed is not True or review_confirmed is not True:
        raise ValueError("Offline confirmation and post-backup send/opt-out review confirmation are required")
    target = canonical_database_path(db_path)
    with _offline_connection(target) as (target, db):
        row = db.execute("SELECT value FROM settings WHERE key='restore_review_required'").fetchone()
        if not row or row[0] != "true":
            raise ValueError("No pending restore review exists")
        with db:
            db.execute("UPDATE settings SET value='false' WHERE key='restore_review_required'")
            db.execute("INSERT INTO settings(key,value) VALUES ('outbox_paused','true') ON CONFLICT(key) DO UPDATE SET value=excluded.value")
            ensure_privacy_schema(db)
            db.execute("INSERT INTO privacy_actions(action,counts,created_at) VALUES ('restore_review_acknowledged',?,?)", (json.dumps({"operator_confirmed_post_backup_sends_and_optouts": True, "outbox_remains_paused": True}), _stamp()))
    return {"status": "restore_review_acknowledged", "outbox_remains_paused": True}


def _retain_suppression(db, key, contact):
    local = contact.get("consent_status") == "revoked"
    carrier = bool(contact.get("carrier_blocked"))
    existing = suppression_for(db, key, contact["phone"])
    local = local or existing in {"local_opt_out", "local_and_carrier_opt_out"}
    carrier = carrier or "carrier" in existing
    if local or carrier:
        reason = "local_and_carrier_opt_out" if local and carrier else "carrier_opt_out" if carrier else "local_opt_out"
        db.execute("INSERT INTO privacy_suppressions(phone_hash,reason,created_at) VALUES (?,?,?) ON CONFLICT(phone_hash) DO UPDATE SET reason=excluded.reason", (privacy_hash(key, contact["phone"]), reason, _stamp()))


def _tombstone_events(db, rows):
    for row in rows:
        db.execute("INSERT OR IGNORE INTO privacy_dedup(event_hash,created_at) VALUES (?,?)", (event_hash(row[0]), _stamp()))


def _invalidate_reply_sources(db, event_ids):
    """Deleting source evidence also withdraws derived service permissions."""
    counts = {'reply_permissions_cleared': 0, 'reply_queues_canceled': 0}
    contact_columns = _columns(db, 'contacts')
    outbox_columns = _columns(db, 'outbox')
    for source_id in set(event_ids):
        if not source_id:
            continue
        if 'reply_permission_event_id' in contact_columns:
            fields = ["reply_permission_event_id=''", 'reply_permission_until=NULL']
            if 'reply_permission_basis' in contact_columns:
                fields.append("reply_permission_basis=''")
            if 'last_service_inquiry_at' in contact_columns:
                fields.append('last_service_inquiry_at=NULL')
            counts['reply_permissions_cleared'] += db.execute(
                'UPDATE contacts SET ' + ','.join(fields) + ' WHERE reply_permission_event_id=?',
                (source_id,)).rowcount
        if 'permission_source_event_id' not in outbox_columns:
            continue
        not_started = ("submission_started_at=''" if 'submission_started_at' in outbox_columns
                       else 'NOT EXISTS (SELECT 1 FROM outbox_attempts a WHERE a.outbox_id=outbox.id)')
        pending = db.execute("SELECT id,message_id FROM outbox WHERE permission_source_event_id=? AND "
                             "(status='queued' OR (status='submitting' AND " + not_started + '))',
                             (source_id,)).fetchall()
        for queue_id, message_id in pending:
            db.execute("UPDATE outbox SET status='canceled',error_code='REPLY_SOURCE_REMOVED',updated_at=? WHERE id=?",
                       (_stamp(), queue_id))
            db.execute("UPDATE messages SET status='canceled',error_code='REPLY_SOURCE_REMOVED' WHERE id=?", (message_id,))
        counts['reply_queues_canceled'] += len(pending)
        fields = ["permission_source_event_id=''"]
        for column in ('permission_basis', 'permission_until'):
            if column in outbox_columns:
                fields.append(column + "=''")
        db.execute('UPDATE outbox SET ' + ','.join(fields) + ' WHERE permission_source_event_id=?', (source_id,))
    return counts


def _purge_outbox(db, message_ids):
    if not message_ids or "outbox" not in _tables(db):
        return {"queued_canceled": 0, "ambiguous_removed": 0}
    counts = {"queued_canceled": 0, "ambiguous_removed": 0}
    for mid in message_ids:
        rows = db.execute("SELECT id,status FROM outbox WHERE message_id=?", (mid,)).fetchall()
        for queue_id, status in rows:
            counts["queued_canceled"] += int(status == "queued")
            counts["ambiguous_removed"] += int(status in {"submitting", "uncertain", "accepted", "sending", "sent"})
            for table in ("outbox_callbacks", "outbox_attempts"):
                if table in _tables(db):
                    db.execute(f"DELETE FROM {table} WHERE outbox_id=?", (queue_id,))
            db.execute("DELETE FROM outbox WHERE id=?", (queue_id,))
    return counts


def _purge_inbox_for_phone(db, phone):
    columns = _columns(db, "provider_inbox")
    # Schema versions use phone; no user-controlled identifier enters SQL.
    column = next((c for c in ("phone", "from_phone", "from_number") if c in columns), None)
    if not column:
        return 0
    if "sid" in columns:
        rows = db.execute(f"SELECT sid FROM provider_inbox WHERE {column}=?", (phone,)).fetchall()
        _invalidate_reply_sources(db, ['twilio:' + r[0] for r in rows])
        _tombstone_events(db, [("twilio:" + r[0],) for r in rows])
        for row in rows:
            db.execute("DELETE FROM audit WHERE event_id=?", ("twilio:" + row[0],))
        if "inbox_tombstones" in _tables(db):
            for row in rows:
                db.execute("INSERT OR IGNORE INTO inbox_tombstones(sid_hash,created_at) VALUES (?,?)", (event_hash(row[0]), _stamp()))
    elif "event_id" in columns:
        rows = db.execute(f"SELECT event_id FROM provider_inbox WHERE {column}=?", (phone,)).fetchall()
        _invalidate_reply_sources(db, [r[0] for r in rows])
        _tombstone_events(db, rows)
    return db.execute(f"DELETE FROM provider_inbox WHERE {column}=?", (phone,)).rowcount


def _delete_contact(db, phone, key):
    row = db.execute("SELECT * FROM contacts WHERE phone=?", (phone,)).fetchone()
    if row is None:
        # A signed envelope may be durable before an Engine contact exists.
        return {"contacts_deleted": 0, "queued_canceled": 0, "ambiguous_removed": 0, "inbox_deleted": _purge_inbox_for_phone(db, phone)}
    contact = dict(zip((c[1] for c in db.execute("PRAGMA table_info(contacts)")), row))
    _retain_suppression(db, key, contact)
    events = db.execute("SELECT event_id FROM events WHERE phone=? UNION SELECT event_id FROM messages WHERE phone=? AND event_id IS NOT NULL", (phone, phone)).fetchall()
    _invalidate_reply_sources(db, [r[0] for r in events])
    _tombstone_events(db, events)
    for event in events:
        db.execute("DELETE FROM audit WHERE event_id=?", (event[0],))
    mids = [r[0] for r in db.execute("SELECT id FROM messages WHERE phone=?", (phone,))]
    counts = _purge_outbox(db, mids)
    counts["inbox_deleted"] = _purge_inbox_for_phone(db, phone)
    for table in ("messages", "appointments", "consent_history", "events", "audit"):
        db.execute(f"DELETE FROM {table} WHERE phone=?", (phone,))
    db.execute("DELETE FROM contacts WHERE phone=?", (phone,))
    counts["contacts_deleted"] = 1
    return counts


def _scrub_database_files(db):
    # Removes freed content from the current DB/WAL; not old backups, disk
    # snapshots, provider records or SSD wear-leveling copies.
    try:
        for step in ('PRAGMA wal_checkpoint(TRUNCATE)', 'VACUUM', 'PRAGMA wal_checkpoint(TRUNCATE)'):
            row = db.execute(step).fetchone()
            if step.startswith('PRAGMA') and row and row[0] != 0:
                raise sqlite3.OperationalError('checkpoint busy')
    except sqlite3.OperationalError:
        raise RuntimeError('Privacy changes committed but database compaction is incomplete; keep the service stopped and retry maintenance') from None


@contextmanager
def _offline_ownership(target):
    """One global order: service owner first, then exclusive maintenance lease."""
    from .platform_fs import maintenance_lock
    with service_lock(target) as canonical:
        with maintenance_lock(canonical, exclusive=True) as canonical:
            yield canonical


@contextmanager
def _offline_connection(target):
    with _offline_ownership(target) as canonical:
        _refuse_pending_recovery(canonical)
        if os.name == 'nt':
            _restore_probe(canonical)
        with closing(sqlite3.connect(canonical)) as db:
            yield canonical, db


def delete_contact(db_path, phone, key, offline_confirmed=False):
    from .engine import normalize_phone
    phone = normalize_phone(phone)
    privacy_hash(key, phone)
    if offline_confirmed is not True:
        raise ValueError("Stop the service and explicitly confirm offline maintenance")
    target = canonical_database_path(db_path)
    with _offline_connection(target) as (target, db):
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA secure_delete=ON")
        with db:
            ensure_privacy_schema(db)
            counts = _delete_contact(db, phone, key)
            db.execute("INSERT INTO privacy_actions(action,counts,created_at) VALUES ('delete_contact',?,?)", (json.dumps(counts), _stamp()))
        _scrub_database_files(db)
    return {"status": "deleted", **counts, "suppression_and_event_hashes_retained": True}


def run_retention(db_path, key, retention_days=30, offline_confirmed=False, now=None):
    if type(retention_days) is not int or not 1 <= retention_days <= 365:
        raise ValueError("Retention days must be an integer between 1 and 365")
    privacy_hash(key, "key-validation")
    if offline_confirmed is not True:
        raise ValueError("Stop the service and explicitly confirm offline maintenance")
    cutoff = _stamp((now or datetime.now(timezone.utc)) - timedelta(days=retention_days))
    target = canonical_database_path(db_path)
    totals = {"contacts_deleted": 0, "queued_canceled": 0, "ambiguous_removed": 0, "inbox_deleted": 0, "old_messages_deleted": 0, "old_events_deleted": 0, "booking_states_cleared": 0,
              'reply_permissions_cleared': 0, 'reply_queues_canceled': 0}
    with _offline_connection(target) as (target, db):
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA secure_delete=ON")
        with db:
            ensure_privacy_schema(db)
            old_contacts = [r[0] for r in db.execute("SELECT phone FROM contacts WHERE last_activity IS NULL OR last_activity<?", (cutoff,))]
            for phone in old_contacts:
                for field, value in _delete_contact(db, phone, key).items():
                    totals[field] += value
            # Keep active contact state, but purge old customer content.
            old_message_phones = {r[0] for r in db.execute("SELECT DISTINCT phone FROM messages WHERE created_at<?", (cutoff,))}
            if "booking_updated_at" in _columns(db, "contacts"):
                for phone, booking, updated_at in db.execute("SELECT phone,booking,booking_updated_at FROM contacts").fetchall():
                    if booking not in ("", "{}") and (updated_at and updated_at < cutoff or not updated_at and phone in old_message_phones):
                        db.execute("UPDATE contacts SET booking='{}',booking_updated_at=NULL WHERE phone=?", (phone,))
                        totals["booking_states_cleared"] += 1
            mids = [r[0] for r in db.execute("SELECT id FROM messages WHERE created_at<?", (cutoff,))]
            for field, value in _purge_outbox(db, mids).items():
                totals[field] += value
            totals["old_messages_deleted"] = db.execute("DELETE FROM messages WHERE created_at<?", (cutoff,)).rowcount
            events = db.execute("SELECT event_id FROM events WHERE received_at<?", (cutoff,)).fetchall()
            for name, count in _invalidate_reply_sources(db, [r[0] for r in events]).items():
                totals[name] += count
            _tombstone_events(db, events)
            totals["old_events_deleted"] = db.execute("DELETE FROM events WHERE received_at<?", (cutoff,)).rowcount
            old_bookings = [r[0] for r in db.execute("SELECT DISTINCT phone FROM appointments WHERE created_at<?", (cutoff,))]
            db.execute("DELETE FROM appointments WHERE created_at<?", (cutoff,))
            for phone in old_bookings:
                db.execute("UPDATE contacts SET booking='{}' WHERE phone=?", (phone,))
            db.execute("DELETE FROM consent_history WHERE occurred_at<?", (cutoff,))
            db.execute("DELETE FROM audit WHERE created_at<?", (cutoff,))
            # Unprocessed expired intake must not remain a source of late sends.
            columns = _columns(db, "provider_inbox")
            time_column = next((c for c in ("received_at", "created_at") if c in columns), None)
            if time_column:
                if "sid" in columns:
                    rows = db.execute(f"SELECT sid FROM provider_inbox WHERE {time_column}<?", (cutoff,)).fetchall()
                    for name, count in _invalidate_reply_sources(db, ['twilio:' + r[0] for r in rows]).items():
                        totals[name] += count
                    _tombstone_events(db, [("twilio:" + r[0],) for r in rows])
                    if "inbox_tombstones" in _tables(db):
                        for row in rows:
                            db.execute("INSERT OR IGNORE INTO inbox_tombstones(sid_hash,created_at) VALUES (?,?)", (event_hash(row[0]), _stamp()))
                elif "event_id" in columns:
                    rows = db.execute(f"SELECT event_id FROM provider_inbox WHERE {time_column}<?", (cutoff,)).fetchall()
                    for name, count in _invalidate_reply_sources(db, [r[0] for r in rows]).items():
                        totals[name] += count
                    _tombstone_events(db, rows)
                totals["inbox_deleted"] += db.execute(f"DELETE FROM provider_inbox WHERE {time_column}<?", (cutoff,)).rowcount
            db.execute("INSERT INTO privacy_actions(action,counts,created_at) VALUES ('retention',?,?)", (json.dumps(totals), _stamp()))
        _scrub_database_files(db)
    return {"status": "retention_completed", "retention_days": retention_days, **totals, "suppression_and_event_hashes_retained": True}
