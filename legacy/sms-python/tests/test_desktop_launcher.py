"""Real Windows desktop process/lease acceptance; no live provider or kernel mocks."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import importlib.util
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import time
import unittest
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT / '.venv' / 'Scripts' / 'python.exe'
LAUNCHER = ROOT / 'scripts' / 'desktop_launcher.py'


def module():
    spec = importlib.util.spec_from_file_location('desktop_launcher_acceptance', LAUNCHER)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


@unittest.skipUnless(os.name == 'nt', 'Windows desktop process acceptance requires native Windows')
class DesktopLauncherTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        configured = os.environ.get('HARBOR_DESKTOP_EVIDENCE_DIRECTORY')
        base = Path(configured) if configured else ROOT / 'artifacts' / 'final-acceptance-runs'
        if not base.is_relative_to(ROOT / 'artifacts' / 'final-acceptance-runs'):
            raise ValueError('Desktop evidence must be isolated under final-acceptance-runs')
        cls.evidence = base / ('proc-' + secrets.token_hex(4))
        cls.evidence.mkdir(parents=True)
        cls.controller = module()

    def setUp(self):
        self.state = self.evidence / ('case-' + hashlib.sha256(self._testMethodName.encode()).hexdigest()[:6])
        self.owned = []

    def tearDown(self):
        for state, root in reversed(self.owned):
            result = self.invoke('stop', state=state, root=root, check=False)
            if result.returncode:
                self.fail('Normal cleanup failed; no force-kill was used: ' + result.stderr)

    def invoke(self, action, *, state=None, root=ROOT, check=True, port=0, extra=(), environment=None, cwd=None):
        state = state or self.state
        command = [str(root / '.venv' / 'Scripts' / 'python.exe'), '-B',
                   str(root / 'scripts' / 'desktop_launcher.py'), action,
                   '--state-directory', str(state), '--port', str(port), '--no-browser', *extra]
        env = self.controller.clean_environment()
        if environment:
            env.update(environment)
        result = subprocess.run(command, cwd=cwd or ROOT, env=env, capture_output=True,
                                encoding='utf-8', timeout=65, creationflags=subprocess.CREATE_NO_WINDOW)
        evidence = self.evidence / (self._testMethodName + '-' + action + '-' + secrets.token_hex(4) + '.json')
        evidence.write_text(json.dumps({'command': command, 'cwd': str(cwd or ROOT),
                            'exit_code': result.returncode, 'stdout': result.stdout,
                            'stderr': result.stderr}, ensure_ascii=False, indent=2), encoding='utf-8')
        if check:
            self.assertEqual(result.returncode, 0, result.stderr)
            value = json.loads(result.stdout)
            if action == 'launch' and (state, root) not in self.owned:
                self.owned.append((state, root))
            return value
        return result

    def stop(self, *, state=None, root=ROOT):
        state = state or self.state
        result = self.invoke('stop', state=state, root=root)
        self.owned.remove((state, root))
        return result

    def test_first_launch_reuse_normal_stop_restart(self):
        first = self.invoke('launch')
        self.assertEqual(first['status'], 'started')
        self.assertEqual(first['app_mode'], 'demo')
        self.assertFalse(first['real_outbound'])
        self.assertEqual(first['health']['python_prefix'], str(ROOT / '.venv'))
        self.assertEqual(first['app_identity']['parent_pid'], first['launcher_identity']['pid'])
        self.assertNotEqual(first['app_identity']['pid'], first['launcher_identity']['pid'])
        second = self.invoke('launch')
        self.assertEqual(second['status'], 'reused')
        self.assertEqual(first['run_id'], second['run_id'])
        self.assertEqual(first['app_identity'], second['app_identity'])
        self.assertEqual(len(list((self.state / 'sessions').iterdir())), 1)
        stopped = self.stop()
        self.assertEqual(stopped['instance_lock'], 'released')
        with self.controller.lock_database(first['database']):
            pass
        restarted = self.invoke('launch')
        self.assertNotEqual(first['run_id'], restarted['run_id'])
        self.assertNotEqual(first['database'], restarted['database'])
        self.assertTrue(Path(first['database']).is_file())
        self.stop()

    def test_concurrent_launch_reuses_one_real_instance(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(self.invoke, 'launch') for _ in range(2)]
            results = [future.result() for future in futures]
        self.assertEqual({value['status'] for value in results}, {'started', 'reused'})
        self.assertEqual(results[0]['run_id'], results[1]['run_id'])
        self.assertEqual(results[0]['app_identity'], results[1]['app_identity'])
        self.assertEqual(len(list((self.state / 'sessions').iterdir())), 1)
        self.stop()

    def test_occupied_port_is_not_adopted_or_killed(self):
        listener = socket.socket()
        listener.bind(('127.0.0.1', 0))
        listener.listen()
        try:
            port = listener.getsockname()[1]
            failed = self.invoke('launch', port=port, check=False)
            self.assertEqual(failed.returncode, 2)
            self.assertIn('occupied by another listener', failed.stderr)
            self.assertFalse((self.state / 'sessions').exists())
            self.assertEqual(listener.getsockname()[1], port)
            client = socket.create_connection(('127.0.0.1', port))
            accepted, _ = listener.accept()
            accepted.close()
            client.close()
        finally:
            listener.close()

    def test_any_working_directory_and_unicode_state(self):
        outside = self.evidence / 'another working folder'
        outside.mkdir()
        self.state = self.evidence / '路径 空格 demo'
        result = self.invoke('launch', cwd=outside)
        self.assertIn('路径 空格 demo', result['database'])
        self.assertEqual(result['health']['python_executable'], str(PYTHON))
        self.stop()

    def test_powershell_project_entry_from_another_folder(self):
        pwsh = shutil.which('pwsh')
        self.assertIsNotNone(pwsh, 'Existing PowerShell 7.5+ host is required; nothing is installed')
        outside = self.evidence / 'powershell working folder'
        outside.mkdir()
        command = [pwsh, '-NoLogo', '-NoProfile', '-File', str(ROOT / 'scripts' / 'desktop.ps1'),
                   '-Action', 'Launch', '-StateDirectory', str(self.state), '-Port', '0', '-NoBrowser', '-Quiet']
        process = subprocess.run(command, cwd=outside, capture_output=True, encoding='utf-8', timeout=65,
                                 creationflags=subprocess.CREATE_NO_WINDOW)
        self.assertEqual(process.returncode, 0, process.stderr)
        result = json.loads(process.stdout)
        self.owned.append((self.state, ROOT))
        self.assertEqual(result['status'], 'started')
        self.assertEqual(result['health']['python_prefix'], str(ROOT / '.venv'))
        self.stop()

    def test_inherited_live_and_invalid_config_are_cleared(self):
        result = self.invoke('launch', environment={
            'APP_MODE': 'service', 'ENABLE_REAL_SEND': 'true',
            'TWILIO_ACCOUNT_SID': 'synthetic-do-not-contact', 'TWILIO_AUTH_TOKEN': 'synthetic-do-not-use',
            'TWILIO_PUBLIC_BASE_URL': 'not-an-origin', 'TWILIO_PHONE_NUMBER': 'invalid',
            'ADMIN_PASSWORD_HASH': 'invalid', 'RECIPIENT_POLICY': 'inbound_service',
            'MAX_MESSAGES_PER_DAY': 'invalid', 'DEMO_INTERNAL_QA': '1', 'DEMO_PREVIEW_HOST': 'terminal.local'})
        self.assertEqual(result['app_mode'], 'demo')
        self.assertFalse(result['real_outbound'])
        self.assertTrue(result['url'].startswith('http://127.0.0.1:'))
        self.stop()

    def test_invalid_configuration_fails_before_namespace_creation(self):
        process = self.invoke('launch', check=False, extra=('--port', '65536'))
        self.assertEqual(process.returncode, 2)
        self.assertIn('Port must be', process.stderr)
        self.assertFalse(self.state.exists())

    def test_unmarked_database_namespace_is_refused_without_reading_store(self):
        self.state.mkdir()
        unknown = self.state / 'synthetic-demo.sqlite3'
        content = b'unknown pre-existing file; must not be opened as SQLite'
        unknown.write_bytes(content)
        failed = self.invoke('launch', check=False)
        self.assertEqual(failed.returncode, 2)
        self.assertIn('unmarked desktop directory', failed.stderr)
        self.assertEqual(unknown.read_bytes(), content)
        self.assertFalse((self.state / 'sessions').exists())

    def test_wrong_pid_creation_time_cannot_stop_owned_app(self):
        first = self.invoke('launch')
        path = self.state / 'active-instance.json'
        original = self.controller.read_json(path)
        altered = original | {'app_identity': original['app_identity'] | {'creation_filetime': 1}}
        self.controller.write_json(path, altered, replace=True)
        try:
            refused = self.invoke('stop', check=False)
            self.assertEqual(refused.returncode, 2)
            self.assertIn('creation time', refused.stderr)
            self.assertFalse((Path(first['run_directory']) / 'stop-request.json').exists())
            self.assertEqual(self.controller.health_matches(first['health'])['run_id'], first['run_id'])
        finally:
            self.controller.write_json(path, original, replace=True)
        self.stop()

    def test_corrupt_manifest_cannot_select_non_loopback_endpoint(self):
        first = self.invoke('launch')
        path = Path(first['run_directory']) / 'run-manifest.json'
        original = self.controller.read_json(path)
        try:
            for changes in ({'url': 'https://external.invalid'}, {'port': '8765@external.invalid'}):
                with self.subTest(changes=changes):
                    self.controller.write_json(path, original | changes, replace=True)
                    refused = self.invoke('launch', check=False)
                    self.assertEqual(refused.returncode, 2)
                    self.assertIn('exact numeric loopback', refused.stderr)
                    self.assertEqual(self.controller.health_matches(first['health'])['run_id'], first['run_id'])
        finally:
            self.controller.write_json(path, original, replace=True)
        self.stop()

    def test_ui_exit_normally_releases_lease(self):
        result = self.invoke('launch')
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(result['url'] + '/api/session', timeout=3) as response:
            token = json.load(response)['token']
        request = urllib.request.Request(result['url'] + '/api/exit', data=b'{}', method='POST',
            headers={'Content-Type': 'application/json', 'X-Demo-Token': token, 'Origin': result['url']})
        with opener.open(request, timeout=3) as response:
            self.assertEqual(response.status, 200)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                self.controller.matching_process(result['app_identity'])
            except LookupError:
                break
            time.sleep(.1)
        else:
            self.fail('Demo UI normal exit timed out; no force kill was used')
        manifest = self.controller.read_json(Path(result['run_directory']) / 'run-manifest.json')
        self.assertEqual(manifest['status'], 'stopped')
        self.assertEqual(manifest['instance_lock'], 'released')
        with self.controller.lock_database(result['database']):
            pass
        self.assertEqual(self.stop()['status'], 'already_stopped')

    def test_missing_dependencies_print_guidance_without_installing(self):
        project = self.evidence / 'dependency probe project'
        (project / 'scripts').mkdir(parents=True)
        shutil.copy2(LAUNCHER, project / 'scripts' / 'desktop_launcher.py')
        shutil.copytree(ROOT / 'reception', project / 'reception', ignore=shutil.ignore_patterns('__pycache__'))
        shutil.copy2(ROOT / 'requirements-windows.lock.txt', project / 'requirements-windows.lock.txt')
        setup = subprocess.run([sys._base_executable, '-B', '-m', 'venv', '--without-pip', str(project / '.venv')],
                    env=self.controller.clean_environment(), capture_output=True, encoding='utf-8', timeout=60,
                    creationflags=subprocess.CREATE_NO_WINDOW)
        self.assertEqual(setup.returncode, 0, setup.stderr)
        failed = self.invoke('launch', root=project, state=project / 'runtime' / 'desktop-demo-v1', check=False)
        self.assertEqual(failed.returncode, 2)
        self.assertIn('twilio==9.11.2 (found missing)', failed.stderr)
        self.assertIn('-m pip install -r', failed.stderr)
        self.assertIn('Nothing was installed', failed.stderr)
        self.assertFalse((project / 'runtime').exists())

    def test_unicode_project_path_uses_its_own_copied_venv(self):
        project = self.evidence / '完整 项目 Unicode'
        (project / 'scripts').mkdir(parents=True)
        shutil.copy2(LAUNCHER, project / 'scripts' / 'desktop_launcher.py')
        shutil.copy2(ROOT / 'app.py', project / 'app.py')
        shutil.copy2(ROOT / 'requirements-windows.lock.txt', project / 'requirements-windows.lock.txt')
        for name in ('reception', 'web', 'data', 'migrations', '.venv'):
            # Preserve the real full installed SDK, including deep lazy modules,
            # without changing Windows' global long-path setting.
            shutil.copytree('\\\\?\\' + str(ROOT / name), '\\\\?\\' + str(project / name),
                            ignore=shutil.ignore_patterns('__pycache__'))
        state = project / 'runtime' / 'desktop-demo-v1'
        result = self.invoke('launch', root=project, state=state, cwd=self.evidence)
        self.assertEqual(result['health']['python_prefix'], str(project / '.venv'))
        self.assertEqual(result['health']['python_executable'], str(project / '.venv' / 'Scripts' / 'python.exe'))
        self.assertTrue(Path(result['database']).is_relative_to(project))
        self.stop(state=state, root=project)

    def test_post_start_artifact_collision_normally_stops_child(self):
        command = [str(PYTHON), '-B', str(LAUNCHER), 'launch', '--state-directory', str(self.state),
                   '--port', '0', '--no-browser']
        process = subprocess.Popen(command, cwd=ROOT, env=self.controller.clean_environment(),
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding='utf-8',
                    creationflags=subprocess.CREATE_NO_WINDOW)
        deadline = time.monotonic() + 30
        run = None
        while time.monotonic() < deadline and process.poll() is None:
            sessions = self.state / 'sessions'
            if sessions.exists():
                directories = list(sessions.iterdir())
                if directories:
                    run = directories[0]
                    try:
                        with (run / 'launcher-ready.json').open('x', encoding='utf-8') as fixture:
                            fixture.write('reserved collision fixture')
                        break
                    except (FileNotFoundError, FileExistsError):
                        pass
            time.sleep(.001)
        stdout, stderr = process.communicate(timeout=65)
        if process.returncode == 0:
            self.owned.append((self.state, ROOT))
        self.assertIsNotNone(run, stderr)
        self.assertEqual(process.returncode, 2, stdout + stderr)
        self.assertIn('launcher-ready.json', stderr)
        manifest = self.controller.read_json(run / 'run-manifest.json')
        self.assertEqual(manifest['status'], 'stopped')
        self.assertEqual(manifest['instance_lock'], 'released')
        with self.assertRaises(LookupError):
            self.controller.process_identity(manifest['pid'])
        with self.controller.lock_database(run / 'synthetic-demo.sqlite3'):
            pass
        self.assertFalse((self.state / 'active-instance.json').exists())
        (self.evidence / ('failure-cleanup-' + secrets.token_hex(4) + '.json')).write_text(
            json.dumps({'command': command, 'exit_code': process.returncode, 'stdout': stdout, 'stderr': stderr,
                        'run_manifest': manifest}, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    unittest.main(verbosity=2)
