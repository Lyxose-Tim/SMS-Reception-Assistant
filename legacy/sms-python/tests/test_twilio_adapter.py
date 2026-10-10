"""Provider intake boundaries, with synthetic signatures and trusted proofs only.

No Twilio client, account, or network request is used. SDK-dependent tests skip
when the optional official signature-validation dependency is not installed.
"""
from datetime import datetime, timedelta, timezone
import unittest

from reception.twilio_adapter import IntakeError, TwilioAdapter, WebhookSettings

try:
    from twilio.request_validator import RequestValidator
except ImportError:
    RequestValidator = None


class TwilioConfigurationTests(unittest.TestCase):
    def test_unsigned_webhook_is_rejected_without_lookup(self):
        calls = []
        adapter = TwilioAdapter(trusted_lookup=lambda *args: calls.append(args))
        with self.assertRaises(IntakeError) as caught:
            adapter.event('/webhooks/twilio/sms', '', {})
        self.assertEqual((caught.exception.status, caught.exception.code), (403, 'invalid_signature'))
        self.assertEqual(calls, [])

    def test_missing_configuration_fails_closed(self):
        adapter = TwilioAdapter(WebhookSettings())
        with self.assertRaises(IntakeError) as caught:
            adapter.event('/webhooks/twilio/sms', 'synthetic-signature', {})
        self.assertEqual(caught.exception.status, 503)


@unittest.skipUnless(RequestValidator, 'Install requirements-twilio.txt for official SDK signature tests')
class SignedTwilioIntakeTests(unittest.TestCase):
    def setUp(self):
        self.settings = WebhookSettings(
            auth_token='synthetic-test-secret-never-live',
            account_sid='AC' + 'a' * 32,
            phone_number='+12025550100',
            public_base_url='https://synthetic.example.test',
            max_age_seconds=300,
        )
        self.params = {
            'AccountSid': self.settings.account_sid,
            'MessageSid': 'SM' + 'b' * 32,
            'From': '+12025550106',
            'To': self.settings.phone_number,
            'Body': 'What are your hours?',
        }
        self.path = '/webhooks/twilio/sms'
        self.lookup_calls = []
        self.proof_changes = {}
        self.adapter = TwilioAdapter(self.settings, self.lookup)

    def lookup(self, kind, sid, params):
        self.lookup_calls.append((kind, sid, params))
        proof = {
            'verified': True,
            'account_sid': self.settings.account_sid,
            'phone': self.params['From'],
            'to': self.settings.phone_number,
            'resource_id': sid,
            'occurred_at': datetime.now(timezone.utc).isoformat(),
            'direction': 'inbound',
            'body': params.get('Body', ''),
            'dial_status': params.get('DialCallStatus'),
            'dial_sid': params.get('DialCallSid'),
            'parent_call_sid': sid,
        }
        proof.update(self.proof_changes)
        return proof

    def signature(self, path=None, params=None):
        return RequestValidator(self.settings.auth_token).compute_signature(
            self.settings.public_base_url + (path or self.path),
            self.params if params is None else params,
        )

    def event(self):
        return self.adapter.event(self.path, self.signature(), dict(self.params))

    def assert_denied(self, code, status=None):
        with self.assertRaises(IntakeError) as caught:
            self.event()
        self.assertEqual(caught.exception.code, code)
        if status is not None:
            self.assertEqual(caught.exception.status, status)

    def test_valid_signature_creates_verified_inbound_event(self):
        event = self.event()
        self.assertEqual(event['event_id'], 'twilio:' + self.params['MessageSid'])
        self.assertEqual(event['origin'], 'twilio')
        self.assertTrue(event['verified'])
        self.assertEqual(event['text'], self.params['Body'])
        self.assertNotIn('auth_token', event)
        self.assertEqual(self.lookup_calls[0][0], 'sms')

    def test_signature_covers_all_form_parameters(self):
        self.params['UnrecognizedAdditionalParameter'] = 'covered by Twilio signing'
        signed = self.signature()
        altered = dict(self.params, UnrecognizedAdditionalParameter='tampered')
        with self.assertRaises(IntakeError) as caught:
            self.adapter.event(self.path, signed, altered)
        self.assertEqual(caught.exception.code, 'invalid_signature')
        self.assertEqual(self.lookup_calls, [])

    def test_signature_uses_canonical_url_and_raw_query(self):
        self.path += '?source=dial%20action&x=1'
        self.assertEqual(self.event()['kind'], 'sms')
        wrong_url_signature = RequestValidator(self.settings.auth_token).compute_signature(
            'https://attacker.example.test' + self.path, self.params)
        with self.assertRaises(IntakeError) as caught:
            self.adapter.event(self.path, wrong_url_signature, self.params)
        self.assertEqual(caught.exception.code, 'invalid_signature')
        with self.assertRaises(IntakeError):
            self.adapter.event('/webhooks/twilio/sms?source=dial+action&x=1', self.signature(), self.params)

    def test_modified_body_invalidates_signature_before_lookup(self):
        signed = self.signature()
        self.params['Body'] = 'START'
        with self.assertRaises(IntakeError) as caught:
            self.adapter.event(self.path, signed, self.params)
        self.assertEqual(caught.exception.status, 403)
        self.assertEqual(self.lookup_calls, [])

    def test_validly_signed_wrong_account_or_recipient_is_denied(self):
        for field, value in [('AccountSid', 'AC' + 'c' * 32), ('To', '+12025550999')]:
            with self.subTest(field=field):
                original = self.params[field]
                self.params[field] = value
                self.assert_denied('account_or_recipient_mismatch', 403)
                self.params[field] = original
        self.assertEqual(self.lookup_calls, [])

    def test_signed_form_without_trusted_freshness_lookup_fails_closed(self):
        self.adapter = TwilioAdapter(self.settings)
        self.assert_denied('trusted_provider_lookup_required', 503)

    def test_lookup_unavailable_or_unverified_fails_closed(self):
        def broken(*_):
            raise RuntimeError('synthetic credential should not escape')
        self.adapter = TwilioAdapter(self.settings, broken)
        self.assert_denied('provider_verification_unavailable', 503)
        self.adapter = TwilioAdapter(self.settings, self.lookup)
        self.proof_changes['verified'] = False
        self.assert_denied('unverified_provider_resource', 403)

    def test_verified_proof_must_bind_sender_account_recipient_and_resource(self):
        for field, value in [('phone', '+12025550999'), ('to', '+12025550999'),
                             ('account_sid', 'AC' + 'c' * 32), ('resource_id', 'SM' + 'c' * 32)]:
            with self.subTest(field=field):
                self.proof_changes = {field: value}
                self.assert_denied('provider_resource_mismatch', 403)

    def test_proof_must_bind_inbound_direction_and_exact_message_body(self):
        for changes in [{'direction': 'outbound-api'}, {'body': 'START'}]:
            with self.subTest(changes=changes):
                self.proof_changes = changes
                self.assert_denied('provider_message_mismatch', 403)

    def test_unsigned_or_naive_timestamp_is_not_freshness_evidence(self):
        for stamp in ['not-a-date', datetime.now().isoformat(), None]:
            with self.subTest(stamp=stamp):
                self.proof_changes = {'occurred_at': stamp}
                self.assert_denied('provider_timestamp_required', 403)

    def test_stale_start_is_denied_and_stale_stop_is_enforced_without_confirmation(self):
        self.proof_changes = {'occurred_at': (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()}
        self.params['Body'] = 'START'
        self.assert_denied('expired_provider_event', 409)
        self.params['Body'] = ' STOP '
        event = self.event()
        self.assertEqual(event['text'].strip(), 'STOP')
        self.assertTrue(event['suppress_confirmation'])
        self.params['Body'] = 'ordinary consultation'
        self.params['OptOutType'] = 'STOP'
        event = self.event()
        self.assertEqual(event['text'], 'STOP')
        self.assertTrue(event['suppress_confirmation'])

    def test_future_resource_is_denied_even_for_stop(self):
        self.proof_changes = {'occurred_at': (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()}
        self.params['Body'] = 'STOP'
        self.assert_denied('expired_provider_event', 409)

    def test_advanced_optout_does_not_generate_duplicate_application_confirmation(self):
        for command in ['STOP', 'START', 'HELP']:
            with self.subTest(command=command):
                self.params.update(Body=command, OptOutType=command)
                event = self.event()
                self.assertTrue(event['provider_managed_optout'])
                self.assertTrue(event['suppress_confirmation'])
                self.assertEqual(event['opt_out_type'], command)

    def dial_params(self, status='no-answer'):
        self.path = '/webhooks/twilio/dial-result'
        self.params.pop('MessageSid', None)
        self.params.pop('Body', None)
        self.params.update(CallSid='CA' + 'd' * 32, DialCallSid='CA' + 'e' * 32, DialCallStatus=status)

    def test_dial_action_result_retains_no_answer_and_completed_for_domain_decision(self):
        for status in ['no-answer', 'busy', 'failed', 'canceled', 'completed']:
            with self.subTest(status=status):
                self.dial_params(status)
                event = self.event()
                self.assertEqual(event['kind'], 'missed_call')
                self.assertEqual(event['dial_status'], status)
                self.assertEqual(self.lookup_calls[-1][0], 'missed_call')

    def test_ordinary_call_completion_without_dial_result_is_rejected(self):
        self.dial_params()
        self.params.pop('DialCallStatus')
        self.params['CallStatus'] = 'completed'
        self.assert_denied('dial_result_required', 400)

    def test_parent_child_or_status_proof_mismatch_is_rejected(self):
        self.dial_params()
        for field, value in [('parent_call_sid', 'CA' + 'f' * 32),
                             ('dial_sid', 'CA' + 'f' * 32), ('dial_status', 'completed')]:
            with self.subTest(field=field):
                self.proof_changes = {field: value}
                self.assert_denied('provider_dial_mismatch', 403)

    def test_missing_or_malformed_child_sid_is_rejected_before_any_trusted_lookup(self):
        self.dial_params()
        for child_sid in [None, '', 'CA-short', 'SM' + 'e' * 32, 'CA' + 'g' * 32]:
            with self.subTest(child_sid=child_sid):
                if child_sid is None:
                    self.params.pop('DialCallSid', None)
                else:
                    self.params['DialCallSid'] = child_sid
                # Even a resolver that would claim an absent child is verified
                # cannot make an incomplete parent/child dial result acceptable.
                self.proof_changes = {'dial_sid': None}
                self.assert_denied('dial_child_sid_required', 400)
                self.assertEqual(self.lookup_calls, [])


if __name__ == '__main__':
    unittest.main()
