"""Actual demo HTTP lifecycle and Windows atomic manifest publication."""
from contextlib import closing
import http.client
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest

from app import DemoServer, _write_run_manifest
from reception.engine import Engine


class DemoLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)
        self.engine = Engine(self.path / 'synthetic.sqlite3')
        self.server = DemoServer(('127.0.0.1', 0), self.engine)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.begin_close()
        self.server.shutdown()
        self.server.server_close()
        self.assertTrue(self.server.drain(3))
        self.thread.join(3)
        self.engine.close()
        self.temp.cleanup()

    def request(self, method, path, body=None, token=None):
        with closing(http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=4)) as client:
            headers = {'Content-Type': 'application/json'}
            if token is not None:
                headers['X-Demo-Token'] = token
            client.request(method, path, body=json.dumps(body).encode() if body is not None else None, headers=headers)
            response = client.getresponse()
            return response.status, json.loads(response.read())

    def test_unmanaged_demo_has_no_health_or_exit_and_bad_exit_token_does_not_stop(self):
        self.assertEqual(self.request('GET', '/api/health')[0], 404)
        self.assertEqual(self.request('POST', '/api/exit', {}, 'incorrect')[0], 403)
        self.assertEqual(self.request('POST', '/api/exit', {}, self.server.demo_token)[0], 404)
        self.assertEqual(self.request('GET', '/api/state')[0], 200)
        self.assertFalse(self.server.closing.is_set())

    def test_real_active_handler_must_finish_before_demo_drain_succeeds(self):
        results = []
        with self.server.domain_lock:
            request_thread = threading.Thread(target=lambda: results.append(self.request('GET', '/api/state')))
            request_thread.start()
            deadline = time.monotonic() + 2
            while not self.server.active_handlers and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertEqual(self.server.active_handlers, 1)
            self.assertFalse(self.server.drain(.02))
            self.assertEqual(self.engine.db.execute('SELECT 1').fetchone()[0], 1)
        request_thread.join(3)
        self.assertFalse(request_thread.is_alive())
        self.assertEqual(results[0][0], 200)
        self.assertTrue(self.server.drain(2))


@unittest.skipUnless(os.name == 'nt', 'Atomic Windows reader sharing requires real Windows')
class ManifestPublicationTests(unittest.TestCase):
    def test_real_reader_temporarily_denying_delete_does_not_break_atomic_publication(self):
        from reception.platform_fs import _api
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            _write_run_manifest(base, {'status': 'starting', 'synthetic_only': True})
            handle = _api().open(base / 'run-manifest.json', access=0x80000000, share=1)
            errors = []
            def publish():
                try:
                    _write_run_manifest(base, {'status': 'running', 'synthetic_only': True})
                except BaseException as error:
                    errors.append(error)
            writer = threading.Thread(target=publish)
            try:
                writer.start()
                time.sleep(.15)
                self.assertTrue(writer.is_alive())
                self.assertEqual(json.loads((base / 'run-manifest.json').read_text())['status'], 'starting')
            finally:
                _api().dll.CloseHandle(handle)
            writer.join(3)
            self.assertFalse(writer.is_alive())
            self.assertEqual(errors, [])
            self.assertEqual(json.loads((base / 'run-manifest.json').read_text())['status'], 'running')
