"""Real local SQLite operations; all contacts and provider IDs are synthetic."""
import contextlib
import getpass
import io
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import manage
from reception.auth import verify_password
from reception.engine import Engine, stamp
from reception.outbox import SendPolicy
from reception.operations import (acknowledge_restore, backup_database, clear_suppression, delete_contact,
    ensure_privacy_schema, event_hash, event_was_deleted, privacy_hash,
    restore_database, run_retention, service_lock, suppression_for, verify_database,
    _freeze_restored_tasks, _invalidate_reply_sources)


LINUX = sys.platform.startswith('linux')
linux_operation = unittest.skipUnless(LINUX or os.name == 'nt', 'Local filesystem operations require supported Windows or Linux')


KEY = "synthetic-privacy-key-for-local-tests-" + "x" * 32
PHONE = "+12025550121"
SID = "SM" + "b" * 32


class OperationsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.path = self.base / "service.db"
        self.engine = Engine(self.path, production=True, seed_demo=False)

    def tearDown(self):
        if self.engine:
            self.engine.close()
        self.temp.cleanup()

    def close_engine(self):
        self.engine.close()
        self.engine = None

    def queue(self, phone=PHONE, event_id="synthetic-outgoing"):
        with self.engine._lock, self.engine.db:
            self.engine._contact(phone)
            mid = self.engine._message(phone, "Synthetic private appointment details", "outbound", "queued", event_id, "service_reply")
            queue_id = self.engine.outbox.enqueue(mid, event_id, phone, "Synthetic private appointment details", "automatic", "service_reply")
        return mid, queue_id

    def inbox_schema(self):
        self.engine.db.executescript("""
          CREATE TABLE IF NOT EXISTS provider_inbox(sid TEXT PRIMARY KEY,phone TEXT NOT NULL,recipient TEXT,body TEXT,received_at TEXT,status TEXT,error_code TEXT DEFAULT '');
          CREATE TABLE IF NOT EXISTS inbox_tombstones(sid_hash TEXT PRIMARY KEY,created_at TEXT NOT NULL);
        """)

    @linux_operation
    def test_backup_captures_committed_wal_and_verifies_permissions(self):
        self.engine.db.execute("PRAGMA journal_mode=WAL")
        self.queue()
        output = self.base / "backup.db"
        result = backup_database(self.path, output)
        self.assertEqual(result["status"], "verified_backup")
        self.assertTrue(verify_database(output))
        if LINUX:
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)
        # Windows shared backup assertions do not establish this Linux mode bit.
        # Real Windows DACL/access evidence lives in test_windows_operations.
        with contextlib.closing(sqlite3.connect(output)) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM outbox").fetchone()[0], 1)

    @linux_operation
    def test_backup_never_clobbers_existing_file(self):
        output = self.base / "existing.db"
        output.write_bytes(b"preserve me")
        with self.assertRaises(ValueError):
            backup_database(self.path, output)
        self.assertEqual(output.read_bytes(), b"preserve me")
        with self.assertRaises(ValueError):
            backup_database(self.path, self.path)

    @linux_operation
    def test_backup_rejects_symlinks_and_corrupt_foreign_keys(self):
        with self.subTest(capability='file_symlink'):
            linked = self.base / "linked.db"
            try:
                linked.symlink_to(self.path)
            except OSError as exc:
                if os.name == 'nt' and getattr(exc, 'winerror', None) == 1314:
                    self.skipTest('Windows symlink privilege unavailable; no Developer Mode or ACL changes authorized')
                raise
            with self.assertRaises(ValueError):
                backup_database(linked, self.base / "new.db")
        self.engine.db.execute("PRAGMA foreign_keys=OFF")
        self.engine.db.execute("INSERT INTO messages(phone,direction,text,status,created_at) VALUES ('+12025550999','inbound','bad synthetic foreign key','received',?)", (stamp(),))
        self.engine.db.commit()
        with self.assertRaises(ValueError):
            backup_database(self.path, self.base / "bad-backup.db")

    @linux_operation
    def test_restore_requires_offline_confirmation_and_refuses_service_lock(self):
        backup = self.base / "backup.db"
        backup_database(self.path, backup)
        with self.assertRaises(ValueError):
            restore_database(self.path, backup)
        self.close_engine()
        with service_lock(self.path):
            with self.assertRaises(RuntimeError):
                restore_database(self.path, backup, True)

    @linux_operation
    def test_restore_refuses_external_database_writer(self):
        backup = self.base / "backup.db"
        backup_database(self.path, backup)
        self.close_engine()
        writer = sqlite3.connect(self.path)
        try:
            writer.execute("BEGIN EXCLUSIVE")
            with self.assertRaises(RuntimeError):
                restore_database(self.path, backup, True)
        finally:
            writer.rollback()
            writer.close()

    @linux_operation
    def test_restore_preserves_rollback_and_freezes_all_pending_work(self):
        _, queued = self.queue()
        _, submitting = self.queue(event_id="synthetic-second-outgoing")
        self.engine.db.execute("UPDATE outbox SET status='submitting' WHERE id=?", (submitting,))
        self.inbox_schema()
        self.engine.db.execute("INSERT INTO provider_inbox(sid,phone,body,received_at,status) VALUES (?,?,?,?,?)", (SID, PHONE, "Synthetic inquiry", stamp(), "verifying"))
        self.engine.db.commit()
        backup = self.base / "backup.db"
        backup_database(self.path, backup)
        self.engine.db.execute("INSERT INTO settings(key,value) VALUES ('post_backup_marker','exists')")
        self.engine.db.commit()
        self.close_engine()
        result = restore_database(self.path, backup, True)
        self.assertEqual(result["queued_canceled"], 1)
        self.assertEqual(result["submitting_uncertain"], 1)
        self.assertEqual(result["inbox_frozen"], 1)
        self.assertTrue(verify_database(self.path))
        with contextlib.closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute("SELECT value FROM settings WHERE key='outbox_paused'").fetchone()[0], "true")
            self.assertEqual(db.execute("SELECT value FROM settings WHERE key='restore_review_required'").fetchone()[0], "true")
            self.assertEqual(db.execute("SELECT status,error_code FROM outbox WHERE id=?", (queued,)).fetchone(), ("canceled", "RESTORE_REQUIRES_REVIEW"))
            self.assertEqual(db.execute("SELECT status FROM outbox WHERE id=?", (submitting,)).fetchone()[0], "uncertain")
            self.assertEqual(db.execute("SELECT status FROM provider_inbox").fetchone()[0], "rejected")
            self.assertIsNone(db.execute("SELECT 1 FROM settings WHERE key='post_backup_marker'").fetchone())
        with contextlib.closing(sqlite3.connect(result["rollback_backup"])) as db:
            self.assertIsNotNone(db.execute("SELECT 1 FROM settings WHERE key='post_backup_marker'").fetchone())

    @linux_operation
    def test_restore_preserves_later_stop_and_replay_hashes_without_current_pii(self):
        backup = self.base / "before-stop.db"
        backup_database(self.path, backup)
        with self.engine.db:
            ensure_privacy_schema(self.engine.db)
            self.engine.db.execute("INSERT INTO privacy_suppressions VALUES (?,?,?)", (privacy_hash(KEY, PHONE), "local_opt_out", stamp()))
            self.engine.db.execute("INSERT INTO privacy_dedup VALUES (?,?)", (event_hash("twilio:" + SID), stamp()))
            self.engine.db.execute("CREATE TABLE inbox_tombstones(sid_hash TEXT PRIMARY KEY,created_at TEXT NOT NULL)")
            self.engine.db.execute("INSERT INTO inbox_tombstones VALUES (?,?)", (event_hash(SID), stamp()))
            self.engine._contact(PHONE)
            self.engine._message(PHONE, "private current customer content", "inbound", "received")
            self.engine.db.execute("INSERT INTO events(event_id,kind,phone,occurred_at,received_at,origin,status,result) VALUES ('newer-manual-event','sms',?,?,?,'twilio','processed','{}')", (PHONE, stamp(), stamp()))
        self.close_engine()
        result = restore_database(self.path, backup, True)
        self.assertEqual(result["suppression_hashes_merged"], 1)
        with contextlib.closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(suppression_for(db, KEY, PHONE), "local_opt_out")
            self.assertTrue(event_was_deleted(db, KEY, "twilio:" + SID))
            self.assertTrue(event_was_deleted(db, KEY, "newer-manual-event"))
            self.assertIsNotNone(db.execute("SELECT 1 FROM inbox_tombstones WHERE sid_hash=?", (event_hash(SID),)).fetchone())
            self.assertIsNone(db.execute("SELECT 1 FROM contacts WHERE phone=?", (PHONE,)).fetchone())
        self.assertNotIn(b"private current customer content", self.path.read_bytes())

    @linux_operation
    def test_restore_keeps_latest_stop_time_and_rejects_start_between_stops(self):
        base = datetime.now(timezone.utc)
        old_stop_at = base - timedelta(minutes=3)
        latest_stop_at = base - timedelta(minutes=1)
        intermediate_start_at = base - timedelta(minutes=2)
        self.engine.privacy_key = KEY
        self.engine.send_policy = SendPolicy(optout_reviewed=True)
        def incoming(text, suffix, occurred):
            return {"kind": "sms", "phone": PHONE, "text": text,
                    "event_id": "twilio:SM" + suffix * 32, "origin": "twilio", "verified": True,
                    "occurred_at": stamp(occurred), "provider_fresh": True,
                    "provider_keyword_verified": True, "provider_managed_optout": True}
        with patch("reception.engine.now_utc", return_value=old_stop_at):
            self.engine.process_event(incoming("STOP", "c", old_stop_at))
        backup = self.base / "old-stop-backup.db"
        backup_database(self.path, backup)
        with patch("reception.engine.now_utc", return_value=latest_stop_at):
            self.engine.process_event(incoming("STOP", "d", latest_stop_at))
        self.close_engine()
        restore_database(self.path, backup, True)
        self.engine = Engine(self.path, production=True, seed_demo=False,
                             privacy_key=KEY, send_policy=SendPolicy(optout_reviewed=True))
        with self.engine._lock:
            stored = self.engine.db.execute("SELECT created_at FROM privacy_suppressions WHERE phone_hash=?", (privacy_hash(KEY, PHONE),)).fetchone()[0]
        self.assertEqual(stored, stamp(latest_stop_at))
        result = self.engine.process_event(incoming("START", "e", intermediate_start_at))
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["reason"], "start_predates_applied_opt_out")
        self.assertEqual(self.engine._contact(PHONE)["consent_status"], "revoked")
        self.assertEqual(suppression_for(self.engine.db, KEY, PHONE), "local_opt_out")

    @linux_operation
    def test_restore_keeps_anonymous_spend_and_attempt_ledgers(self):
        self.engine.db.executescript("""
          CREATE TABLE IF NOT EXISTS outbox_quota_reservations(reservation_key TEXT PRIMARY KEY,day_utc TEXT NOT NULL,segments INTEGER NOT NULL,created_at TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS outbox_quota_attempts(attempt_key TEXT PRIMARY KEY,started_at TEXT NOT NULL);
        """)
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        existing = event_hash("synthetic-existing-reservation")
        newer = event_hash("synthetic-newer-reservation")
        attempt = event_hash("synthetic-newer-attempt")
        self.engine.db.execute("INSERT INTO outbox_quota_reservations VALUES (?,?,?,?)", (existing, today, 1, stamp()))
        self.engine.db.commit()
        backup = self.base / "before-new-spend.db"
        backup_database(self.path, backup)
        self.engine.db.execute("UPDATE outbox_quota_reservations SET segments=2 WHERE reservation_key=?", (existing,))
        self.engine.db.execute("INSERT INTO outbox_quota_reservations VALUES (?,?,?,?)", (newer, today, 3, stamp()))
        self.engine.db.execute("INSERT INTO outbox_quota_attempts VALUES (?,?)", (attempt, stamp()))
        self.engine.db.commit()
        self.close_engine()
        restore_database(self.path, backup, True)
        with contextlib.closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute("SELECT sum(segments) FROM outbox_quota_reservations").fetchone()[0], 5)
            self.assertEqual(db.execute("SELECT count(*) FROM outbox_quota_attempts").fetchone()[0], 1)

    @linux_operation
    def test_restore_rejects_malformed_safety_metadata(self):
        backup = self.base / "before-malformed.db"
        backup_database(self.path, backup)
        with self.engine.db:
            ensure_privacy_schema(self.engine.db)
            self.engine.db.execute("INSERT INTO privacy_suppressions VALUES (?,?,?)", (PHONE, "local_opt_out", stamp()))
        self.close_engine()
        original = self.path.read_bytes()
        with self.assertRaises(RuntimeError):
            restore_database(self.path, backup, True)
        self.assertEqual(original, self.path.read_bytes())

    @linux_operation
    def test_restore_acknowledgment_requires_explicit_review_and_leaves_paused(self):
        backup = self.base / "backup.db"
        backup_database(self.path, backup)
        self.close_engine()
        restore_database(self.path, backup, True)
        with self.assertRaises(ValueError):
            acknowledge_restore(self.path, True, False)
        result = acknowledge_restore(self.path, True, True)
        self.assertTrue(result["outbox_remains_paused"])
        with contextlib.closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute("SELECT value FROM settings WHERE key='outbox_paused'").fetchone()[0], "true")
            self.assertEqual(db.execute("SELECT value FROM settings WHERE key='restore_review_required'").fetchone()[0], "false")

    @linux_operation
    def test_invalid_backup_does_not_modify_target(self):
        corrupt = self.base / "corrupt.db"
        corrupt.write_bytes(b"not sqlite")
        original = self.path.read_bytes()
        with self.assertRaises(sqlite3.DatabaseError):
            restore_database(self.path, corrupt, True)
        self.assertEqual(self.path.read_bytes(), original)

    def test_phone_hmac_and_event_hash_helpers(self):
        self.assertNotEqual(privacy_hash(KEY, PHONE), privacy_hash(KEY + "other", PHONE))
        self.assertEqual(len(privacy_hash(KEY, PHONE)), 64)
        with self.assertRaises(ValueError):
            privacy_hash("short", PHONE)
        ensure_privacy_schema(self.engine.db)
        self.engine.db.execute("INSERT INTO privacy_dedup(event_hash,created_at) VALUES (?,?)", (event_hash("synthetic-id"), stamp()))
        self.assertTrue(event_was_deleted(self.engine.db, KEY, "synthetic-id"))
        self.assertFalse(event_was_deleted(self.engine.db, KEY, "other-id"))

    @linux_operation
    def test_delete_removes_content_and_queue_but_keeps_stop_and_dedup(self):
        self.engine.process_event({"kind": "sms", "phone": PHONE, "text": "STOP", "event_id": "twilio:" + SID, "origin": "twilio", "verified": True})
        self.queue()
        self.engine.db.execute("UPDATE contacts SET carrier_blocked=1 WHERE phone=?", (PHONE,))
        self.inbox_schema()
        self.engine.db.execute("INSERT INTO provider_inbox(sid,phone,body,received_at,status) VALUES (?,?,?,?,?)", (SID, PHONE, "STOP", stamp(), "processed"))
        self.engine.db.commit()
        self.close_engine()
        result = delete_contact(self.path, PHONE, KEY, True)
        self.assertEqual(result["queued_canceled"], 1)
        with contextlib.closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM contacts").fetchone()[0], 0)
            for table in ("messages", "outbox", "consent_history", "events", "provider_inbox"):
                self.assertEqual(db.execute("SELECT count(*) FROM " + table).fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT count(*) FROM audit WHERE phone=?", (PHONE,)).fetchone()[0], 0)
            self.assertEqual(suppression_for(db, KEY, PHONE), "local_and_carrier_opt_out")
            self.assertTrue(event_was_deleted(db, KEY, "twilio:" + SID))
            self.assertEqual(db.execute("SELECT count(*) FROM inbox_tombstones").fetchone()[0], 1)
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])
        self.assertNotIn(PHONE.encode(), self.path.read_bytes())
        self.assertNotIn(b"Synthetic private appointment details", self.path.read_bytes())

    @linux_operation
    def test_delete_unprocessed_inbox_without_contact(self):
        self.inbox_schema()
        self.engine.db.execute("INSERT INTO provider_inbox(sid,phone,body,received_at,status) VALUES (?,?,?,?,?)", (SID, PHONE, "Synthetic inquiry", stamp(), "pending"))
        self.engine.db.commit()
        self.close_engine()
        result = delete_contact(self.path, PHONE, KEY, True)
        self.assertEqual(result["contacts_deleted"], 0)
        self.assertEqual(result["inbox_deleted"], 1)
        with contextlib.closing(sqlite3.connect(self.path)) as db:
            self.assertTrue(event_was_deleted(db, KEY, "twilio:" + SID))

    def test_clear_local_suppression_keeps_carrier_block_until_verified(self):
        ensure_privacy_schema(self.engine.db)
        self.engine.db.execute("INSERT INTO privacy_suppressions VALUES (?,?,?)", (privacy_hash(KEY, PHONE), "local_and_carrier_opt_out", stamp()))
        clear_suppression(self.engine.db, KEY, PHONE)
        self.assertEqual(suppression_for(self.engine.db, KEY, PHONE), "carrier_opt_out")
        clear_suppression(self.engine.db, KEY, PHONE, include_carrier=True)
        self.assertEqual(suppression_for(self.engine.db, KEY, PHONE), "")

    def test_source_removal_withdraws_permissions_and_only_cancels_unsubmitted_rows(self):
        source = 'twilio:' + SID
        with self.engine.db:
            self.engine._contact(PHONE)
            self.engine.db.execute("UPDATE contacts SET reply_permission_event_id=?,reply_permission_basis='operator_review_human_only',reply_permission_until=? WHERE phone=?",
                                   (source, stamp(datetime.now(timezone.utc) + timedelta(hours=1)), PHONE))
        queue_ids = [self.queue(event_id='synthetic-source-reply-' + str(index))[1] for index in range(4)]
        with self.engine.db:
            for queue_id in queue_ids:
                self.engine.db.execute("UPDATE outbox SET permission_source_event_id=?,permission_basis='operator_review_human_only',permission_until=? WHERE id=?",
                                       (source, stamp(), queue_id))
            # A historical retry attempt does not mean this claim has begun.
            self.engine.db.execute("UPDATE outbox SET status='submitting',attempt_count=1,submission_started_at='' WHERE id=?", (queue_ids[1],))
            self.engine.db.execute("UPDATE outbox SET status='submitting',submission_started_at=? WHERE id=?", (stamp(), queue_ids[2]))
            self.engine.db.execute("UPDATE outbox SET status='uncertain',submission_started_at=? WHERE id=?", (stamp(), queue_ids[3]))
            counts = _invalidate_reply_sources(self.engine.db, [source])
        self.assertEqual(counts, {'reply_permissions_cleared': 1, 'reply_queues_canceled': 2})
        contact = self.engine._contact(PHONE)
        self.assertEqual(contact['reply_permission_event_id'], '')
        self.assertIsNone(contact['reply_permission_until'])
        statuses = [self.engine.db.execute('SELECT status FROM outbox WHERE id=?', (queue_id,)).fetchone()[0] for queue_id in queue_ids]
        self.assertEqual(statuses, ['canceled', 'canceled', 'submitting', 'uncertain'])

    def test_verify_database_closes_connection_before_file_removal(self):
        disposable = self.base / 'read-only-check.db'
        with contextlib.closing(sqlite3.connect(disposable)) as db:
            db.execute('CREATE TABLE disposable(value TEXT)')
            db.commit()
        self.assertTrue(verify_database(disposable))
        disposable.unlink()
        self.assertFalse(disposable.exists())

    def test_freeze_without_settings_preserves_both_restore_gates_after_engine_initialization(self):
        prepared_path = self.base / 'prepared-empty-restore.db'
        with contextlib.closing(sqlite3.connect(':memory:')) as memory:
            _freeze_restored_tasks(memory)
            self.assertEqual(dict(memory.execute('SELECT key,value FROM settings')),
                             {'outbox_paused': 'true', 'restore_review_required': 'true'})
            memory.commit()
            with contextlib.closing(sqlite3.connect(prepared_path)) as prepared:
                memory.backup(prepared)
        rebuilt = Engine(prepared_path, production=True, seed_demo=False)
        try:
            self.assertTrue(rebuilt.outbox.restore_review_required())
            self.assertTrue(rebuilt.outbox.is_paused())
            self.assertEqual(rebuilt.outbox.run_once(None)['reason'], 'restore_review_required')
        finally:
            rebuilt.close()

    def test_unsupported_platform_operations_fail_closed(self):
        # Policy-only portability check; it is not evidence of a native lock.
        with patch('reception.operations.os.name', 'unsupported'), patch('reception.operations.sys.platform', 'unsupported'):
            with self.assertRaisesRegex(RuntimeError, 'supported Windows or Linux'):
                with service_lock(self.path):
                    self.fail('Unsupported platforms cannot acquire service ownership')
            with self.assertRaisesRegex(RuntimeError, 'supported Windows or Linux'):
                backup_database(self.path, self.base / 'unsupported-backup.db')

    @linux_operation
    def test_retention_purges_old_inactive_and_active_content_preserving_tombstones(self):
        self.engine.process_event({"kind": "sms", "phone": PHONE, "text": "STOP", "event_id": "old-stop", "origin": "twilio", "verified": True})
        old = stamp(datetime.now(timezone.utc) - timedelta(days=40))
        self.engine.db.execute("UPDATE contacts SET last_activity=? WHERE phone=?", (old, PHONE))
        active = "+12025550122"
        with self.engine.db:
            self.engine._contact(active)
            self.engine._message(active, "old private synthetic content", "inbound", "received", "old-active-event", at=old)
            self.engine._message(active, "current synthetic inquiry", "inbound", "received", "current-event")
            self.engine.db.execute("INSERT INTO events(event_id,kind,phone,occurred_at,received_at,origin,status,result) VALUES ('old-active-event','sms',?,?,?,'twilio','processed','{}')", (active, old, old))
        self.close_engine()
        result = run_retention(self.path, KEY, 30, True)
        self.assertEqual(result["contacts_deleted"], 1)
        self.assertEqual(result["old_messages_deleted"], 1)
        with contextlib.closing(sqlite3.connect(self.path)) as db:
            self.assertIsNotNone(db.execute("SELECT 1 FROM contacts WHERE phone=?", (active,)).fetchone())
            self.assertEqual(db.execute("SELECT text FROM messages").fetchall(), [("current synthetic inquiry",)])
            self.assertTrue(event_was_deleted(db, KEY, "old-active-event"))
            self.assertEqual(suppression_for(db, KEY, PHONE), "local_opt_out")

    @linux_operation
    def test_retention_clears_old_unfinished_booking_for_still_active_customer(self):
        old = stamp(datetime.now(timezone.utc) - timedelta(days=40))
        columns = {r[1] for r in self.engine.db.execute("PRAGMA table_info(contacts)")}
        if "booking_updated_at" not in columns:
            self.engine.db.execute("ALTER TABLE contacts ADD COLUMN booking_updated_at TEXT")
        with self.engine.db:
            self.engine._contact(PHONE)
            self.engine.db.execute("UPDATE contacts SET booking=?,booking_updated_at=? WHERE phone=?", (json.dumps({"contact": "old-private-synthetic@example.invalid"}), old, PHONE))
        self.close_engine()
        result = run_retention(self.path, KEY, 30, True)
        self.assertEqual(result["booking_states_cleared"], 1)
        with contextlib.closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute("SELECT booking FROM contacts WHERE phone=?", (PHONE,)).fetchone()[0], "{}")
        self.assertNotIn(b"old-private-synthetic@example.invalid", self.path.read_bytes())

    @linux_operation
    def test_retention_removing_inquiry_source_withdraws_current_queue_permission(self):
        old = stamp(datetime.now(timezone.utc) - timedelta(days=40))
        source = 'twilio:' + SID
        with self.engine.db:
            self.engine._contact(PHONE)
            self.engine._message(PHONE, 'Expired synthetic inquiry', 'inbound', 'received', source, at=old)
            self.engine.db.execute("INSERT INTO events VALUES (?, 'sms', ?, ?, ?, 'twilio', 'processed', '{}')",
                                   (source, PHONE, old, old))
            self.engine.db.execute("UPDATE contacts SET reply_permission_event_id=?,reply_permission_basis='rules',reply_permission_until=? WHERE phone=?",
                                   (source, stamp(datetime.now(timezone.utc) + timedelta(hours=1)), PHONE))
        _, queue_id = self.queue(event_id='synthetic-current-reply')
        self.close_engine()
        result = run_retention(self.path, KEY, 30, True)
        self.assertEqual(result['reply_permissions_cleared'], 1)
        self.assertEqual(result['reply_queues_canceled'], 1)
        with contextlib.closing(sqlite3.connect(self.path)) as db:
            contact = db.execute('SELECT reply_permission_event_id,reply_permission_until FROM contacts WHERE phone=?', (PHONE,)).fetchone()
            self.assertEqual(contact, ('', None))
            self.assertEqual(db.execute('SELECT status FROM outbox WHERE id=?', (queue_id,)).fetchone()[0], 'canceled')
            self.assertIsNone(db.execute('SELECT 1 FROM events WHERE event_id=?', (source,)).fetchone())

    @linux_operation
    def test_privacy_commands_fail_closed_without_key_offline_or_lock(self):
        self.close_engine()
        with self.assertRaises(ValueError):
            delete_contact(self.path, PHONE, "", True)
        with self.assertRaises(ValueError):
            run_retention(self.path, KEY, 30)
        with self.assertRaises(ValueError):
            run_retention(self.path, KEY, 0, True)
        with service_lock(self.path):
            with self.assertRaises(RuntimeError):
                delete_contact(self.path, PHONE, KEY, True)

    def test_password_cli_uses_private_prompt_and_outputs_hash_only(self):
        output = io.StringIO()
        password = "a sufficiently long private test password"
        with patch.object(getpass, "getpass", side_effect=[password, password]), contextlib.redirect_stdout(output):
            self.assertEqual(manage.main(["password-hash"]), 0)
        self.assertNotIn(password, output.getvalue())
        self.assertTrue(verify_password(password, output.getvalue().strip()))

    @linux_operation
    def test_delete_cli_does_not_accept_or_print_phone_parameter(self):
        self.close_engine()
        output = io.StringIO()
        with patch.object(getpass, "getpass", return_value=PHONE), patch.dict(os.environ, {"PRIVACY_HMAC_KEY": KEY}), contextlib.redirect_stdout(output):
            self.assertEqual(manage.main(["delete-contact", "--db", str(self.path), "--offline-confirmed"]), 0)
        self.assertNotIn(PHONE, output.getvalue())
        self.assertNotIn(KEY, output.getvalue())


if __name__ == "__main__":
    unittest.main()
