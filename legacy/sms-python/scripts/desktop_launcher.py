"""Owned, loopback-only desktop demo. This entry never installs dependencies."""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
from contextlib import contextmanager
from datetime import datetime, timezone
import importlib.metadata
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from reception.platform_fs import canonical_path, lock_database, validate_creation_path

VENV = ROOT / '.venv'
PYTHON = VENV / 'Scripts' / 'python.exe'
STATE = ROOT / 'runtime' / 'desktop-demo-v1'
NAMESPACE = 'sms-reception-assistant-desktop-synthetic-v1'
MANIFEST_FIELDS = ('run_id', 'pid', 'process_started_at', 'python_executable',
                   'python_prefix', 'base_prefix', 'db_path', 'port', 'app_mode', 'real_outbound')


def utc():
    return datetime.now(timezone.utc).isoformat()


def same_path(first, second):
    return os.path.normcase(os.path.abspath(first)) == os.path.normcase(os.path.abspath(second))


def read_json(path):
    canonical_path(path)
    # App manifests are atomically replaced. Python's ordinary Windows open
    # does not share deletion and can cause the writer's ReplaceFile to fail.
    # Keep a complete snapshot while explicitly sharing read/write/delete.
    for attempt in range(20):
        try:
            if os.name == 'nt':
                import msvcrt
                kernel = ctypes.WinDLL('kernel32', use_last_error=True)
                kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                              ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
                kernel.CreateFileW.restype = wintypes.HANDLE
                kernel.CloseHandle.argtypes = [wintypes.HANDLE]
                native = kernel.CreateFileW(str(path), 0x80000000, 7, None, 3, 0x00200080, None)
                if native == ctypes.c_void_p(-1).value:
                    raise ctypes.WinError(ctypes.get_last_error())
                try:
                    descriptor = msvcrt.open_osfhandle(native, os.O_RDONLY | os.O_BINARY)
                except BaseException:
                    kernel.CloseHandle(native)
                    raise
                stream = os.fdopen(descriptor, 'r', encoding='utf-8')
            else:
                stream = Path(path).open(encoding='utf-8')
            with stream as handle:
                return json.load(handle)
        except PermissionError:
            if attempt == 19:
                raise
            time.sleep(.025)


def write_json(path, value, *, replace=False):
    path = Path(path)
    validate_creation_path(path)
    temporary = path.with_name('.' + path.name + '-' + secrets.token_hex(8)) if replace else path
    try:
        with temporary.open('x', encoding='utf-8') as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write('\n')
            handle.flush()
            os.fsync(handle.fileno())
        if replace:
            os.replace(temporary, path)
    finally:
        if replace:
            temporary.unlink(missing_ok=True)


def clean_environment():
    environment = os.environ.copy()
    config_names = {'APP_MODE', 'ENABLE_REAL_SEND', 'ADMIN_PASSWORD_HASH', 'PRIVACY_HMAC_KEY',
                    'ADMIN_ORIGIN', 'TRUSTED_PROXY_CIDRS', 'RECIPIENT_POLICY',
                    'INBOUND_SERVICE_APPROVAL_RECORDED', 'CLIENT_READINESS_APPROVED',
                    'TEST_SEND_APPROVAL_RECORDED', 'TEST_RECIPIENT_ALLOWLIST', 'EMERGENCY_STOP_FILE',
                    'MAX_MESSAGES_PER_MINUTE', 'MAX_MESSAGES_PER_DAY', 'MAX_SEGMENTS_PER_MESSAGE',
                    'MAX_DAILY_SEGMENTS', 'REPLY_WINDOW_HOURS', 'REQUEST_TIMEOUT_SECONDS',
                    'SESSION_TTL_SECONDS', 'RETENTION_DAYS', 'INBOUND_MAX_AGE_SECONDS'}
    for name in tuple(environment):
        upper = name.upper()
        if upper.startswith(('TWILIO_', 'DEMO_', 'PYTHON', 'PIP_')) or upper in config_names:
            del environment[name]
    environment.update(APP_MODE='demo', ENABLE_REAL_SEND='false', PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1')
    return environment


def preflight():
    if os.name != 'nt':
        raise ValueError('The desktop shortcut entry requires Windows; existing Linux entries remain available.')
    if not same_path(sys.prefix, VENV) or not same_path(sys.executable, PYTHON) or sys.prefix == sys.base_prefix:
        raise ValueError('Use the project .venv\\Scripts\\python.exe for this desktop entry.')
    missing = []
    for line in (ROOT / 'requirements-windows.lock.txt').read_text(encoding='utf-8').splitlines():
        if not line or line.startswith('#'):
            continue
        name, version = line.strip().split('==', 1)
        try:
            installed = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            installed = 'missing'
        if installed != version:
            missing.append(f'{name}=={version} (found {installed})')
    if missing:
        raise ValueError('Declared project dependency missing or different: ' + ', '.join(missing) +
                         '. Nothing was installed. To install the declared packages yourself, run: & "' +
                         str(PYTHON) + '" -m pip install -r "' + str(ROOT / 'requirements-windows.lock.txt') + '"')
    # Exercise the actual declared SDK import rather than metadata alone.
    try:
        from twilio.rest import Client
        from twilio.request_validator import RequestValidator
        del Client, RequestValidator
    except ImportError:
        raise ValueError('The declared Twilio dependency could not import. Nothing was installed. Run the project .venv Python with -m pip install -r requirements-windows.lock.txt.') from None


def process_identity(pid, process_handle=None):
    """PID plus OS creation time and parent identity, including venv redirectors."""
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    handle = process_handle or kernel.OpenProcess(0x1000, False, int(pid))
    if not handle:
        if ctypes.get_last_error() == 87:
            raise LookupError('Owned process is no longer present.')
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        code = wintypes.DWORD()
        if not kernel.GetExitCodeProcess(handle, ctypes.byref(code)):
            raise ctypes.WinError(ctypes.get_last_error())
        if code.value != 259:
            raise LookupError('Owned process has exited.')
        creation, exit_time, system, user = (wintypes.FILETIME() for _ in range(4))
        if not kernel.GetProcessTimes(handle, *map(ctypes.byref, (creation, exit_time, system, user))):
            raise ctypes.WinError(ctypes.get_last_error())
        buffer, size = ctypes.create_unicode_buffer(32768), wintypes.DWORD(32768)
        if not kernel.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            raise ctypes.WinError(ctypes.get_last_error())
        class BasicInformation(ctypes.Structure):
            _fields_ = [('reserved1', ctypes.c_void_p), ('peb', ctypes.c_void_p),
                        ('reserved2', ctypes.c_void_p * 2), ('pid', ctypes.c_size_t), ('parent_pid', ctypes.c_size_t)]
        basic = BasicInformation()
        query = ctypes.WinDLL('ntdll').NtQueryInformationProcess
        query.argtypes = [wintypes.HANDLE, wintypes.ULONG, ctypes.c_void_p, wintypes.ULONG, ctypes.c_void_p]
        query.restype = wintypes.LONG
        if query(handle, 0, ctypes.byref(basic), ctypes.sizeof(basic), None) != 0:
            raise ValueError('Cannot establish the app process parent identity.')
        return {'pid': int(pid), 'creation_filetime': (creation.dwHighDateTime << 32) | creation.dwLowDateTime,
                'parent_pid': int(basic.parent_pid), 'os_executable': buffer.value}
    finally:
        if process_handle is None:
            kernel.CloseHandle(handle)


def matching_process(expected):
    actual = process_identity(expected['pid'])
    if (actual['creation_filetime'] != expected['creation_filetime'] or
            not same_path(actual['os_executable'], expected['os_executable'])):
        raise ValueError('Owned PID creation time or executable changed; refusing to control a reused PID.')
    return actual


def resolve_state(value):
    raw = Path(value) if value else STATE
    if not raw.is_absolute():
        raw = ROOT / raw
    state = validate_creation_path(raw, directory=True)
    evidence = ROOT / 'artifacts' / 'final-acceptance-runs'
    if not same_path(state, STATE) and not state.is_relative_to(evidence):
        raise ValueError('Desktop state must use runtime/desktop-demo-v1 or an isolated directory under artifacts/final-acceptance-runs.')
    state.mkdir(parents=True, exist_ok=True)
    return state


@contextmanager
def coordinated(state, timeout):
    deadline = time.monotonic() + timeout
    while True:
        lease = lock_database(state / 'controller-ownership')
        try:
            lease.__enter__()
            break
        except RuntimeError:
            if time.monotonic() >= deadline:
                raise ValueError('Another desktop launch or exit is still finishing. Try again shortly.') from None
            time.sleep(.1)
    try:
        marker = state / 'namespace.json'
        if marker.exists():
            if read_json(marker) != {'namespace': NAMESPACE, 'synthetic_only': True}:
                raise ValueError('Desktop namespace marker is invalid; no database was opened.')
        else:
            unexpected = [path.name for path in state.iterdir() if path.name != 'controller-ownership.service.lock']
            if unexpected:
                raise ValueError('Existing unmarked desktop directory was refused; no database was opened.')
            write_json(marker, {'namespace': NAMESPACE, 'synthetic_only': True})
        yield
    finally:
        lease.__exit__(None, None, None)


def validated_manifest(state, active):
    run = validate_creation_path(active['run_directory'], directory=True)
    if run.parent != state / 'sessions':
        raise ValueError('Recorded run is outside this synthetic desktop namespace.')
    manifest = read_json(run / 'run-manifest.json')
    if (type(manifest['port']) is not int or not 1 <= manifest['port'] <= 65535 or
            manifest['url'] != f"http://127.0.0.1:{manifest['port']}"):
        raise ValueError('Recorded desktop endpoint must be the exact numeric loopback URL; no browser was opened.')
    if (manifest['run_id'] != active['run_id'] or manifest['pid'] != active['app_identity']['pid'] or
            manifest['app_mode'] != 'demo' or manifest['real_outbound'] is not False or
            not same_path(manifest['python_executable'], PYTHON) or
            not same_path(manifest['python_prefix'], VENV) or
            not same_path(manifest['db_path'], run / 'synthetic-demo.sqlite3')):
        raise ValueError('Recorded application identity is inconsistent; no process or database was changed.')
    return run, manifest


def health_matches(manifest):
    # Ignore proxy environment: ownership probes never leave loopback.
    if type(manifest['port']) is not int or not 1 <= manifest['port'] <= 65535:
        raise ValueError('Desktop health probes require a numeric loopback port.')
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(f"http://127.0.0.1:{manifest['port']}/api/health", timeout=2) as response:
        health = json.load(response)
    if health.get('status') != 'ok' or any(health.get(field) != manifest[field] for field in MANIFEST_FIELDS):
        raise ValueError('The loopback listener did not prove the expected app identity.')
    return health


def existing_instance(state, action):
    active_path = state / 'active-instance.json'
    if not active_path.exists():
        return None
    active = read_json(active_path)
    run, manifest = validated_manifest(state, active)
    try:
        matching_process(active['app_identity'])
    except LookupError:
        if action == 'stop' and (manifest['status'] != 'stopped' or manifest['instance_lock'] != 'released'):
            raise ValueError('The previous process exited without a recorded normal drain; inspect its session logs.')
        return None
    if manifest['status'] != 'running':
        raise ValueError('The owned app is finishing startup or exit. Try again shortly.')
    health_matches(manifest)
    return active, run, manifest


def request_stop(active, run, manifest, timeout):
    matching_process(active['app_identity'])
    request_path = run / 'stop-request.json'
    request = {'run_id': manifest['run_id'], 'pid': manifest['pid']}
    if request_path.exists():
        if read_json(request_path) != request:
            raise ValueError('An unrelated stop request was refused.')
    else:
        write_json(request_path, request)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            matching_process(active['app_identity'])
        except LookupError:
            final = read_json(run / 'run-manifest.json')
            if final['status'] != 'stopped' or final['instance_lock'] != 'released':
                raise ValueError('App exited without completed normal drain and lock release; inspect the session logs.')
            # Test the same real kernel lease after shutdown, not a PID-only claim.
            with lock_database(run / 'synthetic-demo.sqlite3'):
                pass
            return {'status': 'stopped', 'run_id': manifest['run_id'], 'run_directory': str(run),
                    'app_identity': active['app_identity'], 'instance_lock': 'released'}
        time.sleep(.1)
    raise ValueError('Normal exit timed out. No process was killed; inspect the owned session logs.')


def cleanup_failed_launch(process, run, timeout):
    """Use the original process handle and app nonce; never terminate a process."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return
        manifest_path = run / 'run-manifest.json'
        if manifest_path.exists():
            manifest = read_json(manifest_path)
            redirector = process_identity(process.pid, int(process._handle))
            try:
                app = process_identity(manifest['pid'])
            except LookupError:
                # The real child can finish just before its redirector exits.
                time.sleep(.05)
                continue
            if ((app['pid'] != redirector['pid'] and app['parent_pid'] != redirector['pid']) or
                    manifest['app_mode'] != 'demo' or manifest['real_outbound'] is not False or
                    not same_path(manifest['python_executable'], PYTHON) or
                    not same_path(manifest['python_prefix'], VENV) or
                    (manifest['db_path'] and not same_path(manifest['db_path'], run / 'synthetic-demo.sqlite3'))):
                raise ValueError('Failure cleanup could not prove the started app identity; no stop request was written.')
            request_path = run / 'stop-request.json'
            request = {'run_id': manifest['run_id'], 'pid': manifest['pid']}
            matching_process(app)
            if request_path.exists():
                if read_json(request_path) != request:
                    raise ValueError('Failure cleanup found an unrelated stop request; no process was changed.')
            else:
                write_json(request_path, request)
            break
        time.sleep(.05)
    while time.monotonic() < deadline:
        if process.poll() is not None:
            final = read_json(run / 'run-manifest.json')
            if final['status'] not in ('stopped', 'failed') or final['instance_lock'] not in ('released', 'not_acquired'):
                raise ValueError('Started app exited without a completed lifecycle record; inspect its session logs.')
            with lock_database(run / 'synthetic-demo.sqlite3'):
                pass
            return
        time.sleep(.05)
    raise ValueError('Normal startup-failure cleanup timed out. No process was killed. Inspect: ' + str(run))


def launch(state, port, timeout):
    if port:
        probe = socket.socket()
        try:
            probe.bind(('127.0.0.1', port))
        except OSError:
            raise ValueError(f'Loopback port {port} is occupied by another listener. No process was stopped. Close that listener yourself or choose another port.') from None
        finally:
            probe.close()
    sessions = validate_creation_path(state / 'sessions', directory=True)
    sessions.mkdir(exist_ok=True)
    run = sessions / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '-' + secrets.token_hex(8))
    run.mkdir()
    database = run / 'synthetic-demo.sqlite3'
    # This UUID directory was created exclusively here; no existing database is read.
    arguments = [str(PYTHON), '-B', str(ROOT / 'app.py'), '--db', str(database), '--port', str(port), '--run-dir', str(run)]
    with (run / 'application-stdout.log').open('xb') as output, (run / 'application-stderr.log').open('xb') as error:
        process = subprocess.Popen(arguments, cwd=ROOT, env=clean_environment(), stdin=subprocess.DEVNULL,
                                   stdout=output, stderr=error, creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        redirector = process_identity(process.pid, int(process._handle))
        write_json(run / 'launch-request.json', {'arguments': arguments, 'launcher_identity': redirector,
                   'requested_venv_executable': str(PYTHON), 'app_mode': 'demo', 'real_outbound': False,
                   'synthetic_only': True, 'started_at': utc()})
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            manifest_path = run / 'run-manifest.json'
            if manifest_path.exists():
                manifest = read_json(manifest_path)
                if manifest['status'] == 'failed':
                    break
                if manifest['status'] == 'running':
                    app = process_identity(manifest['pid'])
                    if app['pid'] != redirector['pid'] and app['parent_pid'] != redirector['pid']:
                        raise ValueError('App is not the process started by this launcher or its venv redirector child.')
                    active = {'run_id': manifest['run_id'], 'run_directory': str(run), 'app_identity': app,
                              'launcher_identity': redirector, 'synthetic_only': True}
                    validated_manifest(state, active)
                    try:
                        health_matches(manifest)
                    except (OSError, ValueError):
                        time.sleep(.1)
                        continue
                    write_json(run / 'launcher-ready.json', active | {'health': {key: manifest[key] for key in MANIFEST_FIELDS}})
                    write_json(state / 'active-instance.json', active, replace=True)
                    return active, run, manifest
            if process.poll() is not None:
                break
            time.sleep(.1)
        raise ValueError('The desktop demo did not become ready. No process was killed. Inspect: ' + str(run / 'application-stderr.log'))
    except BaseException:
        cleanup_failed_launch(process, run, timeout)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('launch', 'stop', 'status'))
    parser.add_argument('--state-directory')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--timeout', type=int, default=30)
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()
    if not 0 <= args.port <= 65535 or not 1 <= args.timeout <= 120:
        parser.error('Port must be 0..65535 and timeout 1..120 seconds. No app was started.')
    try:
        preflight()
        state = resolve_state(args.state_directory)
        with coordinated(state, args.timeout):
            instance = existing_instance(state, args.action)
            if args.action == 'launch':
                reused = instance is not None
                active, run, manifest = instance or launch(state, args.port, args.timeout)
                result = {'status': 'reused' if reused else 'started', 'url': f"http://127.0.0.1:{manifest['port']}",
                          'run_directory': str(run), 'run_id': manifest['run_id'],
                          'app_identity': active['app_identity'], 'launcher_identity': active['launcher_identity'],
                          'app_mode': 'demo', 'real_outbound': False, 'synthetic_only': True,
                          'database': manifest['db_path'], 'health': {key: manifest[key] for key in MANIFEST_FIELDS}}
            elif args.action == 'stop':
                result = request_stop(*instance, args.timeout) if instance else {'status': 'already_stopped'}
            else:
                result = {'status': 'running', 'run_id': instance[2]['run_id'], 'url': instance[2]['url']} if instance else {'status': 'stopped'}
        if args.action == 'launch' and not args.no_browser:
            os.startfile(result['url'])
        print(json.dumps(result, ensure_ascii=False), flush=True)
        return 0
    except (OSError, ValueError, RuntimeError, LookupError, TypeError) as exc:
        print('SMS Reception Assistant: ' + str(exc), file=sys.stderr, flush=True)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
