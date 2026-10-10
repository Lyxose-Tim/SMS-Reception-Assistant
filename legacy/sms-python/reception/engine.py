"""Transactional, dry-run SMS reception domain.

All deliveries are rows in SQLite, never network operations. Consent, time/order,
handoff and send gates run outside the replaceable understanding adapter.
"""
from __future__ import annotations

import copy
import hashlib
import hmac
import json
import re
import sqlite3
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .model import ModelError, ModelTimeout, ModelUnavailable, RuleMockAdapter
from .outbox import Outbox, SendPolicy, sms_segments


ROOT = Path(__file__).resolve().parents[1]
PURPOSES = {"sms_inquiry", "appointment_request", "missed_call_followup"}
STOP_WORDS = {"STOP", "STOPALL", "UNSUBSCRIBE", "CANCEL", "END", "QUIT", "REVOKE", "OPTOUT"}
START_WORDS = {"START", "UNSTOP"}
MAX_EVENT_AGE = timedelta(minutes=15)
MAX_FUTURE_SKEW = timedelta(minutes=5)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def stamp(value: datetime | None = None) -> str:
    return (value or now_utc()).astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def parse_time(value: str) -> datetime:
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError) as exc:
        raise ValueError("occurred_at/time must be an ISO 8601 timestamp with timezone") from exc
    if result.tzinfo is None:
        raise ValueError("Timestamp must include a timezone")
    return result.astimezone(timezone.utc)


def dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def normalize_phone(phone):
    if isinstance(phone, str) and phone.startswith("+"):
        phone = re.sub(r"[ -]", "", phone)
    if not isinstance(phone, str) or not re.fullmatch(r"\+[1-9]\d{7,14}", phone):
        raise ValueError("Phone must use E.164 format; a country code is required")
    return phone


class Engine:
    def __init__(self, db_path, business_path=None, model_adapter=None, production=False, seed_demo=True, send_policy=None, privacy_key=""):
        if not isinstance(production, bool) or not isinstance(seed_demo, bool):
            raise ValueError("production and seed_demo must be boolean")
        self.production = production
        self.seed_demo = seed_demo
        self.send_policy = send_policy or SendPolicy()
        if not isinstance(privacy_key, str):
            raise ValueError("privacy_key must be server-only text")
        self.privacy_key = privacy_key
        self.outbox = None
        self.db_path = str(db_path)
        self.business_path = Path(business_path or ROOT / "data" / "business.json")
        self.model_adapter = model_adapter or RuleMockAdapter()
        self._rules = RuleMockAdapter()
        self._lock = threading.RLock()
        self._barrier_guard = threading.Lock()
        self._contact_barriers = {}
        self.contact_barrier_timeout_seconds = 5
        self.closing = False
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.db_path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA busy_timeout=5000")
        self._schema()
        if not self.db.execute("SELECT 1 FROM settings WHERE key='business'").fetchone():
            if seed_demo:
                self.reset(_initial=True)
            else:
                self._initialize_config()
        self.outbox = Outbox(self, self.send_policy)

    def close(self):
        self.closing = True
        self.db.close()

    @contextmanager
    def contact_barrier(self, phone, timeout=None):
        """Order intake and provider submission for one contact in this instance.

        The service deployment has one process, enforced by its database lock.
        Always acquire this barrier before the database lock/transaction.
        """
        phone = self._validate_phone(phone)
        with self._barrier_guard:
            barrier = self._contact_barriers.setdefault(phone, threading.RLock())
        wait = self.contact_barrier_timeout_seconds if timeout is None else timeout
        if not barrier.acquire(timeout=wait):
            raise TimeoutError("Contact submission boundary is busy")
        try:
            yield
        finally:
            barrier.release()

    @staticmethod
    def _validate_phone(phone):
        return normalize_phone(phone)

    def _schema(self):
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS contacts(
          phone TEXT PRIMARY KEY, name TEXT NOT NULL DEFAULT 'Synthetic demo contact',
          consent_status TEXT NOT NULL DEFAULT 'none', enrollment TEXT NOT NULL DEFAULT '{}',
          handoff INTEGER NOT NULL DEFAULT 0, blocked INTEGER NOT NULL DEFAULT 0,
          booking TEXT NOT NULL DEFAULT '{}', last_event_at TEXT, last_activity TEXT,
          revoked_at TEXT, scenario TEXT NOT NULL DEFAULT 'New simulated inquiry'
        );
        CREATE TABLE IF NOT EXISTS messages(
          id INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT, phone TEXT NOT NULL,
          direction TEXT NOT NULL, text TEXT NOT NULL, status TEXT NOT NULL,
          purpose TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL,
          error_code TEXT NOT NULL DEFAULT '', transport TEXT NOT NULL DEFAULT 'dry_run',
          FOREIGN KEY(phone) REFERENCES contacts(phone)
        );
        CREATE TABLE IF NOT EXISTS appointments(
          id INTEGER PRIMARY KEY AUTOINCREMENT, phone TEXT NOT NULL, name TEXT NOT NULL,
          service TEXT NOT NULL, area TEXT NOT NULL, desired_time TEXT NOT NULL,
          contact TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL,
          event_id TEXT UNIQUE, FOREIGN KEY(phone) REFERENCES contacts(phone)
        );
        CREATE TABLE IF NOT EXISTS consent_history(
          id INTEGER PRIMARY KEY AUTOINCREMENT, phone TEXT NOT NULL, event_id TEXT,
          action TEXT NOT NULL, source TEXT NOT NULL, occurred_at TEXT NOT NULL,
          purposes TEXT NOT NULL, evidence TEXT NOT NULL, verified INTEGER NOT NULL,
          status TEXT NOT NULL, FOREIGN KEY(phone) REFERENCES contacts(phone)
        );
        CREATE TABLE IF NOT EXISTS events(
          event_id TEXT PRIMARY KEY, kind TEXT NOT NULL, phone TEXT NOT NULL,
          occurred_at TEXT NOT NULL, received_at TEXT NOT NULL,
          origin TEXT NOT NULL, status TEXT NOT NULL, result TEXT NOT NULL,
          FOREIGN KEY(phone) REFERENCES contacts(phone)
        );
        CREATE TABLE IF NOT EXISTS audit(
          id INTEGER PRIMARY KEY AUTOINCREMENT, phone TEXT, event_id TEXT,
          action TEXT NOT NULL, details TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS messages_phone_time ON messages(phone,created_at);
        CREATE TABLE IF NOT EXISTS privacy_suppressions(phone_hash TEXT PRIMARY KEY, reason TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS privacy_dedup(event_hash TEXT PRIMARY KEY, created_at TEXT NOT NULL);
        """)
        # Additive migration preserves existing local demonstration databases.
        prior_contact_columns = {r[1] for r in self.db.execute("PRAGMA table_info(contacts)")}
        evidence_upgrade = not {"reply_permission_event_id", "reply_permission_basis"} <= prior_contact_columns
        for table, additions in {
            "contacts": {"carrier_blocked": "INTEGER NOT NULL DEFAULT 0", "carrier_blocked_at": "TEXT", "reply_permission_until": "TEXT", "last_service_inquiry_at": "TEXT", "booking_updated_at": "TEXT", "reply_permission_event_id": "TEXT NOT NULL DEFAULT ''", "reply_permission_basis": "TEXT NOT NULL DEFAULT ''"},
            "messages": {"provider_sid": "TEXT NOT NULL DEFAULT ''"},
        }.items():
            present = {r[1] for r in self.db.execute(f"PRAGMA table_info({table})")}
            for field, declaration in additions.items():
                if field not in present:
                    self.db.execute(f"ALTER TABLE {table} ADD COLUMN {field} {declaration}")
        # A historical timestamp alone is not evidence of an inbound authority.
        self.db.execute("UPDATE contacts SET reply_permission_until=NULL,last_service_inquiry_at=NULL WHERE reply_permission_event_id='' OR reply_permission_basis NOT IN ('rules','operator_review_human_only')")
        if evidence_upgrade:
            closed_drafts = 0
            for row in self.db.execute("SELECT phone,booking FROM contacts").fetchall():
                draft = json.loads(row["booking"])
                if all(draft.get(k) for k in ("service", "area", "desired_time", "contact")):
                    self.db.execute("UPDATE contacts SET booking='{}',booking_updated_at=NULL WHERE phone=?", (row["phone"],))
                    closed_drafts += 1
            if closed_drafts:
                self._audit(None, None, "legacy_booking_drafts_closed", {"completed_drafts_closed": closed_drafts, "appointment_history_preserved": True})
        self.db.commit()

    def _initialize_config(self):
        config = self._validate_business(json.loads(self.business_path.read_text(encoding="utf-8")))
        with self._lock, self.db:
            self.db.execute("INSERT INTO settings(key,value) VALUES ('business',?)", (dumps(config),))
            self._audit(None, None, "empty_service_initialized", {"synthetic_data_seeded": False, "production": self.production})

    def _business(self):
        return json.loads(self.db.execute("SELECT value FROM settings WHERE key='business'").fetchone()[0])

    def _contact(self, phone):
        row = self.db.execute("SELECT * FROM contacts WHERE phone=?", (phone,)).fetchone()
        if not row:
            if self.production:
                self.db.execute("INSERT INTO contacts(phone,name,last_activity,scenario) VALUES (?,?,?,?)", (phone, "Service contact", stamp(), "Inbound service inquiry"))
            else:
                self.db.execute("INSERT INTO contacts(phone,last_activity) VALUES (?,?)", (phone, stamp()))
            row = self.db.execute("SELECT * FROM contacts WHERE phone=?", (phone,)).fetchone()
        if self.privacy_key:
            digest = hmac.new(self.privacy_key.encode(), phone.encode(), hashlib.sha256).hexdigest()
            suppression = self.db.execute("SELECT reason,created_at FROM privacy_suppressions WHERE phone_hash=?", (digest,)).fetchone()
            if suppression:
                reason = suppression[0]
                def latest_barrier(field):
                    stamps = [parse_time(suppression[1])]
                    if row[field]:
                        stamps.append(parse_time(row[field]))
                    return stamp(max(stamps))
                self.db.execute("UPDATE contacts SET consent_status=CASE WHEN ? THEN 'revoked' ELSE consent_status END,carrier_blocked=CASE WHEN ? THEN 1 ELSE carrier_blocked END,revoked_at=CASE WHEN ? THEN ? ELSE revoked_at END,carrier_blocked_at=CASE WHEN ? THEN ? ELSE carrier_blocked_at END WHERE phone=?", ("local" in reason, "carrier" in reason, "local" in reason, latest_barrier("revoked_at"), "carrier" in reason, latest_barrier("carrier_blocked_at"), phone))
                row = self.db.execute("SELECT * FROM contacts WHERE phone=?", (phone,)).fetchone()
        return dict(row)

    def _record_suppression(self, phone, reason):
        if not self.privacy_key:
            return
        digest = hmac.new(self.privacy_key.encode(), phone.encode(), hashlib.sha256).hexdigest()
        existing = self.db.execute("SELECT reason FROM privacy_suppressions WHERE phone_hash=?", (digest,)).fetchone()
        if existing and existing[0] != reason:
            reason = "local_and_carrier_opt_out"
        self.db.execute("INSERT INTO privacy_suppressions(phone_hash,reason,created_at) VALUES (?,?,?) ON CONFLICT(phone_hash) DO UPDATE SET reason=excluded.reason,created_at=excluded.created_at", (digest, reason, stamp()))

    def _privacy_duplicate(self, event_id):
        digest = hashlib.sha256(event_id.encode()).hexdigest()
        return bool(self.db.execute("SELECT 1 FROM privacy_dedup WHERE event_hash=?", (digest,)).fetchone())

    def _audit(self, phone, event_id, action, details):
        self.db.execute("INSERT INTO audit(phone,event_id,action,details,created_at) VALUES (?,?,?,?,?)",
                        (phone, event_id, action, dumps(details), stamp()))

    def _message(self, phone, text, direction, status, event_id=None, purpose="", error_code="", at=None):
        cur = self.db.execute("""INSERT INTO messages(event_id,phone,direction,text,status,purpose,created_at,error_code)
          VALUES (?,?,?,?,?,?,?,?)""", (event_id, phone, direction, text, status, purpose, at or stamp(), error_code))
        return cur.lastrowid

    def _history(self, phone, event_id, action, consent, status, at=None):
        self.db.execute("""INSERT INTO consent_history(phone,event_id,action,source,occurred_at,purposes,evidence,verified,status)
          VALUES (?,?,?,?,?,?,?,?,?)""", (phone, event_id, action, str(consent.get("source", "")),
          at or stamp(), dumps(consent.get("purposes", [])), str(consent.get("evidence", "")),
          int(consent.get("verified") is True), status))

    def _active_scope(self, contact, purpose):
        enrollment = json.loads(contact["enrollment"])
        return (contact["consent_status"] == "active" and enrollment.get("verified") is True
                and bool(enrollment.get("source")) and bool(enrollment.get("evidence"))
                and purpose in enrollment.get("purposes", []))

    def _source_details(self, phone, source_event_id, allow_pending_event=None):
        """Return persistent provider proof; flags supplied by a caller are insufficient."""
        if not isinstance(source_event_id, str) or not re.fullmatch(r"twilio:SM[0-9a-fA-F]{32}", source_event_id):
            return "source_unavailable", None
        sid = source_event_id[7:]
        if self._privacy_duplicate(source_event_id):
            return "source_unavailable", None
        tables = {r[0] for r in self.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "provider_inbox" not in tables:
            return "source_not_verified", None
        if "inbox_tombstones" in tables and self.db.execute("SELECT 1 FROM inbox_tombstones WHERE sid_hash=?", (hashlib.sha256(sid.encode()).hexdigest(),)).fetchone():
            return "source_unavailable", None
        row = self.db.execute("SELECT * FROM provider_inbox WHERE sid=?", (sid,)).fetchone()
        if not row:
            return "source_unavailable", None
        source = dict(row)
        if source["status"] != "processed" or not source.get("resource_at"):
            return "source_not_verified", None
        if source["phone"] != phone or source["recipient"] != self.send_policy.sender_number:
            return "source_contact_mismatch", None
        try:
            occurred = parse_time(source["resource_at"])
        except ValueError:
            return "source_not_verified", None
        if source["body"].strip().upper() in STOP_WORDS | START_WORDS | {"HELP", "INFO"} or source.get("opt_out_type") in {"STOP", "START", "HELP"}:
            return "source_not_service_inquiry", None
        record = self.db.execute("SELECT * FROM events WHERE event_id=?", (source_event_id,)).fetchone()
        if record:
            if record["phone"] != phone or record["kind"] != "sms" or record["origin"] != "twilio" or record["status"] == "ignored":
                return "source_not_verified", None
            try:
                event_time = parse_time(record["occurred_at"])
            except ValueError:
                return "source_not_verified", None
            if event_time != occurred:
                return "source_not_verified", None
            message = self.db.execute("SELECT text,provider_sid FROM messages WHERE event_id=? AND phone=? AND direction='inbound' ORDER BY id LIMIT 1", (source_event_id, phone)).fetchone()
            if not message or message["text"] != source["body"].strip() or message["provider_sid"] != sid:
                return "source_not_verified", None
        else:
            pending = allow_pending_event
            try:
                pending_time = parse_time(pending.get("occurred_at")) if isinstance(pending, dict) else None
            except ValueError:
                pending_time = None
            if not (isinstance(pending, dict) and pending.get("event_id") == source_event_id and pending.get("phone") == phone
                    and pending.get("kind") == "sms" and pending.get("origin") == "twilio"
                    and pending.get("verified") is True and pending.get("provider_fresh") is True
                    and pending.get("provider_sid") == sid and pending.get("text") == source["body"]
                    and pending_time == occurred):
                return "source_unavailable", None
        source["occurred_at"] = stamp(occurred)
        return "", source

    def _trusted_keyword_resource(self, event):
        """START needs durable resource proof; STOP needs only signed restriction."""
        sid = event.get("provider_sid", "")
        if not re.fullmatch(r"SM[0-9a-fA-F]{32}", sid) or event.get("event_id") != "twilio:" + sid:
            return False
        tables = {r[0] for r in self.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "provider_inbox" not in tables:
            return False
        if "inbox_tombstones" in tables and self.db.execute("SELECT 1 FROM inbox_tombstones WHERE sid_hash=?", (hashlib.sha256(sid.encode()).hexdigest(),)).fetchone():
            return False
        row = self.db.execute("SELECT * FROM provider_inbox WHERE sid=?", (sid,)).fetchone()
        if not row or row["status"] != "processed" or not row["resource_at"]:
            return False
        try:
            same_time = parse_time(row["resource_at"]) == parse_time(event["occurred_at"])
        except ValueError:
            return False
        return (same_time and row["phone"] == event["phone"] and row["recipient"] == self.send_policy.sender_number
                and row["body"] == event["text"] and row["opt_out_type"] == event.get("opt_out_type", ""))

    def _permission_gate(self, phone, source_event_id, basis, until, human=False, allow_pending_event=None):
        if basis not in {"rules", "operator_review_human_only"}:
            return "service_reply_evidence_required"
        if basis == "operator_review_human_only" and not human:
            return "human_only_service_permission"
        reason, source = self._source_details(phone, source_event_id, allow_pending_event)
        if reason:
            return reason
        try:
            deadline = parse_time(until)
            source_time = parse_time(source["occurred_at"])
        except ValueError:
            return "service_reply_evidence_required"
        if deadline <= now_utc():
            return "service_reply_window_expired"
        if deadline <= source_time or deadline > source_time + timedelta(hours=self.send_policy.reply_window_hours):
            return "service_reply_evidence_required"
        return ""

    def _review_source(self, phone, source_event_id):
        reason, source = self._source_details(phone, source_event_id)
        if reason:
            return reason, None
        contact_row = self.db.execute("SELECT * FROM contacts WHERE phone=?", (phone,)).fetchone()
        if not contact_row:
            return "source_contact_mismatch", None
        contact = dict(contact_row)
        if contact["consent_status"] == "revoked":
            return "opted_out", None
        if contact["blocked"] or contact.get("carrier_blocked"):
            return "contact_blocked", None
        barriers = [parse_time(contact[k]) for k in ("revoked_at", "carrier_blocked_at") if contact.get(k)]
        if barriers and parse_time(source["occurred_at"]) <= max(barriers):
            return "source_predates_opt_out", None
        until = stamp(parse_time(source["occurred_at"]) + timedelta(hours=self.send_policy.reply_window_hours))
        reason = self._permission_gate(phone, source_event_id, "operator_review_human_only", until, human=True)
        source["reply_until"] = until
        return reason, source

    def _reply_sources(self, contact):
        tables = {r[0] for r in self.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not self.production or "provider_inbox" not in tables:
            return []
        sources = []
        rows = self.db.execute("SELECT * FROM provider_inbox WHERE phone=? ORDER BY received_at DESC LIMIT 10", (contact["phone"],)).fetchall()
        current_id = contact.get("reply_permission_event_id", "")
        if current_id.startswith("twilio:") and not any("twilio:" + row["sid"] == current_id for row in rows):
            current = self.db.execute("SELECT * FROM provider_inbox WHERE sid=? AND phone=?", (current_id[7:], contact["phone"])).fetchone()
            if current:
                rows.append(current)
        for row in rows:
            source_id = "twilio:" + row["sid"]
            proof_reason, proof = self._source_details(contact["phone"], source_id)
            reason, reviewed = self._review_source(contact["phone"], source_id)
            until = reviewed["reply_until"] if reviewed else ""
            if source_id == contact.get("reply_permission_event_id") and contact.get("reply_permission_basis") in {"rules", "operator_review_human_only"}:
                until = contact.get("reply_permission_until") or ""
                reason = self._permission_gate(contact["phone"], source_id, contact["reply_permission_basis"], until, human=True)
                if contact["consent_status"] == "revoked":
                    reason = "opted_out"
                elif contact["blocked"] or contact.get("carrier_blocked"):
                    reason = "contact_blocked"
            sources.append({"event_id": source_id, "text": row["body"], "received_at": row["received_at"], "occurred_at": row["resource_at"], "status": row["status"], "verified": not bool(proof_reason), "eligible": not bool(reason), "reason": reason, "reply_until": until})
        return sources

    def _send_gate(self, contact, business, purpose, event=None, permit_handoff=False):
        if self.closing:
            return "service_closing"
        if contact["consent_status"] == "revoked":
            return "opted_out"
        if contact["blocked"] or contact.get("carrier_blocked"):
            return "contact_blocked"
        if contact["handoff"] and not permit_handoff:
            return "human_takeover_active"
        if not business.get("sending_enabled", True):
            return "business_sending_disabled"
        if self.production:
            if purpose != "service_reply":
                return "production_service_replies_only"
            if not contact.get("reply_permission_until") or parse_time(contact["reply_permission_until"]) <= now_utc():
                return "service_reply_window_expired"
            reason = self._permission_gate(contact["phone"], contact.get("reply_permission_event_id", ""), contact.get("reply_permission_basis", ""), contact["reply_permission_until"], human=permit_handoff, allow_pending_event=event)
            if reason:
                return reason
        else:
            if purpose != "inbound_reply" and not self._active_scope(contact, purpose):
                return "verified_scoped_consent_required"
            if purpose == "inbound_reply" and (not event or event.get("kind") != "sms"):
                return "current_inbound_required"
        return self._hourly_send_gate(contact, business)

    def _hourly_send_gate(self, contact, business):
        threshold = stamp(now_utc() - timedelta(hours=1))
        count = self.db.execute("""SELECT count(*) FROM messages WHERE phone=? AND direction='outbound'
          AND purpose!='opt_out_confirmation' AND created_at>=?""", (contact["phone"], threshold)).fetchone()[0]
        if count >= business.get("max_messages_per_hour", 8):
            return "hourly_send_limit"
        return ""

    def _send(self, contact, business, text, purpose, event, permit_handoff=False, special=False):
        event_id = event.get("event_id")
        if self.production:
            if special:
                return {"status": "processed", "reason": "provider_managed_confirmation_only", "simulated": False}
            purpose = "service_reply"
            try:
                encoding = sms_segments(text)
                if encoding["segments"] > self.send_policy.max_segments_per_message:
                    raise ValueError("Reply exceeds the reviewed SMS segment limit")
                text = encoding["body"]
            except ValueError:
                if permit_handoff:
                    raise ValueError("Reply cannot be sent within the reviewed SMS length and segment limits") from None
                return self._handoff(contact, event, "reply_length_or_segment_limit", "The automatic response exceeds the configured SMS limits. The owner must review this request; nothing was queued.")
        reason = "" if special else self._send_gate(contact, business, purpose, event, permit_handoff)
        if reason:
            self._audit(contact["phone"], event_id, "send_blocked", {"reason": reason, "purpose": purpose})
            return {"status": "blocked", "reason": reason, "simulated": not self.production}
        if self.production:
            message_id = self._message(contact["phone"], text, "outbound", "queued", event_id, purpose)
            queue_id = self.outbox.enqueue(message_id, event_id, contact["phone"], text, "human" if permit_handoff else "automatic", purpose)
            return {"status": "queued", "reason": "durable_outbox", "reply": text, "message_id": message_id,
                    "outbox_id": queue_id, "simulated": False, "sent": False}
        failure = event.get("origin", "simulation") == "simulation" and event.get("demo", {}).get("delivery_failure") is True
        message_id = self._message(contact["phone"], text, "outbound", "failed" if failure else "simulated",
                                   event_id, purpose, "SIMULATED_DELIVERY_FAILURE" if failure else "")
        self._audit(contact["phone"], event_id, "delivery_simulated" if not failure else "delivery_failed",
                    {"message_id": message_id, "purpose": purpose, "transport": "dry_run"})
        if failure:
            self.db.execute("UPDATE contacts SET handoff=1 WHERE phone=?", (contact["phone"],))
            self._audit(contact["phone"], event_id, "handoff_started", {"reason": "simulated_delivery_failure"})
        return {"status": "processed" if not failure else "handoff", "reason": "simulated_delivery" if not failure else "simulated_delivery_failure",
                "reply": text, "message_id": message_id, "simulated": True}

    def _handoff(self, contact, event, reason, text=None):
        # A single final explanatory response may precede a new handoff. Once the
        # handoff already exists, no automation can send until operator resumes.
        result = {"status": "handoff", "reason": reason, "simulated": not self.production}
        if self.production and text:
            self._message(contact["phone"], text, "system", "note", event.get("event_id"), "handoff_note")
        elif text and not contact["handoff"]:
            result = self._send(contact, self._business(), text, "inbound_reply", event)
            result.update(status="handoff", reason=reason)
        self.db.execute("UPDATE contacts SET handoff=1 WHERE phone=?", (contact["phone"],))
        self._audit(contact["phone"], event.get("event_id"), "handoff_started", {"reason": reason})
        return result

    @staticmethod
    def _validate_business(config):
        if not isinstance(config, dict):
            raise ValueError("Business config must be an object")
        allowed = {"name", "hours", "service_areas", "services", "price_note", "contact_email", "contact_phone",
                   "sending_enabled", "max_messages_per_hour", "model_mode", "model_timeout_seconds", "approved_faqs"}
        if set(config) - allowed:
            raise ValueError("Business config contains unsupported fields")
        clean = copy.deepcopy(config)
        for field in ("name", "hours", "price_note", "contact_email", "contact_phone"):
            if not isinstance(clean.get(field, ""), str) or len(clean.get(field, "")) > 1600:
                raise ValueError(f"Invalid business field: {field}")
            clean.setdefault(field, "")
        if not clean["name"].strip():
            raise ValueError("Business name is required")
        areas = clean.get("service_areas", [])
        if not isinstance(areas, list) or len(areas) > 30 or any(not isinstance(a, str) or not a.strip() or len(a) > 100 for a in areas):
            raise ValueError("service_areas must contain short area names")
        clean["service_areas"] = areas
        services = clean.get("services", [])
        if not isinstance(services, list) or len(services) > 30:
            raise ValueError("services must be a list")
        for service in services:
            if not isinstance(service, dict) or set(service) - {"name", "description", "pricing"}:
                raise ValueError("Invalid service fields")
            if not isinstance(service.get("name"), str) or not service["name"].strip():
                raise ValueError("Each service needs a name")
            for key in ("name", "description", "pricing"):
                if not isinstance(service.get(key, ""), str) or len(service.get(key, "")) > 1000:
                    raise ValueError("Invalid service text")
                service.setdefault(key, "")
        clean["services"] = services
        if "approved_faqs" in clean:
            faqs = clean["approved_faqs"]
            if not isinstance(faqs, list) or len(faqs) > 5:
                raise ValueError("At most five approved_faqs entries are allowed")
            topics = set()
            for faq in faqs:
                if not isinstance(faq, dict) or set(faq) != {"topic", "answer", "approved"}:
                    raise ValueError("FAQ fields must be topic, answer and approved")
                if not isinstance(faq["topic"], str) or faq["topic"] not in {"hours", "areas", "services", "pricing", "requests"} or faq["topic"] in topics:
                    raise ValueError("FAQ topics must be supported and unique")
                topics.add(faq["topic"])
                if not isinstance(faq["approved"], bool) or not isinstance(faq["answer"], str) or len(faq["answer"]) > 1200:
                    raise ValueError("Invalid approved FAQ answer")
                if faq["approved"] and not faq["answer"].strip():
                    raise ValueError("Approved FAQ answers cannot be blank")
        if not isinstance(clean.get("sending_enabled", True), bool):
            raise ValueError("sending_enabled must be true or false")
        clean.setdefault("sending_enabled", True)
        limit = clean.get("max_messages_per_hour", 8)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("max_messages_per_hour must be 1–100")
        clean["max_messages_per_hour"] = limit
        if clean.get("model_mode", "rules") not in {"rules", "mock"}:
            raise ValueError("This demo supports rules or mock mode only")
        clean.setdefault("model_mode", "rules")
        try:
            timeout = float(clean.get("model_timeout_seconds", 1.0))
        except (ValueError, TypeError) as exc:
            raise ValueError("Invalid model timeout") from exc
        if not 0.01 <= timeout <= 10:
            raise ValueError("model_timeout_seconds must be 0.01–10")
        clean["model_timeout_seconds"] = timeout
        return clean

    def save_business(self, config):
        # Legacy demo editor has no FAQ controls: omission preserves any explicit
        # owner approvals, while changing them requires an explicit supplied list.
        if isinstance(config, dict) and "approved_faqs" not in config:
            previous = self._business()
            if "approved_faqs" in previous:
                config = {**config, "approved_faqs": previous["approved_faqs"]}
        clean = self._validate_business(config)
        with self._lock, self.db:
            self.db.execute("UPDATE settings SET value=? WHERE key='business'", (dumps(clean),))
            self._audit(None, None, "business_updated", {"fields": sorted(clean), "live_sending": False})
        return {"status": "saved", "business": clean, "simulated": True}

    def _understand(self, text, business, event):
        behavior = event.get("demo", {}).get("model", "normal") if event.get("origin") == "simulation" else "normal"
        # Future live adapters must set their own network deadline as well. The
        # engine deadline safely routes the response to a human; it cannot kill
        # an underlying running adapter thread or retry an external operation.
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="understanding")
        future = executor.submit(self.model_adapter.understand, text, copy.deepcopy(business), behavior)
        try:
            result = future.result(timeout=business.get("model_timeout_seconds", 1.0))
        except FutureTimeout as exc:
            future.cancel()
            raise ModelTimeout("Understanding deadline exceeded") from exc
        finally:
            executor.shutdown(wait=False, cancel_futures=True)
        if not isinstance(result, dict):
            raise ModelError("Invalid adapter result")
        return result

    def _faq(self, contact, event, parsed):
        business = self._business()
        if self.production:
            approved = {f["topic"]: f["answer"] for f in business.get("approved_faqs", []) if f["approved"]}
            topics = parsed.get("topics", [])
            if parsed.get("unrecognized_service") or not topics or any(t not in approved for t in topics):
                return self._handoff(contact, event, "unapproved_faq", "I don't have approved information for that question. The owner will review your message.")
            return self._send(contact, business, " ".join(approved[t] for t in topics), "service_reply", event)
        sections = []
        for topic in parsed.get("topics", []):
            if topic == "hours":
                if not business.get("hours"):
                    return self._handoff(contact, event, "missing_approved_hours", "I don't have approved opening hours. I'll ask the owner to help.")
                sections.append(f"Our hours: {business['hours']}.")
            elif topic == "areas":
                if not business.get("service_areas"):
                    return self._handoff(contact, event, "missing_approved_areas", "I don't have an approved service-area list. I'll ask the owner to help.")
                sections.append("We serve " + ", ".join(business["service_areas"]) + ".")
            elif topic == "services":
                if not business.get("services"):
                    return self._handoff(contact, event, "missing_approved_services", "I don't have an approved service list. I'll ask the owner to help.")
                sections.append("We offer " + ", ".join(s["name"] for s in business["services"]) + ".")
            elif topic == "pricing":
                if parsed.get("unrecognized_service"):
                    return self._handoff(contact, event, "unlisted_service_pricing", "I don't have approved pricing for that service. I'll ask the owner to review it; I can't confirm a quote.")
                selected = [s for s in business.get("services", []) if not parsed.get("service") or s["name"] == parsed["service"]]
                if not selected or any(not s.get("pricing") for s in selected):
                    return self._handoff(contact, event, "missing_approved_pricing", "I don't have approved pricing for that request. The owner will need to review it; I can't confirm a quote.")
                sections.extend(f"{s['name']}: {s['pricing']}" for s in selected)
                if business.get("price_note"):
                    sections.append(business["price_note"])
            elif topic == "requests":
                approved = next((f["answer"] for f in business.get("approved_faqs", []) if f["topic"] == "requests" and f["approved"]), None)
                if "approved_faqs" in business and not approved:
                    return self._handoff(contact, event, "unapproved_faq", "The owner will explain the appointment request process.")
                sections.append(approved or "Send the service, area, preferred time and contact details. Requests remain pending until the owner confirms availability and the quote.")
        if not sections:
            return self._handoff(contact, event, "unknown_question", "I don't have approved information for that question. I'll pass it to the owner.")
        return self._send(contact, business, " ".join(sections), "inbound_reply", event)

    def _booking(self, contact, event, parsed):
        business = self._business()
        if self.production and not any(f["topic"] == "requests" and f["approved"] for f in business.get("approved_faqs", [])):
            return self._handoff(contact, event, "appointment_flow_not_approved", "The owner will review your appointment inquiry. No appointment has been confirmed.")
        booking = json.loads(contact["booking"])
        if self.production and (not booking.get("_in_progress") or self._permission_gate(contact["phone"], contact.get("reply_permission_event_id", ""), contact.get("reply_permission_basis", ""), contact.get("reply_permission_until"), allow_pending_event=event)):
            booking = {}
        if parsed.get("new_booking"):
            booking = {}
        booking["_in_progress"] = True
        configured_services = {s["name"] for s in business.get("services", [])}
        for key in ("service", "area", "desired_time", "contact"):
            candidate = parsed.get(key)
            if not isinstance(candidate, str) or not candidate or len(candidate) > 160:
                continue
            if key == "service" and candidate not in configured_services:
                continue
            if key == "area" and candidate not in business.get("service_areas", []):
                continue
            if key == "contact":
                if candidate == "__CURRENT_PHONE__":
                    candidate = contact["phone"]
                elif not (re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", candidate) or
                          re.fullmatch(r"[+()\d -]{10,25}", candidate)):
                    continue
            booking[key] = candidate
        self.db.execute("UPDATE contacts SET booking=?,booking_updated_at=? WHERE phone=?", (dumps(booking), stamp(), contact["phone"]))
        missing = [key for key in ("service", "area", "desired_time", "contact") if not booking.get(key)]
        if not missing:
            cur = self.db.execute("""INSERT INTO appointments(phone,name,service,area,desired_time,contact,status,created_at,event_id)
              VALUES (?,?,?,?,?,?, 'pending_owner_confirmation',?,?)""", (contact["phone"], contact["name"],
              booking["service"], booking["area"], booking["desired_time"], booking["contact"], stamp(), event["event_id"]))
            appointment = dict(self.db.execute("SELECT * FROM appointments WHERE id=?", (cur.lastrowid,)).fetchone())
            self._audit(contact["phone"], event["event_id"], "appointment_request_created", {"id": cur.lastrowid, "status": "pending_owner_confirmation"})
            self.db.execute("UPDATE contacts SET booking='{}',booking_updated_at=NULL WHERE phone=?", (contact["phone"],))
            reply = (f"Request received: {booking['service']} in {booking['area']}, preferred time {booking['desired_time']}. "
                     f"Contact: {booking['contact']}. This is an appointment request only, pending owner confirmation. "
                     "The owner must confirm availability and the final quote.")
            result = self._send(contact, business, reply, "inbound_reply", event)
            result["appointment"] = appointment
            return result
        prompts = {
            "service": "Which service would you like: " + ", ".join(s["name"] for s in business.get("services", [])) + "?",
            "area": "Which area is the property in? We serve " + ", ".join(business.get("service_areas", [])) + ".",
            "desired_time": "What day and time would you prefer? Availability still requires owner confirmation.",
            "contact": "What is your preferred contact email or phone? You can also say 'this number'.",
        }
        if missing[0] == "service" and not configured_services or missing[0] == "area" and not business.get("service_areas"):
            return self._handoff(contact, event, "missing_booking_configuration", "I don't have approved service details. I'll pass your request to the owner.")
        return self._send(contact, business, prompts[missing[0]], "inbound_reply", event)

    def _sms(self, contact, event):
        text = event["text"].strip()
        keyword = text.upper()
        inbound_id = self._message(contact["phone"], text, "inbound", "received", event["event_id"], at=event["occurred_at"])
        sid = event.get("provider_sid", "")
        if event.get("origin") == "twilio" and event.get("verified") is True and re.fullmatch(r"SM[0-9a-fA-F]{32}", sid):
            self.db.execute("UPDATE messages SET provider_sid=? WHERE id=?", (sid, inbound_id))
        # Twilio Advanced Opt-Out semantics are authoritative when supplied by
        # a verified production webhook; text spoofing cannot set this field.
        advanced = event.get("opt_out_type", "") if event.get("origin") == "twilio" and event.get("verified") is True else ""
        provider_suppressed = event.get("origin") == "twilio" and event.get("verified") is True and (
            event.get("suppress_confirmation") is True or event.get("provider_managed_optout") is True)
        if advanced == "HELP":
            self._audit(contact["phone"], event["event_id"], "provider_handled_help", {"extra_reply_suppressed": True})
            return {"status": "processed", "reason": "provider_handled_help", "simulated": True}
        if self.production and keyword in {"HELP", "INFO"}:
            self._audit(contact["phone"], event["event_id"], "provider_handled_help", {"extra_reply_suppressed": True})
            return {"status": "processed", "reason": "provider_handled_help", "simulated": False}
        if keyword in STOP_WORDS or advanced == "STOP":
            was_revoked = contact["consent_status"] == "revoked"
            enrolled = json.loads(contact["enrollment"])
            record = {**enrolled, "source": "Inbound STOP keyword", "evidence": event["event_id"], "verified": True}
            self.db.execute("UPDATE contacts SET consent_status='revoked',revoked_at=? WHERE phone=?", (stamp(), contact["phone"]))
            self._record_suppression(contact["phone"], "local_opt_out")
            if self.outbox:
                self.outbox.cancel_for_contact(contact["phone"], "OPT_OUT")
            self._history(contact["phone"], event["event_id"], "opt_out", record, "revoked", event["occurred_at"])
            self._audit(contact["phone"], event["event_id"], "opt_out_applied", {"normal_sending_blocked": True, "stale_stop_accepted": True})
            stale_stop = parse_time(event["occurred_at"]) < now_utc() - MAX_EVENT_AGE or (
                contact["last_event_at"] and parse_time(event["occurred_at"]) < parse_time(contact["last_event_at"])) or (
                parse_time(event["occurred_at"]) > now_utc() + MAX_FUTURE_SKEW)
            if not self.production and not was_revoked and advanced != "STOP" and not provider_suppressed and not stale_stop:
                result = self._send(contact, self._business(), f"{self._business()['name']}: You are unsubscribed. No regular messages will be sent. Reply START to request restoration of previously verified permissions.",
                                    "opt_out_confirmation", event, special=True)
                result["reason"] = "opt_out_applied_with_simulated_confirmation"
                return result
            return {"status": "processed", "reason": "opt_out_applied_no_extra_confirmation", "simulated": True}
        if keyword in START_WORDS or advanced == "START":
            if self.production and (keyword not in START_WORDS or self.send_policy.optout_profile == "advanced" and advanced != "START"):
                self._audit(contact["phone"], event["event_id"], "start_rejected", {"reason": "unsupported_platform_start_keyword_or_profile"})
                return {"status": "blocked", "reason": "unsupported_platform_start_keyword_or_profile", "simulated": False}
            withdrawal_times = [parse_time(contact[key]) for key in ("revoked_at", "carrier_blocked_at") if contact.get(key)]
            if withdrawal_times and parse_time(event["occurred_at"]) <= max(withdrawal_times):
                self._audit(contact["phone"], event["event_id"], "start_rejected", {"reason": "start_predates_applied_opt_out"})
                self._history(contact["phone"], event["event_id"], "start_rejected", {"source": "Inbound START keyword", "evidence": event["event_id"], "verified": True}, contact["consent_status"], event["occurred_at"])
                return {"status": "blocked", "reason": "start_predates_applied_opt_out", "simulated": True}
            if self.production:
                trusted = (event.get("origin") == "twilio" and event.get("verified") is True
                           and event.get("provider_fresh") is True and event.get("provider_keyword_verified") is True
                           and self.send_policy.optout_reviewed and self._trusted_keyword_resource(event))
                fresh = now_utc() - MAX_EVENT_AGE <= parse_time(event["occurred_at"]) <= now_utc() + MAX_FUTURE_SKEW
                if not trusted or not fresh:
                    return {"status": "blocked", "reason": "trusted_fresh_platform_start_required", "simulated": False}
                # START restores the ability to answer a still-current service
                # inquiry. It never creates ongoing or marketing enrollment.
                permission_reason = self._permission_gate(contact["phone"], contact.get("reply_permission_event_id", ""), contact.get("reply_permission_basis", ""), contact.get("reply_permission_until"), human=True)
                self.db.execute("UPDATE contacts SET consent_status='none',carrier_blocked=0 WHERE phone=?", (contact["phone"],))
                if permission_reason:
                    self.db.execute("UPDATE contacts SET reply_permission_event_id='',reply_permission_basis='',reply_permission_until=NULL,last_service_inquiry_at=NULL WHERE phone=?", (contact["phone"],))
                if self.privacy_key:
                    digest = hmac.new(self.privacy_key.encode(), contact["phone"].encode(), hashlib.sha256).hexdigest()
                    self.db.execute("DELETE FROM privacy_suppressions WHERE phone_hash=?", (digest,))
                self._history(contact["phone"], event["event_id"], "platform_start_service_reply_only", {"source": "Verified fresh platform START", "evidence": event["event_id"], "verified": True, "purposes": ["service_reply"]}, "none", event["occurred_at"])
                self._audit(contact["phone"], event["event_id"], "service_reply_restored", {"marketing_authorization": False, "other_gates_preserved": True, "restored": not bool(permission_reason), "permission_reason": permission_reason, "source_event_id": contact.get("reply_permission_event_id", ""), "original_deadline": contact.get("reply_permission_until")})
                return {"status": "processed", "reason": "provider_managed_start_service_reply_only", "simulated": False}
            enrollment = json.loads(contact["enrollment"])
            permitted = (enrollment.get("verified") is True and bool(enrollment.get("source"))
                         and bool(enrollment.get("evidence")) and bool(set(enrollment.get("purposes", [])) & PURPOSES))
            if not permitted:
                self._history(contact["phone"], event["event_id"], "start_rejected", {"source": "Inbound START keyword", "evidence": event["event_id"], "verified": True}, contact["consent_status"], event["occurred_at"])
                self._audit(contact["phone"], event["event_id"], "start_rejected", {"reason": "no_verified_previous_enrollment"})
                return {"status": "blocked", "reason": "no_verified_previous_enrollment", "simulated": True}
            self.db.execute("UPDATE contacts SET consent_status='active' WHERE phone=?", (contact["phone"],))
            self._history(contact["phone"], event["event_id"], "restore_by_start", {**enrollment, "source": "Inbound START / verified previous enrollment", "evidence": event["event_id"]}, "active", event["occurred_at"])
            self._audit(contact["phone"], event["event_id"], "consent_restored", {"purposes": enrollment["purposes"], "other_gates_preserved": True})
            if advanced == "START" or provider_suppressed:
                return {"status": "processed", "reason": "consent_restored_provider_confirmation_only", "simulated": True}
            return self._send(self._contact(contact["phone"]), self._business(), f"Previously verified SMS permissions restored for {self._business()['name']}. Other contact restrictions and human takeover still apply. Reply STOP to opt out.", "inbound_reply", event)
        rule = self._rules.understand(text, self._business())
        if self.production:
            if contact["consent_status"] == "revoked" or contact["blocked"] or contact.get("carrier_blocked"):
                reason = "opted_out" if contact["consent_status"] == "revoked" else "contact_blocked"
                self._audit(contact["phone"], event["event_id"], "inbound_stored_without_automation", {"reason": reason})
                return {"status": "blocked", "reason": reason, "simulated": False}
            if rule.get("ambiguous_booking"):
                return self._handoff(contact, event, "ambiguous_booking_details",
                                     "The request includes conflicting or negated details. A person must confirm the intended fields before collecting or submitting an appointment.")
            # Keep short, valid field replies in the existing appointment flow.
            # A continuation cannot create or revive an expired reply window.
            booking_context = json.loads(contact["booking"])
            window_current = not bool(self._permission_gate(contact["phone"], contact.get("reply_permission_event_id", ""), contact.get("reply_permission_basis", ""), contact.get("reply_permission_until")))
            if booking_context and (not window_current or not booking_context.get("_in_progress") or rule.get("new_booking")):
                self.db.execute("UPDATE contacts SET booking='{}',booking_updated_at=NULL WHERE phone=?", (contact["phone"],))
                booking_context = {}
                contact = self._contact(contact["phone"])
            if booking_context and not rule.get("desired_time"):
                normalized_time = re.sub(r"\b(today|tomorrow)(?=\d)", r"\1 ", text, flags=re.I)
                if normalized_time != text:
                    time_rule = self._rules.understand(normalized_time, self._business())
                    rule["desired_time"] = time_rule.get("desired_time", "")
            contact_only = bool(rule.get("contact") and (
                re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", text.strip()) or
                re.fullmatch(r"[+()\d -]{10,25}", text.strip()) or
                rule.get("contact") == "__CURRENT_PHONE__" and text.lower().strip() in {"this number", "this phone", "use this number", "use this phone", "text me here"}))
            if contact_only:
                # Contact data cannot request a person, quote or new service
                # inquiry merely because its email name/domain contains a word.
                rule.update(intent="unknown", topics=[], service="", area="", desired_time="")
            continuation = (bool(booking_context.get("_in_progress") or booking_context.get("service")) and window_current
                            and any(rule.get(k) for k in ("service", "area", "desired_time", "contact")))
            service_inquiry = not contact_only and not rule.get("malicious") and (rule.get("intent") in {"faq", "booking"} or bool(rule.get("topics")))
            if service_inquiry:
                hourly_reason = self._hourly_send_gate(contact, self._business())
                if hourly_reason:
                    self._audit(contact["phone"], event["event_id"], "service_permission_rejected", {"reason": hourly_reason})
                    return {"status": "blocked", "reason": hourly_reason, "simulated": False}
                reason, source = self._source_details(contact["phone"], event["event_id"], event)
                if reason:
                    self._audit(contact["phone"], event["event_id"], "service_permission_rejected", {"reason": reason})
                    return {"status": "blocked", "reason": reason, "simulated": False}
                until = stamp(parse_time(source["occurred_at"]) + timedelta(hours=self.send_policy.reply_window_hours))
                self.db.execute("UPDATE contacts SET last_service_inquiry_at=?,reply_permission_until=?,reply_permission_event_id=?,reply_permission_basis='rules' WHERE phone=?", (source["occurred_at"], until, event["event_id"], contact["phone"]))
                self._audit(contact["phone"], event["event_id"], "service_reply_permission_created", {"basis": "rules", "source_event_id": event["event_id"], "resource_at": source["occurred_at"], "reply_until": until, "marketing_authorization": False})
                contact = self._contact(contact["phone"])
            elif not continuation:
                return self._handoff(contact, event, "not_a_service_inquiry", "A person will review this message. No service-reply permission was created.")
        gate = self._send_gate(contact, self._business(), "service_reply" if self.production else "inbound_reply", event)
        if gate:
            self._audit(contact["phone"], event["event_id"], "inbound_stored_without_automation", {"reason": gate})
            return {"status": "blocked", "reason": gate, "simulated": True}
        if rule.get("malicious"):
            return self._handoff(contact, event, "unsafe_instruction_rejected", "I can only help with approved cleaning information and appointment requests. I'll ask a person to help with your message.")
        if rule.get("ambiguous_booking"):
            return self._handoff(contact, event, "ambiguous_booking_details",
                                 "A person must confirm these conflicting or negated appointment details. No appointment was submitted.")
        if rule.get("intent") == "human":
            return self._handoff(contact, event, "customer_requested_human", "I'll pass this conversation to the owner. Automatic replies are paused until a person explicitly resumes them.")
        if rule.get("topics"):
            return self._faq(contact, event, rule)
        try:
            parsed = rule if self.production else self._understand(text, self._business(), event)
        except Exception as exc:
            code = "model_timeout" if isinstance(exc, ModelTimeout) else "model_unavailable" if isinstance(exc, ModelUnavailable) else "model_error"
            self._audit(contact["phone"], event["event_id"], "understanding_failed", {"code": code, "fallback": "human_handoff"})
            return self._handoff(contact, event, code, "I'm unable to safely process that request right now. I've passed it to the owner; no appointment has been confirmed.")
        # Never accept adapter text, consent, send instructions or arbitrary facts.
        if parsed.get("malicious"):
            return self._handoff(contact, event, "unsafe_instruction_rejected", "I can only help with approved cleaning information. I'll pass this to the owner.")
        if parsed.get("intent") == "human":
            return self._handoff(contact, event, "customer_requested_human", "I'll pass this conversation to the owner. Automatic replies are paused.")
        booking = json.loads(contact["booking"])
        area_mentioned = parsed.get("mentioned_area", "")
        if area_mentioned and not parsed.get("area"):
            return self._handoff(contact, event, "outside_approved_service_area", "That location is outside our approved service-area list. I'll ask the owner to review it; I can't promise service there.")
        if booking and not booking.get("area") and not any(parsed.get(k) for k in ("service", "desired_time", "contact", "area")) and len(text) <= 60:
            return self._handoff(contact, event, "unrecognized_service_area", "I can't verify that location against our service-area list. I'll pass your request to the owner.")
        if parsed.get("intent") == "booking" or (booking and any(parsed.get(k) for k in ("service", "area", "desired_time", "contact"))):
            return self._booking(contact, event, parsed)
        return self._handoff(contact, event, "unknown_question", "I don't have approved information for that question. I'll pass it to the owner rather than invent an answer.")

    def _consent(self, contact, event):
        consent = event.get("consent", {})
        if not isinstance(consent, dict):
            raise ValueError("consent must be an object")
        purposes = consent.get("purposes", [])
        if not isinstance(purposes, list) or any(not isinstance(p, str) for p in purposes):
            raise ValueError("consent purposes must be a list")
        valid_purposes = sorted(set(purposes) & PURPOSES)
        ctime = parse_time(consent["time"]) if consent.get("time") else None
        normalized = {"source": str(consent.get("source", ""))[:500], "time": stamp(ctime) if ctime else "",
                      "purposes": valid_purposes, "evidence": str(consent.get("evidence", ""))[:500],
                      "verified": consent.get("verified") is True}
        verified = (normalized["verified"] and bool(normalized["source"].strip()) and bool(normalized["evidence"].strip())
                    and bool(valid_purposes) and ctime is not None and ctime <= now_utc() + MAX_FUTURE_SKEW)
        if contact["consent_status"] == "revoked":
            # A local operator cannot overwrite a STOP with a claimed consent
            # record. Restoration must use fresh START plus verified enrollment.
            self._history(contact["phone"], event["event_id"], "enrollment_blocked_after_opt_out", normalized, "revoked", event["occurred_at"])
            return {"status": "blocked", "reason": "opt_out_requires_start", "simulated": True}
        self._history(contact["phone"], event["event_id"], "verified_enrollment" if verified else "unverified_record", normalized,
                      "active" if verified else contact["consent_status"], event["occurred_at"])
        if verified:
            self.db.execute("UPDATE contacts SET consent_status='active',enrollment=? WHERE phone=?", (dumps(normalized), contact["phone"]))
        self._audit(contact["phone"], event["event_id"], "consent_recorded", {"verified": bool(verified), "purposes": valid_purposes, "source": normalized["source"]})
        return {"status": "processed", "reason": "verified_consent_recorded" if verified else "unverified_consent_not_activated", "simulated": True}

    def _validate_event(self, event):
        if not isinstance(event, dict):
            raise ValueError("Event must be an object")
        event = copy.deepcopy(event)
        eid = event.get("event_id")
        phone = event.get("phone")
        kind = event.get("kind")
        if not isinstance(eid, str) or not eid.strip() or len(eid) > 160:
            raise ValueError("A short unique event_id is required")
        phone = self._validate_phone(phone)
        event["phone"] = phone
        if kind not in {"sms", "missed_call", "consent"}:
            raise ValueError("Unsupported event kind")
        origin = event.get("origin", "simulation")
        if origin not in {"simulation", "twilio"}:
            raise ValueError("Unsupported event origin")
        if origin == "twilio" and event.get("verified") is not True:
            raise ValueError("Production event requires a verified webhook")
        if self.production and (origin != "twilio" or event.get("verified") is not True):
            raise ValueError("Production intake requires a trusted platform event")
        if self.production and kind == "consent":
            raise ValueError("Production enrollment is not available through this endpoint")
        event["origin"] = origin
        event_time = parse_time(event["occurred_at"]) if event.get("occurred_at") else now_utc()
        event["occurred_at"] = stamp(event_time)
        if kind == "sms":
            if not isinstance(event.get("text"), str) or not event["text"].strip() or len(event["text"]) > 1600:
                raise ValueError("SMS text must be 1–1600 characters")
        if not isinstance(event.get("demo", {}), dict):
            raise ValueError("demo controls must be an object")
        return event

    def process_event(self, event):
        event = self._validate_event(event)
        with self.contact_barrier(event["phone"]), self._lock, self.db:
            return self._process_event_in_transaction(event)

    def _process_event_in_transaction(self, event):
        """Process a validated event under the caller's barrier and transaction."""
        eid, phone, kind, origin = (event[k] for k in ("event_id", "phone", "kind", "origin"))
        event_time = parse_time(event["occurred_at"])
        if self._privacy_duplicate(eid):
            return {"event_id": eid, "status": "duplicate", "reason": "purged_event_tombstone", "simulated": not self.production}
        previous = self.db.execute("SELECT result FROM events WHERE event_id=?", (eid,)).fetchone()
        if previous:
            return {"event_id": eid, "status": "duplicate", "reason": "event_already_processed", "original": json.loads(previous[0]), "simulated": True}
        contact = self._contact(phone)
        is_stop = kind == "sms" and (event["text"].strip().upper() in STOP_WORDS or
                   origin == "twilio" and event.get("opt_out_type") == "STOP")
        late = contact["last_event_at"] and event_time < parse_time(contact["last_event_at"])
        stale = event_time < now_utc() - MAX_EVENT_AGE
        future = event_time > now_utc() + MAX_FUTURE_SKEW
        if (late or stale or future) and not is_stop:
            reason = "out_of_order_event" if late else "expired_event" if stale else "future_event"
            if kind == "sms":
                self._message(phone, event["text"], "inbound", "received", eid, at=event["occurred_at"])
            result = {"status": "ignored", "reason": reason, "simulated": True}
            self._audit(phone, eid, "event_ignored", {"reason": reason})
        elif kind == "sms":
            result = self._sms(contact, event)
        elif kind == "consent":
            result = self._consent(contact, event)
        else:
            status = event.get("dial_status", "")
            self._message(phone, f"Simulated Dial result: {status or 'missing'}", "system", "note", eid, "missed_call_event")
            if self.production:
                result = {"status": "blocked", "reason": "production_missed_call_followup_disabled", "simulated": False}
                self._audit(phone, eid, "call_followup_skipped", {"reason": result["reason"]})
            elif status not in {"no-answer", "busy", "failed"}:
                result = {"status": "ignored", "reason": "not_a_supported_missed_dial_result", "simulated": True}
                self._audit(phone, eid, "call_followup_skipped", {"dial_status": status, "reason": result["reason"]})
            else:
                result = self._send(contact, self._business(), f"Hi, this is {self._business()['name']}. We missed your call. Reply with the cleaning service, area and preferred time you need, and the owner will follow up. This is not a confirmed appointment. Reply STOP to opt out.", "missed_call_followup", event)
        result["event_id"] = eid
        self.db.execute("INSERT INTO events(event_id,kind,phone,occurred_at,received_at,origin,status,result) VALUES (?,?,?,?,?,?,?,?)",
                        (eid, kind, phone, event["occurred_at"], stamp(), origin, result["status"], dumps(result)))
        # Regressive STOP is honored but never moves the event watermark back.
        if not contact["last_event_at"] or event_time > parse_time(contact["last_event_at"]):
            self.db.execute("UPDATE contacts SET last_event_at=? WHERE phone=?", (event["occurred_at"], phone))
        self.db.execute("UPDATE contacts SET last_activity=? WHERE phone=?", (stamp(), phone))
        return result

    def takeover(self, phone, active):
        phone = self._validate_phone(phone)
        if not isinstance(active, bool):
            raise ValueError("active must be a boolean")
        with self.contact_barrier(phone), self._lock, self.db:
            contact = self._contact(phone)
            self.db.execute("UPDATE contacts SET handoff=? WHERE phone=?", (int(active), phone))
            if active and self.outbox:
                rows = [dict(r) for r in self.db.execute("SELECT * FROM outbox WHERE phone=? AND status='queued' AND origin='automatic'", (phone,))]
                for row in rows:
                    self.outbox._status(row, "canceled", "HUMAN_TAKEOVER")
            self._audit(phone, None, "handoff_started" if active else "automation_explicitly_resumed", {"operator_action": True})
            self._message(phone, "Human takeover enabled; automatic replies paused." if active else "Operator explicitly resumed automatic replies; all send gates still apply.", "system", "note")
        return {"status": "saved", "phone": phone, "handoff": active, "simulated": True}

    def human_reply(self, phone, text, event_id=None, reply_to_event_id=None, confirm_service_inquiry=False):
        phone = self._validate_phone(phone)
        if not isinstance(text, str) or not text.strip() or len(text) > 1600:
            raise ValueError("Reply must be 1–1600 characters")
        if type(confirm_service_inquiry) is not bool or reply_to_event_id is not None and not isinstance(reply_to_event_id, str):
            raise ValueError("Service inquiry review fields are invalid")
        if confirm_service_inquiry and (not self.production or not reply_to_event_id):
            raise ValueError("Service inquiry review requires a real inbound source")
        with self.contact_barrier(phone), self._lock, self.db:
            if self.production and (not isinstance(event_id, str) or not event_id.strip() or len(event_id) > 160):
                raise ValueError("Production human reply requires a stable event_id")
            if event_id:
                if self._privacy_duplicate(event_id):
                    return {"status": "duplicate", "event_id": event_id, "reason": "purged_event_tombstone", "simulated": not self.production}
                previous = self.db.execute("SELECT result FROM events WHERE event_id=?", (event_id,)).fetchone()
                if previous:
                    return {"status": "duplicate", "event_id": event_id, "original": json.loads(previous[0]), "simulated": not self.production}
            contact = self._contact(phone)
            event = {"event_id": event_id or "operator-" + uuid.uuid4().hex, "kind": "operator", "origin": "operator" if self.production else "simulation"}
            if self.production and reply_to_event_id:
                already_authorized = reply_to_event_id == contact.get("reply_permission_event_id") and contact.get("reply_permission_basis") in {"rules", "operator_review_human_only"}
                if already_authorized:
                    reason = self._permission_gate(phone, reply_to_event_id, contact["reply_permission_basis"], contact["reply_permission_until"], human=True)
                elif confirm_service_inquiry:
                    reason, source = self._review_source(phone, reply_to_event_id)
                    if not reason:
                        reason = self._hourly_send_gate(contact, self._business())
                    if not reason:
                        self.db.execute("UPDATE contacts SET reply_permission_event_id=?,reply_permission_basis='operator_review_human_only',reply_permission_until=?,last_service_inquiry_at=? WHERE phone=?", (reply_to_event_id, source["reply_until"], source["occurred_at"], phone))
                        self._audit(phone, event["event_id"], "service_inquiry_operator_reviewed", {"source_event_id": reply_to_event_id, "resource_at": source["occurred_at"], "reply_until": source["reply_until"], "basis": "operator_review_human_only", "service_related_confirmed": True, "marketing_authorization": False})
                        contact = self._contact(phone)
                else:
                    reason = "service_inquiry_confirmation_required"
                if reason:
                    result = {"status": "blocked", "reason": reason, "simulated": False}
                    self._audit(phone, event["event_id"], "operator_reply", {"result": "blocked", "reason": reason, "source_event_id": reply_to_event_id, "automation_resumed": False})
                    self.db.execute("INSERT INTO events(event_id,kind,phone,occurred_at,received_at,origin,status,result) VALUES (?,?,?,?,?,?,?,?)", (event["event_id"], "operator", phone, stamp(), stamp(), "operator", "blocked", dumps(result)))
                    return result
            # Manual composing never silently resumes automation or bypasses STOP.
            purpose = "sms_inquiry" if self._active_scope(contact, "sms_inquiry") else "appointment_request"
            result = self._send(contact, self._business(), text.strip(), purpose, event, permit_handoff=True)
            self._audit(phone, event["event_id"], "operator_reply", {"result": result["status"], "automation_resumed": False})
            if self.production:
                self.db.execute("INSERT INTO events(event_id,kind,phone,occurred_at,received_at,origin,status,result) VALUES (?,?,?,?,?,?,?,?)", (event["event_id"], "operator", phone, stamp(), stamp(), "operator", result["status"], dumps(result)))
        return result

    def update_delivery(self, message_id, status, error_code=""):
        # In this demo there is no provider send SID. A provider callback cannot
        # turn an unsent dry-run row into a genuinely delivered message.
        if status not in {"failed", "simulated"}:
            raise ValueError("Dry-run delivery status must be failed or simulated")
        if not isinstance(error_code, str) or len(error_code) > 120 or not re.fullmatch(r"[A-Za-z0-9_ -]*", error_code):
            raise ValueError("error_code must be a short code, not provider response text")
        with self._lock, self.db:
            message = self.db.execute("SELECT * FROM messages WHERE id=? AND direction='outbound'", (message_id,)).fetchone()
            if not message:
                raise ValueError("Outbound message not found")
            if message["status"] == "failed" and status == "simulated":
                return {"status": "ignored", "reason": "terminal_failure_not_regressed", "simulated": True}
            self.db.execute("UPDATE messages SET status=?,error_code=? WHERE id=?", (status, error_code, message_id))
            self._audit(message["phone"], message["event_id"], "delivery_status_recorded", {"message_id": message_id, "status": status, "error_code": error_code})
            if status == "failed":
                self.db.execute("UPDATE contacts SET handoff=1 WHERE phone=?", (message["phone"],))
                self._audit(message["phone"], message["event_id"], "handoff_started", {"reason": "delivery_failure"})
        return {"status": "saved", "message_id": message_id, "delivery_status": status, "simulated": True}

    def reset(self, _initial=False):
        if self.production and not _initial:
            raise ValueError("Demo reset is disabled for the production service")
        business = self._validate_business(json.loads(self.business_path.read_text(encoding="utf-8")))
        contacts = json.loads((ROOT / "data" / "synthetic_contacts.json").read_text(encoding="utf-8"))
        base = now_utc() - timedelta(minutes=3)
        with self._lock, self.db:
            if self.outbox:
                for table in ("outbox_callbacks", "outbox_attempts", "outbox"):
                    self.db.execute(f"DELETE FROM {table}")
            for table in ("messages", "appointments", "consent_history", "events", "audit", "contacts", "settings"):
                self.db.execute(f"DELETE FROM {table}")
            self.db.execute("DELETE FROM sqlite_sequence")
            self.db.execute("INSERT INTO settings(key,value) VALUES ('business',?)", (dumps(business),))
            for index, item in enumerate(contacts):
                at = stamp(base + timedelta(seconds=index * 10))
                consent = item.get("consent") or {}
                if consent:
                    consent["time"] = at
                state = "active" if consent.get("verified") else "none"
                if item.get("revoked"):
                    state = "revoked"
                self.db.execute("INSERT INTO contacts(phone,name,consent_status,enrollment,last_activity,scenario) VALUES (?,?,?,?,?,?)",
                                (item["phone"], item["name"], state, dumps(consent), at, item["scenario"]))
                if consent:
                    self._history(item["phone"], f"seed-consent-{index}", "synthetic_enrollment" if consent.get("verified") else "unverified_record", consent, "active" if consent.get("verified") else "none", at)
            self._message("+12025550101", "What are your opening hours?", "inbound", "received", "seed-faq", at=stamp(base))
            self._message("+12025550101", "Our hours: " + business["hours"] + ".", "outbound", "simulated", "seed-faq", "inbound_reply", at=stamp(base + timedelta(seconds=1)))
            self.db.execute("UPDATE contacts SET booking=? WHERE phone='+12025550102'", (dumps({"service": "Deep cleaning"}),))
            self._message("+12025550102", "I need a deep cleaning.", "inbound", "received", "seed-booking", at=stamp(base + timedelta(seconds=10)))
            self._message("+12025550102", "Which area is the property in? We serve Oakwood, Riverside, Maple Heights.", "outbound", "simulated", "seed-booking", "inbound_reply", at=stamp(base + timedelta(seconds=11)))
            self.db.execute("UPDATE contacts SET handoff=1 WHERE phone IN ('+12025550103','+12025550104')")
            self._message("+12025550103", "Can you clean my home in Hillcrest?", "inbound", "received", "seed-outside", at=stamp(base + timedelta(seconds=20)))
            self._message("+12025550103", "Hillcrest is outside our approved service-area list. I'll ask the owner to review it; I can't promise service there.", "outbound", "simulated", "seed-outside", "inbound_reply", at=stamp(base + timedelta(seconds=21)))
            self._audit("+12025550103", "seed-outside", "handoff_started", {"reason": "outside_approved_service_area"})
            self._message("+12025550104", "Could I speak with a person?", "inbound", "received", "seed-human", at=stamp(base + timedelta(seconds=30)))
            self._message("+12025550104", "The owner is reviewing this conversation. Automatic replies are paused.", "system", "note", "seed-human", at=stamp(base + timedelta(seconds=31)))
            self._audit("+12025550104", "seed-human", "handoff_started", {"reason": "customer_requested_human"})
            self._message("+12025550105", "Simulated Dial result: no-answer; follow-up blocked: no verified scoped consent.", "system", "note", "seed-no-consent", at=stamp(base + timedelta(seconds=40)))
            self._audit("+12025550105", "seed-no-consent", "send_blocked", {"reason": "verified_scoped_consent_required", "purpose": "missed_call_followup"})
            self._message("+12025550106", "Simulated Dial result: no-answer", "system", "note", "seed-consented-call", at=stamp(base + timedelta(seconds=50)))
            self._message("+12025550106", f"Hi, this is {business['name']}. We missed your call. Reply with the cleaning service, area and preferred time you need. The owner must confirm any request. Reply STOP to opt out.", "outbound", "simulated", "seed-consented-call", "missed_call_followup", at=stamp(base + timedelta(seconds=51)))
            sam = next(c for c in contacts if c["phone"] == "+12025550107")
            self._history(sam["phone"], "seed-stop", "opt_out", {**sam["consent"], "source": "Synthetic inbound STOP", "evidence": "seed-stop"}, "revoked", stamp(base + timedelta(seconds=60)))
            self._message(sam["phone"], "STOP", "inbound", "received", "seed-stop", at=stamp(base + timedelta(seconds=60)))
            self._message(sam["phone"], "You are unsubscribed. No regular messages will be sent. Reply START to request restoration of previously verified permissions.", "outbound", "simulated", "seed-stop", "opt_out_confirmation", at=stamp(base + timedelta(seconds=61)))
            self._message(sam["phone"], "Are you open today?", "inbound", "received", "seed-after-stop", at=stamp(base + timedelta(seconds=62)))
            self._audit(sam["phone"], "seed-after-stop", "inbound_stored_without_automation", {"reason": "opted_out"})
            self._audit(None, None, "demo_reset", {"synthetic_contacts": len(contacts), "transport": "dry_run", "live_sending": False})
        return {"status": "reset", "simulated": True}

    def snapshot(self):
        with self._lock:
            contacts = []
            conversations = []
            for row in self.db.execute("SELECT * FROM contacts ORDER BY phone"):
                item = dict(row)
                enrollment = json.loads(item.pop("enrollment"))
                item["booking"] = json.loads(item["booking"])
                item["handoff"] = bool(item["handoff"])
                item["blocked"] = bool(item["blocked"])
                item["carrier_blocked"] = bool(item["carrier_blocked"])
                item["consent"] = enrollment
                item["consent_purposes"] = enrollment.get("purposes", []) if item["consent_status"] == "active" else []
                item["reply_sources"] = self._reply_sources(item)
                contacts.append(item)
                messages = [dict(m) for m in self.db.execute("SELECT * FROM messages WHERE phone=? ORDER BY id", (item["phone"],))]
                conversations.append({"phone": item["phone"], "name": item["name"], "handoff": item["handoff"],
                                      "consent_status": item["consent_status"], "messages": messages})
            history = []
            for row in self.db.execute("SELECT * FROM consent_history ORDER BY id DESC"):
                item = dict(row)
                item["purposes"] = json.loads(item["purposes"])
                item["verified"] = bool(item["verified"])
                history.append(item)
            events = []
            for row in self.db.execute("SELECT * FROM events ORDER BY received_at DESC"):
                item = dict(row)
                item["result"] = json.loads(item["result"])
                events.append(item)
            audit = []
            for row in self.db.execute("SELECT * FROM audit ORDER BY id DESC"):
                item = dict(row)
                item["details"] = json.loads(item["details"])
                audit.append(item)
            appointments = [dict(a) for a in self.db.execute("SELECT * FROM appointments ORDER BY id DESC")]
            all_messages = [m for c in conversations for m in c["messages"]]
            return {"mode": "Local service / gated outbox" if self.production else "Demo / Simulated", "live_sending": self.production and self.send_policy.live_enabled, "transport": "durable_outbox" if self.production else "dry_run",
                    "model_label": "Approved deterministic rules only; no live AI" if self.production else getattr(self.model_adapter, "label", "Structured adapter; provider capability unverified"),
                    "production": self.production, "outbox": self.outbox.snapshot() if self.outbox else {},
                    "business": self._business(), "contacts": contacts, "conversations": conversations,
                    "appointments": appointments, "consent_history": history, "events": events, "audit": audit,
                    "metrics": {"contacts": len(contacts), "messages": len(all_messages), "appointments": len(appointments),
                                "handoffs": sum(c["handoff"] for c in contacts), "blocked": sum(c["blocked"] or c["consent_status"] == "revoked" for c in contacts),
                                "simulated_outbound": sum(m["direction"] == "outbound" and m["status"] == "simulated" for m in all_messages),
                                "failed": sum(m["status"] == "failed" for m in all_messages)}}
