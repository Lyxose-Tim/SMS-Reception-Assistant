"""Independent final business acceptance using synthetic, offline provider proof.

These cases fill combinations absent from the earlier suite. Real sending stays
disabled. Assertions cover durable production inbox/domain/outbox behavior, not
Twilio API delivery, handset receipt, or platform access-control acceptance.
"""
import copy
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest

from reception.auth import hash_password
from reception.config import ServiceConfig
from reception.engine import Engine, parse_time
from reception.service import ServiceInbox


class OfflineProofProvider:
    def __init__(self):
        self.proofs = {}
        self.fetch_calls = []
        self.send_calls = []

    def fetch_inbound(self, sid):
        self.fetch_calls.append(sid)
        return copy.deepcopy(self.proofs[sid])

    def send(self, to, body, callback_url):
        self.send_calls.append((to, body, callback_url))
        raise AssertionError("Independent acceptance must never submit an SMS")


class IndependentBusinessAcceptanceTests(unittest.TestCase):
    PASSWORD_HASH = hash_password("Synthetic independent admin 123!", salt=b"independent-test")
    PHONE = "+12025550171"
    QUESTIONS = {
        "hours": "What are your opening hours?",
        "areas": "What areas do you serve?",
        "services": "What services do you offer?",
        "pricing": "What are your prices?",
        "requests": "How do I book an appointment?",
    }

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="independent-business-")
        self.db_path = Path(self.temp.name) / "synthetic-service.sqlite3"
        self.config = ServiceConfig(
            mode="service", admin_password_hash=self.PASSWORD_HASH,
            account_sid="AC" + "a" * 32, auth_token="synthetic-never-live-token",
            phone_number="+12025550100", live_enabled=False,
            allowed_recipients=(self.PHONE,), optout_reviewed=True,
        )
        self.provider = OfflineProofProvider()
        self.counter = 0
        self.engine = None
        self.reopen()
        business = self.engine.snapshot()["business"]
        business["max_messages_per_hour"] = 100
        business["approved_faqs"] = [
            {"topic": topic, "answer": "Synthetic approved " + topic + " answer v1.", "approved": True}
            for topic in self.QUESTIONS
        ]
        self.engine.save_business(business)

    def tearDown(self):
        if self.engine is not None:
            self.engine.close()
        self.temp.cleanup()
        self.assertEqual(self.provider.send_calls, [], "No provider submission is permitted")

    def reopen(self):
        if self.engine is not None:
            self.engine.close()
        self.engine = Engine(self.db_path, production=True, seed_demo=False,
                             send_policy=self.config.send_policy())
        self.inbox = ServiceInbox(self.engine, self.provider, self.config)

    def contact(self):
        return next(row for row in self.engine.snapshot()["contacts"] if row["phone"] == self.PHONE)

    def queue(self):
        return self.engine.outbox.snapshot()["items"]

    def receive(self, text, *, occurred_at=None):
        self.counter += 1
        sid = "SM" + f"{self.counter:032x}"
        at = occurred_at or datetime.now(timezone.utc).isoformat()
        envelope = {"sid": sid, "from": self.PHONE, "to": self.config.phone_number,
                    "body": text, "account_sid": self.config.account_sid, "opt_out_type": ""}
        self.provider.proofs[sid] = {
            "verified": True, "resource_id": sid, "account_sid": self.config.account_sid,
            "phone": self.PHONE, "to": self.config.phone_number, "body": text,
            "direction": "inbound", "occurred_at": at,
        }
        accepted = self.inbox.accept(envelope)
        self.assertEqual(accepted["status"], "accepted")
        processed = self.inbox.process_one()
        return processed, "twilio:" + sid

    def faq_saved_reloaded_and_disabled(self, topic):
        business = self.engine.snapshot()["business"]
        entry = next(item for item in business["approved_faqs"] if item["topic"] == topic)
        entry["answer"] = "Synthetic edited " + topic + " answer v2."
        expected = entry["answer"]
        self.assertEqual(self.engine.save_business(business)["status"], "saved")
        self.reopen()
        restored = next(item for item in self.engine.snapshot()["business"]["approved_faqs"] if item["topic"] == topic)
        self.assertEqual(restored, entry)
        result, source = self.receive(self.QUESTIONS[topic])
        self.assertEqual(result["status"], "queued", result)
        queued = self.queue()[0]
        self.assertEqual(queued["body"], expected)
        self.assertEqual(queued["permission_source_event_id"], source)
        self.assertEqual(queued["permission_basis"], "rules")
        self.assertEqual(self.engine.snapshot()["appointments"], [])
        business = self.engine.snapshot()["business"]
        next(item for item in business["approved_faqs"] if item["topic"] == topic)["approved"] = False
        self.engine.save_business(business)
        self.reopen()
        before = len(self.queue())
        result, _ = self.receive(self.QUESTIONS[topic])
        self.assertEqual(result["status"], "handoff", result)
        self.assertEqual(result["reason"], "unapproved_faq")
        self.assertEqual(len(self.queue()), before)
        self.assertTrue(self.contact()["handoff"])
        self.assertEqual(self.engine.snapshot()["appointments"], [])

    def test_hours_faq_edit_save_reopen_exact_hit_and_disable(self):
        self.faq_saved_reloaded_and_disabled("hours")

    def test_areas_faq_edit_save_reopen_exact_hit_and_disable(self):
        self.faq_saved_reloaded_and_disabled("areas")

    def test_services_faq_edit_save_reopen_exact_hit_and_disable(self):
        self.faq_saved_reloaded_and_disabled("services")

    def test_pricing_faq_edit_save_reopen_exact_hit_and_disable(self):
        self.faq_saved_reloaded_and_disabled("pricing")

    def test_requests_faq_edit_save_reopen_exact_hit_and_disable(self):
        self.faq_saved_reloaded_and_disabled("requests")

    def test_disabled_requests_faq_blocks_actual_appointment_collection(self):
        business = self.engine.snapshot()["business"]
        next(item for item in business["approved_faqs"] if item["topic"] == "requests")["approved"] = False
        self.engine.save_business(business)
        self.reopen()
        result, _ = self.receive("Book deep cleaning in Riverside tomorrow at 10 AM. Contact this number.")
        self.assertEqual(result["status"], "handoff")
        self.assertEqual(result["reason"], "appointment_flow_not_approved")
        self.assertEqual(self.contact()["booking"], {})
        self.assertEqual(self.engine.snapshot()["appointments"], [])
        self.assertEqual(self.queue(), [])

    def test_repeated_takeover_and_resume_do_not_revive_canceled_automatic_rows(self):
        result, _ = self.receive(self.QUESTIONS["hours"])
        self.assertEqual(result["status"], "queued")
        automatic = self.queue()[0]
        for _ in range(2):
            self.engine.takeover(self.PHONE, True)
        self.assertTrue(self.contact()["handoff"])
        self.assertEqual(self.queue()[0]["status"], "canceled")
        self.assertEqual(self.queue()[0]["error_code"], "HUMAN_TAKEOVER")
        manual = self.engine.human_reply(self.PHONE, "Synthetic human answer.", event_id="stable-manual-click")
        repeated = self.engine.human_reply(self.PHONE, "Synthetic human answer.", event_id="stable-manual-click")
        self.assertEqual(manual["status"], "queued")
        self.assertEqual(repeated["status"], "duplicate")
        self.assertEqual(sum(row["origin"] == "human" for row in self.queue()), 1)
        for _ in range(2):
            self.engine.takeover(self.PHONE, False)
        self.assertFalse(self.contact()["handoff"])
        original = next(row for row in self.queue() if row["id"] == automatic["id"])
        self.assertEqual(original["status"], "canceled")
        self.assertEqual(original["error_code"], "HUMAN_TAKEOVER")
        fresh, _ = self.receive(self.QUESTIONS["hours"])
        self.assertEqual(fresh["status"], "queued")
        self.assertEqual(len(self.queue()), 3)
        self.reopen()
        original = next(row for row in self.queue() if row["id"] == automatic["id"])
        self.assertEqual(original["status"], "canceled")

    def test_single_value_booking_corrections_update_current_draft_and_keep_pending(self):
        first, _ = self.receive("I need deep cleaning.")
        self.assertEqual(first["status"], "queued")
        for text in ("Riverside", "Actually Oakwood", "tomorrow at 10 AM", "Friday at 2 PM instead", "Standard cleaning instead"):
            result, _ = self.receive(text)
            self.assertEqual(result["status"], "queued", (text, result))
        draft = self.contact()["booking"]
        self.assertEqual(draft["service"], "Standard cleaning")
        self.assertEqual(draft["area"], "Oakwood")
        self.assertEqual(draft["desired_time"], "Friday at 2 PM instead")
        result, _ = self.receive("this number")
        self.assertEqual(result["status"], "queued")
        appointments = self.engine.snapshot()["appointments"]
        self.assertEqual(len(appointments), 1)
        for field in ("service", "area", "desired_time"):
            self.assertEqual(appointments[0][field], draft[field])
        self.assertEqual(appointments[0]["status"], "pending_owner_confirmation")
        self.assertEqual(self.contact()["booking"], {})

    def assert_ambiguous_correction_hands_off(self, text):
        first, _ = self.receive("I need deep cleaning.")
        self.assertEqual(first["status"], "queued")
        original = self.contact()
        result, _ = self.receive(text)
        self.assertEqual(result["status"], "handoff", result)
        self.assertTrue(self.contact()["handoff"])
        for field in ("booking", "reply_permission_event_id", "reply_permission_basis",
                      "reply_permission_until", "last_service_inquiry_at"):
            self.assertEqual(self.contact()[field], original[field], field)
        for followup in ("Riverside", "tomorrow at 10 AM", "this number"):
            result, _ = self.receive(followup)
            self.assertEqual(result["status"], "blocked", result)
        self.assertEqual(self.engine.snapshot()["appointments"], [])

    def test_negated_old_and_new_area_never_silently_selects_old_location(self):
        self.assert_ambiguous_correction_hands_off("Not Oakwood, use Riverside instead.")

    def test_negated_old_and_new_time_never_creates_ambiguous_pending_request(self):
        self.assert_ambiguous_correction_hands_off("Not tomorrow at 10 AM, Friday at 2 PM instead.")

    def test_negated_old_and_new_service_never_silently_selects_old_service(self):
        self.assert_ambiguous_correction_hands_off("Not standard cleaning, deep cleaning instead.")

    def test_negated_single_area_is_not_treated_as_requested_location(self):
        self.assert_ambiguous_correction_hands_off("Not Oakwood.")

    def test_negated_single_time_is_not_treated_as_preferred_time(self):
        self.assert_ambiguous_correction_hands_off("Not tomorrow at 10 AM.")

    def test_negated_single_service_is_not_treated_as_requested_service(self):
        self.assert_ambiguous_correction_hands_off("No standard cleaning.")

    def test_old_and_new_contact_values_require_review_before_pending_request(self):
        self.assert_ambiguous_correction_hands_off("Change my contact from old@example.test to new@example.test.")

    def test_single_email_correction_updates_current_draft_contact(self):
        first, _ = self.receive("I need deep cleaning. Contact old@example.test.")
        self.assertEqual(first["status"], "queued")
        self.assertEqual(self.contact()["booking"]["contact"], "old@example.test")
        changed, _ = self.receive("Use new@example.test instead.")
        self.assertEqual(changed["status"], "queued")
        self.assertEqual(self.contact()["booking"]["contact"], "new@example.test")
        for field in ("Riverside", "tomorrow at 10 AM"):
            result, _ = self.receive(field)
            self.assertEqual(result["status"], "queued")
        appointments = self.engine.snapshot()["appointments"]
        self.assertEqual(len(appointments), 1)
        self.assertEqual(appointments[0]["contact"], "new@example.test")
        self.assertEqual(appointments[0]["status"], "pending_owner_confirmation")

    def test_unknown_review_survives_restart_with_original_deadline_and_no_marketing(self):
        resource_at = datetime.now(timezone.utc) - timedelta(minutes=5)
        result, source = self.receive("Are you insured?", occurred_at=resource_at.isoformat())
        self.assertEqual(result["status"], "handoff")
        self.assertFalse(self.contact()["reply_permission_until"])
        confirmed = self.engine.human_reply(self.PHONE, "Synthetic owner insurance answer.",
            event_id="independent-review-first", reply_to_event_id=source, confirm_service_inquiry=True)
        self.assertEqual(confirmed["status"], "queued")
        deadline = self.contact()["reply_permission_until"]
        self.assertEqual(parse_time(deadline), resource_at + timedelta(hours=self.config.reply_window_hours))
        self.reopen()
        repeated = self.engine.human_reply(self.PHONE, "Synthetic related follow-up answer.",
            event_id="independent-review-second", reply_to_event_id=source, confirm_service_inquiry=True)
        self.assertEqual(repeated["status"], "queued")
        contact = self.contact()
        self.assertEqual(contact["reply_permission_until"], deadline)
        self.assertEqual(contact["reply_permission_basis"], "operator_review_human_only")
        self.assertEqual(contact["consent_status"], "none")
        self.assertEqual(contact["consent_purposes"], [])
        self.assertTrue(contact["handoff"])
        for row in self.queue():
            self.assertEqual(row["origin"], "human")
            self.assertEqual(row["permission_basis"], "operator_review_human_only")
            self.assertEqual(row["permission_until"], deadline)
            self.assertEqual(row["permission_source_event_id"], source)
        reviews = self.engine.db.execute("SELECT details FROM audit WHERE action='service_inquiry_operator_reviewed'").fetchall()
        self.assertEqual(len(reviews), 1)
        self.assertIs(json.loads(reviews[0][0])["marketing_authorization"], False)


if __name__ == "__main__":
    unittest.main()
