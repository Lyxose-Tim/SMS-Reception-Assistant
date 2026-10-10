"""Durable outbound boundary. Enqueue is transactional; delivery is a worker.

STOP acceptance and transport submission share a per-contact barrier. A STOP
committed first prevents a new submission; an already-started request cannot be
recalled. Unknown transport outcomes never automatically retry.
"""
from __future__ import annotations

import hashlib
import hmac
import math
import re
import secrets
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode, urlsplit


GSM_BASIC = set("@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà")
GSM_EXT = set("\f^{}\\[~]|€")
TERMINAL = {"delivered", "undelivered", "failed", "canceled", "simulated"}
PROVIDER_STATUS = {"queued", "accepted", "sending", "sent", "delivered", "undelivered", "failed"}
PROGRESS = {"queued": 0, "submitting": 1, "accepted": 2, "sending": 3, "sent": 4, "delivered": 5}


def utcnow():
    return datetime.now(timezone.utc)


def iso(dt=None):
    return (dt or utcnow()).isoformat(timespec="microseconds").replace("+00:00", "Z")


def sms_segments(body):
    """NFC and CRLF normalization; GSM extension costs two septets.

    Non-GSM text uses UTF-16 code units (astral characters cost two). This is a
    conservative preflight count, not a billing guarantee; Smart Encoding is off.
    """
    if not isinstance(body, str):
        raise ValueError("Message body must be text")
    body = unicodedata.normalize("NFC", body.replace("\r\n", "\n").replace("\r", "\n"))
    if not body.strip() or len(body) > 1600 or any(ord(c) < 32 and c not in "\n\t\f" for c in body):
        raise ValueError("Message body must be 1–1600 characters without unsupported controls")
    if all(c in GSM_BASIC or c in GSM_EXT for c in body):
        units = sum(2 if c in GSM_EXT else 1 for c in body)
        encoding = "GSM-7"
        segments = 1 if units <= 160 else math.ceil(units / 153)
    else:
        units = len(body.encode("utf-16-be")) // 2
        encoding = "UCS-2"
        segments = 1 if units <= 70 else math.ceil(units / 67)
    return {"body": body, "encoding": encoding, "units": units, "segments": segments}


@dataclass(frozen=True)
class SendPolicy:
    live_enabled: bool = False
    sender_number: str = ""
    public_base_url: str = ""
    allowed_recipients: tuple = ()
    recipient_policy: str = "test_allowlist"
    inbound_service_approval_recorded: bool = False
    max_messages_per_minute: int = 5
    max_messages_per_day: int = 40
    max_segments_per_message: int = 3
    max_daily_segments: int = 80
    reply_window_hours: float = 24
    emergency_stop_file: str = ""
    optout_profile: str = "standard"
    optout_reviewed: bool = False

    def __post_init__(self):
        for field in ("live_enabled", "optout_reviewed", "inbound_service_approval_recorded"):
            if not isinstance(getattr(self, field), bool):
                raise ValueError(f"{field} must be boolean")
        for field in ("max_messages_per_minute", "max_messages_per_day", "max_segments_per_message", "max_daily_segments"):
            value = getattr(self, field)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{field} must be positive integer")
        if isinstance(self.reply_window_hours, bool) or not isinstance(self.reply_window_hours, (int, float)) or not 0 < self.reply_window_hours <= 24:
            raise ValueError("reply_window_hours must be greater than zero and at most 24")
        if self.optout_profile not in {"standard", "advanced"}:
            raise ValueError("optout_profile must be standard or advanced")
        if self.recipient_policy not in {"test_allowlist", "inbound_service"}:
            raise ValueError("Unknown recipient policy")
        if not isinstance(self.allowed_recipients, tuple) or any(not isinstance(p, str) or not re.fullmatch(r"\+[1-9]\d{7,14}", p) for p in self.allowed_recipients):
            raise ValueError("allowed_recipients must be a tuple of E.164 numbers")


class Outbox:
    def __init__(self, engine, policy=None):
        self.engine = engine
        self.db = engine.db
        self.lock = engine._lock
        self.policy = policy or SendPolicy()
        with self.lock:
            self.db.executescript((Path(__file__).resolve().parents[1] / "migrations" / "001_outbox.sql").read_text())
            if "archive_key" not in {r[1] for r in self.db.execute("PRAGMA table_info(outbox_attempts)")}:
                self.db.execute("ALTER TABLE outbox_attempts ADD COLUMN archive_key TEXT NOT NULL DEFAULT ''")
            columns = {r[1] for r in self.db.execute("PRAGMA table_info(outbox)")}
            for field in ("permission_source_event_id", "permission_basis", "permission_until", "submission_started_at"):
                if field not in columns:
                    self.db.execute(f"ALTER TABLE outbox ADD COLUMN {field} TEXT NOT NULL DEFAULT ''")
            legacy = [dict(row) for row in self.db.execute("SELECT * FROM outbox WHERE status='queued' AND permission_source_event_id=''")]
            for row in legacy:
                self._status(row, "canceled", "LEGACY_REPLY_EVIDENCE_MISSING")
            self.db.commit()
        self._migrate_quota_history()
        self.recover()

    @staticmethod
    def _reservation_key(row, day):
        return hashlib.sha256((row["callback_nonce"] + "|" + day).encode()).hexdigest()

    def _migrate_quota_history(self):
        with self.lock, self.db:
            rows = self.db.execute("SELECT a.id,a.started_at,a.segments,a.archive_key,o.callback_nonce FROM outbox_attempts a JOIN outbox o ON o.id=a.outbox_id").fetchall()
            for row in rows:
                day = row["started_at"][:10]
                reservation = hashlib.sha256((row["callback_nonce"] + "|" + day).encode()).hexdigest()
                attempt = row["archive_key"] or hashlib.sha256((row["callback_nonce"] + "|legacy|" + str(row["id"]) + "|" + row["started_at"]).encode()).hexdigest()
                self.db.execute("INSERT OR IGNORE INTO outbox_quota_reservations VALUES (?,?,?,?)", (reservation, day, row["segments"], row["started_at"]))
                self.db.execute("INSERT OR IGNORE INTO outbox_quota_attempts VALUES (?,?)", (attempt, row["started_at"]))
                if not row["archive_key"]:
                    self.db.execute("UPDATE outbox_attempts SET archive_key=? WHERE id=?", (attempt, row["id"]))

    def _archive_attempt(self, row, at):
        day = at[:10]
        self.db.execute("INSERT OR IGNORE INTO outbox_quota_reservations VALUES (?,?,?,?)", (self._reservation_key(row, day), day, row["segments"], at))
        key = secrets.token_hex(32)
        self.db.execute("INSERT INTO outbox_quota_attempts VALUES (?,?)", (key, at))
        return key

    def enqueue(self, message_id, event_id, phone, body, origin, purpose):
        # The caller owns the same transaction as inbound-event persistence.
        data = sms_segments(body)
        contact = self.engine._contact(phone)
        cursor = self.db.execute("""INSERT INTO outbox(message_id,event_id,phone,body,origin,purpose,
          callback_nonce,encoding,segments,created_at,updated_at,permission_source_event_id,
          permission_basis,permission_until) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
          (message_id, event_id, phone, data["body"], origin, purpose, secrets.token_urlsafe(24),
           data["encoding"], data["segments"], iso(), iso(), contact.get("reply_permission_event_id", ""),
           contact.get("reply_permission_basis", ""), contact.get("reply_permission_until") or ""))
        self.db.execute("UPDATE messages SET text=?,status='queued',transport='outbox' WHERE id=?", (data["body"], message_id))
        self.engine._audit(phone, event_id, "outbound_queued", {"outbox_id": cursor.lastrowid, "message_id": message_id, "encoding": data["encoding"], "segments": data["segments"]})
        return cursor.lastrowid

    def _row(self, queue_id):
        row = self.db.execute("SELECT * FROM outbox WHERE id=?", (queue_id,)).fetchone()
        if not row:
            raise ValueError("Outbox item not found")
        return dict(row)

    def _status(self, row, status, code=""):
        self.db.execute("UPDATE outbox SET status=?,error_code=?,updated_at=? WHERE id=?", (status, code, iso(), row["id"]))
        self.db.execute("UPDATE messages SET status=?,error_code=?,provider_sid=? WHERE id=?", (status, code, row.get("provider_sid", ""), row["message_id"]))
        self.engine._audit(row["phone"], row["event_id"], "outbox_status", {"outbox_id": row["id"], "status": status, "error_code": code})

    def snapshot(self):
        with self.lock:
            items = []
            metrics = {s: 0 for s in ("queued", "submitting", "accepted", "sending", "sent", "delivered", "undelivered", "failed", "canceled", "uncertain", "simulated")}
            for source in self.db.execute("SELECT * FROM outbox ORDER BY id DESC"):
                row = dict(source)
                row.pop("callback_nonce", None)
                row["conflict"] = bool(row["conflict"])
                items.append(row)
                metrics[row["status"]] = metrics.get(row["status"], 0) + 1
            metrics["total"] = len(items)
            metrics["conflicts"] = sum(r["conflict"] for r in items)
            usage_day = utcnow().date().isoformat()
            usage = self.db.execute("SELECT count(*),coalesce(sum(segments),0) FROM outbox_quota_reservations WHERE day_utc=?", (usage_day,)).fetchone()
            return {"paused": self.is_paused(), "live_enabled": self.policy.live_enabled, "restore_review_required": self.restore_review_required(),
                    "items": items, "metrics": metrics, "counts": dict(metrics),
                    "usage": {"messages_today": usage[0], "segments_today": usage[1], "utc_day": usage_day}}

    def is_paused(self):
        setting = self.db.execute("SELECT value FROM settings WHERE key='outbox_paused'").fetchone()
        return bool(setting and setting[0] == "true")

    def restore_review_required(self):
        setting = self.db.execute("SELECT value FROM settings WHERE key='restore_review_required'").fetchone()
        return bool(setting and setting[0] in {"true", "1"})

    def pause(self, active):
        if not isinstance(active, bool):
            raise ValueError("paused must be boolean")
        with self.lock, self.db:
            self.db.execute("INSERT INTO settings(key,value) VALUES ('outbox_paused',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", ("true" if active else "false",))
            self.engine._audit(None, None, "outbox_pause_changed", {"paused": active})
        return {"status": "saved", "paused": active}

    def recover(self):
        with self.lock, self.db:
            rows = [dict(r) for r in self.db.execute("SELECT * FROM outbox WHERE status='submitting'")]
            for row in rows:
                self._status(row, "uncertain", "PROCESS_INTERRUPTED")
        return {"status": "recovered", "uncertain": len(rows)}

    def cancel_for_contact(self, phone, reason="OPT_OUT"):
        # STOP owns the contact barrier and transaction. A claim with no current
        # submission remains cancelable, including after a definite 429 retry.
        rows = [dict(r) for r in self.db.execute("SELECT * FROM outbox WHERE phone=? AND (status='queued' OR (status='submitting' AND submission_started_at=''))", (phone,))]
        for row in rows:
            self._status(row, "canceled", reason)
        return len(rows)

    def cancel(self, queue_id):
        with self.lock, self.db:
            row = self._row(queue_id)
            if row["status"] != "queued":
                return {"status": "ignored", "reason": "item_not_unsent_queued", "outbox_id": queue_id}
            self._status(row, "canceled", "OPERATOR_CANCELED")
        return {"status": "canceled", "outbox_id": queue_id}

    def _global_gate(self):
        policy = self.policy
        if getattr(self.engine, "closing", False):
            return "service_closing"
        if self.restore_review_required():
            return "restore_review_required"
        if not self.engine.production:
            return "demo_outbox_disabled"
        if not policy.live_enabled:
            return "live_sending_disabled"
        if policy.recipient_policy == "inbound_service" and not policy.inbound_service_approval_recorded:
            return "inbound_service_approval_required"
        if self.is_paused():
            return "outbox_paused"
        if policy.emergency_stop_file and Path(policy.emergency_stop_file).exists():
            return "emergency_stop_file"
        if not policy.optout_reviewed:
            return "optout_configuration_not_reviewed"
        if not re.fullmatch(r"\+[1-9]\d{7,14}", policy.sender_number):
            return "sender_not_configured"
        public = urlsplit(policy.public_base_url)
        if public.scheme != "https" or not public.hostname or public.query or public.fragment or public.username or public.password:
            return "public_callback_url_not_configured"
        return ""

    def _gate(self, row):
        reason = self._global_gate()
        if reason:
            return reason, "hold"
        if self.policy.recipient_policy == "test_allowlist" and row["phone"] not in self.policy.allowed_recipients:
            return "recipient_not_allowlisted", "cancel"
        if row["purpose"] != "service_reply":
            return "production_service_replies_only", "cancel"
        contact = self.engine._contact(row["phone"])
        if contact["consent_status"] == "revoked":
            return "opted_out", "cancel"
        if contact.get("carrier_blocked") or contact["blocked"]:
            return "carrier_or_contact_blocked", "cancel"
        if contact["handoff"] and row["origin"] == "automatic":
            return "human_takeover_active", "cancel"
        if not self.engine._business().get("sending_enabled", True):
            return "business_sending_disabled", "hold"
        if not row.get("permission_until") or row["permission_until"] < iso():
            return "service_reply_window_expired", "cancel"
        reason = self.engine._permission_gate(row["phone"], row.get("permission_source_event_id", ""),
            row.get("permission_basis", ""), row.get("permission_until", ""), human=row["origin"] == "human")
        if reason:
            return reason, "cancel"
        # A legacy/interrupted durable STOP must fail closed before recovery.
        if self.db.execute("SELECT 1 FROM sqlite_master WHERE name='provider_inbox'").fetchone():
            stops = self.db.execute("SELECT body,opt_out_type FROM provider_inbox WHERE phone=? AND status IN ('pending','verifying')", (row["phone"],)).fetchall()
            from .engine import STOP_WORDS
            if any(stop["opt_out_type"] == "STOP" or stop["body"].strip().upper() in STOP_WORDS for stop in stops):
                return "pending_stop", "cancel"
        data = sms_segments(row["body"])
        if data["segments"] > self.policy.max_segments_per_message:
            return "message_segment_limit", "cancel"
        now = utcnow()
        minute = iso(now - timedelta(minutes=1))
        day = now.date().isoformat()
        per_minute = self.db.execute("SELECT count(*) FROM outbox_quota_attempts WHERE started_at>=?", (minute,)).fetchone()[0]
        contact_attempts = self.db.execute("SELECT count(*) FROM outbox_attempts a JOIN outbox o ON o.id=a.outbox_id WHERE o.phone=? AND a.started_at>=?", (row["phone"], iso(now - timedelta(hours=1)))).fetchone()[0]
        if contact_attempts >= self.engine._business().get("max_messages_per_hour", 8):
            return "hourly_send_limit", "defer"
        totals = self.db.execute("SELECT count(*),coalesce(sum(segments),0) FROM outbox_quota_reservations WHERE day_utc=?", (day,)).fetchone()
        reserved = bool(self.db.execute("SELECT 1 FROM outbox_quota_reservations WHERE reservation_key=?", (self._reservation_key(row, day),)).fetchone())
        if per_minute >= self.policy.max_messages_per_minute:
            return "minute_message_limit", "defer"
        if not reserved and totals[0] >= self.policy.max_messages_per_day:
            return "daily_message_limit", "defer"
        if totals[1] + (0 if reserved else data["segments"]) > self.policy.max_daily_segments:
            return "daily_segment_limit", "defer"
        return "", ""

    def _carrier_block(self, row):
        self.db.execute("UPDATE contacts SET carrier_blocked=1,carrier_blocked_at=? WHERE phone=?", (iso(), row["phone"]))
        self.cancel_for_contact(row["phone"], "CARRIER_21610")
        self.engine._record_suppression(row["phone"], "carrier_opt_out")
        self.engine._audit(row["phone"], row["event_id"], "carrier_optout_block", {"error_code": "21610", "queued_replies_canceled": True})

    def _conflict(self, row):
        self.db.execute("UPDATE outbox SET conflict=1 WHERE id=?", (row["id"],))
        self._status(row, row["status"] if row["status"] in TERMINAL else "uncertain", "SID_CONFLICT")
        return {"status": "conflict", "reason": "provider_sid_mismatch", "outbox_id": row["id"]}

    def _provider_status(self, row, sid, status, code=""):
        if not isinstance(sid, str) or not re.fullmatch(r"SM[0-9a-fA-F]{32}", sid):
            if row["status"] in TERMINAL:
                return {"status": "ignored", "reason": "terminal_state_preserved", "outbox_id": row["id"], "delivery_status": row["status"]}
            self._status(row, "uncertain", "INVALID_PROVIDER_RESPONSE")
            return {"status": "uncertain", "reason": "invalid_provider_sid", "outbox_id": row["id"]}
        if row["provider_sid"] and row["provider_sid"] != sid:
            return self._conflict(row)
        other = self.db.execute("SELECT id FROM outbox WHERE provider_sid=? AND id!=?", (sid, row["id"])).fetchone()
        if other:
            return self._conflict(row)
        if status not in PROVIDER_STATUS:
            if row["status"] in TERMINAL:
                return {"status": "ignored", "reason": "terminal_state_preserved", "outbox_id": row["id"], "delivery_status": row["status"]}
            self._status(row, "uncertain", "INVALID_PROVIDER_STATUS")
            return {"status": "uncertain", "reason": "invalid_provider_status", "outbox_id": row["id"]}
        self.db.execute("UPDATE outbox SET provider_sid=? WHERE id=?", (sid, row["id"]))
        row["provider_sid"] = sid
        self.db.execute("UPDATE messages SET provider_sid=? WHERE id=?", (sid, row["message_id"]))
        if str(code) == "21610":
            self._carrier_block(row)
        status = "accepted" if status == "queued" else status
        if row["conflict"]:
            return {"status": "conflict", "reason": "frozen_sid_conflict", "outbox_id": row["id"]}
        if row["status"] in TERMINAL:
            return {"status": "ignored", "reason": "terminal_state_preserved", "outbox_id": row["id"], "delivery_status": row["status"]}
        if PROGRESS.get(status, 99) < PROGRESS.get(row["status"], -1):
            return {"status": "ignored", "reason": "status_regression", "outbox_id": row["id"]}
        self._status(row, status, str(code))
        return {"status": "updated", "outbox_id": row["id"], "delivery_status": status, "provider_sid": sid}

    def apply_callback(self, outbox_id, token, sid, status, error_code=""):
        if not isinstance(token, str) or not isinstance(error_code, str) or not re.fullmatch(r"[A-Za-z0-9_ -]{0,120}", error_code):
            raise ValueError("Invalid callback token or error code")
        with self.lock, self.db:
            row = self._row(outbox_id)
            if not hmac.compare_digest(row["callback_nonce"], token):
                raise ValueError("Callback correlation token mismatch")
            # Authentication/signature/proof belongs to the HTTP intake adapter.
            if row["attempt_count"] == 0:
                raise ValueError("Callback cannot bind an item never submitted")
            fingerprint = hashlib.sha256(f"{outbox_id}|{sid}|{status}|{error_code}".encode()).hexdigest()
            if self.db.execute("SELECT 1 FROM outbox_callbacks WHERE fingerprint=?", (fingerprint,)).fetchone():
                return {"status": "duplicate", "outbox_id": outbox_id}
            result = self._provider_status(row, sid, status, error_code)
            self.db.execute("INSERT INTO outbox_callbacks VALUES (?,?,?,?,?,?)", (fingerprint, outbox_id, sid, status, error_code, iso()))
            return result

    def run_once(self, provider):
        with self.lock, self.db:
            if getattr(provider, "phone_number", self.policy.sender_number) != self.policy.sender_number:
                return {"status": "paused", "reason": "provider_sender_mismatch"}
            global_reason = self._global_gate()
            if global_reason:
                return {"status": "paused", "reason": global_reason}
            source = self.db.execute("SELECT * FROM outbox WHERE status='queued' AND (next_attempt_at IS NULL OR next_attempt_at<=?) ORDER BY id LIMIT 1", (iso(),)).fetchone()
            if not source:
                return {"status": "idle"}
            row = dict(source)
            reason, action = self._gate(row)
            if reason:
                if action == "cancel":
                    self._status(row, "canceled", reason.upper())
                elif action == "defer":
                    self.db.execute("UPDATE outbox SET next_attempt_at=?,error_code=? WHERE id=?", (iso(utcnow() + timedelta(seconds=60)), reason.upper(), row["id"]))
                return {"status": "canceled" if action == "cancel" else "deferred" if action == "defer" else "paused", "reason": reason, "outbox_id": row["id"]}
            self._status(row, "submitting")
            self.db.execute("UPDATE outbox SET claimed_at=?,submission_started_at='' WHERE id=?", (iso(), row["id"]))
        # Selection releases the database lock before acquiring the contact
        # barrier. STOP may win here and cancel this still-unsubmitted claim.
        try:
            with self.engine.contact_barrier(row["phone"]):
                return self._submit_claim(row["id"], provider)
        except TimeoutError:
            with self.lock, self.db:
                current = self._row(row["id"])
                if current["status"] == "submitting" and not current["submission_started_at"]:
                    self._status(current, "queued", "CONTACT_BARRIER_BUSY")
            return {"status": "deferred", "reason": "contact_barrier_busy", "outbox_id": row["id"]}

    def _submit_claim(self, queue_id, provider):
        # Caller retains the contact barrier until outcome persistence. Network
        # I/O holds neither the Engine lock nor a SQLite transaction, so an early
        # signed callback can establish delivery evidence without deadlocking.
        with self.lock, self.db:
            row = self._row(queue_id)
            if row["status"] != "submitting" or row["submission_started_at"]:
                return {"status": row["status"], "reason": row["error_code"].lower(), "outbox_id": queue_id}
            reason, action = self._gate(row)
            if reason:
                self._status(row, "canceled" if action == "cancel" else "queued", reason.upper())
                return {"status": "canceled" if action == "cancel" else "paused", "reason": reason, "outbox_id": row["id"]}
            attempt_at = iso()
            self.db.execute("UPDATE outbox SET attempt_count=attempt_count+1,submission_started_at=? WHERE id=?", (attempt_at, row["id"]))
            archive_key = self._archive_attempt(row, attempt_at)
            self.db.execute("INSERT INTO outbox_attempts(outbox_id,started_at,segments,archive_key) VALUES (?,?,?,?)", (row["id"], attempt_at, row["segments"], archive_key))
            callback = self.policy.public_base_url.rstrip("/") + "/webhooks/twilio/status?" + urlencode({"outbox_id": row["id"], "token": row["callback_nonce"]})
        try:
            result = provider.send(row["phone"], row["body"], callback)
        except Exception as exc:
            from .provider import SendRejected, SendUncertain
            with self.lock, self.db:
                row = self._row(row["id"])
                # A signed callback may have established the outcome while REST
                # timed out. Its evidence takes precedence over local ambiguity.
                if row["provider_sid"] and row["status"] != "submitting":
                    return {"status": row["status"], "outbox_id": row["id"], "provider_sid": row["provider_sid"]}
                raw_code = str(getattr(exc, "code", "TRANSPORT_UNCERTAIN"))
                code = raw_code if re.fullmatch(r"[A-Za-z0-9_ -]{1,120}", raw_code) else "PROVIDER_ERROR"
                if isinstance(exc, SendRejected):
                    if code == "21610":
                        self._carrier_block(row)
                    if (getattr(exc, "retryable", False) and
                            (getattr(exc, "http_status", None) == 429 or code in {"429", "20429", "http_429"})
                            and row["attempt_count"] < 3):
                        self._status(row, "queued", code or "429")
                        self.db.execute("UPDATE outbox SET next_attempt_at=? WHERE id=?", (iso(utcnow() + timedelta(seconds=5 * 2 ** row["attempt_count"])), row["id"]))
                        return {"status": "retry_scheduled", "outbox_id": row["id"], "error_code": code or "429"}
                    self._status(row, "failed", code)
                    return {"status": "failed", "outbox_id": row["id"], "error_code": code}
                self._status(row, "uncertain", code)
                return {"status": "uncertain", "outbox_id": row["id"], "error_code": code}
        with self.lock, self.db:
            row = self._row(row["id"])
            if not isinstance(result, dict):
                if row["provider_sid"] and row["status"] in TERMINAL:
                    return {"status": "ignored", "reason": "terminal_state_preserved", "outbox_id": row["id"], "delivery_status": row["status"]}
                self._status(row, "uncertain", "INVALID_PROVIDER_RESPONSE")
                return {"status": "uncertain", "outbox_id": row["id"]}
            code = str(result.get("error_code") or "")
            if not re.fullmatch(r"[A-Za-z0-9_ -]{0,120}", code):
                code = "PROVIDER_ERROR"
            return self._provider_status(row, result.get("sid"), result.get("status"), code)

    def reconcile(self, queue_id, provider):
        with self.lock:
            row = self._row(queue_id)
            sid = row["provider_sid"]
            if not sid:
                return {"status": "blocked", "reason": "needs_manual_provider_evidence", "outbox_id": queue_id}
            if row["conflict"]:
                return {"status": "blocked", "reason": "sid_conflict_requires_review", "outbox_id": queue_id}
        # Fetching a known SID is read-only. Reconciliation never queues a resend.
        try:
            proof = provider.fetch_status(sid)
        except Exception:
            return {"status": "blocked", "reason": "provider_evidence_unavailable", "outbox_id": queue_id}
        if (not isinstance(proof, dict) or proof.get("verified") is not True or proof.get("sid") != sid
                or proof.get("to") != row["phone"] or proof.get("from") != self.policy.sender_number
                or proof.get("body") != row["body"]):
            return {"status": "blocked", "reason": "provider_evidence_mismatch", "outbox_id": queue_id}
        with self.lock, self.db:
            current = self._row(queue_id)
            return self._provider_status(current, sid, proof.get("status"), str(proof.get("error_code") or ""))
