"""Layer B: synthetic SDK/resource/signature tests, never actual delivery.

Fake credentials and reserved synthetic phones are used. No request reaches
Twilio, and no account test credentials or real participant are available.
"""
from datetime import datetime, timezone
import logging
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from reception.provider import (SendRejected, SendUncertain, ServiceWebhookAdapter,
                                TwilioRESTProvider, WebhookRejected, normalize_phone)

try:
    from twilio.base.exceptions import TwilioRestException
    from twilio.request_validator import RequestValidator
except ImportError:
    TwilioRestException = RequestValidator = None

try:
    from requests.exceptions import ReadTimeout, ConnectionError as RequestsConnectionError
except ImportError:
    ReadTimeout = RequestsConnectionError = None


ACCOUNT = "AC" + "a" * 32
SID = "SM" + "b" * 32
BUSINESS = "+12025550190"
CUSTOMER = "+12025550101"
BASE = "https://synthetic.example.test"
TOKEN = "synthetic-never-live-secret"
CALLBACK = BASE + "/webhooks/twilio/status?outbox_id=42&token=synthetic-nonce"


class ProviderTests(unittest.TestCase):
    def setUp(self):
        self.client = Mock()
        self.message = SimpleNamespace(sid=SID, status="queued", error_code=None, num_segments="1")
        self.client.messages.create.return_value = self.message
        self.provider = TwilioRESTProvider(account_sid=ACCOUNT, auth_token=TOKEN,
            phone_number=BUSINESS, public_base_url=BASE, client=self.client)

    def test_send_uses_one_rest_submission_with_explicit_owned_from(self):
        result = self.provider.send(CUSTOMER, "Our approved hours are 8 AM–6 PM.", CALLBACK)
        self.client.messages.create.assert_called_once_with(to=CUSTOMER, from_=BUSINESS,
            body="Our approved hours are 8 AM–6 PM.", status_callback=CALLBACK)
        self.assertEqual(result, {"sid": SID, "status": "queued", "error_code": None, "num_segments": "1"})
        self.assertNotIn("messaging_service_sid", self.client.messages.create.call_args.kwargs)

    def test_explicit_international_phone_is_normalized_without_country_guess(self):
        self.assertEqual(normalize_phone("+1 (202) 555-0101"), CUSTOMER)
        self.provider.send("+1 (202) 555-0101", "Approved reply", CALLBACK)
        self.assertEqual(self.client.messages.create.call_args.kwargs["to"], CUSTOMER)
        for value in ("2025550101", "0012025550101", "+012025550101", "+123", None, "sms:+12025550101"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_phone(value)

    def test_invalid_send_parameters_never_call_sdk(self):
        for to, body, callback in [("2025550101", "hello", CALLBACK),
                                  (CUSTOMER, "", CALLBACK), (CUSTOMER, "a" * 1601, CALLBACK),
                                  (CUSTOMER, "hello", "http://synthetic.example.test/status"),
                                  (CUSTOMER, "hello", CALLBACK + "#fragment"),
                                  (CUSTOMER, "hello", "https://attacker.example.test/status")]:
            with self.subTest(to=to, body_length=len(body)), self.assertRaises(SendRejected):
                self.provider.send(to, body, callback)
        self.client.messages.create.assert_not_called()

    def assert_uncertain_transport_error(self, exc, code):
        self.client.messages.create.reset_mock()
        self.client.messages.create.side_effect = exc
        with self.assertRaises(SendUncertain) as caught:
            self.provider.send(CUSTOMER, "approved", CALLBACK)
        self.assertEqual(str(caught.exception), code)
        self.assertFalse(caught.exception.retryable)
        self.assertIsNone(caught.exception.__cause__)
        self.assertTrue(caught.exception.__suppress_context__)
        self.client.messages.create.assert_called_once()

    def test_stdlib_timeout_is_uncertain_without_retry(self):
        self.assert_uncertain_transport_error(TimeoutError("secret"), "transport_timeout")

    @unittest.skipUnless(ReadTimeout, "Install optional Twilio SDK/requests for HTTP transport exception tests")
    def test_requests_timeout_and_connection_are_uncertain_without_retry(self):
        for exc, code in [(ReadTimeout("secret body phone token"), "transport_timeout"),
                          (RequestsConnectionError("secret body phone token"), "transport_connection_error")]:
            with self.subTest(code=code):
                self.assert_uncertain_transport_error(exc, code)

    def test_unknown_create_error_has_no_exception_text(self):
        self.client.messages.create.side_effect = RuntimeError(TOKEN + CUSTOMER)
        with self.assertRaises(SendUncertain) as caught:
            self.provider.send(CUSTOMER, "customer sensitive text", CALLBACK)
        self.assertEqual(caught.exception.code, "ambiguous_provider_response")
        self.assertNotIn(TOKEN, str(caught.exception))
        self.assertNotIn(CUSTOMER, str(caught.exception))

    def test_unusable_success_response_is_uncertain(self):
        for fields in [{"sid": "bad"}, {"status": "unexpected"}]:
            self.client.messages.create.return_value = SimpleNamespace(
                **dict(sid=SID, status="queued", error_code=None, num_segments="1", **{}))
            for key, value in fields.items():
                setattr(self.client.messages.create.return_value, key, value)
            with self.subTest(fields=fields), self.assertRaises(SendUncertain) as caught:
                self.provider.send(CUSTOMER, "approved", CALLBACK)
            self.assertEqual(caught.exception.code, "invalid_provider_create_response")

    def inbound_resource(self, **updates):
        data = dict(sid=SID, account_sid=ACCOUNT, from_=CUSTOMER, to=BUSINESS,
                    body="What are your hours?", direction="inbound", num_media="0",
                    date_created=datetime.now(timezone.utc))
        data.update(updates)
        return SimpleNamespace(**data)

    def test_inbound_fetch_returns_bound_utc_resource_proof(self):
        self.client.messages.return_value.fetch.return_value = self.inbound_resource()
        proof = self.provider.fetch_inbound(SID)
        self.client.messages.assert_called_once_with(SID)
        self.assertEqual(proof["resource_id"], SID)
        self.assertEqual(proof["phone"], CUSTOMER)
        self.assertEqual(proof["to"], BUSINESS)
        self.assertEqual(proof["direction"], "inbound")
        self.assertTrue(proof["verified"])
        self.assertTrue(proof["occurred_at"].endswith("+00:00"))

    def test_inbound_proof_rejects_wrong_account_direction_sender_or_timestamp(self):
        for changes in [{"account_sid": "AC" + "c" * 32}, {"direction": "outbound-api"},
                        {"to": CUSTOMER}, {"sid": "SM" + "c" * 32}, {"from_": "whatsapp:" + CUSTOMER},
                        {"date_created": datetime.now()}, {"num_media": "1"}]:
            self.client.messages.return_value.fetch.return_value = self.inbound_resource(**changes)
            with self.subTest(changes=list(changes)), self.assertRaises(SendRejected) as caught:
                self.provider.fetch_inbound(SID)
            self.assertEqual(caught.exception.code, "provider_inbound_resource_mismatch")

    def test_status_lookup_binds_known_outbound_sid_and_sanitized_code(self):
        resource = self.inbound_resource(from_=BUSINESS, to=CUSTOMER, direction="outbound-api",
                                         status="undelivered", error_code=21610, num_segments="1")
        self.client.messages.return_value.fetch.return_value = resource
        proof = self.provider.lookup(SID)
        self.assertEqual(proof["error_code"], "21610")
        self.assertEqual(proof["status"], "undelivered")
        self.assertTrue(proof["verified"])
        self.assertEqual(proof["from"], BUSINESS)
        self.assertEqual(proof["to"], CUSTOMER)
        self.assertEqual(proof["body"], "What are your hours?")

    def test_outbound_lookup_rejects_foreign_sender_account_or_direction(self):
        for changes in [{"from_": CUSTOMER}, {"account_sid": "AC" + "c" * 32},
                        {"direction": "inbound"}, {"status": "read"}]:
            baseline = dict(from_=BUSINESS, to=CUSTOMER, direction="outbound-api",
                            status="sent", error_code=None, num_segments="1")
            baseline.update(changes)
            self.client.messages.return_value.fetch.return_value = self.inbound_resource(**baseline)
            with self.subTest(changes=list(changes)), self.assertRaises(SendRejected):
                self.provider.lookup(SID)

    def test_invalid_sid_is_never_sent_to_fetch(self):
        with self.assertRaises(SendRejected):
            self.provider.fetch_inbound("not-a-message")
        self.client.messages.assert_not_called()

    def test_readiness_only_reads_account_and_owned_number_not_registration(self):
        self.client.api.accounts.return_value.fetch.return_value = SimpleNamespace(
            sid=ACCOUNT, status="active", type="Trial")
        number = SimpleNamespace(account_sid=ACCOUNT, phone_number=BUSINESS,
                                 capabilities={"sms": True}, iso_country="US",
                                 address_requirements="none", origin="twilio", bundle_sid=None)
        self.client.incoming_phone_numbers.list.return_value = [number]
        result = self.provider.read_only_readiness()
        self.client.api.accounts.assert_called_once_with(ACCOUNT)
        self.client.incoming_phone_numbers.list.assert_called_once_with(phone_number=BUSINESS, limit=2)
        self.client.messages.create.assert_not_called()
        self.assertTrue(result["is_trial"])
        self.assertTrue(result["owned_number"])
        self.assertTrue(result["sms_capable"])
        self.assertEqual(result["country"], "US")
        self.assertFalse(result["registration_verified"])

    def test_readiness_foreign_unowned_number_does_not_pass_capability(self):
        self.client.api.accounts.return_value.fetch.return_value = SimpleNamespace(
            sid=ACCOUNT, status="active", type="Full")
        self.client.incoming_phone_numbers.list.return_value = [SimpleNamespace(
            account_sid="AC" + "c" * 32, phone_number=BUSINESS, capabilities={"sms": True})]
        result = self.provider.read_only_readiness()
        self.assertFalse(result["owned_number"])
        self.assertFalse(result["sms_capable"])

    def test_configuration_rejects_insecure_or_partial_credentials(self):
        for updates in [{"public_base_url": "http://example.test"},
                        {"public_base_url": "https://user:password@example.test"},
                        {"account_sid": "not-account"}, {"timeout_seconds": 0},
                        {"timeout_seconds": 31}, {"api_key_sid": "SK" + "c" * 32},
                        {"api_key_sid": "bad", "api_key_secret": "synthetic"}]:
            arguments = dict(account_sid=ACCOUNT, auth_token=TOKEN, phone_number=BUSINESS,
                             public_base_url=BASE, client=self.client)
            arguments.update(updates)
            with self.subTest(updates=list(updates)), self.assertRaises(ValueError):
                TwilioRESTProvider(**arguments)


@unittest.skipUnless(TwilioRestException, "Install requirements-twilio.txt for official SDK transport tests")
class OfficialSDKTransportTests(unittest.TestCase):
    setUp = ProviderTests.setUp
    def test_definite_4xx_error21610_is_rejected_and_sanitized(self):
        self.client.messages.create.side_effect = TwilioRestException(400, "https://secret.invalid/" + TOKEN,
            msg="Contains " + CUSTOMER + TOKEN, code=21610, method="POST")
        with self.assertRaises(SendRejected) as caught:
            self.provider.send(CUSTOMER, "sensitive text", CALLBACK)
        self.assertEqual(caught.exception.code, "21610")
        self.assertFalse(caught.exception.retryable)
        self.assertNotIn(TOKEN, str(caught.exception))
        self.assertTrue(caught.exception.__suppress_context__)

    def test_429_is_only_rejection_marked_safe_for_capped_retry(self):
        self.client.messages.create.side_effect = TwilioRestException(429, "/synthetic", code=20429)
        with self.assertRaises(SendRejected) as caught:
            self.provider.send(CUSTOMER, "approved", CALLBACK)
        self.assertEqual(caught.exception.code, "20429")
        self.assertTrue(caught.exception.retryable)
        self.assertEqual(caught.exception.http_status, 429)
        self.client.messages.create.assert_called_once()

    def test_provider_5xx_is_uncertain_without_automatic_second_submission(self):
        self.client.messages.create.side_effect = TwilioRestException(503, "/synthetic", msg=TOKEN)
        with self.assertRaises(SendUncertain) as caught:
            self.provider.send(CUSTOMER, "approved", CALLBACK)
        self.assertEqual(caught.exception.code, "http_503")
        self.assertFalse(caught.exception.retryable)
        self.client.messages.create.assert_called_once()

    def test_real_sdk_client_has_timeout_no_auto_retries_and_disabled_logging(self):
        # Client construction is local; no HTTP request is performed.
        provider = TwilioRESTProvider(account_sid=ACCOUNT, auth_token=TOKEN,
            phone_number=BUSINESS, public_base_url=BASE, timeout_seconds=3.5)
        self.assertEqual(provider.http_client.timeout, 3.5)
        adapter = provider.http_client.session.get_adapter("https://")
        self.assertEqual(adapter.max_retries.total, 0)
        self.assertTrue(provider.http_client.logger.disabled)
        provider.http_client.session.close()

    def test_optional_api_key_is_scoped_to_configured_account(self):
        key = "SK" + "d" * 32
        with patch("twilio.rest.Client") as constructor:
            provider = TwilioRESTProvider(account_sid=ACCOUNT, auth_token=TOKEN,
                phone_number=BUSINESS, public_base_url=BASE,
                api_key_sid=key, api_key_secret="synthetic-key-secret")
        self.assertEqual(constructor.call_args.args, (key, "synthetic-key-secret"))
        self.assertEqual(constructor.call_args.kwargs["account_sid"], ACCOUNT)
        provider.http_client.session.close()


@unittest.skipUnless(RequestValidator, "Install requirements-twilio.txt for official SDK signature tests")
class WebhookEnvelopeTests(unittest.TestCase):
    def setUp(self):
        self.cfg = dict(account_sid=ACCOUNT, auth_token=TOKEN, phone_number=BUSINESS, public_base_url=BASE)
        self.adapter = ServiceWebhookAdapter(self.cfg)
        self.path = "/webhooks/twilio/sms"
        self.params = dict(AccountSid=ACCOUNT, MessageSid=SID, From=CUSTOMER,
                           To=BUSINESS, Body="What are your hours?", NumMedia="0")

    def signature(self, path=None, params=None, base=None):
        return RequestValidator(TOKEN).compute_signature((base or BASE) + (path or self.path),
            self.params if params is None else params)

    def inbound(self, changes=None):
        params = dict(self.params)
        params.update(changes or {})
        return self.adapter.validate_inbound(self.path, self.signature(params=params), params)

    def test_valid_envelope_does_not_perform_rest_lookup(self):
        result = self.inbound()
        self.assertEqual(result["sid"], SID)
        self.assertEqual(result["from"], CUSTOMER)
        self.assertEqual(result["to"], BUSINESS)
        self.assertEqual(result["opt_out_type"], "")
        self.assertNotIn("verified", result)
        self.assertNotIn("auth_token", result)

    def test_invalid_or_missing_signature_rejected(self):
        for signature in ("", "forged", self.signature(base="https://attacker.example.test")):
            with self.subTest(signature=signature[:8]), self.assertRaises(WebhookRejected) as caught:
                self.adapter.validate_inbound(self.path, signature, self.params)
            self.assertEqual(caught.exception.code, "invalid_signature")
            self.assertEqual(caught.exception.status, 403)

    def test_all_unknown_form_parameters_and_raw_query_are_signed(self):
        self.path += "?q=one%20two&x=1"
        self.params["UnknownFutureParameter"] = "signed value"
        self.assertEqual(self.inbound()["sid"], SID)
        signature = self.signature()
        for path, params in [(self.path.replace("%20", "+"), self.params),
                             (self.path, dict(self.params, UnknownFutureParameter="tampered"))]:
            with self.subTest(path=path), self.assertRaises(WebhookRejected):
                self.adapter.validate_inbound(path, signature, params)

    def test_duplicate_critical_fields_reject_even_when_all_values_are_signed(self):
        class MultiValues(dict):
            def getlist(self, key):
                value = self.get(key, [])
                return value if isinstance(value, list) else [value]
        params = MultiValues(self.params)
        params["From"] = [CUSTOMER, "+12025550102"]
        signature = self.signature(params=params)
        with self.assertRaises(WebhookRejected) as caught:
            self.adapter.validate_inbound(self.path, signature, params)
        self.assertEqual(caught.exception.code, "ambiguous_form_parameter")
        self.assertEqual(caught.exception.status, 400)

    def test_multiple_unknown_parameter_values_are_covered_without_loss(self):
        class MultiValues(dict):
            def getlist(self, key):
                value = self.get(key, [])
                return value if isinstance(value, list) else [value]
        params = MultiValues(self.params)
        params["FutureField"] = ["first", "second"]
        signature = self.signature(params=params)
        self.assertEqual(self.adapter.validate_inbound(self.path, signature, params)["sid"], SID)
        params["FutureField"] = ["first", "tampered"]
        with self.assertRaises(WebhookRejected):
            self.adapter.validate_inbound(self.path, signature, params)

    def test_signed_wrong_account_business_number_or_malformed_sid_rejected(self):
        for changes, expected in [({"AccountSid": "AC" + "e" * 32}, "account_mismatch"),
                                  ({"To": CUSTOMER}, "recipient_mismatch"),
                                  ({"MessageSid": "SMbad"}, "invalid_message_sid"),
                                  ({"From": "whatsapp:" + CUSTOMER}, "invalid_sender_phone")]:
            with self.subTest(changes=changes), self.assertRaises(WebhookRejected) as caught:
                self.inbound(changes)
            self.assertEqual(caught.exception.code, expected)

    def test_text_only_body_limit_and_supported_opt_out_types(self):
        for changes, expected in [({"NumMedia": "1"}, "sms_only_no_attachments"),
                                  ({"Body": "a" * 1601}, "message_body_too_long"),
                                  ({"OptOutType": "MYSTERY"}, "unknown_opt_out_type")]:
            with self.subTest(expected=expected), self.assertRaises(WebhookRejected) as caught:
                self.inbound(changes)
            self.assertEqual(caught.exception.code, expected)
        for optout in ("STOP", "START", "HELP"):
            self.assertEqual(self.inbound({"OptOutType": optout})["opt_out_type"], optout)

    def status_params(self, **updates):
        params = dict(AccountSid=ACCOUNT, MessageSid=SID, From=BUSINESS,
                      To=CUSTOMER, MessageStatus="delivered", ErrorCode="")
        params.update(updates)
        return params

    def status(self, **updates):
        path = "/webhooks/twilio/status?outbox_id=42&token=synthetic-nonce"
        params = self.status_params(**updates)
        return self.adapter.validate_status(path, self.signature(path, params), params)

    def test_status_callback_direction_and_exact_query_correlation(self):
        result = self.status()
        self.assertEqual(result["from"], BUSINESS)
        self.assertEqual(result["to"], CUSTOMER)
        self.assertEqual(result["status"], "delivered")
        path = "/webhooks/twilio/status?outbox_id=42&token=synthetic-nonce"
        params = self.status_params()
        with self.assertRaises(WebhookRejected):
            self.adapter.validate_status(path.replace("42", "43"), self.signature(path, params), params)

    def test_genuine_status_subset_without_to_retains_signed_row_correlation(self):
        path = "/webhooks/twilio/status?outbox_id=42&token=synthetic-nonce"
        params = self.status_params(SmsStatus="delivered", SmsSid=SID)
        del params["To"]
        result = self.adapter.validate_status(path, self.signature(path, params), params)
        self.assertEqual(result["to"], "")
        self.assertEqual(result["from"], BUSINESS)
        self.assertEqual(result["sid"], SID)
        self.assertEqual(result["account_sid"], ACCOUNT)

    def test_status_validation_checks_sender_account_recipient_and_sms_status(self):
        for updates, expected in [({"From": CUSTOMER}, "sender_mismatch"),
                                  ({"AccountSid": "AC" + "e" * 32}, "account_mismatch"),
                                  ({"To": "not-e164"}, "invalid_recipient_phone"),
                                  ({"MessageStatus": "read"}, "invalid_message_status"),
                                  ({"SmsStatus": "failed"}, "conflicting_message_status"),
                                  ({"ErrorCode": "secret error text"}, "invalid_error_code")]:
            with self.subTest(expected=expected), self.assertRaises(WebhookRejected) as caught:
                self.status(**updates)
            self.assertEqual(caught.exception.code, expected)

    def test_opt_out_failure_callback_code21610_is_preserved(self):
        result = self.status(MessageStatus="undelivered", ErrorCode="21610")
        self.assertEqual(result["error_code"], "21610")
        self.assertEqual(result["status"], "undelivered")

    def test_missing_canonical_configuration_fails_closed(self):
        for changes in [{"auth_token": ""}, {"public_base_url": "http://synthetic.example.test"},
                        {"public_base_url": "https://user:secret@synthetic.example.test"},
                        {"account_sid": ""}]:
            adapter = ServiceWebhookAdapter(dict(self.cfg, **changes))
            with self.subTest(changes=list(changes)), self.assertRaises(WebhookRejected) as caught:
                adapter.validate_inbound(self.path, self.signature(), self.params)
            self.assertEqual(caught.exception.status, 503)


if __name__ == "__main__":
    unittest.main()
