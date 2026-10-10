"""Malformed input and explicit proxy/configuration trust; no provider calls."""
from dataclasses import replace
import unittest
from unittest.mock import patch

from reception.auth import AuthSessions, hash_password, login_peer, valid_password_hash, verify_password
from reception.config import ServiceConfig


class AuthSecurityTests(unittest.TestCase):
    PASSWORD = 'Synthetic secure admin password!'
    HASH = hash_password(PASSWORD, salt=b'0123456789abcdef')

    def test_hash_parser_rejects_unbounded_parameters_and_invalid_encodings_without_scrypt(self):
        parts = self.HASH.split('$')
        malformed = [None, 23, 'scrypt$bogus', self.HASH.replace('$16384$', '$1048576$'),
                     '$'.join(parts[:4] + ['', parts[5]]),
                     '$'.join(parts[:4] + ['é' * 24, parts[5]]),
                     '$'.join(parts[:4] + [parts[4], 'AAAA']),
                     self.HASH + '$extra', self.HASH + ' ', self.HASH.replace('==', '===')]
        with patch('reception.auth.hashlib.scrypt') as scrypt:
            for encoded in malformed:
                with self.subTest(encoded_type=type(encoded).__name__):
                    self.assertFalse(valid_password_hash(encoded))
                    self.assertFalse(verify_password(self.PASSWORD, encoded))
            scrypt.assert_not_called()
        self.assertTrue(valid_password_hash(self.HASH))
        self.assertTrue(verify_password(self.PASSWORD, self.HASH))
        self.assertFalse(verify_password(self.PASSWORD + 'wrong', self.HASH))

    def test_malformed_or_duplicate_cookies_do_not_raise_or_authenticate(self):
        auth = AuthSessions(self.HASH)
        (sid, _), _ = auth.login(self.PASSWORD, '192.0.2.1')
        valid = 'reception_session=' + sid
        for cookie in [None, 4, '', '[bad]=x', 'reception_session="unterminated',
                       valid + '; ' + valid, valid + '\r\nother=x', valid + '; illegal[=x',
                       'reception_session=' + 'x' * 9000]:
            with self.subTest(cookie_type=type(cookie).__name__):
                self.assertIsNone(auth.get(cookie))
        self.assertIsNotNone(auth.get(valid))

    def test_untrusted_forwarding_cannot_change_login_bucket(self):
        self.assertEqual(login_peer('127.0.0.1', ['192.0.2.1']), '127.0.0.1')
        self.assertEqual(login_peer('192.0.2.9', ['garbage'], ('127.0.0.1/32',)), '192.0.2.9')

    def test_login_prunes_expired_peer_records_and_keeps_current_new_failure(self):
        auth = AuthSessions(self.HASH)
        auth.failures = {'expired-peer': [600, 700], 'empty-peer': [],
                         'mixed-peer': [650, 998], 'recent-peer': [999]}
        with patch('reception.auth.time.time', return_value=1000):
            login, error = auth.login('wrong', '192.0.2.30')
        self.assertIsNone(login)
        self.assertEqual(error, 'invalid_credentials')
        self.assertEqual(auth.failures, {'mixed-peer': [998], 'recent-peer': [999], '192.0.2.30': [1000]})

    def test_trusted_single_hop_accepts_one_ip_and_rejects_chains_duplicate_or_missing_headers(self):
        trusted = ('127.0.0.1/32', '::1/128')
        self.assertEqual(login_peer('127.0.0.1', ['192.0.2.1'], trusted), '192.0.2.1')
        self.assertEqual(login_peer('::1', ['2001:db8::1'], trusted), '2001:db8::1')
        for values in [[], ['invalid'], ['192.0.2.1, 192.0.2.2'], ['192.0.2.1', '192.0.2.2'],
                       [' 192.0.2.1'], ['[2001:db8::1]'], ['192.0.2.1:80'], ['fe80::1%eth0']]:
            with self.subTest(values=values), self.assertRaises(ValueError):
                login_peer('127.0.0.1', values, trusted)

    def config(self, **updates):
        return replace(ServiceConfig(mode='service', admin_password_hash=self.HASH), **updates)

    def test_service_rejects_invalid_hash_and_proxy_networks_before_starting(self):
        for encoded in ['scrypt$broken', None, self.HASH.replace('$8$', '$999$')]:
            with self.subTest(encoded=encoded), self.assertRaises(ValueError):
                self.config(admin_password_hash=encoded).validate()
        for networks in [('127.0.0.1',), ('127.0.0.1/24',), ('garbage/32',), ['127.0.0.1/32']]:
            with self.subTest(networks=networks), self.assertRaises(ValueError):
                self.config(trusted_proxy_cidrs=networks).validate()

    def test_formal_activation_has_independent_approval_and_test_mode_still_requires_allowlist(self):
        live = self.config(live_enabled=True, privacy_hmac_key='synthetic-' + 'x' * 32,
            account_sid='AC' + 'a' * 32, auth_token='synthetic', phone_number='+12025550100',
            public_base_url='https://synthetic.example.test', admin_origin='https://synthetic.example.test',
            optout_reviewed=True, readiness_approved=True, send_approval_recorded=True)
        with self.assertRaises(ValueError):
            live.validate()
        with self.assertRaises(ValueError):
            replace(live, recipient_policy='inbound_service').validate()
        formal = replace(live, recipient_policy='inbound_service',
                         send_approval_recorded=False, inbound_service_approval_recorded=True).validate()
        self.assertEqual(formal.send_policy().recipient_policy, 'inbound_service')
        self.assertTrue(formal.send_policy().inbound_service_approval_recorded)
        test = replace(live, allowed_recipients=('+12025550101',)).validate()
        self.assertEqual(test.send_policy().recipient_policy, 'test_allowlist')


if __name__ == '__main__':
    unittest.main()
