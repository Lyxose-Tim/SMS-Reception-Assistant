"""Production domain and durable-outbox tests use fake providers only."""
import hashlib
import hmac
import os
import tempfile
import threading
import unittest
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from reception.engine import Engine, now_utc, stamp
from reception.outbox import SendPolicy, sms_segments
from reception.provider import SendRejected, SendUncertain


PHONE = "+12025550111"
SENDER = "+12025550190"


class FakeProvider:
    def __init__(self, behavior=None):
        self.calls = []
        self.behavior = behavior
        self.proofs = {}

    def send(self, to, body, callback_url):
        self.calls.append((to, body, callback_url))
        if self.behavior:
            return self.behavior(to, body, callback_url)
        sid = "SM" + f"{len(self.calls):032x}"
        self.proofs[sid] = {"sid": sid, "status": "delivered", "error_code": "", "verified": True, "to": to, "from": SENDER, "body": body}
        return {"sid": sid, "status": "queued", "error_code": ""}

    def fetch_status(self, sid):
        return self.proofs[sid]


class SegmentTests(unittest.TestCase):
    def test_gsm_single_multipart_and_extension_counts(self):
        self.assertEqual(sms_segments("A" * 160)["segments"], 1)
        self.assertEqual(sms_segments("A" * 161)["segments"], 2)
        self.assertEqual(sms_segments("{" * 80)["segments"], 1)
        self.assertEqual(sms_segments("{" * 81)["segments"], 2)
        self.assertEqual(sms_segments("{" * 81)["units"], 162)

    def test_ucs2_counts_utf16_not_codepoints(self):
        self.assertEqual(sms_segments("清" * 70)["segments"], 1)
        self.assertEqual(sms_segments("清" * 71)["segments"], 2)
        self.assertEqual(sms_segments("😀" * 35)["segments"], 1)
        self.assertEqual(sms_segments("😀" * 36)["segments"], 2)
        self.assertEqual(sms_segments("😀" * 36)["encoding"], "UCS-2")

    def test_normalization_is_exact_body_sent_and_controls_rejected(self):
        data = sms_segments("Cafe\u0301\r\nHello")
        self.assertEqual(data["body"], "Café\nHello")
        self.assertEqual(data["encoding"], "GSM-7")
        with self.assertRaises(ValueError):
            sms_segments("hello\x00world")


class ProductionOutboxTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "live.sqlite3"
        self.policy = SendPolicy(live_enabled=True, sender_number=SENDER, public_base_url="https://sms.example.test", allowed_recipients=(PHONE,), optout_reviewed=True, max_messages_per_minute=20)
        self.engine = Engine(self.path, production=True, seed_demo=False, send_policy=self.policy, privacy_key="synthetic-privacy-key-32-characters-only")
        business = self.engine.snapshot()["business"]
        business["approved_faqs"] = [{"topic": "hours", "answer": "We open Monday to Friday, 8 AM to 6 PM.", "approved": True}, {"topic": "requests", "answer": "Requests require owner confirmation.", "approved": True}]
        self.engine.save_business(business)
        self.engine.db.executescript((Path(__file__).resolve().parents[1] / "migrations/002_service_inbox.sql").read_text())
        self.seq = 0

    def tearDown(self):
        self.engine.close()
        self.temp.cleanup()

    def inbound(self, text="What are your hours?", phone=PHONE, **extra):
        self.seq += 1
        label = extra.pop("event_id", f"live-{self.seq}")
        sid = "SM" + hashlib.sha256(label.encode()).hexdigest()[:32]
        occurred_at = extra.get("occurred_at") or stamp()
        event = {"event_id": "twilio:" + sid, "kind": "sms", "phone": phone, "text": text,
                 "origin": "twilio", "verified": True, "provider_fresh": True, "provider_keyword_verified": True,
                 "provider_sid": sid, "occurred_at": occurred_at}
        event.update(extra)
        # Domain fixtures represent a durable, verified fake-provider resource.
        # ServicePipelineTests exercise the actual accept/fetch/atomic apply path.
        if not self.engine.db.execute("SELECT 1 FROM sqlite_master WHERE name='provider_inbox'").fetchone():
            self.engine.db.executescript((Path(__file__).resolve().parents[1] / "migrations/002_service_inbox.sql").read_text())
        with self.engine._lock, self.engine.db:
            self.engine.db.execute("""INSERT OR IGNORE INTO provider_inbox
                (sid,phone,recipient,body,opt_out_type,received_at,status,next_attempt_at,resource_at)
                VALUES (?,?,?,?,?,?,'processed',?,?)""",
                (sid, phone, SENDER, text, event.get("opt_out_type", ""), stamp(), stamp(), occurred_at))
        return self.engine.process_event(event)

    def item(self, queue_id=None):
        items = self.engine.outbox.snapshot()["items"]
        return next(r for r in items if r["id"] == queue_id) if queue_id else items[0]

    def contact(self):
        return next(c for c in self.engine.snapshot()["contacts"] if c["phone"] == PHONE)

    def nonce(self, queue_id):
        return self.engine.db.execute("SELECT callback_nonce FROM outbox WHERE id=?", (queue_id,)).fetchone()[0]

    def test_config_only_initialization_is_empty_and_no_historical_queue(self):
        self.assertEqual(self.engine.snapshot()["contacts"], [])
        self.assertEqual(self.engine.outbox.snapshot()["items"], [])
        with self.assertRaises(ValueError):
            self.engine.reset()

    def test_inbound_reply_queues_atomically_without_marketing_enrollment(self):
        result = self.inbound()
        self.assertEqual(result["status"], "queued")
        self.assertFalse(result["sent"])
        self.assertEqual(self.item()["body"], "We open Monday to Friday, 8 AM to 6 PM.")
        self.assertEqual(self.contact()["consent_status"], "none")
        self.assertEqual(self.contact()["consent_purposes"], [])
        self.assertTrue(self.contact()["reply_permission_until"])
        messages = self.engine.snapshot()["conversations"][0]["messages"]
        self.assertTrue(messages[0]["provider_sid"].startswith("SM"))
        self.assertEqual(len(self.engine.snapshot()["events"]), 1)
        self.assertNotIn("callback_nonce", self.item())

    def test_duplicate_inbound_produces_one_queue_item(self):
        self.inbound(event_id="same-inbound")
        result = self.inbound(event_id="same-inbound")
        self.assertEqual(result["status"], "duplicate")
        self.assertEqual(len(self.engine.outbox.snapshot()["items"]), 1)

    def test_production_never_calls_model_and_unknown_handoffs(self):
        class ExplosiveModel:
            def understand(self, *args):
                raise AssertionError("Production called a model")
        self.engine.model_adapter = ExplosiveModel()
        self.inbound()
        result = self.inbound("Does your cleaning service remove silk stains?")
        self.assertEqual(result["status"], "handoff")
        self.assertEqual(len(self.engine.outbox.snapshot()["items"]), 1)

    def test_unapproved_faq_and_booking_route_to_human(self):
        config = self.engine.snapshot()["business"]
        config["approved_faqs"] = []
        self.engine.save_business(config)
        result = self.inbound()
        self.assertEqual(result["reason"], "unapproved_faq")
        self.assertEqual(self.engine.outbox.snapshot()["items"], [])

    def test_long_multi_faq_response_persists_inbound_and_handoff_without_queueing(self):
        config = self.engine.snapshot()["business"]
        config["approved_faqs"] = [{"topic": topic, "answer": "A" * 750, "approved": True} for topic in ("hours", "areas", "services", "pricing")]
        self.engine.save_business(config)
        result = self.inbound("What services do you offer, what are your hours and service areas, and your prices?")
        self.assertEqual(result["reason"], "reply_length_or_segment_limit")
        self.assertEqual(result["status"], "handoff")
        self.assertEqual(self.engine.outbox.snapshot()["items"], [])
        self.assertFalse(self.engine.outbox.snapshot()["paused"])
        self.assertEqual(len(self.engine.snapshot()["events"]), 1)
        self.assertEqual(self.engine.snapshot()["conversations"][0]["messages"][0]["direction"], "inbound")

    def test_oversized_booking_prompt_persists_request_state_without_queue_failure(self):
        config = self.engine.snapshot()["business"]
        config["services"] = [{"name": f"Service {i}: " + "A" * 200, "description": "", "pricing": ""} for i in range(10)]
        self.engine.save_business(config)
        result = self.inbound("I'd like to book a cleaning")
        self.assertEqual(result["status"], "handoff")
        self.assertEqual(result["reason"], "reply_length_or_segment_limit")
        self.assertEqual(len(self.engine.snapshot()["events"]), 1)
        self.assertTrue(self.contact()["booking"]["_in_progress"])
        self.assertTrue(self.contact()["booking_updated_at"])
        self.assertEqual(self.engine.outbox.snapshot()["items"], [])
        self.assertFalse(self.engine.outbox.snapshot()["paused"])

    def test_segment_or_control_invalid_faq_handoffs_before_outbox_insert(self):
        for answer in ("A" * 600, "Approved text\x00invalid control"):
            config = self.engine.snapshot()["business"]
            config["approved_faqs"] = [{"topic": "hours", "answer": answer, "approved": True}]
            self.engine.save_business(config)
            self.engine.takeover(PHONE, False)
            result = self.inbound()
            self.assertEqual(result["reason"], "reply_length_or_segment_limit")
            self.assertEqual(self.engine.outbox.snapshot()["items"], [])

    def test_faq_configuration_is_explicit_unique_and_bounded(self):
        config = self.engine.snapshot()["business"]
        for faqs in (
            [{"topic": "hours", "answer": "", "approved": True}],
            [{"topic": "hours", "answer": "a", "approved": True}] * 2,
            [{"topic": "hours", "answer": "a", "approved": True}] * 6,
            [{"topic": "marketing", "answer": "a", "approved": True}],
        ):
            with self.subTest(faqs=faqs), self.assertRaises(ValueError):
                self.engine.save_business({**config, "approved_faqs": faqs})
        legacy = {k: v for k, v in config.items() if k != "approved_faqs"}
        self.engine.save_business(legacy)
        self.assertEqual(self.engine.snapshot()["business"]["approved_faqs"], config["approved_faqs"])

    def test_missed_calls_and_local_enrollment_are_disabled(self):
        result = self.engine.process_event({"event_id": "live-call", "kind": "missed_call", "phone": PHONE, "origin": "twilio", "verified": True, "dial_status": "no-answer"})
        self.assertEqual(result["reason"], "production_missed_call_followup_disabled")
        with self.assertRaises(ValueError):
            self.engine.process_event({"event_id": "local", "kind": "sms", "phone": PHONE, "text": "START", "origin": "simulation"})
        with self.assertRaises(ValueError):
            self.engine.process_event({"event_id": "enroll", "kind": "consent", "phone": PHONE, "origin": "twilio", "verified": True})
        self.assertEqual(self.engine.outbox.snapshot()["items"], [])

    def test_stop_cancels_both_automatic_and_human_queued_rows(self):
        self.inbound()
        self.engine.human_reply(PHONE, "The owner is reviewing it.", event_id="manual-1")
        result = self.inbound("STOP", provider_managed_optout=True)
        self.assertNotIn("reply", result)
        self.assertTrue(all(r["status"] == "canceled" for r in self.engine.outbox.snapshot()["items"]))
        self.assertEqual(self.contact()["consent_status"], "revoked")
        result = self.engine.human_reply(PHONE, "hello", event_id="manual-2")
        self.assertEqual(result["reason"], "opted_out")

    def test_platform_managed_stop_start_help_do_not_duplicate_confirmations(self):
        self.inbound("STOP", provider_managed_optout=True)
        self.inbound("HELP", opt_out_type="HELP", provider_managed_optout=True)
        result = self.inbound("START", provider_managed_optout=True)
        self.assertEqual(result["reason"], "provider_managed_start_service_reply_only")
        self.assertEqual(self.contact()["consent_status"], "none")
        self.assertEqual(self.contact()["consent_purposes"], [])
        self.assertEqual(self.engine.outbox.snapshot()["items"], [])

    def test_plain_help_and_info_are_provider_managed_without_any_state_change(self):
        self.inbound("STOP")
        before = self.contact()
        for word in ("HELP", "INFO"):
            result = self.inbound(word)
            self.assertEqual(result["reason"], "provider_handled_help")
        after = self.contact()
        self.assertEqual(after["consent_status"], before["consent_status"])
        self.assertEqual(after["handoff"], before["handoff"])
        self.assertEqual(after["reply_permission_until"], before["reply_permission_until"])
        self.assertEqual(self.engine.outbox.snapshot()["items"], [])

    def test_start_keyword_must_match_reviewed_standard_or_advanced_profile(self):
        self.inbound("STOP")
        result = self.inbound("YES", opt_out_type="START")
        self.assertEqual(result["reason"], "unsupported_platform_start_keyword_or_profile")
        self.assertEqual(self.contact()["consent_status"], "revoked")
        self.engine.send_policy = replace(self.policy, optout_profile="advanced")
        result = self.inbound("START")
        self.assertEqual(result["reason"], "unsupported_platform_start_keyword_or_profile")
        self.assertEqual(self.contact()["consent_status"], "revoked")
        result = self.inbound("START", opt_out_type="START")
        self.assertEqual(result["reason"], "provider_managed_start_service_reply_only")
        self.assertEqual(self.contact()["consent_status"], "none")

    def test_production_multiturn_booking_accepts_only_structured_current_continuations(self):
        self.inbound("I need a deep cleaning")
        permission = self.contact()["reply_permission_until"]
        area = self.inbound("Riverside")
        self.assertIn("What day and time", area["reply"])
        desired = self.inbound("tomorrow2pm")
        self.assertIn("preferred contact", desired["reply"])
        result = self.inbound("customer@example.test")
        self.assertEqual(result["appointment"]["status"], "pending_owner_confirmation")
        self.assertEqual(result["appointment"]["area"], "Riverside")
        self.assertEqual(self.contact()["reply_permission_until"], permission)
        self.assertFalse(result["sent"])

    def test_unrelated_or_expired_booking_continuation_does_not_grant_permission(self):
        self.inbound("I need a deep cleaning")
        permission = self.contact()["reply_permission_until"]
        result = self.inbound("Send me casino discounts")
        self.assertEqual(result["status"], "handoff")
        self.assertEqual(self.contact()["reply_permission_until"], permission)
        self.engine.takeover(PHONE, False)
        expired = stamp(now_utc() - timedelta(seconds=1))
        self.engine.db.execute("UPDATE contacts SET reply_permission_until=? WHERE phone=?", (expired, PHONE))
        self.engine.db.commit()
        result = self.inbound("Riverside")
        self.assertEqual(result["status"], "handoff")
        self.assertEqual(self.contact()["reply_permission_until"], expired)

    def test_start_needs_fresh_verified_platform_keyword_and_never_enrolls(self):
        self.inbound("STOP")
        result = self.inbound("START", provider_keyword_verified=False)
        self.assertEqual(result["reason"], "trusted_fresh_platform_start_required")
        self.assertEqual(self.contact()["consent_status"], "revoked")
        self.inbound("START")
        self.assertEqual(self.contact()["consent_status"], "none")
        self.assertEqual(self.contact()["consent_purposes"], [])

    def test_human_reply_uses_recent_service_window_and_stable_uuid(self):
        self.inbound()
        self.engine.takeover(PHONE, True)
        first = self.engine.human_reply(PHONE, "The owner is reviewing it.", event_id="manual-uuid")
        second = self.engine.human_reply(PHONE, "Changed body", event_id="manual-uuid")
        self.assertEqual(first["status"], "queued")
        self.assertEqual(second["status"], "duplicate")
        self.assertTrue(self.contact()["handoff"])
        with self.assertRaises(ValueError):
            self.engine.human_reply(PHONE, "missing UUID")
        self.engine.db.execute("UPDATE contacts SET reply_permission_until=? WHERE phone=?", (stamp(now_utc() - timedelta(seconds=1)), PHONE))
        self.engine.db.commit()
        result = self.engine.human_reply(PHONE, "too late", event_id="late-uuid")
        self.assertEqual(result["reason"], "service_reply_window_expired")

    def test_default_live_gate_never_calls_provider(self):
        self.inbound()
        self.engine.outbox.policy = replace(self.policy, live_enabled=False)
        provider = FakeProvider()
        result = self.engine.outbox.run_once(provider)
        self.assertEqual(result["reason"], "live_sending_disabled")
        self.assertEqual(provider.calls, [])

    def test_restore_review_gate_cannot_be_cleared_by_operator_pause_toggle(self):
        self.inbound()
        self.engine.db.execute("INSERT INTO settings(key,value) VALUES ('restore_review_required','true')")
        self.engine.db.commit()
        self.engine.outbox.pause(False)
        provider = FakeProvider()
        result = self.engine.outbox.run_once(provider)
        self.assertEqual(result["reason"], "restore_review_required")
        self.assertTrue(self.engine.outbox.snapshot()["restore_review_required"])
        self.assertEqual(provider.calls, [])

    def test_send_uses_claimed_queue_normalized_body_and_callback(self):
        self.inbound()
        provider = FakeProvider()
        result = self.engine.outbox.run_once(provider)
        self.assertEqual(result["delivery_status"], "accepted")
        self.assertEqual(len(provider.calls), 1)
        query = parse_qs(urlsplit(provider.calls[0][2]).query)
        self.assertEqual(int(query["outbox_id"][0]), self.item()["id"])
        self.assertEqual(query["token"][0], self.nonce(self.item()["id"]))
        self.assertEqual(self.item()["attempt_count"], 1)
        self.assertTrue(self.item()["provider_sid"])

    def test_early_callback_before_rest_result_cannot_regress_delivery(self):
        self.inbound()
        sid = "SM" + "a" * 32
        def early(to, body, url):
            query = parse_qs(urlsplit(url).query)
            self.engine.outbox.apply_callback(int(query["outbox_id"][0]), query["token"][0], sid, "delivered")
            return {"sid": sid, "status": "accepted", "error_code": ""}
        provider = FakeProvider(early)
        self.engine.outbox.run_once(provider)
        self.assertEqual(self.item()["status"], "delivered")
        result = self.engine.outbox.apply_callback(self.item()["id"], self.nonce(self.item()["id"]), sid, "delivered")
        self.assertEqual(result["status"], "duplicate")

    def test_terminal_early_callback_wins_over_malformed_rest_result(self):
        self.inbound()
        sid = "SM" + "a" * 32
        def early(to, body, url):
            query = parse_qs(urlsplit(url).query)
            self.engine.outbox.apply_callback(int(query["outbox_id"][0]), query["token"][0], sid, "delivered")
            return None
        self.engine.outbox.run_once(FakeProvider(early))
        self.assertEqual(self.item()["status"], "delivered")

    def test_callback_nonce_mismatch_or_unsubmitted_binding_rejected(self):
        self.inbound()
        row = self.item()
        with self.assertRaises(ValueError):
            self.engine.outbox.apply_callback(row["id"], "wrong-token", "SM" + "1" * 32, "sent")
        with self.assertRaises(ValueError):
            self.engine.outbox.apply_callback(row["id"], self.nonce(row["id"]), "SM" + "1" * 32, "sent")

    def test_sid_conflict_freezes_and_terminal_callback_never_regresses(self):
        self.inbound()
        self.engine.outbox.run_once(FakeProvider())
        row = self.item()
        token = self.nonce(row["id"])
        self.engine.outbox.apply_callback(row["id"], token, row["provider_sid"], "sent")
        result = self.engine.outbox.apply_callback(row["id"], token, "SM" + "b" * 32, "delivered")
        self.assertEqual(result["status"], "conflict")
        self.assertEqual(self.item()["status"], "uncertain")
        self.assertTrue(self.item()["conflict"])

    def test_terminal_failure_and_progress_cannot_regress(self):
        self.inbound()
        self.engine.outbox.run_once(FakeProvider())
        row = self.item()
        token = self.nonce(row["id"])
        self.engine.outbox.apply_callback(row["id"], token, row["provider_sid"], "sent")
        self.engine.outbox.apply_callback(row["id"], token, row["provider_sid"], "sending")
        self.assertEqual(self.item()["status"], "sent")
        self.engine.outbox.apply_callback(row["id"], token, row["provider_sid"], "undelivered", "30003")
        self.engine.outbox.apply_callback(row["id"], token, row["provider_sid"], "delivered")
        self.assertEqual(self.item()["status"], "undelivered")

    def test_unknown_transport_failure_is_frozen_without_automatic_retry(self):
        self.inbound()
        def uncertain(*args):
            raise SendUncertain("transport_timeout")
        provider = FakeProvider(uncertain)
        self.assertEqual(self.engine.outbox.run_once(provider)["status"], "uncertain")
        self.assertEqual(self.engine.outbox.run_once(provider)["status"], "idle")
        self.assertEqual(len(provider.calls), 1)
        self.assertEqual(self.engine.outbox.reconcile(self.item()["id"], provider)["reason"], "needs_manual_provider_evidence")

    def test_unclassified_exception_or_invalid_response_is_uncertain(self):
        self.inbound()
        provider = FakeProvider(lambda *args: {"unexpected": "response"})
        self.assertEqual(self.engine.outbox.run_once(provider)["status"], "uncertain")
        self.assertEqual(len(provider.calls), 1)

    def test_definite_429_retries_are_capped_and_quota_reservation_is_unique(self):
        self.inbound()
        self.engine.outbox.policy = replace(self.policy, max_messages_per_day=1, max_daily_segments=1)
        def reject(*args):
            raise SendRejected("20429", retryable=True, http_status=429)
        provider = FakeProvider(reject)
        for number in range(3):
            result = self.engine.outbox.run_once(provider)
            self.assertEqual(result["status"], "retry_scheduled" if number < 2 else "failed")
            self.assertEqual(result["error_code"], "20429")
            self.assertEqual(self.item()["error_code"], "20429")
            self.engine.db.execute("UPDATE outbox SET next_attempt_at=NULL")
            self.engine.db.commit()
        self.assertEqual(len(provider.calls), 3)
        self.assertEqual(self.item()["attempt_count"], 3)
        self.assertEqual(self.engine.outbox.run_once(provider)["status"], "idle")

    def test_definite_400_never_retries(self):
        self.inbound()
        def rejected(*args):
            raise SendRejected("21211")
        provider = FakeProvider(rejected)
        self.assertEqual(self.engine.outbox.run_once(provider)["status"], "failed")
        self.assertEqual(self.engine.outbox.run_once(provider)["status"], "idle")
        self.assertEqual(len(provider.calls), 1)

    def test_carrier_21610_blocks_cancels_and_only_platform_start_clears(self):
        self.inbound()
        self.inbound()
        def opted_out(*args):
            raise SendRejected("21610")
        self.engine.outbox.run_once(FakeProvider(opted_out))
        self.assertTrue(self.contact()["carrier_blocked"])
        self.assertEqual(sorted(r["status"] for r in self.engine.outbox.snapshot()["items"]), ["canceled", "failed"])
        self.engine.takeover(PHONE, False)
        self.assertEqual(self.inbound()["reason"], "contact_blocked")
        self.inbound("START", provider_keyword_verified=False)
        self.assertTrue(self.contact()["carrier_blocked"])
        self.inbound("START")
        self.assertFalse(self.contact()["carrier_blocked"])
        self.assertEqual(self.contact()["consent_status"], "none")

    def test_pause_kill_allowlist_handoff_and_segment_gates(self):
        self.inbound()
        provider = FakeProvider()
        self.engine.outbox.pause(True)
        self.assertEqual(self.engine.outbox.run_once(provider)["reason"], "outbox_paused")
        self.engine.outbox.pause(False)
        kill = Path(self.temp.name) / "STOP"
        kill.touch()
        self.engine.outbox.policy = replace(self.policy, emergency_stop_file=str(kill))
        self.assertEqual(self.engine.outbox.run_once(provider)["reason"], "emergency_stop_file")
        kill.unlink()
        self.engine.outbox.policy = replace(self.policy, allowed_recipients=())
        self.assertEqual(self.engine.outbox.run_once(provider)["reason"], "recipient_not_allowlisted")
        self.assertEqual(provider.calls, [])
        self.engine.outbox.policy = self.policy
        self.inbound()
        self.engine.takeover(PHONE, True)
        self.assertTrue(all(r["status"] == "canceled" for r in self.engine.outbox.snapshot()["items"]))
        self.engine.send_policy = replace(self.policy, max_segments_per_message=1)
        with self.assertRaisesRegex(ValueError, "segment limits"):
            self.engine.human_reply(PHONE, "清" * 140, event_id="long-manual")
        self.assertEqual(provider.calls, [])

    def test_window_is_rechecked_and_expired_queue_is_canceled(self):
        self.inbound()
        self.engine.db.execute("UPDATE contacts SET reply_permission_until=? WHERE phone=?", (stamp(now_utc() - timedelta(seconds=1)), PHONE))
        self.engine.db.execute("UPDATE outbox SET permission_until=?", (stamp(now_utc() - timedelta(seconds=1)),))
        self.engine.db.commit()
        provider = FakeProvider()
        self.assertEqual(self.engine.outbox.run_once(provider)["reason"], "service_reply_window_expired")
        self.assertEqual(provider.calls, [])

    def test_stop_between_claim_and_final_check_prevents_submission(self):
        self.inbound()
        original = self.engine.outbox._gate
        checks = [0]
        def stop_before_second_gate(row):
            checks[0] += 1
            if checks[0] == 2:
                self.inbound("STOP")
            return original(row)
        self.engine.outbox._gate = stop_before_second_gate
        provider = FakeProvider()
        result = self.engine.outbox.run_once(provider)
        self.assertEqual(result["reason"], "opted_out")
        self.assertEqual(provider.calls, [])

    def test_day_and_minute_quota_rechecked_before_submission(self):
        self.inbound()
        self.inbound()
        self.engine.outbox.policy = replace(self.policy, max_messages_per_minute=1)
        provider = FakeProvider()
        self.engine.outbox.run_once(provider)
        self.assertEqual(self.engine.outbox.run_once(provider)["reason"], "minute_message_limit")
        self.assertEqual(len(provider.calls), 1)

    def test_restart_submitting_is_uncertain_queued_is_retained(self):
        first = self.inbound()
        second = self.inbound()
        self.engine.db.execute("UPDATE outbox SET status='submitting',attempt_count=1 WHERE id=?", (first["outbox_id"],))
        self.engine.db.commit()
        self.engine.close()
        self.engine = Engine(self.path, production=True, seed_demo=False, send_policy=self.policy)
        self.assertEqual(self.item(first["outbox_id"])["status"], "uncertain")
        self.assertEqual(self.item(second["outbox_id"])["status"], "queued")
        provider = FakeProvider()
        self.engine.outbox.run_once(provider)
        self.assertEqual(len(provider.calls), 1)
        self.assertEqual(self.item(first["outbox_id"])["status"], "uncertain")

    def test_known_sid_reconcile_uses_bound_provider_evidence_never_resends(self):
        self.inbound()
        provider = FakeProvider()
        self.engine.outbox.run_once(provider)
        row = self.item()
        result = self.engine.outbox.reconcile(row["id"], provider)
        self.assertEqual(result["delivery_status"], "delivered")
        self.assertEqual(len(provider.calls), 1)
        provider.proofs[row["provider_sid"]]["to"] = "+12025550112"
        self.assertEqual(self.engine.outbox.reconcile(row["id"], provider)["reason"], "provider_evidence_mismatch")

    def test_privacy_suppression_survives_contact_deletion_and_tombstones_dedup(self):
        self.inbound("STOP")
        digest = hmac.new(self.engine.privacy_key.encode(), PHONE.encode(), hashlib.sha256).hexdigest()
        self.assertTrue(self.engine.db.execute("SELECT 1 FROM privacy_suppressions WHERE phone_hash=?", (digest,)).fetchone())
        self.engine.db.execute("DELETE FROM consent_history")
        self.engine.db.execute("DELETE FROM messages")
        self.engine.db.execute("DELETE FROM events")
        self.engine.db.execute("DELETE FROM contacts")
        source = "twilio:SM" + hashlib.sha256(b"deleted-event").hexdigest()[:32]
        self.engine.db.execute("INSERT INTO privacy_dedup(event_hash,created_at) VALUES (?,?)", (hashlib.sha256(source.encode()).hexdigest(), stamp()))
        self.engine.db.commit()
        result = self.inbound()
        self.assertEqual(result["reason"], "opted_out")
        result = self.inbound(event_id="deleted-event")
        self.assertEqual(result["reason"], "purged_event_tombstone")

    @unittest.skipUnless(os.name in {"posix", "nt"}, "Native local offline lock acceptance requires Windows or Linux")
    def test_actual_offline_delete_reopen_keeps_stop_and_provider_replay_blocked(self):
        from reception.operations import delete_contact
        key = self.engine.privacy_key
        self.inbound("STOP", event_id="twilio:deleted-provider-sid")
        barrier = self.contact()["revoked_at"]
        self.engine.close()
        delete_contact(self.path, PHONE, key, offline_confirmed=True)
        self.engine = Engine(self.path, production=True, seed_demo=False, send_policy=self.policy, privacy_key=key)
        replay = self.inbound("STOP", event_id="twilio:deleted-provider-sid")
        self.assertEqual(replay["status"], "duplicate")
        blocked = self.inbound()
        self.assertEqual(blocked["reason"], "opted_out")
        manual = self.engine.human_reply(PHONE, "Cannot bypass deletion suppression", event_id="deleted-manual")
        self.assertEqual(manual["reason"], "opted_out")
        delayed = self.inbound("START", occurred_at=barrier)
        self.assertIn(delayed["status"], {"blocked", "ignored"})
        self.assertEqual(self.contact()["consent_status"], "revoked")
        self.inbound("START", provider_keyword_verified=False)
        self.assertEqual(self.contact()["consent_status"], "revoked")
        self.inbound("START")
        self.assertEqual(self.contact()["consent_status"], "none")

    def test_latest_hash_suppression_overrides_older_restored_contact_barriers(self):
        self.inbound()
        old = stamp(now_utc() - timedelta(minutes=2))
        newest = stamp(now_utc() - timedelta(seconds=10))
        digest = hmac.new(self.engine.privacy_key.encode(), PHONE.encode(), hashlib.sha256).hexdigest()
        self.engine.db.execute("UPDATE contacts SET revoked_at=?,carrier_blocked_at=?,last_event_at=NULL WHERE phone=?", (old, old, PHONE))
        self.engine.db.execute("INSERT INTO privacy_suppressions VALUES (?,?,?)", (digest, "local_and_carrier_opt_out", newest))
        self.engine.db.commit()
        result = self.inbound("START", occurred_at=stamp(now_utc() - timedelta(seconds=30)))
        self.assertEqual(result["reason"], "start_predates_applied_opt_out")
        self.assertEqual(self.contact()["revoked_at"], newest)
        self.assertEqual(self.contact()["carrier_blocked_at"], newest)

    @unittest.skipUnless(os.name in {"posix", "nt"}, "Native local offline lock acceptance requires Windows or Linux")
    def test_actual_retention_reopen_preserves_carrier_suppression(self):
        from reception.operations import run_retention
        key = self.engine.privacy_key
        self.inbound()
        def opted_out(*args):
            raise SendRejected("21610")
        self.engine.outbox.run_once(FakeProvider(opted_out))
        self.engine.db.execute("UPDATE contacts SET last_activity=? WHERE phone=?", (stamp(now_utc() - timedelta(days=45)), PHONE))
        self.engine.db.commit()
        self.engine.close()
        run_retention(self.path, key, retention_days=30, offline_confirmed=True)
        self.engine = Engine(self.path, production=True, seed_demo=False, send_policy=self.policy, privacy_key=key)
        self.assertEqual(self.inbound()["reason"], "contact_blocked")
        self.assertTrue(self.contact()["carrier_blocked"])
        self.inbound("START", provider_keyword_verified=False)
        self.assertTrue(self.contact()["carrier_blocked"])
        self.inbound("START")
        self.assertFalse(self.contact()["carrier_blocked"])

    @unittest.skipUnless(os.name in {"posix", "nt"}, "Native local offline lock acceptance requires Windows or Linux")
    def test_same_day_privacy_delete_cannot_reset_message_or_segment_quota(self):
        from reception.operations import delete_contact
        key = self.engine.privacy_key
        other = "+12025550112"
        policy = replace(self.policy, allowed_recipients=(PHONE, other), max_messages_per_day=1, max_daily_segments=1)
        self.engine.send_policy = policy
        self.engine.outbox.policy = policy
        self.inbound()
        self.engine.outbox.run_once(FakeProvider())
        self.engine.close()
        delete_contact(self.path, PHONE, key, offline_confirmed=True)
        self.engine = Engine(self.path, production=True, seed_demo=False, send_policy=policy, privacy_key=key)
        self.inbound(phone=other)
        provider = FakeProvider()
        self.assertEqual(self.engine.outbox.run_once(provider)["reason"], "daily_message_limit")
        self.assertEqual(provider.calls, [])
        self.assertEqual(self.engine.outbox.snapshot()["usage"]["messages_today"], 1)
        self.assertEqual(self.engine.outbox.snapshot()["usage"]["segments_today"], 1)
        self.assertEqual(self.engine.db.execute("SELECT count(*) FROM outbox_attempts").fetchone()[0], 0)

    @unittest.skipUnless(os.name in {"posix", "nt"}, "Native local offline lock acceptance requires Windows or Linux")
    def test_same_day_retention_cannot_reset_minute_attempt_quota(self):
        from reception.operations import run_retention
        key = self.engine.privacy_key
        other = "+12025550112"
        policy = replace(self.policy, allowed_recipients=(PHONE, other), max_messages_per_minute=1)
        self.engine.send_policy = policy
        self.engine.outbox.policy = policy
        self.inbound()
        self.engine.outbox.run_once(FakeProvider())
        self.engine.db.execute("UPDATE contacts SET last_activity=? WHERE phone=?", (stamp(now_utc() - timedelta(days=45)), PHONE))
        self.engine.db.commit()
        self.engine.close()
        run_retention(self.path, key, retention_days=30, offline_confirmed=True)
        self.engine = Engine(self.path, production=True, seed_demo=False, send_policy=policy, privacy_key=key)
        self.inbound(phone=other)
        provider = FakeProvider()
        self.assertEqual(self.engine.outbox.run_once(provider)["reason"], "minute_message_limit")
        self.assertEqual(provider.calls, [])

    def test_restart_does_not_duplicate_anonymous_quota_archives(self):
        self.inbound()
        self.engine.outbox.run_once(FakeProvider())
        self.engine.close()
        self.engine = Engine(self.path, production=True, seed_demo=False, send_policy=self.policy)
        self.assertEqual(self.engine.db.execute("SELECT count(*) FROM outbox_quota_reservations").fetchone()[0], 1)
        self.assertEqual(self.engine.db.execute("SELECT count(*) FROM outbox_quota_attempts").fetchone()[0], 1)

    def test_formal_policy_accepts_verified_new_customer_without_test_allowlist(self):
        newcomer = "+12025550112"
        self.engine.outbox.policy = replace(self.policy, recipient_policy="inbound_service", inbound_service_approval_recorded=True)
        result = self.inbound(phone=newcomer)
        self.assertEqual(result["status"], "queued")
        provider = FakeProvider()
        self.assertEqual(self.engine.outbox.run_once(provider)["delivery_status"], "accepted")
        self.assertEqual(provider.calls[0][0], newcomer)

    def test_formal_policy_requires_separate_approval(self):
        self.inbound()
        self.engine.outbox.policy = replace(self.policy, recipient_policy="inbound_service")
        provider = FakeProvider()
        self.assertEqual(self.engine.outbox.run_once(provider)["reason"], "inbound_service_approval_required")
        self.assertEqual(provider.calls, [])

    def test_contact_hourly_limit_is_rechecked_without_blocking_first_queued_reply(self):
        self.inbound()
        self.inbound()
        business = self.engine.snapshot()["business"]
        business["max_messages_per_hour"] = 1
        self.engine.save_business(business)
        provider = FakeProvider()
        self.assertEqual(self.engine.outbox.run_once(provider)["delivery_status"], "accepted")
        self.assertEqual(self.engine.outbox.run_once(provider)["reason"], "hourly_send_limit")
        self.assertEqual(len(provider.calls), 1)

    def test_callback_cannot_bind_canceled_never_submitted_item(self):
        queued = self.inbound()
        nonce = self.nonce(queued["outbox_id"])
        self.inbound("STOP")
        with self.assertRaises(ValueError):
            self.engine.outbox.apply_callback(queued["outbox_id"], nonce, "SM" + "9" * 32, "delivered")
        self.assertEqual(self.item(queued["outbox_id"])["status"], "canceled")

    def test_formal_policy_does_not_authorize_marketing_purpose(self):
        self.inbound()
        self.engine.outbox.policy = replace(self.policy, recipient_policy="inbound_service", inbound_service_approval_recorded=True)
        self.engine.db.execute("UPDATE outbox SET purpose='marketing'")
        self.engine.db.commit()
        provider = FakeProvider()
        self.assertEqual(self.engine.outbox.run_once(provider)["reason"], "production_service_replies_only")
        self.assertEqual(provider.calls, [])

    def test_new_inquiry_cannot_extend_old_queue_deadline(self):
        first = self.inbound()
        expired = stamp(now_utc() - timedelta(seconds=1))
        self.engine.db.execute("UPDATE outbox SET permission_until=? WHERE id=?", (expired, first["outbox_id"]))
        self.engine.db.commit()
        second = self.inbound()
        provider = FakeProvider()
        self.assertEqual(self.engine.outbox.run_once(provider)["reason"], "service_reply_window_expired")
        self.assertEqual(provider.calls, [])
        self.assertEqual(self.item(second["outbox_id"])["status"], "queued")
        self.assertEqual(self.engine.outbox.run_once(provider)["delivery_status"], "accepted")

    def test_deleted_source_cancels_queue_despite_current_contact_window(self):
        self.inbound()
        self.engine.db.execute("DELETE FROM provider_inbox")
        self.engine.db.commit()
        provider = FakeProvider()
        self.assertEqual(self.engine.outbox.run_once(provider)["reason"], "source_unavailable")
        self.assertEqual(provider.calls, [])

    def test_direct_verified_flags_without_persistent_source_cannot_authorize(self):
        result = self.engine.process_event({"event_id": "twilio:SM" + "e" * 32, "kind": "sms", "phone": PHONE,
            "text": "What are your hours?", "origin": "twilio", "verified": True, "provider_fresh": True,
            "provider_sid": "SM" + "e" * 32})
        self.assertNotEqual(result["status"], "queued")
        self.assertEqual(self.engine.outbox.snapshot()["items"], [])

    def test_stop_wins_before_claim_enters_submission_barrier(self):
        from contextlib import contextmanager
        self.inbound()
        original = self.engine.contact_barrier
        claimed = threading.Event()
        proceed = threading.Event()
        outcome = []
        errors = []
        @contextmanager
        def paused_barrier(phone, timeout=None):
            if threading.current_thread().name == "paused-dispatch":
                claimed.set()
                if not proceed.wait(3):
                    raise RuntimeError("dispatch test coordination timed out")
            with original(phone, timeout):
                yield
        self.engine.contact_barrier = paused_barrier
        provider = FakeProvider()
        def dispatch():
            try: outcome.append(self.engine.outbox.run_once(provider))
            except Exception as exc: errors.append(exc)
        thread = threading.Thread(target=dispatch, name="paused-dispatch")
        thread.start()
        try:
            self.assertTrue(claimed.wait(3))
            self.inbound("STOP")
        finally:
            proceed.set()
            thread.join(4)
            self.engine.contact_barrier = original
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(outcome[0]["status"], "canceled")
        self.assertEqual(provider.calls, [])
        self.assertEqual(self.engine.db.execute("SELECT count(*) FROM outbox_attempts").fetchone()[0], 0)

    def test_inflight_send_precedes_stop_and_early_callback_is_not_blocked(self):
        self.inbound()
        self.inbound()
        entered = threading.Event()
        release = threading.Event()
        withdrawing = threading.Event()
        stopped = threading.Event()
        errors = []
        sid = "SM" + "c" * 32
        def transport(to, body, callback):
            entered.set()
            if not release.wait(3): raise RuntimeError("transport test coordination timed out")
            return {"sid": sid, "status": "queued"}
        provider = FakeProvider(transport)
        def dispatch():
            try: self.engine.outbox.run_once(provider)
            except Exception as exc: errors.append(exc)
        def withdraw():
            withdrawing.set()
            try: self.inbound("STOP"); stopped.set()
            except Exception as exc: errors.append(exc)
        send_thread = threading.Thread(target=dispatch)
        stop_thread = threading.Thread(target=withdraw)
        send_thread.start()
        try:
            self.assertTrue(entered.wait(3))
            stop_thread.start()
            self.assertTrue(withdrawing.wait(3))
            self.assertFalse(stopped.is_set())
            # Real callback processing runs in another thread while send waits.
            query = parse_qs(urlsplit(provider.calls[0][2]).query)
            callback_done = threading.Event()
            def callback():
                try:
                    self.engine.outbox.apply_callback(int(query["outbox_id"][0]), query["token"][0], sid, "delivered")
                    callback_done.set()
                except Exception as exc: errors.append(exc)
            callback_thread = threading.Thread(target=callback)
            callback_thread.start()
            callback_thread.join(2)
            self.assertTrue(callback_done.is_set())
        finally:
            release.set()
            send_thread.join(4)
            if stop_thread.ident: stop_thread.join(4)
        self.assertEqual(errors, [])
        self.assertTrue(stopped.is_set())
        self.assertEqual(self.engine.outbox.run_once(provider)["status"], "idle")
        self.assertEqual(len(provider.calls), 1)
        self.assertIn("delivered", {r["status"] for r in self.engine.outbox.snapshot()["items"]})

    def test_stop_cancels_unsubmitted_retry_claim_with_previous_attempt(self):
        self.inbound()
        provider = FakeProvider(lambda *args: (_ for _ in ()).throw(SendRejected("20429", retryable=True, http_status=429)))
        self.assertEqual(self.engine.outbox.run_once(provider)["status"], "retry_scheduled")
        self.engine.db.execute("UPDATE outbox SET status='submitting',submission_started_at='',claimed_at=?", (stamp(),))
        self.engine.db.commit()
        self.inbound("STOP")
        self.assertEqual(self.item()["status"], "canceled")
        self.assertEqual(self.engine.outbox.run_once(provider)["status"], "idle")
        self.assertEqual(len(provider.calls), 1)

    def test_pending_legacy_stop_fails_closed_before_submission(self):
        self.inbound()
        with self.engine.db:
            self.engine.db.execute("""INSERT INTO provider_inbox
                (sid,phone,recipient,body,received_at,status,next_attempt_at)
                VALUES (?,?,?,'STOP',?,'pending',?)""", ("SM" + "d" * 32, PHONE, SENDER, stamp(), stamp()))
        provider = FakeProvider()
        self.assertEqual(self.engine.outbox.run_once(provider)["reason"], "pending_stop")
        self.assertEqual(provider.calls, [])

    def test_queue_without_legacy_permission_snapshot_is_canceled_on_upgrade(self):
        self.inbound()
        self.engine.db.execute("UPDATE outbox SET permission_source_event_id='' ")
        self.engine.db.commit()
        self.engine.close()
        self.engine = Engine(self.path, production=True, seed_demo=False, send_policy=self.policy)
        self.assertEqual(self.item()["status"], "canceled")
        self.assertEqual(self.item()["error_code"], "LEGACY_REPLY_EVIDENCE_MISSING")

    def test_old_demo_database_does_not_queue_historical_simulated_rows(self):
        demo_path = Path(self.temp.name) / "old-demo.sqlite3"
        demo = Engine(demo_path)
        self.assertGreater(demo.snapshot()["metrics"]["simulated_outbound"], 0)
        demo.close()
        live = Engine(demo_path, production=True, seed_demo=False, send_policy=self.policy)
        try:
            self.assertEqual(live.outbox.snapshot()["items"], [])
        finally:
            live.close()


if __name__ == "__main__":
    unittest.main()
