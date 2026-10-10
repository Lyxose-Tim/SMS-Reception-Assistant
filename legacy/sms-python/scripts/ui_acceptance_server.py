"""Isolated loopback UI fixture: synthetic data, fake inbound proof, no sending.

Not a service launcher. It never constructs a Twilio REST client. A workspace
command file feeds fake-provider events through the real ServiceInbox so browser
checks can inspect subsequent state without adding simulation to service routes.
"""
import json
import os
import argparse
import secrets
import sys
import threading
import time
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import ServiceServer
from reception.auth import hash_password
from reception.config import ServiceConfig
from reception.engine import Engine, stamp
from reception.operations import service_lock
from reception.platform_fs import validate_creation_path

PASSWORD = "Synthetic QA password 123!"


class FakeInboundProvider:
    def __init__(self):
        self.proofs = {}

    def fetch_inbound(self, sid):
        return dict(self.proofs[sid])

    def send(self, *args):
        raise AssertionError("UI acceptance fixture has sending disabled")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', help='New exclusive synthetic fixture directory; existing output is never overwritten')
    args = parser.parse_args()
    requested = args.output_dir or str(ROOT / 'artifacts' / 'ui-runs' / (
        datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '-' + secrets.token_hex(4)))
    output = validate_creation_path(requested, directory=True)
    output.mkdir(parents=True, exist_ok=False)
    database = output / ("ui-" + str(time.time_ns()) + ".sqlite3")
    config = ServiceConfig(mode="service", admin_password_hash=hash_password(PASSWORD),
        phone_number="+12025550100", account_sid="AC" + "a" * 32,
        public_base_url="https://synthetic.example.test", live_enabled=False,
        privacy_hmac_key="synthetic-ui-privacy-key-32-characters", optout_reviewed=True)
    with service_lock(database) as database:
        _run_fixture(database, config, output)


def _run_fixture(database, config, output):
    engine = Engine(database, production=True, seed_demo=False,
        send_policy=config.send_policy(), privacy_key=config.privacy_hmac_key)
    business = engine.snapshot()["business"]
    business["approved_faqs"] = [
        {"topic": "hours", "answer": "Approved hours: Monday-Friday, 9 AM-5 PM.", "approved": True},
        {"topic": "areas", "answer": "Approved areas: Oakwood, Riverside and Maple Heights.", "approved": True},
        {"topic": "services", "answer": "Approved services: standard and deep cleaning.", "approved": True},
        {"topic": "pricing", "answer": "Approved pricing: the owner reviews the final quote.", "approved": True},
        {"topic": "requests", "answer": "Approved request rules: provide service, area, time and contact; owner confirmation is required.", "approved": True},
    ]
    engine.save_business(business)
    provider = FakeInboundProvider()
    server = ServiceServer(("127.0.0.1", 0), engine, config, provider=provider)
    sequence = 0

    def inbound(phone, body, name=None):
        nonlocal sequence
        sequence += 1
        sid = "SM" + f"{sequence:032x}"
        provider.proofs[sid] = {"verified": True, "resource_id": sid,
            "account_sid": config.account_sid, "phone": phone, "to": config.phone_number,
            "body": body, "direction": "inbound", "occurred_at": stamp()}
        server.inbox.accept({"sid": sid, "from": phone, "to": config.phone_number,
            "body": body, "account_sid": config.account_sid})
        result = server.inbox.process_one()
        if name:
            with engine._lock, engine.db:
                engine.db.execute("UPDATE contacts SET name=? WHERE phone=?", (name, phone))
        return result

    inbound("+12025550810", "What are your hours?", "Synthetic Hours")
    inbound("+12025550811", "How do I book?", "Synthetic Request Rules")
    inbound("+12025550812", "Are you insured?", "Synthetic Insurance")
    for body in ("I need a deep cleaning", "Riverside", "tomorrow2pm", "this number"):
        inbound("+12025550813", body, "Synthetic Booking")
    inbound("+12025550814", "What are your hours?", "Synthetic STOP")
    inbound("+12025550814", "STOP")
    engine.takeover("+12025550814", True)
    for phone, body, name in (("+12025550816", "Where do you serve?", "Synthetic Areas"),
        ("+12025550817", "What services do you offer?", "Synthetic Services"),
        ("+12025550818", "What are your prices?", "Synthetic Pricing")):
        inbound(phone, body, name)

    info = {"url": f"http://127.0.0.1:{server.server_port}", "database": str(database),
        "synthetic_only": True, "real_sending_enabled": False}
    (output / "server-info.json").write_text(json.dumps(info, indent=2), encoding="utf-8")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print(json.dumps(info), flush=True)
    command_path = output / "commands.json"
    command_path.write_text('{"id":null}', encoding="utf-8")
    last_id = None
    try:
        while True:
            if command_path.exists():
                command = json.loads(command_path.read_text(encoding="utf-8"))
                if command.get("id") != last_id:
                    last_id = command["id"]
                    if command.get("action") == "stop": break
                    result = inbound(command["phone"], command["body"])
                    (output / "command-result.json").write_text(json.dumps({"id": last_id, "result": result}), encoding="utf-8")
            time.sleep(0.1)
    except KeyboardInterrupt:
        pass
    finally:
        server.begin_close()
        server.shutdown()
        server.server_close()
        thread.join(3)
        if server.drain(5):
            engine.close()
        else:
            # A running writer must retain DB/lock ownership until process exit.
            print("shutdown_incomplete: fixture resources retained until process exit", file=sys.stderr, flush=True)
            os._exit(2)


if __name__ == "__main__":
    main()
