#!/usr/bin/env python3
"""Run the local demo with `python app.py`. No dependency installation needed."""
import argparse
import json
import mimetypes
import os
from pathlib import Path
import secrets
import sys
import threading
import time
import contextlib
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from reception.engine import Engine
from reception.twilio_adapter import IntakeError, TwilioAdapter

ROOT = Path(__file__).resolve().parent


class DemoServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, engine, adapter=None, host_alias=None):
        super().__init__(address, DemoHandler)
        self.engine = engine
        self.adapter = adapter or TwilioAdapter()
        self.demo_token = secrets.token_urlsafe(32)
        self.domain_lock = threading.RLock()
        self.lifecycle = None
        self.closing = threading.Event()
        self.handlers = threading.Condition()
        self.active_handlers = 0
        self.capacity = threading.BoundedSemaphore(32)
        port = self.server_port
        self.allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        if host_alias:
            self.allowed_hosts.add(f"{host_alias}:{port}")

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(10)
        return connection, address

    def process_request(self, request, client_address):
        with self.handlers:
            if self.closing.is_set() or not self.capacity.acquire(blocking=False):
                self.shutdown_request(request)
                return
            self.active_handlers += 1
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.capacity.release()
            with self.handlers:
                self.active_handlers -= 1
                self.handlers.notify_all()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.capacity.release()
            with self.handlers:
                self.active_handlers -= 1
                self.handlers.notify_all()

    def begin_close(self):
        self.closing.set()
        self.engine.closing = True

    def drain(self, timeout=20):
        self.begin_close()
        deadline = time.monotonic() + max(0, timeout)
        with self.handlers:
            while self.active_handlers:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self.handlers.wait(remaining)
        return True

    def handle_error(self, request, client_address):
        # A malformed local request must not emit phone/body/query tracebacks.
        pass


class DemoHandler(BaseHTTPRequestHandler):
    server_version = "ReceptionDemo/1.0"

    def log_message(self, *_):
        # No query strings, sender IDs, message bodies or credentials in logs.
        pass

    def send(self, status, value, content_type="application/json; charset=utf-8"):
        body = json.dumps(value).encode() if content_type.startswith("application/json") else value
        if isinstance(body, str):
            body = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        for name, value in getattr(self, 'response_headers', {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def local_guard(self, write=False):
        if self.headers.get("Host") not in self.server.allowed_hosts:
            self.send(403, {"error": "Local demo host is not allowed."})
            return False
        origin = self.headers.get("Origin")
        if origin and origin not in {"http://" + x for x in self.server.allowed_hosts}:
            self.send(403, {"error": "Cross-origin access is blocked."})
            return False
        if self.headers.get("Sec-Fetch-Site") == "cross-site":
            self.send(403, {"error": "Cross-site access is blocked."})
            return False
        if write and not secrets.compare_digest(self.headers.get("X-Demo-Token", ""), self.server.demo_token):
            self.send(403, {"error": "Demo session expired. Refresh the page."})
            return False
        return True

    def do_GET(self):
        if not self.local_guard():
            return
        path = urlsplit(self.path).path
        if path == "/api/health":
            if not self.server.lifecycle:
                return self.send(404, {"error": "Desktop lifecycle is unavailable."})
            fields = ('run_id', 'pid', 'process_started_at', 'python_executable',
                      'python_prefix', 'base_prefix', 'db_path', 'port', 'app_mode')
            return self.send(200, {**{name: self.server.lifecycle[name] for name in fields},
                                   "status": "ok", "real_outbound": False})
        if path == "/api/session":
            return self.send(200, {"token": self.server.demo_token})
        if path == "/api/state":
            with self.server.domain_lock:
                state = self.server.engine.snapshot()
            state["runtime"] = {"mode": "simulated", "model": "rules / mock adapter", "real_outbound": False,
                                "desktop_managed": bool(self.server.lifecycle)}
            return self.send(200, state)
        files = {"/": "index.html", "/index.html": "index.html", "/app.js": "app.js", "/styles.css": "styles.css"}
        if path not in files:
            return self.send(404, {"error": "Page not found."})
        file = ROOT / "web" / files[path]
        if not file.is_file():
            return self.send(503, {"error": "Demo interface is not ready."})
        return self.send(200, file.read_bytes(), mimetypes.guess_type(file.name)[0] + "; charset=utf-8")

    def read_body(self, mime):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise IntakeError(400, "invalid_content_length") from None
        if length < 1 or length > 32768:
            raise IntakeError(413, "request_size_out_of_range")
        if self.headers.get("Content-Type", "").split(";", 1)[0] != mime:
            raise IntakeError(415, "unsupported_content_type")
        try:
            return self.rfile.read(length).decode("utf-8", errors="strict")
        except UnicodeError:
            raise IntakeError(400, "invalid_utf8") from None

    def do_POST(self):
        path = urlsplit(self.path).path
        if path.startswith("/webhooks/twilio/"):
            return self.twilio_webhook()
        if not self.local_guard(write=True):
            return
        try:
            payload = json.loads(self.read_body("application/json"))
            if not isinstance(payload, dict):
                raise ValueError("Request must be a JSON object.")
            if path == "/api/exit":
                if not self.server.lifecycle:
                    return self.send(404, {"error": "Desktop lifecycle is unavailable."})
                if payload:
                    raise ValueError("Exit does not accept parameters.")
                self.send(200, {"status": "stopping", "real_outbound": False})
                def close_demo():
                    self.server.begin_close()
                    self.server.shutdown()
                threading.Thread(target=close_demo, name='demo-exit', daemon=True).start()
                return
            with self.server.domain_lock:
                engine = self.server.engine
                if path == "/api/simulate":
                    payload["origin"] = "simulation"
                    # Browser inputs cannot claim a provider-managed confirmation.
                    for key in ("verified", "provider_managed_optout", "opt_out_type", "suppress_confirmation"):
                        payload.pop(key, None)
                    result = engine.process_event(payload)
                elif path == "/api/handoff":
                    if type(payload.get("active")) is not bool:
                        raise ValueError("active must be true or false.")
                    result = engine.takeover(payload.get("phone", ""), payload["active"])
                elif path == "/api/human-reply":
                    result = engine.human_reply(payload.get("phone", ""), payload.get("text", ""))
                elif path == "/api/business":
                    result = engine.save_business(payload)
                elif path == "/api/reset":
                    result = engine.reset()
                else:
                    return self.send(404, {"error": "Operation not found."})
            return self.send(200, result)
        except IntakeError as exc:
            return self.send(exc.status, {"error": exc.code})
        except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            # Domain validation messages contain no secrets, unlike provider exceptions.
            return self.send(400, {"error": str(exc)[:180]})
        except Exception:
            return self.send(500, {"error": "Could not process this event. Automation has not been retried; check the audit view."})

    def twilio_webhook(self):
        try:
            body = self.read_body("application/x-www-form-urlencoded")
            multi = parse_qs(body, keep_blank_values=True, strict_parsing=True, max_num_fields=100)
            if any(len(values) != 1 for values in multi.values()):
                raise IntakeError(400, "duplicate_form_parameter")
            params = {key: values[0] for key, values in multi.items()}
            event = self.server.adapter.event(self.path, self.headers.get("X-Twilio-Signature", ""), params)
            with self.server.domain_lock:
                self.server.engine.process_event(event)
            # Empty TwiML deliberately cannot send an SMS or make a call.
            return self.send(200, "<?xml version=\"1.0\"?><Response/>", "application/xml; charset=utf-8")
        except IntakeError as exc:
            return self.send(exc.status, {"error": exc.code})
        except (ValueError, UnicodeError):
            return self.send(400, {"error": "invalid_webhook_body"})
        except Exception:
            return self.send(503, {"error": "webhook_processing_unavailable"})


class ServiceServer(ThreadingHTTPServer):
    """Single-process service behind an authorized HTTPS reverse proxy.

    Construction never starts a worker or invokes the provider. Tests can
    inspect the durable inbox and outbox before explicitly running a worker.
    """
    daemon_threads = True
    request_queue_size = 32

    def __init__(self, address, engine, config, provider=None, adapter=None, host_alias=None):
        from reception.auth import AuthSessions
        from reception.provider import ServiceWebhookAdapter
        from reception.service import ServiceInbox, ServiceWorker
        if not engine.production:
            raise ValueError('Service server requires a production-mode engine')
        config.validate()
        super().__init__(address, ServiceHandler)
        try:
            self._configure_service(engine, config, provider, adapter, host_alias)
        except BaseException:
            self.server_close()
            raise

    def _configure_service(self, engine, config, provider, adapter, host_alias):
        from reception.auth import AuthSessions
        from reception.provider import ServiceWebhookAdapter
        from reception.service import ServiceInbox, ServiceWorker
        self.engine,self.config,self.provider=engine,config,provider
        engine.contact_barrier_timeout_seconds = config.request_timeout_seconds
        self.closing = threading.Event()
        self.handlers = threading.Condition()
        self.active_handlers = 0
        self.auth=AuthSessions(config.admin_password_hash,config.session_ttl_seconds)
        self.adapter=adapter or ServiceWebhookAdapter(config)
        self.domain_lock=engine._lock
        self.inbox=ServiceInbox(engine,provider,config)
        self.worker=ServiceWorker(self.inbox,engine.outbox,provider)
        self.capacity=threading.BoundedSemaphore(32)
        self.allowed_hosts={f'127.0.0.1:{self.server_port}',f'localhost:{self.server_port}'}
        self.allowed_origins={'http://'+host for host in self.allowed_hosts}
        if host_alias:
            self.allowed_hosts.add(f'{host_alias}:{self.server_port}')
            self.allowed_origins.add(f'http://{host_alias}:{self.server_port}')
        if config.admin_origin:
            self.allowed_hosts.add(urlsplit(config.admin_origin).netloc)
            self.allowed_origins.add(config.admin_origin.rstrip('/'))

    def get_request(self):
        connection,address=super().get_request()
        connection.settimeout(10)
        return connection,address

    def process_request(self, request, client_address):
        with self.handlers:
            if self.closing.is_set() or not self.capacity.acquire(blocking=False):
                self.shutdown_request(request)
                return
            self.active_handlers += 1
        try: super().process_request(request,client_address)
        except Exception:
            self.capacity.release()
            with self.handlers:
                self.active_handlers -= 1
                self.handlers.notify_all()
            raise

    def process_request_thread(self,request,client_address):
        try: super().process_request_thread(request,client_address)
        finally:
            self.capacity.release()
            with self.handlers:
                self.active_handlers -= 1
                self.handlers.notify_all()

    def begin_close(self):
        self.closing.set()
        self.engine.closing = True
        self.worker.closing = True
        self.worker.stop_event.set()

    def drain(self, timeout=20):
        """False leaves the DB and instance lock owned by the caller."""
        self.begin_close()
        deadline = time.monotonic() + max(0, timeout)
        stopped = self.worker.stop(timeout=max(0, deadline - time.monotonic()))
        with self.handlers:
            while self.active_handlers:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self.handlers.wait(remaining)
        return stopped

    def handle_error(self,request,client_address):
        # Never emit request/exception details that might contain identifiers.
        try:
            with self.engine._lock,self.engine.db:
                self.engine._audit(None,None,'http_request_failed',{'code':'request_error'})
        except Exception: pass


class ServiceHandler(DemoHandler):
    server_version='HarborReception/2.0'

    def origin_guard(self):
        if self.headers.get('Host') not in self.server.allowed_hosts:
            self.send(403,{'error':'host_not_allowed'}); return False
        origin=self.headers.get('Origin')
        if origin and origin not in self.server.allowed_origins:
            self.send(403,{'error':'cross_origin_blocked'}); return False
        if self.headers.get('Sec-Fetch-Site')=='cross-site':
            self.send(403,{'error':'cross_site_blocked'}); return False
        return True

    def admin_guard(self,write=False):
        if not self.origin_guard(): return None
        cookies = self.headers.get_all('Cookie', [])
        session=self.server.auth.get(cookies[0] if len(cookies) == 1 else '')
        if not session:
            self.send(401,{'error':'operator_sign_in_required','mode':'service'}); return None
        if write and not secrets.compare_digest(self.headers.get('X-Demo-Token',''),session['token']):
            self.send(403,{'error':'csrf_token_invalid'}); return None
        return session

    def cookie(self,sid='',clear=False):
        secure=self.server.config.admin_origin.startswith('https://')
        value='reception_session='+sid+'; Path=/; HttpOnly; SameSite=Strict'
        value+='; Max-Age='+('0' if clear else str(self.server.config.session_ttl_seconds))
        if secure: value+='; Secure'
        self.response_headers={'Set-Cookie':value}

    def do_GET(self):
        if self.server.closing.is_set():
            return self.send(503, {'error': 'service_shutting_down'})
        path=urlsplit(self.path).path
        if path in {'/','/index.html','/app.js','/styles.css'}:
            if not self.origin_guard(): return
            names={'/':'index.html','/index.html':'index.html','/app.js':'app.js','/styles.css':'styles.css'}
            file=ROOT/'web'/names[path]
            return self.send(200,file.read_bytes(),mimetypes.guess_type(file.name)[0]+'; charset=utf-8')
        session=self.admin_guard()
        if not session: return
        if path=='/api/session':
            return self.send(200,{'token':session['token'],'authenticated':True,'mode':'service',
                                 'real_outbound':self.server.config.live_enabled})
        if path=='/api/state':
            with self.server.domain_lock:
                state=self.server.engine.snapshot()
                state['inbound_processing']=self.server.inbox.snapshot()
            phone=self.server.config.phone_number
            state['runtime']={'mode':'service','model':'rules only; no LLM',
                'real_outbound':self.server.config.live_enabled,'simulator_enabled':False,
                'recipient_policy':self.server.config.recipient_policy,
                'number_masked':phone[:3]+'…'+phone[-2:] if phone else 'Not configured',
                'live_e2e_verified':False}
            return self.send(200,state)
        self.send(404,{'error':'operation_not_found'})

    def do_POST(self):
        if self.server.closing.is_set():
            return self.send(503, {'error': 'service_shutting_down'})
        path=urlsplit(self.path).path
        if path.startswith('/webhooks/twilio/'):
            return self.service_webhook(path)
        if not self.origin_guard(): return
        if path in {'/api/simulate','/api/reset'}:
            return self.send(404,{'error':'simulation_not_available_in_service'})
        session=None if path=='/api/login' else self.admin_guard(write=True)
        if path!='/api/login' and not session: return
        try:
            if self.headers.get('Transfer-Encoding'):
                raise IntakeError(400,'transfer_encoding_not_supported')
            payload=json.loads(self.read_body('application/json'))
            if not isinstance(payload,dict): raise ValueError()
            engine=self.server.engine
            if path=='/api/login':
                if set(payload)!={'password'} or not isinstance(payload['password'],str): raise ValueError()
                from reception.auth import login_peer
                peer = login_peer(self.client_address[0], self.headers.get_all('X-Forwarded-For', []),
                                  self.server.config.trusted_proxy_cidrs)
                login,error=self.server.auth.login(payload['password'],peer)
                if not login:
                    return self.send(429 if error=='login_rate_limited' else 401,{'error':error})
                self.cookie(login[0])
                return self.send(200,{'status':'signed_in','token':login[1],'authenticated':True,'mode':'service'})
            if path=='/api/logout':
                if payload: raise ValueError()
                self.server.auth.logout(session['sid']); self.cookie(clear=True)
                return self.send(200,{'status':'signed_out'})
            if path=='/api/handoff':
                if set(payload)!={'phone','active'} or type(payload['active']) is not bool: raise ValueError()
                result=engine.takeover(payload['phone'],payload['active'])
            elif path=='/api/human-reply':
                required = {'phone','text','event_id'}
                if not required <= set(payload) or set(payload) - required - {'reply_to_event_id','confirm_service_inquiry'}: raise ValueError()
                event_id=payload['event_id']
                if not isinstance(event_id,str) or not event_id.startswith('operator-') or len(event_id)>160: raise ValueError()
                source = payload.get('reply_to_event_id')
                if source is not None and (not isinstance(source, str) or not source.strip() or len(source) > 160): raise ValueError()
                confirm = payload.get('confirm_service_inquiry', False)
                if type(confirm) is not bool: raise ValueError()
                result=engine.human_reply(payload['phone'],payload['text'],event_id=event_id,
                                          reply_to_event_id=source,confirm_service_inquiry=confirm)
            elif path=='/api/business':
                result=engine.save_business(payload)
            elif path=='/api/outbox/pause':
                if set(payload)!={'paused'} or type(payload['paused']) is not bool: raise ValueError()
                result=engine.outbox.pause(payload['paused'])
            elif path in {'/api/outbox/cancel','/api/outbox/reconcile'}:
                if set(payload)!={'id'} or type(payload['id']) is not int or payload['id']<1: raise ValueError()
                result=engine.outbox.cancel(payload['id']) if path.endswith('cancel') else engine.outbox.reconcile(payload['id'],self.server.provider)
            else: return self.send(404,{'error':'operation_not_found'})
            return self.send(200,result)
        except IntakeError as exc: self.send(exc.status,{'error':exc.code})
        except (ValueError,TypeError,KeyError): self.send(400,{'error':'invalid_request_check_fields_and_limits'})
        except Exception: self.send(503,{'error':'service_operation_unavailable_check_audit'})

    def service_webhook(self,path):
        from reception.provider import WebhookRejected
        try:
            if self.headers.get('Transfer-Encoding'):
                raise IntakeError(400,'transfer_encoding_not_supported')
            if path not in {'/webhooks/twilio/sms','/webhooks/twilio/status'}:
                return self.send(404,{'error':'unsupported_channel'})
            raw=self.read_body('application/x-www-form-urlencoded')
            multi=parse_qs(raw,keep_blank_values=True,strict_parsing=True,max_num_fields=100)
            if any(len(v)!=1 for v in multi.values()): raise IntakeError(400,'duplicate_form_parameter')
            params={k:v[0] for k,v in multi.items()}
            signature=self.headers.get('X-Twilio-Signature','')
            if path.endswith('/sms'):
                envelope=self.server.adapter.validate_inbound(self.path,signature,params)
                self.server.inbox.accept(envelope)
                return self.send(200,'<?xml version="1.0"?><Response/>','application/xml; charset=utf-8')
            event=self.server.adapter.validate_status(self.path,signature,params)
            query=parse_qs(urlsplit(self.path).query,keep_blank_values=True,strict_parsing=True,max_num_fields=4)
            if set(query)!={'outbox_id','token'} or any(len(v)!=1 for v in query.values()): raise ValueError()
            queue_id=int(query['outbox_id'][0])
            if queue_id<1: raise ValueError()
            with self.server.domain_lock:
                row=self.server.engine.outbox._row(queue_id)
                if event.get('to') and event['to']!=row['phone']:
                    raise WebhookRejected('callback_recipient_mismatch')
                self.server.engine.outbox.apply_callback(queue_id,query['token'][0],event['sid'],event['status'],event.get('error_code') or '')
            return self.send(200,'','text/plain; charset=utf-8')
        except WebhookRejected as exc: self.send(exc.status_code,{'error':exc.reason})
        except IntakeError as exc: self.send(exc.status,{'error':exc.code})
        except (ValueError,TypeError,KeyError): self.send(400,{'error':'invalid_webhook_or_correlation'})
        except Exception: self.send(503,{'error':'webhook_processing_unavailable'})


def _write_run_manifest(directory, value):
    """Only lifecycle metadata; never credentials or customer content."""
    from reception.platform_fs import canonical_path, flush_file
    target = directory / 'run-manifest.json'
    if os.path.lexists(target):
        canonical_path(target)
    temporary = directory / ('.run-manifest-' + secrets.token_hex(8) + '.json')
    try:
        with temporary.open('x', encoding='utf-8') as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        flush_file(temporary)
        deadline = time.monotonic() + 2
        while True:
            try:
                os.replace(temporary, target)
                break
            except PermissionError as exc:
                # Windows readers without delete sharing can hold a short
                # snapshot of the previous manifest. Keep publication atomic
                # and bounded; persistent access errors still fail closed.
                if os.name != 'nt' or exc.winerror not in (5, 32) or time.monotonic() >= deadline:
                    raise
                time.sleep(.025)
                if os.path.lexists(target):
                    canonical_path(target)
    finally:
        temporary.unlink(missing_ok=True)


def _stop_request_matches(directory, run_id, pid):
    from reception.platform_fs import canonical_path
    request = directory / 'stop-request.json'
    if not os.path.lexists(request):
        return False
    try:
        canonical_path(request)
        if request.stat().st_size > 1024:
            return False
        value = json.loads(request.read_text(encoding='utf-8'))
        return (isinstance(value, dict) and set(value) == {'run_id', 'pid'} and
                value['run_id'] == run_id and type(value['pid']) is int and value['pid'] == pid)
    except (OSError, ValueError, TypeError):
        return False


def _synthetic_database_detected(engine):
    marker = engine.db.execute("SELECT value FROM settings WHERE key='windows_acceptance_synthetic'").fetchone()
    return (bool(marker and marker[0] in ('true', '1')) or
            engine.db.execute("SELECT 1 FROM messages WHERE status='simulated' OR event_id LIKE 'seed-%' LIMIT 1").fetchone() is not None)


def _validate_service_database(engine, live_enabled):
    if live_enabled and _synthetic_database_detected(engine):
        raise ValueError('Synthetic demo records cannot enable live sending; use a separate service database')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--db", default=None)
    parser.add_argument('--run-dir', default=None, help='Optional new lifecycle-manifest directory; no background service is installed')
    args = parser.parse_args()
    from reception.config import ServiceConfig
    try: cfg=ServiceConfig.from_env()
    except ValueError as exc: parser.error(str(exc))
    args.db=args.db or str(ROOT/'runtime'/('service.sqlite3' if cfg.mode=='service' else 'demo.sqlite3'))
    from reception.platform_fs import validate_creation_path, validate_lexical_path
    try:
        if args.db != ':memory:':
            validate_lexical_path(args.db)
        if args.run_dir:
            validate_lexical_path(args.run_dir)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    if args.db != ":memory:":
        try:
            args.db = str(validate_creation_path(args.db))
        except (ValueError, OSError) as exc:
            parser.error(str(exc))
        Path(args.db).parent.mkdir(parents=True, exist_ok=True)
    # Internal QA proxy needs a container-facing listener. It is never enabled
    # by the normal launcher and is not a public deployment path.
    qa_preview = os.environ.get("DEMO_INTERNAL_QA") == "1" and os.environ.get("DEMO_PREVIEW_HOST") == "terminal.local"
    host = "0.0.0.0" if qa_preview else "127.0.0.1"
    instance_lock = None
    lock_owned = False
    engine = server = watcher = None
    watcher_stop = threading.Event()
    run_directory = None
    manifest = None
    exit_code = 0
    try:
        if args.run_dir:
            from reception.platform_fs import validate_creation_path
            run_directory = validate_creation_path(args.run_dir, directory=True)
            if os.path.lexists(run_directory):
                metadata = run_directory.lstat()
                if run_directory.is_symlink() or getattr(metadata, 'st_file_attributes', 0) & 0x400:
                    raise ValueError('Lifecycle directory must not be a reparse point')
            run_directory.mkdir(parents=True, exist_ok=True)
            run_directory = run_directory.resolve()
            if any(os.path.lexists(run_directory / name) for name in ('run-manifest.json', 'stop-request.json')):
                raise ValueError('Lifecycle directory must be new for this run')
            manifest = {'run_id': secrets.token_hex(16), 'pid': os.getpid(),
                        'process_started_at': datetime.now(timezone.utc).isoformat(),
                        'python_executable': sys.executable, 'python_prefix': sys.prefix,
                        'base_prefix': sys.base_prefix,
                        'app_mode': cfg.mode,
                        'db_path': '', 'port': None, 'url': '', 'status': 'starting',
                        'real_outbound': cfg.live_enabled, 'recipient_policy': cfg.recipient_policy,
                        'instance_lock': 'not_acquired', 'stop_request_path': str(run_directory / 'stop-request.json')}
            # Record the owned nonce/PID before database initialization. A launch
            # failure can then request this process's normal shutdown without
            # guessing or terminating another process.
            _write_run_manifest(run_directory, manifest)
        if cfg.mode=='service':
            if os.name != 'nt':
                os.umask(0o077)
            if cfg.live_enabled and qa_preview:
                raise ValueError('Internal preview cannot enable real sending')
            if args.db == ':memory:':
                raise ValueError('Service mode requires a persistent database')
        # Every supported app writer of a persistent DB uses the same lease.
        # Demo's reset/simulate routes must not open a live service's database.
        if args.db != ':memory:':
            from reception.operations import service_lock
            instance_lock=service_lock(args.db)
            args.db=str(instance_lock.__enter__())
            lock_owned = True
            if manifest:
                manifest.update(db_path=args.db, instance_lock='held')
            from reception.operations import restore_recovery_pending
            if restore_recovery_pending(args.db):
                raise RuntimeError('Restore recovery marker requires offline recovery before service startup')
        if cfg.mode=='service':
            engine=Engine(args.db,production=True,seed_demo=False,send_policy=cfg.send_policy(),privacy_key=cfg.privacy_hmac_key)
            _validate_service_database(engine, cfg.live_enabled)
            provider=None
            if all((cfg.account_sid,cfg.auth_token,cfg.phone_number,cfg.public_base_url)):
                from reception.provider import TwilioRESTProvider
                provider=TwilioRESTProvider(account_sid=cfg.account_sid,auth_token=cfg.auth_token,phone_number=cfg.phone_number,
                    public_base_url=cfg.public_base_url,api_key_sid=cfg.api_key_sid,api_key_secret=cfg.api_key_secret,timeout_seconds=cfg.request_timeout_seconds)
            server=ServiceServer((host,args.port),engine,cfg,provider=provider,host_alias='terminal.local' if qa_preview else None)
            server.worker.start()
            print(f'SERVICE / {"LIVE ENABLED — E2E unverified" if cfg.live_enabled else "DRY RUN"} — loopback port {server.server_port} — pid {os.getpid()}',flush=True)
        else:
            engine=Engine(args.db)
            server=DemoServer((host,args.port),engine,host_alias='terminal.local' if qa_preview else None)
            server.lifecycle = manifest
            print(f'DEMO / SIMULATED — http://127.0.0.1:{server.server_port} — real outbound disabled',flush=True)
        import signal
        if threading.current_thread() is threading.main_thread():
            def stop_signal(*_): raise KeyboardInterrupt
            signal.signal(signal.SIGTERM,stop_signal)
            if hasattr(signal, 'SIGBREAK'):
                signal.signal(signal.SIGBREAK, stop_signal)
        if manifest:
            manifest.update(status='running', port=server.server_port,
                            url=f'http://127.0.0.1:{server.server_port}')
            _write_run_manifest(run_directory, manifest)
            def watch_requests():
                while not watcher_stop.wait(0.1):
                    if _stop_request_matches(run_directory, manifest['run_id'], manifest['pid']):
                        server.begin_close()
                        server.shutdown()
                        return
            watcher = threading.Thread(target=watch_requests, name='owned-stop-request', daemon=True)
            watcher.start()
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    except (ValueError, RuntimeError, OSError) as exc:
        # Only controlled local validation/ownership errors. Provider exceptions
        # are already sanitized by the transport adapter.
        print('Service startup/operation failed: ' + str(exc), file=sys.stderr, flush=True)
        exit_code = 2
    except Exception:
        print('Service startup/operation failed; no request or credential details are logged', file=sys.stderr, flush=True)
        exit_code = 2
    finally:
        watcher_stop.set()
        if server is not None:
            server.begin_close()
            server.server_close()
        if server is not None and not server.drain(timeout=20):
            print('shutdown_incomplete: pending work remains uncertain; process exit required before offline maintenance',file=sys.stderr,flush=True)
            # Do not close a connection or unlock while a daemon worker/handler
            # can still use it. Process termination releases both together.
            os._exit(2)
        if watcher is not None:
            watcher.join(timeout=1)
        if engine is not None:
            engine.close()
        if lock_owned:
            instance_lock.__exit__(None,None,None)
        if manifest:
            manifest.update(status='failed' if exit_code else 'stopped', instance_lock='released' if lock_owned else 'not_acquired',
                            stopped_at=datetime.now(timezone.utc).isoformat())
            try:
                _write_run_manifest(run_directory, manifest)
            except (OSError, ValueError):
                print('Lifecycle manifest could not be finalized', file=sys.stderr, flush=True)
                exit_code = 2
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
