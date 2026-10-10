"""Exercise the real local HTTP server; all data and transport stay synthetic."""
import http.client
from datetime import datetime, timedelta, timezone
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.parse import urlencode

import app as app_module
from app import DemoServer
from reception.engine import Engine
from reception.twilio_adapter import TwilioAdapter, WebhookSettings

try:
    from twilio.request_validator import RequestValidator
except ImportError:
    RequestValidator = None


class LocalHttpTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.engine = Engine(str(Path(self.tmp.name) / 'test.sqlite3'))
        self.server = DemoServer(('127.0.0.1', 0), self.engine)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.host = f'127.0.0.1:{self.server.server_port}'
        status, _, data = self.request('GET', '/api/session')
        self.assertEqual(status, 200)
        self.token = data['token']

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        if hasattr(self.engine, 'close'):
            self.engine.close()
        self.tmp.cleanup()

    def request(self, method, path, payload=None, headers=None, raw=None):
        req_headers = {'Host': self.host}
        if payload is not None:
            raw = json.dumps(payload).encode()
            req_headers.update({'Content-Type': 'application/json', 'X-Demo-Token': self.token})
        req_headers.update(headers or {})
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
        try:
            connection.request(method, path, body=raw, headers=req_headers)
            response = connection.getresponse()
            body = response.read().decode('utf-8')
            response_headers = dict(response.getheaders())
            parsed = json.loads(body) if response_headers.get('Content-Type', '').startswith('application/json') else body
            return response.status, response_headers, parsed
        finally:
            connection.close()

    def state(self):
        status, _, data = self.request('GET', '/api/state')
        self.assertEqual(status, 200)
        return data

    def contact(self, phone):
        return next(c for c in self.state()['contacts'] if c['phone'] == phone)

    def outbound(self, phone):
        conversation = next(c for c in self.state()['conversations'] if c['phone'] == phone)
        return [m for m in conversation['messages'] if m['direction'] == 'outbound']

    def install_synthetic_provider(self):
        self.webhook_settings = WebhookSettings('synthetic-webhook-secret', 'AC' + 'a' * 32,
                                               '+12025550100', 'https://synthetic.example.test')
        def proof(kind, sid, params):
            return {'verified': True, 'account_sid': params['AccountSid'], 'phone': params['From'],
                    'to': params['To'], 'resource_id': sid, 'direction': 'inbound',
                    'body': params.get('Body', ''), 'dial_status': params.get('DialCallStatus'),
                    'dial_sid': params.get('DialCallSid'), 'parent_call_sid': sid,
                    'occurred_at': self.provider_time}
        self.provider_time = datetime.now(timezone.utc).isoformat()
        self.server.adapter = TwilioAdapter(self.webhook_settings, proof)

    def signed_webhook(self, path, params, signature=None):
        params = {'AccountSid': self.webhook_settings.account_sid,
                  'To': self.webhook_settings.phone_number, **params}
        signature = signature or RequestValidator(self.webhook_settings.auth_token).compute_signature(
            self.webhook_settings.public_base_url + path, params)
        return self.request('POST', path, raw=urlencode(params).encode(), headers={
            'Content-Type': 'application/x-www-form-urlencoded', 'X-Twilio-Signature': signature})

    def test_local_session_and_state_label_simulation_and_disabled_real_outbound(self):
        self.assertGreaterEqual(len(self.token), 32)
        state = self.state()
        self.assertEqual(state['runtime']['mode'], 'simulated')
        self.assertFalse(state['runtime']['real_outbound'])
        self.assertIn('mock', state['runtime']['model'])
        status, headers, _ = self.request('GET', '/api/state')
        self.assertEqual(status, 200)
        self.assertEqual(headers['Cache-Control'], 'no-store')
        self.assertIn("frame-ancestors 'none'", headers['Content-Security-Policy'])

    def test_same_origin_valid_session_can_simulate_an_inbound_sms(self):
        status, _, result = self.request('POST', '/api/simulate', {
            'event_id': 'http-faq-1', 'kind': 'sms', 'phone': '+12025550101', 'text': 'What are your hours?'
        }, {'Origin': 'http://' + self.host})
        self.assertEqual(status, 200, result)
        self.assertIn('hours', json.dumps(self.state()).lower())

    def test_mutations_need_session_token_and_cannot_be_cross_origin(self):
        before = self.state()
        payload = {'event_id': 'blocked-write', 'kind': 'sms', 'phone': '+12025550101', 'text': 'STOP'}
        cases = [
            {'X-Demo-Token': ''},
            {'X-Demo-Token': 'wrong-token'},
            {'Origin': 'https://untrusted.example.test'},
            {'Origin': 'null'},
            {'Sec-Fetch-Site': 'cross-site'},
            {'Host': 'evil.example.test'},
        ]
        for headers in cases:
            with self.subTest(headers=headers):
                status, _, _ = self.request('POST', '/api/simulate', payload, headers)
                self.assertEqual(status, 403)
        self.assertEqual(self.state(), before)

    def test_dns_rebinding_host_is_rejected_even_on_read(self):
        for path in ['/api/session', '/api/state', '/']:
            with self.subTest(path=path):
                status, _, _ = self.request('GET', path, headers={'Host': 'rebound.example.test'})
                self.assertEqual(status, 403)

    def test_client_cannot_forge_verified_provider_optout_or_origin(self):
        payload = {
            'event_id': 'http-strip-flags', 'kind': 'sms', 'phone': '+12025550106', 'text': 'STOP',
            'origin': 'twilio', 'verified': True, 'provider_managed_optout': True,
            'opt_out_type': 'START', 'suppress_confirmation': True,
        }
        with patch.object(self.engine, 'process_event', wraps=self.engine.process_event) as process:
            status, _, result = self.request('POST', '/api/simulate', payload)
            self.assertEqual(status, 200, result)
            received = process.call_args.args[0]
        self.assertEqual(received['origin'], 'simulation')
        for field in ['verified', 'provider_managed_optout', 'opt_out_type', 'suppress_confirmation']:
            self.assertNotIn(field, received)
        self.assertEqual(self.contact('+12025550106')['consent_status'], 'revoked')
        self.assertEqual(self.outbound('+12025550106')[-1]['purpose'], 'opt_out_confirmation')

    def test_invalid_json_types_oversized_body_and_utf8_are_rejected(self):
        cases = [
            (b'{not json', 'application/json', 400),
            (b'[]', 'application/json', 400),
            (b'{}', 'text/plain', 415),
            (b'\xff', 'application/json', 400),
            (b' ' * 32769, 'application/json', 413),
        ]
        for raw, content_type, expected in cases:
            with self.subTest(expected=expected, content_type=content_type, length=len(raw)):
                status, _, data = self.request('POST', '/api/simulate', raw=raw, headers={
                    'X-Demo-Token': self.token, 'Content-Type': content_type})
                self.assertEqual(status, expected, data)

    def test_handoff_requires_actual_boolean_and_sms_requires_valid_fields(self):
        for active in ['false', 1, None]:
            with self.subTest(active=active):
                status, _, _ = self.request('POST', '/api/handoff', {'phone': '+12025550104', 'active': active})
                self.assertEqual(status, 400)
        for path, payload in [('/api/handoff', {'phone': 'invalid-phone', 'active': True}),
                              ('/api/human-reply', {'phone': 'invalid-phone', 'text': 'Hello'})]:
            with self.subTest(path=path):
                status, _, _ = self.request('POST', path, payload)
                self.assertEqual(status, 400)
        for payload in [
            {'event_id': 'invalid-phone', 'kind': 'sms', 'phone': 'not-a-phone', 'text': 'hello'},
            {'event_id': 'empty-text', 'kind': 'sms', 'phone': '+12025550101', 'text': ''},
            {'event_id': 'invalid-kind', 'kind': 'unsupported-kind', 'phone': '+12025550101', 'text': 'hello'},
        ]:
            with self.subTest(payload=payload):
                status, _, _ = self.request('POST', '/api/simulate', payload)
                self.assertEqual(status, 400)

    def test_production_routes_reject_json_and_duplicate_form_parameters(self):
        status, _, _ = self.request('POST', '/webhooks/twilio/sms', {'kind': 'sms', 'text': 'START'})
        self.assertEqual(status, 415)
        status, _, data = self.request('POST', '/webhooks/twilio/sms', raw=b'Body=STOP&Body=START', headers={
            'Content-Type': 'application/x-www-form-urlencoded', 'X-Twilio-Signature': 'synthetic'})
        self.assertEqual(status, 400)
        self.assertEqual(data['error'], 'duplicate_form_parameter')

    def test_unsigned_production_webhook_cannot_mutate_consent(self):
        before = self.state()
        form = urlencode({'From': '+12025550107', 'To': '+12025550100', 'Body': 'START'}).encode()
        status, _, _ = self.request('POST', '/webhooks/twilio/sms', raw=form, headers={
            'Content-Type': 'application/x-www-form-urlencoded'})
        self.assertEqual(status, 403)
        self.assertEqual(self.state(), before)

    @unittest.skipUnless(RequestValidator, 'Install requirements-twilio.txt for signed HTTP webhook tests')
    def test_valid_signed_webhook_is_empty_twiml_and_replay_does_not_duplicate_reply(self):
        self.install_synthetic_provider()
        phone = '+12025550101'
        before = len(self.outbound(phone))
        params = {'From': phone, 'MessageSid': 'SM' + '1' * 32, 'Body': 'What are your hours?'}
        for _ in range(2):
            status, headers, response = self.signed_webhook('/webhooks/twilio/sms', params)
            self.assertEqual(status, 200, response)
            self.assertTrue(headers['Content-Type'].startswith('application/xml'))
            self.assertIn('<Response/>', response)
            self.assertNotIn('<Message', response)
            self.assertNotIn('<Dial', response)
        self.assertEqual(len(self.outbound(phone)), before + 1)
        self.assertTrue(all(m['transport'] == 'dry_run' and m['status'] == 'simulated' for m in self.outbound(phone)))

    @unittest.skipUnless(RequestValidator, 'Install requirements-twilio.txt for signed HTTP webhook tests')
    def test_valid_provider_start_never_creates_enrollment_and_call_alone_is_not_consent(self):
        self.install_synthetic_provider()
        phone = '+12025550101'
        before = len(self.outbound(phone))
        status, _, _ = self.signed_webhook('/webhooks/twilio/sms', {
            'From': phone, 'MessageSid': 'SM' + '2' * 32, 'Body': 'START',
            'consent': 'verified', 'verified': 'true', 'OptOutType': 'START'})
        self.assertEqual(status, 200)
        self.assertEqual(self.contact(phone)['consent_status'], 'none')
        self.assertFalse(self.contact(phone)['consent'].get('verified', False))
        status, _, _ = self.signed_webhook('/webhooks/twilio/dial-result', {
            'From': phone, 'CallSid': 'CA' + '2' * 32, 'DialCallSid': 'CA' + '3' * 32,
            'DialCallStatus': 'no-answer'})
        self.assertEqual(status, 200)
        self.assertEqual(len(self.outbound(phone)), before)
        self.assertEqual(self.contact(phone)['consent_status'], 'none')

    @unittest.skipUnless(RequestValidator, 'Install requirements-twilio.txt for signed HTTP webhook tests')
    def test_provider_advanced_optout_stale_stop_and_start_preserve_other_gates(self):
        self.install_synthetic_provider()
        phone = '+12025550106'
        before = len(self.outbound(phone))
        self.provider_time = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        status, _, _ = self.signed_webhook('/webhooks/twilio/sms', {
            'From': phone, 'MessageSid': 'SM' + '3' * 32, 'Body': 'STOP', 'OptOutType': 'STOP'})
        self.assertEqual(status, 200)
        self.assertEqual(self.contact(phone)['consent_status'], 'revoked')
        self.assertEqual(len(self.outbound(phone)), before)
        status, _, _ = self.signed_webhook('/webhooks/twilio/sms', {
            'From': phone, 'MessageSid': 'SM' + '4' * 32, 'Body': 'START', 'OptOutType': 'START'})
        self.assertEqual(status, 409)
        self.assertEqual(self.contact(phone)['consent_status'], 'revoked')
        self.request('POST', '/api/handoff', {'phone': phone, 'active': True})
        self.provider_time = datetime.now(timezone.utc).isoformat()
        status, _, _ = self.signed_webhook('/webhooks/twilio/sms', {
            'From': phone, 'MessageSid': 'SM' + '5' * 32, 'Body': 'START', 'OptOutType': 'START'})
        self.assertEqual(status, 200)
        self.assertEqual(self.contact(phone)['consent_status'], 'active')
        self.assertTrue(self.contact(phone)['handoff'])
        self.assertEqual(len(self.outbound(phone)), before)
        status, _, _ = self.signed_webhook('/webhooks/twilio/dial-result', {
            'From': phone, 'CallSid': 'CA' + '4' * 32, 'DialCallSid': 'CA' + '5' * 32,
            'DialCallStatus': 'no-answer'})
        self.assertEqual(status, 200)
        self.assertEqual(len(self.outbound(phone)), before)

    @unittest.skipUnless(RequestValidator, 'Install requirements-twilio.txt for signed HTTP webhook tests')
    def test_answered_dial_does_not_trigger_missed_followup_but_no_answer_does(self):
        self.install_synthetic_provider()
        phone = '+12025550106'
        before = len(self.outbound(phone))
        status, _, _ = self.signed_webhook('/webhooks/twilio/dial-result', {
            'From': phone, 'CallSid': 'CA' + '6' * 32, 'DialCallSid': 'CA' + '7' * 32,
            'DialCallStatus': 'completed'})
        self.assertEqual(status, 200)
        self.assertEqual(len(self.outbound(phone)), before)
        self.provider_time = datetime.now(timezone.utc).isoformat()
        status, _, _ = self.signed_webhook('/webhooks/twilio/dial-result', {
            'From': phone, 'CallSid': 'CA' + '8' * 32, 'DialCallSid': 'CA' + '9' * 32,
            'DialCallStatus': 'no-answer'})
        self.assertEqual(status, 200)
        self.assertEqual(len(self.outbound(phone)), before + 1)

    @unittest.skipUnless(RequestValidator, 'Install requirements-twilio.txt for signed HTTP webhook tests')
    def test_incorrect_provider_signature_rejected_without_state_change(self):
        self.install_synthetic_provider()
        before = self.state()
        status, _, _ = self.signed_webhook('/webhooks/twilio/sms', {
            'From': '+12025550107', 'MessageSid': 'SM' + '6' * 32, 'Body': 'START'}, 'bad-signature')
        self.assertEqual(status, 403)
        self.assertEqual(self.state(), before)

    def test_errors_and_frontend_never_expose_configured_secrets(self):
        secret = 'synthetic-secret-must-not-appear-42'
        with patch.dict(os.environ, {'TWILIO_AUTH_TOKEN': secret, 'MODEL_API_KEY': secret}):
            self.server.adapter = TwilioAdapter()
            for path in ['/api/state', '/api/session', '/', '/app.js', '/styles.css']:
                with self.subTest(path=path):
                    status, _, result = self.request('GET', path)
                    self.assertEqual(status, 200)
                    self.assertNotIn(secret, json.dumps(result))
            status, _, error = self.request('POST', '/api/simulate', {'kind': 'bad', 'phone': '+12025550101'})
            self.assertEqual(status, 400)
            self.assertNotIn(secret, json.dumps(error))

    def test_simulated_workflow_never_connects_to_external_transport(self):
        connections = []
        original_connect = socket.socket.connect
        def loopback_only(sock, address):
            connections.append(address)
            if not isinstance(address, tuple) or address[0] not in {'127.0.0.1', '::1'}:
                raise AssertionError('Simulated workflow tried an external network connection')
            return original_connect(sock, address)
        phone = '+12025550106'
        actions = [
            ('/api/simulate', {'event_id': 'transport-faq', 'kind': 'sms', 'phone': phone, 'text': 'What are your hours?'}),
            ('/api/simulate', {'event_id': 'transport-call', 'kind': 'missed_call', 'phone': phone, 'dial_status': 'no-answer'}),
            ('/api/simulate', {'event_id': 'transport-stop', 'kind': 'sms', 'phone': phone, 'text': 'STOP'}),
            ('/api/simulate', {'event_id': 'transport-start', 'kind': 'sms', 'phone': phone, 'text': 'START'}),
            ('/api/handoff', {'phone': phone, 'active': True}),
            ('/api/human-reply', {'phone': phone, 'text': 'The owner will review your request.'}),
        ]
        with patch.object(socket.socket, 'connect', loopback_only):
            for path, payload in actions:
                with self.subTest(path=path, event=payload.get('event_id')):
                    status, _, result = self.request('POST', path, payload)
                    self.assertEqual(status, 200, result)
            state = self.state()
        self.assertGreaterEqual(len(connections), len(actions))
        self.assertTrue(all(addr[0] in {'127.0.0.1', '::1'} for addr in connections))
        messages = [m for c in state['conversations'] for m in c['messages'] if m['direction'] == 'outbound']
        self.assertTrue(all(m['transport'] == 'dry_run' and m['status'] in {'simulated', 'failed'} for m in messages))


class ProcessSafetyTests(unittest.TestCase):
    def test_normal_startup_really_binds_loopback_and_partial_qa_flags_do_not_open_it(self):
        cases = [{}, {'DEMO_PREVIEW_HOST': 'terminal.local'}, {'DEMO_INTERNAL_QA': '1'},
                 {'DEMO_INTERNAL_QA': '0', 'DEMO_PREVIEW_HOST': 'terminal.local'}]
        for environment in cases:
            with self.subTest(environment=environment):
                servers = []
                def actual_server(address, engine, **kwargs):
                    server = DemoServer(address, engine, **kwargs)
                    servers.append(server)
                    return server
                try:
                    with patch.dict(os.environ, environment, clear=True), \
                         patch.object(sys, 'argv', ['app.py', '--port', '0', '--db', ':memory:']), \
                         patch.object(app_module, 'DemoServer', side_effect=actual_server), \
                         patch.object(DemoServer, 'serve_forever', side_effect=KeyboardInterrupt), \
                         redirect_stdout(io.StringIO()):
                        app_module.main()
                    self.assertEqual(len(servers), 1)
                    self.assertEqual(servers[0].server_address[0], '127.0.0.1')
                    self.assertNotIn(f'terminal.local:{servers[0].server_port}', servers[0].allowed_hosts)
                finally:
                    for server in servers:
                        server.server_close()
                        server.engine.close()

    def test_real_send_environment_flag_refuses_startup(self):
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run([sys.executable, str(root / 'app.py'), '--port', '0', '--db', ':memory:'],
                                cwd=str(root), env={**os.environ, 'ENABLE_REAL_SEND': 'true'},
                                capture_output=True, text=True, timeout=5)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Real sending is unavailable', result.stderr)
        self.assertNotIn('real outbound disabled', result.stdout)


if __name__ == '__main__':
    unittest.main()
