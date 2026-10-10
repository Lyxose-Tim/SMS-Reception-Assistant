"""Service HTTP authentication and signed intake, using local sockets only."""
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
import http.client
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlencode, urlsplit

from app import ServiceServer
from reception.auth import hash_password
from reception.config import ServiceConfig
from reception.engine import Engine

try:
    from twilio.request_validator import RequestValidator
except ImportError:
    RequestValidator = None


class HttpRecordingProvider:
    def __init__(self):
        self.proofs = {}
        self.fetch_calls = []
        self.send_calls = []

    def fetch_inbound(self, sid):
        self.fetch_calls.append(sid)
        return dict(self.proofs[sid])

    def send(self, to, body, callback_url):
        self.send_calls.append((to, body, callback_url))
        return {'sid': 'SM' + f'{len(self.send_calls) + 1000:032x}', 'status': 'queued', 'error_code': None}


class ServiceHttpTests(unittest.TestCase):
    PASSWORD = 'Synthetic service admin password 123!'
    PASSWORD_HASH = hash_password(PASSWORD, salt=b'0123456789abcdef')
    PHONE = '+12025550181'

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.config = ServiceConfig(
            mode='service', admin_password_hash=self.PASSWORD_HASH,
            public_base_url='https://synthetic.example.test', account_sid='AC' + 'a' * 32,
            auth_token='synthetic-signing-secret-never-live', phone_number='+12025550100',
            live_enabled=False, allowed_recipients=(self.PHONE,), optout_reviewed=True,
            readiness_approved=True, send_approval_recorded=True,
        )
        self.engine = Engine(str(Path(self.temp.name) / 'http.sqlite3'), production=True,
                             seed_demo=False, send_policy=self.config.send_policy())
        business = self.engine.snapshot()['business']
        business['approved_faqs'] = [{'topic': 'hours', 'answer': 'Synthetic approved hours: Monday-Friday, 9 AM-5 PM.', 'approved': True}]
        self.engine.save_business(business)
        self.provider = HttpRecordingProvider()
        self.server = ServiceServer(('127.0.0.1', 0), self.engine, self.config, provider=self.provider)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.host = f'127.0.0.1:{self.server.server_port}'
        self.cookie = ''
        self.token = ''

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.engine.close()
        self.temp.cleanup()

    def request(self, method, path, payload=None, headers=None, raw=None, authenticated=True, duplicate_headers=()):
        req_headers = {'Host': self.host}
        if authenticated and self.cookie:
            req_headers['Cookie'] = self.cookie
        if payload is not None:
            raw = json.dumps(payload).encode()
            req_headers['Content-Type'] = 'application/json'
            if authenticated and self.token:
                req_headers['X-Demo-Token'] = self.token
        req_headers.update(headers or {})
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
        try:
            if duplicate_headers:
                connection.putrequest(method, path, skip_host=True)
                for name, value in req_headers.items():
                    connection.putheader(name, value)
                for name, value in duplicate_headers:
                    connection.putheader(name, value)
                if raw is not None:
                    connection.putheader('Content-Length', str(len(raw)))
                connection.endheaders(raw)
            else:
                connection.request(method, path, body=raw, headers=req_headers)
            response = connection.getresponse()
            response_headers = dict(response.getheaders())
            body = response.read().decode()
            value = json.loads(body) if response_headers.get('Content-Type', '').startswith('application/json') else body
            return response.status, response_headers, value
        finally:
            connection.close()

    def login(self):
        status, headers, result = self.request('POST', '/api/login', {'password': self.PASSWORD}, authenticated=False)
        self.assertEqual(status, 200, result)
        self.cookie = headers['Set-Cookie'].split(';', 1)[0]
        self.token = result['token']
        return headers

    def inbound(self, sid='SM' + '1' * 32, body='What are your opening hours?', opt_out_type=''):
        params = {'AccountSid': self.config.account_sid, 'MessageSid': sid,
                  'From': self.PHONE, 'To': self.config.phone_number, 'Body': body,
                  'NumMedia': '0'}
        if opt_out_type:
            params['OptOutType'] = opt_out_type
        self.provider.proofs[sid] = {
            'verified': True, 'resource_id': sid, 'account_sid': self.config.account_sid,
            'phone': self.PHONE, 'to': self.config.phone_number, 'body': body, 'direction': 'inbound',
            'occurred_at': datetime.now(timezone.utc).isoformat(),
        }
        return params

    def signed_request(self, path, params, signature=None, headers=None):
        signature = signature or RequestValidator(self.config.auth_token).compute_signature(
            self.config.public_base_url + path, params)
        return self.request('POST', path, raw=urlencode(params).encode(), headers={
            'Content-Type': 'application/x-www-form-urlencoded', 'X-Twilio-Signature': signature,
            **(headers or {})}, authenticated=False)

    def test_service_session_and_private_state_require_authenticated_cookie(self):
        for path in ['/api/session', '/api/state']:
            with self.subTest(path=path):
                status, _, _ = self.request('GET', path, authenticated=False)
                self.assertEqual(status, 401)
        status, _, _ = self.request('POST', '/api/handoff', {'phone': self.PHONE, 'active': True}, authenticated=False)
        self.assertEqual(status, 401)
        headers = self.login()
        self.assertIn('HttpOnly', headers['Set-Cookie'])
        self.assertIn('SameSite=Strict', headers['Set-Cookie'])
        self.assertGreaterEqual(len(self.token), 32)
        status, _, state = self.request('GET', '/api/state')
        self.assertEqual(status, 200)
        self.assertEqual(state['runtime']['mode'], 'service')
        self.assertFalse(state['runtime']['real_outbound'])

    def test_wrong_login_password_and_rate_limit_are_enforced(self):
        for _ in range(5):
            status, _, _ = self.request('POST', '/api/login', {'password': 'wrong password'}, authenticated=False)
            self.assertEqual(status, 401)
        status, _, error = self.request('POST', '/api/login', {'password': self.PASSWORD}, authenticated=False)
        self.assertEqual(status, 429)
        self.assertNotIn(self.PASSWORD_HASH, str(error))

    def test_explicit_proxy_clients_have_independent_login_limits(self):
        self.config.trusted_proxy_cidrs = ('127.0.0.1/32',)
        first = {'X-Forwarded-For': '192.0.2.10'}
        for _ in range(5):
            status, _, _ = self.request('POST', '/api/login', {'password': 'wrong password'}, first, authenticated=False)
            self.assertEqual(status, 401)
        status, _, _ = self.request('POST', '/api/login', {'password': self.PASSWORD}, first, authenticated=False)
        self.assertEqual(status, 429)
        status, _, _ = self.request('POST', '/api/login', {'password': self.PASSWORD},
                                     {'X-Forwarded-For': '192.0.2.11'}, authenticated=False)
        self.assertEqual(status, 200)

    def test_forwarded_spoof_does_not_bypass_default_socket_peer_limit(self):
        for index in range(5):
            status, _, _ = self.request('POST', '/api/login', {'password': 'wrong password'},
                                         {'X-Forwarded-For': f'192.0.2.{index + 1}'}, authenticated=False)
            self.assertEqual(status, 401)
        status, _, _ = self.request('POST', '/api/login', {'password': self.PASSWORD},
                                     {'X-Forwarded-For': '192.0.2.99'}, authenticated=False)
        self.assertEqual(status, 429)

    def test_trusted_proxy_rejects_malformed_duplicate_and_missing_forwarding(self):
        self.config.trusted_proxy_cidrs = ('127.0.0.1/32',)
        for headers in [{}, {'X-Forwarded-For': 'not-an-ip'},
                        {'X-Forwarded-For': '192.0.2.1, 192.0.2.2'}]:
            status, _, _ = self.request('POST', '/api/login', {'password': self.PASSWORD}, headers, authenticated=False)
            self.assertEqual(status, 400)
        status, _, _ = self.request('POST', '/api/login', {'password': self.PASSWORD},
                                     {'X-Forwarded-For': '192.0.2.1'}, authenticated=False,
                                     duplicate_headers=(('X-Forwarded-For', '192.0.2.2'),))
        self.assertEqual(status, 400)
        self.assertEqual(self.server.auth.sessions, {})

    def test_malformed_and_duplicate_cookie_headers_fail_without_server_error(self):
        self.login()
        for cookie in ['invalid[=x', 'reception_session="unterminated', self.cookie + '; ' + self.cookie]:
            status, _, _ = self.request('GET', '/api/state', headers={'Cookie': cookie})
            self.assertEqual(status, 401)
        status, _, _ = self.request('GET', '/api/state', duplicate_headers=(('Cookie', self.cookie),))
        self.assertEqual(status, 401)

    def test_human_reply_passes_explicit_source_and_confirmation_and_rejects_non_boolean(self):
        self.login()
        payload = {'phone': self.PHONE, 'text': 'Approved manual response',
                   'event_id': 'operator-confirmed-source', 'reply_to_event_id': 'twilio:' + 'SM' + '3' * 32,
                   'confirm_service_inquiry': True}
        with patch.object(self.engine, 'human_reply', return_value={'status': 'queued'}) as reply:
            status, _, _ = self.request('POST', '/api/human-reply', payload)
            self.assertEqual(status, 200)
            self.assertEqual(reply.call_args.kwargs, {'event_id': payload['event_id'],
                'reply_to_event_id': payload['reply_to_event_id'], 'confirm_service_inquiry': True})
            reply.reset_mock()
            status, _, _ = self.request('POST', '/api/human-reply', {**payload, 'confirm_service_inquiry': 'true'})
            self.assertEqual(status, 400)
            reply.assert_not_called()

    def test_verified_unknown_inquiry_requires_manual_confirmation_and_stop_still_wins(self):
        self.login()
        params = self.inbound(body='Are you insured?')
        self.server.inbox.accept({'sid': params['MessageSid'], 'from': params['From'],
                                  'to': params['To'], 'body': params['Body'], 'opt_out_type': ''})
        self.assertEqual(self.server.inbox.process_one()['status'], 'handoff')
        payload = {'phone': self.PHONE, 'text': 'The owner will review your insurance question.',
                   'event_id': 'operator-unconfirmed-inquiry', 'reply_to_event_id': 'twilio:' + params['MessageSid']}
        status, _, result = self.request('POST', '/api/human-reply', payload)
        self.assertEqual(status, 200)
        self.assertEqual(result['reason'], 'service_inquiry_confirmation_required')
        status, _, result = self.request('POST', '/api/human-reply', {
            **payload, 'event_id': 'operator-confirmed-inquiry', 'confirm_service_inquiry': True})
        self.assertEqual(status, 200, result)
        self.assertEqual(result['status'], 'queued')
        contact = self.engine._contact(self.PHONE)
        self.assertEqual(contact['consent_status'], 'none')
        self.assertEqual(contact['reply_permission_basis'], 'operator_review_human_only')
        self.assertTrue(contact['handoff'])
        self.server.inbox.accept({'sid': 'SM' + '2' * 32, 'from': self.PHONE,
                                  'to': self.config.phone_number, 'body': 'STOP', 'opt_out_type': 'STOP'})
        status, _, result = self.request('POST', '/api/human-reply', {
            **payload, 'event_id': 'operator-after-withdrawal', 'confirm_service_inquiry': True})
        self.assertEqual(status, 200)
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(self.provider.send_calls, [])
        self.assertTrue(all(row['status'] == 'canceled' for row in self.engine.outbox.snapshot()['items']))

    def test_shutdown_timeout_retains_database_until_active_handler_drains(self):
        self.login()
        entered, release = threading.Event(), threading.Event()
        results = []
        def slow_save(_):
            entered.set()
            release.wait(2)
            return {'status': 'saved'}
        def perform_request():
            results.append(self.request('POST', '/api/business', {}))
        with patch.object(self.engine, 'save_business', side_effect=slow_save):
            pending = threading.Thread(target=perform_request)
            pending.start()
            try:
                self.assertTrue(entered.wait(1))
                self.assertFalse(self.server.drain(timeout=0.02))
                self.assertEqual(self.engine.db.execute('SELECT 1').fetchone()[0], 1)
                self.assertTrue(self.engine.closing)
            finally:
                release.set()
                pending.join(timeout=2)
            self.assertFalse(pending.is_alive())
            self.assertTrue(self.server.drain(timeout=1))
        self.assertEqual(results[0][0], 200)

    def test_cross_origin_login_is_rejected_before_authentication(self):
        for origin in ['https://attacker.example.test', 'null']:
            with self.subTest(origin=origin):
                status, _, _ = self.request('POST', '/api/login', {'password': self.PASSWORD},
                                             {'Origin': origin}, authenticated=False)
                self.assertEqual(status, 403)

    def test_valid_cookie_does_not_bypass_csrf_origin_or_host_guards(self):
        self.login()
        payload = {'phone': self.PHONE, 'active': True}
        for headers in [{'X-Demo-Token': ''}, {'X-Demo-Token': 'bad-token'},
                        {'Origin': 'https://attacker.example.test'}, {'Sec-Fetch-Site': 'cross-site'},
                        {'Host': 'attacker.example.test'}]:
            with self.subTest(headers=headers):
                status, _, _ = self.request('POST', '/api/handoff', payload, headers)
                self.assertEqual(status, 403)
        self.assertEqual(self.engine.snapshot()['contacts'], [])

    def test_simulation_and_demo_reset_are_unavailable_in_service_mode(self):
        self.login()
        for path in ['/api/simulate', '/api/reset']:
            with self.subTest(path=path):
                status, _, _ = self.request('POST', path, {'event_id': 'forged-demo', 'kind': 'sms',
                                                         'phone': self.PHONE, 'text': 'START'})
                self.assertEqual(status, 404)
        self.assertEqual(self.engine.snapshot()['contacts'], [])

    def test_logout_revokes_cookie_and_token_instead_of_merely_clearing_browser(self):
        self.login()
        cookie, token = self.cookie, self.token
        status, headers, _ = self.request('POST', '/api/logout', {})
        self.assertEqual(status, 200)
        self.assertIn('Max-Age=0', headers['Set-Cookie'])
        status, _, _ = self.request('GET', '/api/state', headers={'Cookie': cookie})
        self.assertEqual(status, 401)
        status, _, _ = self.request('POST', '/api/handoff', {'phone': self.PHONE, 'active': True},
                                     {'Cookie': cookie, 'X-Demo-Token': token})
        self.assertEqual(status, 401)

    def test_session_expiration_and_csrf_tokens_are_bound_to_individual_sessions(self):
        self.login()
        first_cookie, first_token = self.cookie, self.token
        self.login()
        second_cookie, second_token = self.cookie, self.token
        self.assertNotEqual(first_cookie, second_cookie)
        self.assertNotEqual(first_token, second_token)
        payload = {'phone': self.PHONE, 'active': True}
        status, _, _ = self.request('POST', '/api/handoff', payload,
                                     {'Cookie': first_cookie, 'X-Demo-Token': second_token})
        self.assertEqual(status, 403)
        status, _, _ = self.request('POST', '/api/handoff', payload,
                                     {'Cookie': first_cookie, 'X-Demo-Token': first_token})
        self.assertEqual(status, 200)
        future_clock = datetime.now(timezone.utc).timestamp() + self.config.session_ttl_seconds + 60
        with patch('reception.auth.time.time', return_value=future_clock):
            status, _, _ = self.request('GET', '/api/state', headers={'Cookie': second_cookie})
            self.assertEqual(status, 401)

    def test_credential_fields_are_not_exposed_in_state_frontend_or_generic_errors(self):
        self.login()
        secrets = [self.PASSWORD_HASH, self.config.auth_token, self.config.account_sid, self.PASSWORD]
        for path in ['/api/state', '/', '/app.js', '/styles.css']:
            with self.subTest(path=path):
                status, _, content = self.request('GET', path)
                self.assertEqual(status, 200)
                for secret in secrets:
                    self.assertNotIn(secret, str(content))
        with patch.object(self.engine, 'human_reply', side_effect=RuntimeError('synthetic-private-provider-secret')):
            status, _, error = self.request('POST', '/api/human-reply', {
                'phone': self.PHONE, 'text': 'Hello', 'event_id': 'operator-synthetic-error'})
            self.assertGreaterEqual(status, 500)
            self.assertNotIn('synthetic-private-provider-secret', str(error))

    @unittest.skipUnless(RequestValidator, 'Install requirements-twilio.txt for signed service HTTP tests')
    def test_signed_webhook_returns_empty_twiml_and_defers_all_provider_work(self):
        params = self.inbound()
        status, _, response = self.signed_request('/webhooks/twilio/sms', params)
        self.assertEqual(status, 200, response)
        self.assertIn('<Response/>', response)
        self.assertNotIn('<Message', response)
        self.assertEqual(self.provider.fetch_calls, [])
        self.assertEqual(self.provider.send_calls, [])
        self.assertEqual(self.engine.outbox.snapshot()['items'], [])
        self.server.inbox.process_one()
        self.assertEqual(self.provider.fetch_calls, [params['MessageSid']])
        self.assertEqual(len(self.engine.outbox.snapshot()['items']), 1)
        self.assertEqual(self.provider.send_calls, [])

    @unittest.skipUnless(RequestValidator, 'Install requirements-twilio.txt for signed service HTTP tests')
    def test_canonical_raw_query_and_every_form_parameter_are_signed(self):
        path = '/webhooks/twilio/sms?source=one%20two&revision=1'
        params = {**self.inbound(), 'AdditionalParameter': 'original signed value'}
        signature = RequestValidator(self.config.auth_token).compute_signature(self.config.public_base_url + path, params)
        tampered = {**params, 'AdditionalParameter': 'tampered'}
        status, _, _ = self.signed_request(path, tampered, signature)
        self.assertEqual(status, 403)
        status, _, _ = self.signed_request(path.replace('%20', '+'), params, signature)
        self.assertEqual(status, 403)
        status, _, _ = self.signed_request(path, params, signature, {'Host': 'untrusted-forwarded.example.test',
                                                                   'X-Forwarded-Host': 'other.example.test',
                                                                   'X-Forwarded-Proto': 'http'})
        self.assertEqual(status, 200)
        self.assertEqual(self.provider.fetch_calls, [])

    @unittest.skipUnless(RequestValidator, 'Install requirements-twilio.txt for signed service HTTP tests')
    def test_wrong_account_recipient_unsigned_or_json_webhooks_are_rejected(self):
        params = self.inbound()
        for modified in [{**params, 'AccountSid': 'AC' + 'f' * 32}, {**params, 'To': '+12025550999'}]:
            with self.subTest(modified=modified):
                status, _, _ = self.signed_request('/webhooks/twilio/sms', modified)
                self.assertEqual(status, 403)
        status, _, _ = self.signed_request('/webhooks/twilio/sms', params, 'bad-signature')
        self.assertEqual(status, 403)
        status, _, _ = self.request('POST', '/webhooks/twilio/sms', {'Body': 'START'}, authenticated=False)
        self.assertEqual(status, 415)
        self.assertEqual(self.provider.fetch_calls, [])
        self.assertEqual(self.engine.snapshot()['contacts'], [])

    @unittest.skipUnless(RequestValidator, 'Install requirements-twilio.txt for signed service HTTP tests')
    def test_signed_stop_restricts_queued_business_reply_immediately_without_network(self):
        status, _, _ = self.signed_request('/webhooks/twilio/sms', self.inbound())
        self.assertEqual(status, 200)
        self.server.inbox.process_one()
        self.assertEqual(self.engine.outbox.snapshot()['items'][0]['status'], 'queued')
        before = list(self.provider.fetch_calls)
        status, _, _ = self.signed_request('/webhooks/twilio/sms', self.inbound('SM' + '2' * 32, 'STOP', 'STOP'))
        self.assertEqual(status, 200)
        self.assertEqual(self.provider.fetch_calls, before)
        self.assertEqual(self.provider.send_calls, [])
        contact = next(c for c in self.engine.snapshot()['contacts'] if c['phone'] == self.PHONE)
        self.assertEqual(contact['consent_status'], 'revoked')
        self.assertEqual(self.engine.outbox.snapshot()['items'][0]['status'], 'canceled')

    @unittest.skipUnless(RequestValidator, 'Install requirements-twilio.txt for signed service HTTP tests')
    def test_callback_validates_signature_token_recipient_and_preserves_terminal_status(self):
        self.signed_request('/webhooks/twilio/sms', self.inbound())
        self.server.inbox.process_one()
        self.config.live_enabled = True
        self.engine.outbox.policy = self.config.send_policy()
        self.engine.outbox.run_once(self.provider)
        row = self.engine.outbox.snapshot()['items'][0]
        callback_url = self.provider.send_calls[0][2]
        parts = urlsplit(callback_url)
        path = parts.path + '?' + parts.query
        params = {'AccountSid': self.config.account_sid, 'MessageSid': row['provider_sid'],
                  'From': self.config.phone_number, 'To': self.PHONE, 'MessageStatus': 'delivered'}
        status, _, _ = self.signed_request(path, params, 'bad-signature')
        self.assertEqual(status, 403)
        wrong = {**params, 'To': '+12025550999'}
        status, _, _ = self.signed_request(path, wrong)
        self.assertIn(status, [400, 403])
        query = parse_qs(parts.query)
        bad_path = parts.path + '?' + urlencode({'outbox_id': query['outbox_id'][0], 'token': 'wrong-token'})
        status, _, _ = self.signed_request(bad_path, params)
        self.assertIn(status, [400, 403])
        self.assertEqual(self.engine.outbox.snapshot()['items'][0]['status'], 'accepted')
        # Twilio's documented callback subset can omit To. The already-signed
        # nonce, account, sender, and provider SID still bind this queue item.
        callback_without_to = {key: value for key, value in params.items() if key != 'To'}
        status, _, _ = self.signed_request(path, callback_without_to)
        self.assertEqual(status, 200)
        status, _, _ = self.signed_request(path, {**params, 'MessageStatus': 'sent'})
        self.assertEqual(status, 200)
        self.assertEqual(self.engine.outbox.snapshot()['items'][0]['status'], 'delivered')

    @unittest.skipUnless(RequestValidator, 'Install requirements-twilio.txt for signed service HTTP tests')
    def test_http_access_logs_do_not_disclose_phone_body_query_token_or_credentials(self):
        output = io.StringIO()
        sensitive_body = 'Synthetic private cleaning inquiry string 93812'
        with redirect_stdout(output), redirect_stderr(output):
            status, _, _ = self.signed_request('/webhooks/twilio/sms?token=synthetic-private-query-token',
                                               self.inbound(body=sensitive_body))
            self.assertEqual(status, 200)
        for secret in [self.PHONE, sensitive_body, 'synthetic-private-query-token', self.config.auth_token]:
            self.assertNotIn(secret, output.getvalue())


if __name__ == '__main__':
    unittest.main()
