"""Prepare/update a marked synthetic DB for actual app.py service UI checks.

No HTTP server is constructed here. The real PowerShell Start/app.py process
owns the service lock and dashboard afterwards. Updates require that it stopped.
"""
import argparse
import json
import os
import sys
from pathlib import Path
from contextlib import closing
import sqlite3

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from reception.auth import hash_password
from reception.config import ServiceConfig
from reception.engine import Engine, stamp
from reception.operations import service_lock
from reception.service import ServiceInbox
from reception.platform_fs import NativePlatform

PRIVACY_KEY = 'windows-synthetic-privacy-key-32-characters'
PASSWORD = 'Synthetic Windows password 123!'


class DeniedSendingProvider:
    def __init__(self):
        self.proofs = {}
    def fetch_inbound(self, sid):
        return dict(self.proofs[sid])
    def send(self, *args):
        raise AssertionError('Synthetic preparation cannot send any message')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--action', choices=('seed', 'new-request', 'finish-request'), default='seed')
    parser.add_argument('--evidence', type=Path, required=True)
    args = parser.parse_args()
    database = Path(os.path.abspath(args.database))
    if not any(database.is_relative_to(ROOT / 'artifacts' / namespace)
               for namespace in ('windows-alignment-runs', 'final-acceptance-runs')):
        raise ValueError('Synthetic DB must stay within the dedicated acceptance runs')
    evidence_path = Path(os.path.abspath(args.evidence))
    if not any(evidence_path.is_relative_to(ROOT / 'artifacts' / namespace)
               for namespace in ('windows-alignment-runs', 'final-acceptance-runs')):
        raise ValueError('Evidence must stay within the dedicated acceptance runs')
    # The caller creates its recorded run/data directory. This fixture never
    # creates database parents from unvalidated user-supplied components.
    database = NativePlatform.canonical_path(database, allow_create=True)
    evidence_path = NativePlatform.validate_creation_path(evidence_path, directory=True)
    evidence_path.mkdir(parents=True, exist_ok=False)
    if args.action == 'seed' and database.exists():
        raise ValueError('Synthetic seed never overwrites an existing database')
    if args.action != 'seed':
        with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)) as db:
            marker = db.execute("SELECT value FROM settings WHERE key='windows_acceptance_synthetic'").fetchone()
            if not marker or marker[0] != 'true':
                raise ValueError('Only the marked synthetic acceptance DB may be updated')
    config = ServiceConfig(mode='service', admin_password_hash=hash_password(PASSWORD),
        account_sid='AC' + 'a' * 32, phone_number='+12025550100',
        public_base_url='https://synthetic.example.test', live_enabled=False,
        privacy_hmac_key=PRIVACY_KEY, optout_reviewed=True)
    provider = DeniedSendingProvider()
    results = []
    with service_lock(database) as canonical:
        engine = Engine(canonical, production=True, seed_demo=False,
            send_policy=config.send_policy(), privacy_key=PRIVACY_KEY)
        try:
            if args.action == 'seed':
                business = {'name': 'Harbor & Home Cleaning', 'hours': 'Synthetic weekdays',
                    'service_areas': ['Oakwood', 'Riverside', 'Maple Heights'],
                    'services': [{'name': 'Standard cleaning', 'description': 'Synthetic standard service', 'pricing': 'Owner review'},
                                 {'name': 'Deep cleaning', 'description': 'Synthetic deep service', 'pricing': 'Owner review'}],
                    'price_note': 'Synthetic owner review', 'sending_enabled': True,
                    'max_messages_per_hour': 8, 'model_mode': 'rules',
                    'approved_faqs': [
                        {'topic': 'hours', 'answer': 'Approved hours: Monday-Friday, 9 AM-5 PM.', 'approved': True},
                        {'topic': 'areas', 'answer': 'Approved areas: Oakwood, Riverside and Maple Heights.', 'approved': True},
                        {'topic': 'services', 'answer': 'Approved services: standard and deep cleaning.', 'approved': True},
                        {'topic': 'pricing', 'answer': 'Approved pricing: the owner reviews the final quote.', 'approved': True},
                        {'topic': 'requests', 'answer': 'Approved request rules: provide service, area, time and contact; owner confirmation is required.', 'approved': True}]}
                engine.save_business(business)
                with engine.db:
                    engine.db.execute("INSERT INTO settings(key,value) VALUES ('windows_acceptance_synthetic','true')")
            inbox = ServiceInbox(engine, provider, config)
            sequence = engine.db.execute('SELECT count(*) FROM provider_inbox').fetchone()[0]
            def inbound(phone, body, name=None):
                nonlocal sequence
                sequence += 1
                sid = 'SM' + f'{sequence:032x}'
                provider.proofs[sid] = {'verified': True, 'resource_id': sid, 'account_sid': config.account_sid,
                    'phone': phone, 'to': config.phone_number, 'body': body, 'direction': 'inbound', 'occurred_at': stamp()}
                accepted = inbox.accept({'sid': sid, 'from': phone, 'to': config.phone_number, 'body': body,
                    'account_sid': config.account_sid})
                processed = inbox.process_one()
                if name:
                    with engine.db: engine.db.execute('UPDATE contacts SET name=? WHERE phone=?', (name, phone))
                results.append({'body': body, 'accepted': accepted, 'processed': processed})
            if args.action == 'seed':
                inbound('+12025550810', 'What are your hours?', 'Synthetic Hours')
                inbound('+12025550811', 'How do I book?', 'Synthetic Request Rules')
                inbound('+12025550812', 'Are you insured?', 'Synthetic Insurance')
                for body in ('I need a deep cleaning', 'Riverside', 'tomorrow2pm', 'this number'):
                    inbound('+12025550813', body, 'Synthetic Booking')
                inbound('+12025550814', 'What are your hours?', 'Synthetic STOP')
                inbound('+12025550814', 'STOP')
                engine.takeover('+12025550814', True)
                for phone, body, name in (('+12025550816', 'Where do you serve?', 'Synthetic Areas'),
                    ('+12025550817', 'What services do you offer?', 'Synthetic Services'),
                    ('+12025550818', 'What are your prices?', 'Synthetic Pricing')):
                    inbound(phone, body, name)
            elif args.action == 'new-request':
                inbound('+12025550813', 'I want to book a standard cleaning')
            else:
                for body in ('Oakwood', 'next Monday 10am', 'new.synthetic@example.test'):
                    inbound('+12025550813', body)
                inbound('+12025550811', 'How do I book?')
                inbound('+12025550812', 'STOP')
            evidence = {'action': args.action, 'database': str(canonical), 'synthetic_only': True,
                'real_sending': False, 'events': results, 'appointments': engine.snapshot()['appointments']}
            (evidence_path / 'preparation.json').write_text(json.dumps(evidence, indent=2), encoding='utf-8')
            print(json.dumps({'action': args.action, 'database': str(canonical), 'appointments': len(evidence['appointments'])}))
        finally:
            engine.close()


if __name__ == '__main__':
    main()
