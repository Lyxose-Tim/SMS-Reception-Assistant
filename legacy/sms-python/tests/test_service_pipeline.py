"""Production pipeline tests use only a recording synthetic provider.

Passing these tests verifies local persistence and permission boundaries; it
does not verify a Twilio account, destination, registration, or actual delivery.
"""
import copy
import hashlib
import sqlite3
import threading
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from reception.auth import hash_password
from reception.config import ServiceConfig
from reception.engine import Engine, now_utc, stamp
from reception.provider import SendRejected, SendUncertain
from reception.service import ServiceInbox, ServiceWorker


class RecordingProvider:
    def __init__(self):
        self.proofs = {}
        self.fetch_calls = []
        self.send_calls = []
        self.status_calls = []
        self.fetch_error = None
        self.send_error = None
        self.status_proof = None

    def fetch_inbound(self, sid):
        self.fetch_calls.append(sid)
        if self.fetch_error:
            raise self.fetch_error
        return copy.deepcopy(self.proofs[sid])

    def send(self, to, body, callback_url):
        self.send_calls.append((to, body, callback_url))
        if self.send_error:
            raise self.send_error
        return {'sid': 'SM' + f'{len(self.send_calls) + 100:032x}', 'status': 'queued', 'error_code': None}

    def fetch_status(self, sid):
        self.status_calls.append(sid)
        return copy.deepcopy(self.status_proof)

    def lookup(self, sid):
        return self.fetch_status(sid)


class ServicePipelineTests(unittest.TestCase):
    PASSWORD_HASH = hash_password('Synthetic admin password 123!', salt=b'0123456789abcdef')
    PHONE = '+12025550181'

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp.name) / 'service.sqlite3')
        self.config = ServiceConfig(
            mode='service', admin_password_hash=self.PASSWORD_HASH,
            account_sid='AC' + 'a' * 32, auth_token='synthetic-secret-not-a-real-token',
            phone_number='+12025550100', public_base_url='https://synthetic.example.test',
            allowed_recipients=(self.PHONE,), live_enabled=False,
            optout_reviewed=True, readiness_approved=True, send_approval_recorded=True,
        )
        self.provider = RecordingProvider()
        self.engine = Engine(self.db_path, production=True, seed_demo=False, send_policy=self.config.send_policy())
        business = self.engine.snapshot()['business']
        business['approved_faqs'] = [{'topic': 'hours', 'answer': 'Synthetic approved hours: Monday-Friday, 9 AM-5 PM.', 'approved': True}]
        self.engine.save_business(business)
        self.inbox = ServiceInbox(self.engine, self.provider, self.config)
        self.counter = 0

    def tearDown(self):
        self.engine.close()
        self.temp.cleanup()

    def envelope(self, body='What are your opening hours?', phone=None, opt_out_type='', occurred_at=None):
        self.counter += 1
        sid = 'SM' + f'{self.counter:032x}'
        phone = phone or self.PHONE
        params = {'sid': sid, 'from': phone, 'to': self.config.phone_number,
                  'body': body, 'account_sid': self.config.account_sid, 'opt_out_type': opt_out_type}
        self.provider.proofs[sid] = {
            'verified': True, 'resource_id': sid, 'account_sid': self.config.account_sid,
            'phone': phone, 'to': self.config.phone_number, 'body': body, 'direction': 'inbound',
            'occurred_at': occurred_at or datetime.now(timezone.utc).isoformat(),
        }
        return params

    def accept_and_process(self, envelope):
        self.inbox.accept(envelope)
        return self.inbox.process_one()

    def contact(self, phone=None):
        return next(c for c in self.engine.snapshot()['contacts'] if c['phone'] == (phone or self.PHONE))

    def queue(self):
        return self.engine.outbox.snapshot()['items']

    def review(self, envelope, *, confirm=True, event_id='operator-review-1', phone=None):
        return self.engine.human_reply(phone or envelope['from'], 'Synthetic owner answer about this service.', event_id=event_id,
            reply_to_event_id='twilio:'+envelope['sid'], confirm_service_inquiry=confirm)

    def test_unknown_first_service_question_requires_explicit_human_classification(self):
        envelope = self.envelope('Are you insured?')
        result = self.accept_and_process(envelope)
        self.assertEqual(result['status'], 'handoff')
        self.assertFalse(self.contact()['reply_permission_until'])
        blocked = self.review(envelope, confirm=False)
        self.assertEqual(blocked['reason'], 'service_inquiry_confirmation_required')
        granted = self.review(envelope, event_id='operator-approved-service-1')
        self.assertEqual(granted['status'], 'queued')
        contact = self.contact()
        self.assertEqual(contact['reply_permission_basis'], 'operator_review_human_only')
        self.assertEqual(contact['reply_permission_event_id'], 'twilio:'+envelope['sid'])
        self.assertEqual(contact['consent_status'], 'none')
        self.assertEqual(contact['consent_purposes'], [])
        self.assertTrue(contact['handoff'])
        source = next(s for s in contact['reply_sources'] if s['event_id'] == contact['reply_permission_event_id'])
        self.assertTrue(source['verified'])
        self.assertTrue(source['eligible'])
        self.assertEqual(self.queue()[0]['origin'], 'human')
        self.assertTrue(any(a['action']=='service_inquiry_operator_reviewed' for a in self.engine.snapshot()['audit']))

    def test_human_review_uses_resource_time_and_repeated_confirmation_does_not_renew(self):
        envelope = self.envelope('Are you insured?')
        self.accept_and_process(envelope)
        source_time = datetime.fromisoformat(self.provider.proofs[envelope['sid']]['occurred_at'])
        with patch('reception.engine.now_utc',return_value=source_time+timedelta(hours=23)):
            self.assertEqual(self.review(envelope)['status'],'queued')
            original = self.contact()['reply_permission_until']
            self.assertEqual(datetime.fromisoformat(original.replace('Z','+00:00')),source_time+timedelta(hours=24))
            self.review(envelope,event_id='operator-repeat-review')
            self.assertEqual(self.contact()['reply_permission_until'],original)
        with patch('reception.engine.now_utc',return_value=source_time+timedelta(hours=24)):
            blocked = self.review(envelope,event_id='operator-expired-review')
            self.assertEqual(blocked['reason'],'service_reply_window_expired')
        self.assertEqual(self.contact()['reply_permission_until'],original)

    def test_human_only_permission_cannot_be_used_by_resumed_automation(self):
        envelope = self.envelope('Are you insured?')
        self.accept_and_process(envelope)
        self.review(envelope)
        self.engine.takeover(self.PHONE,False)
        contact=self.engine._contact(self.PHONE)
        self.assertEqual(self.engine._send_gate(contact,self.engine._business(),'service_reply'), 'human_only_service_permission')

    def test_valid_reviewed_source_remains_selectable_after_eleven_help_messages(self):
        envelope=self.envelope('Are you insured?')
        self.accept_and_process(envelope)
        self.review(envelope)
        original_until=self.contact()['reply_permission_until']
        for _ in range(11):
            self.accept_and_process(self.envelope('HELP'))
        contact=self.contact()
        self.assertEqual(contact['reply_permission_until'],original_until)
        self.assertLessEqual(len(contact['reply_sources']),11)
        current=next(s for s in contact['reply_sources'] if s['event_id']=='twilio:'+envelope['sid'])
        self.assertTrue(current['verified'])
        self.assertTrue(current['eligible'])
        self.assertEqual(self.review(envelope,confirm=False,event_id='operator-after-help')['status'],'queued')

    def test_hourly_contact_limit_blocks_automatic_and_human_without_new_authority(self):
        business=self.engine.snapshot()['business']
        business['max_messages_per_hour']=1
        self.engine.save_business(business)
        first=self.envelope()
        self.accept_and_process(first)
        original_source=self.contact()['reply_permission_event_id']
        original_until=self.contact()['reply_permission_until']
        automatic=self.accept_and_process(self.envelope())
        self.assertEqual(automatic['reason'],'hourly_send_limit')
        self.assertEqual(self.contact()['reply_permission_event_id'],original_source)
        self.assertEqual(self.contact()['reply_permission_until'],original_until)
        self.engine.takeover(self.PHONE,True)
        manual=self.engine.human_reply(self.PHONE,'Synthetic service answer.',event_id='operator-hourly-blocked')
        self.assertEqual(manual['reason'],'hourly_send_limit')
        unknown=self.envelope('Are you insured?')
        self.accept_and_process(unknown)
        reviewed=self.review(unknown,event_id='operator-hourly-review')
        self.assertEqual(reviewed['reason'],'hourly_send_limit')
        self.assertEqual(self.contact()['reply_permission_event_id'],original_source)
        self.assertEqual(len(self.queue()),1)

    def test_review_source_rejects_wrong_contact_unverified_and_expired_sources(self):
        envelope = self.envelope('Are you insured?')
        self.inbox.accept(envelope)
        pending = self.review(envelope)
        self.assertEqual(pending['reason'],'source_not_verified')
        self.inbox.process_one()
        wrong = self.review(envelope,phone='+12025550182',event_id='operator-wrong-contact')
        self.assertEqual(wrong['reason'],'source_contact_mismatch')
        source_time = datetime.fromisoformat(self.provider.proofs[envelope['sid']]['occurred_at'])
        with patch('reception.engine.now_utc',return_value=source_time+timedelta(hours=25)):
            expired = self.review(envelope,event_id='operator-late-review')
            self.assertEqual(expired['reason'],'service_reply_window_expired')
        self.assertEqual(self.queue(),[])

    def test_stop_and_carrier_block_cannot_be_bypassed_by_human_review(self):
        envelope = self.envelope('Are you insured?')
        self.accept_and_process(envelope)
        self.inbox.accept(self.envelope('STOP'))
        self.assertEqual(self.review(envelope)['reason'],'opted_out')
        self.accept_and_process(self.envelope('START'))
        self.assertEqual(self.review(envelope,event_id='operator-before-stop')['reason'],'source_predates_opt_out')
        later = self.envelope('Are you insured?')
        self.accept_and_process(later)
        with self.engine.db:
            self.engine.db.execute('UPDATE contacts SET carrier_blocked=1 WHERE phone=?',(self.PHONE,))
        self.assertEqual(self.review(later,event_id='operator-carrier-block')['reason'],'contact_blocked')
        self.assertEqual(self.queue(),[])

    def test_start_restores_only_valid_prior_source_and_never_revives_canceled_queue(self):
        envelope = self.envelope()
        self.accept_and_process(envelope)
        before = self.contact()
        self.inbox.accept(self.envelope('STOP'))
        self.assertTrue(all(q['status']=='canceled' for q in self.queue()))
        self.accept_and_process(self.envelope('START'))
        after = self.contact()
        self.assertEqual(after['reply_permission_until'],before['reply_permission_until'])
        self.assertEqual(after['reply_permission_event_id'],before['reply_permission_event_id'])
        self.assertEqual(after['reply_permission_basis'],before['reply_permission_basis'])
        self.assertTrue(all(q['status']=='canceled' for q in self.queue()))
        self.engine.takeover(self.PHONE,True)
        reply = self.engine.human_reply(self.PHONE,'Synthetic service answer.',event_id='operator-after-start')
        self.assertEqual(reply['status'],'queued')
        self.assertEqual(after['consent_status'],'none')

    def test_faq_after_stop_never_replaces_original_authority_before_start(self):
        self.accept_and_process(self.envelope())
        fields=('reply_permission_event_id','reply_permission_basis','reply_permission_until','last_service_inquiry_at')
        original={k:self.contact()[k] for k in fields}
        self.inbox.accept(self.envelope('STOP'))
        blocked_envelope=self.envelope('What are your opening hours?')
        result=self.accept_and_process(blocked_envelope)
        self.assertEqual(result['reason'],'opted_out')
        self.assertEqual({k:self.contact()[k] for k in fields},original)
        self.assertTrue(any(m['event_id']=='twilio:'+blocked_envelope['sid'] and m['direction']=='inbound'
            for c in self.engine.snapshot()['conversations'] for m in c['messages']))
        self.accept_and_process(self.envelope('START'))
        self.assertEqual({k:self.contact()[k] for k in fields},original)
        self.assertTrue(all(q['status']=='canceled' for q in self.queue()))

    def test_faq_after_carrier_block_never_creates_or_renews_authority(self):
        self.accept_and_process(self.envelope())
        fields=('reply_permission_event_id','reply_permission_basis','reply_permission_until','last_service_inquiry_at')
        original={k:self.contact()[k] for k in fields}
        with self.engine.db:
            self.engine.db.execute('UPDATE contacts SET carrier_blocked=1,carrier_blocked_at=? WHERE phone=?',(stamp(),self.PHONE))
            self.engine.outbox.cancel_for_contact(self.PHONE,'CARRIER_21610')
        result=self.accept_and_process(self.envelope())
        self.assertEqual(result['reason'],'contact_blocked')
        self.assertEqual({k:self.contact()[k] for k in fields},original)
        self.accept_and_process(self.envelope('START'))
        self.assertEqual({k:self.contact()[k] for k in fields},original)
        self.assertTrue(all(q['status']=='canceled' for q in self.queue()))

    def test_stop_then_ordinary_faq_and_start_cannot_create_a_first_window(self):
        self.inbox.accept(self.envelope('STOP'))
        result=self.accept_and_process(self.envelope())
        self.assertEqual(result['reason'],'opted_out')
        self.assertEqual(self.contact()['reply_permission_event_id'],'')
        self.assertFalse(self.contact()['reply_permission_until'])
        self.accept_and_process(self.envelope('START'))
        self.assertEqual(self.contact()['reply_permission_event_id'],'')
        self.assertEqual(self.contact()['reply_permission_basis'],'')
        self.assertFalse(self.contact()['reply_permission_until'])
        self.assertEqual(self.queue(),[])

    def test_start_flags_without_durable_keyword_resource_never_restore(self):
        self.inbox.accept(self.envelope('STOP'))
        sid='SM'+'c'*32
        result=self.engine.process_event({'event_id':'twilio:'+sid,'provider_sid':sid,'phone':self.PHONE,'kind':'sms',
            'origin':'twilio','verified':True,'provider_fresh':True,'provider_keyword_verified':True,'text':'START'})
        self.assertEqual(result['reason'],'trusted_fresh_platform_start_required')
        self.assertEqual(self.contact()['consent_status'],'revoked')

    def test_start_preserves_carrier_barrier_against_later_review_of_old_unknown(self):
        envelope=self.envelope('Are you insured?')
        self.accept_and_process(envelope)
        barrier=stamp()
        with self.engine.db:
            self.engine.db.execute('UPDATE contacts SET carrier_blocked=1,carrier_blocked_at=? WHERE phone=?',(barrier,self.PHONE))
        self.accept_and_process(self.envelope('START'))
        self.assertFalse(self.contact()['carrier_blocked'])
        self.assertEqual(self.contact()['carrier_blocked_at'],barrier)
        self.assertEqual(self.review(envelope)['reason'],'source_predates_opt_out')
        self.assertEqual(self.queue(),[])

    def test_start_clears_expired_prior_authority_without_new_window(self):
        self.accept_and_process(self.envelope())
        with self.engine.db:
            self.engine.db.execute('UPDATE contacts SET reply_permission_until=? WHERE phone=?',(stamp(now_utc()-timedelta(seconds=1)),self.PHONE))
        self.inbox.accept(self.envelope('STOP'))
        self.accept_and_process(self.envelope('START'))
        self.assertEqual(self.contact()['reply_permission_event_id'],'')
        self.assertEqual(self.contact()['reply_permission_basis'],'')
        self.assertFalse(self.contact()['reply_permission_until'])
        audit=next(a for a in self.engine.snapshot()['audit'] if a['action']=='service_reply_restored')
        self.assertFalse(audit['details']['restored'])
        self.assertEqual(audit['details']['permission_reason'],'service_reply_window_expired')
        self.assertTrue(all(q['status']=='canceled' for q in self.queue()))

    def test_start_clears_prior_authority_when_source_was_deleted(self):
        envelope=self.envelope()
        self.accept_and_process(envelope)
        with self.engine.db:
            self.engine.db.execute('DELETE FROM events WHERE event_id=?',('twilio:'+envelope['sid'],))
        self.inbox.accept(self.envelope('STOP'))
        self.accept_and_process(self.envelope('START'))
        self.assertEqual(self.contact()['reply_permission_event_id'],'')
        self.assertFalse(self.contact()['reply_permission_until'])
        audit=next(a for a in self.engine.snapshot()['audit'] if a['action']=='service_reply_restored')
        self.assertFalse(audit['details']['restored'])
        self.assertEqual(audit['details']['permission_reason'],'source_unavailable')

    def test_deleted_or_tombstoned_source_never_authorizes_a_reply(self):
        envelope = self.envelope()
        self.accept_and_process(envelope)
        source_id='twilio:'+envelope['sid']
        with self.engine.db:
            self.engine.db.execute('DELETE FROM events WHERE event_id=?',(source_id,))
        blocked=self.engine.human_reply(self.PHONE,'Synthetic answer.',event_id='operator-deleted-source')
        self.assertEqual(blocked['reason'],'source_unavailable')
        with self.engine.db:
            self.engine.db.execute('INSERT INTO privacy_dedup(event_hash,created_at) VALUES (?,?)',(hashlib.sha256(source_id.encode()).hexdigest(),stamp()))
        blocked=self.review(envelope,event_id='operator-tombstoned-source')
        self.assertEqual(blocked['reason'],'source_unavailable')

    def test_direct_verified_flags_without_durable_provider_source_do_not_grant_permission(self):
        sid='SM'+'b'*32
        result=self.engine.process_event({'event_id':'twilio:'+sid,'kind':'sms','phone':self.PHONE,
            'origin':'twilio','verified':True,'provider_fresh':True,'provider_sid':sid,'text':'What are your hours?'})
        self.assertEqual(result['status'],'blocked')
        self.assertFalse(self.contact()['reply_permission_until'])
        self.assertEqual(self.queue(),[])

    def test_first_rule_permission_and_inbox_proof_are_not_visible_before_commit(self):
        envelope=self.envelope()
        self.inbox.accept(envelope)
        original=self.engine._send
        def observe(*args,**kwargs):
            self.assertTrue(self.engine.db.in_transaction)
            with closing(sqlite3.connect(self.db_path)) as reader:
                inbox=reader.execute('SELECT status,resource_at FROM provider_inbox WHERE sid=?',(envelope['sid'],)).fetchone()
                self.assertEqual(inbox,('verifying',None))
                self.assertEqual(reader.execute('SELECT count(*) FROM contacts').fetchone()[0],0)
                self.assertEqual(reader.execute('SELECT count(*) FROM outbox').fetchone()[0],0)
            return original(*args,**kwargs)
        with patch.object(self.engine,'_send',side_effect=observe):
            result=self.inbox.process_one()
        self.assertEqual(result['status'],'queued')
        self.assertTrue(self.contact()['reply_permission_event_id'])

    def test_partial_rule_error_rolls_back_authority_and_outbox_before_handoff(self):
        envelope=self.envelope()
        self.inbox.accept(envelope)
        original=self.engine._send
        def fail_after_queue(*args,**kwargs):
            original(*args,**kwargs)
            raise ValueError('Synthetic validation error after queue write')
        with patch.object(self.engine,'_send',side_effect=fail_after_queue):
            result=self.inbox.process_one()
        self.assertEqual(result['status'],'handoff')
        self.assertEqual(self.queue(),[])
        self.assertFalse(self.contact()['reply_permission_until'])
        self.assertEqual(self.contact()['reply_permission_event_id'],'')
        self.assertEqual(len(self.engine.snapshot()['events']),1)

    def test_requests_faq_answer_is_used_without_starting_an_appointment(self):
        self.approve_appointment_flow()
        answer=next(f['answer'] for f in self.engine.snapshot()['business']['approved_faqs'] if f['topic']=='requests')
        for index,text in enumerate(['How do I book an appointment?','What is your appointment policy?','Are appointment requests automatically confirmed?','How does booking work?']):
            result=self.accept_and_process(self.envelope(text,phone=f'+120255504{index:02d}'))
            self.assertEqual(result['reply'],answer)
        self.assertEqual(self.engine.snapshot()['appointments'],[])
        self.assertTrue(all(c['booking']=={} for c in self.engine.snapshot()['contacts']))

    def test_new_request_recollects_fields_and_identical_new_session_is_not_deduplicated(self):
        self.approve_appointment_flow()
        full='Book deep cleaning in Riverside tomorrow at 10 AM. Contact this number.'
        self.accept_and_process(self.envelope(full))
        self.assertEqual(self.contact()['booking'],{})
        next_request=self.accept_and_process(self.envelope('Book standard cleaning'))
        self.assertIn('Which area',next_request['reply'])
        self.assertFalse(any(k in self.contact()['booking'] for k in ('area','desired_time','contact')))
        self.accept_and_process(self.envelope('another appointment: deep cleaning in Riverside tomorrow at 10 AM. Contact this number.'))
        requests=self.engine.snapshot()['appointments']
        self.assertEqual(len(requests),2)
        self.assertTrue(all(a['status']=='pending_owner_confirmation' for a in requests))

    def test_explicit_new_or_expired_draft_never_supplies_old_area_and_time(self):
        self.approve_appointment_flow()
        self.accept_and_process(self.envelope('Book deep cleaning in Riverside tomorrow at 10 AM.'))
        self.assertTrue(self.contact()['booking'].get('area'))
        another=self.accept_and_process(self.envelope('Book another standard cleaning'))
        self.assertIn('Which area',another['reply'])
        self.assertFalse(any(k in self.contact()['booking'] for k in ('area','desired_time','contact')))
        self.accept_and_process(self.envelope('Riverside'))
        with self.engine.db:
            self.engine.db.execute('UPDATE contacts SET reply_permission_until=? WHERE phone=?',(stamp(now_utc()-timedelta(seconds=1)),self.PHONE))
        fresh=self.accept_and_process(self.envelope('Book deep cleaning'))
        self.assertIn('Which area',fresh['reply'])
        self.assertFalse(any(k in self.contact()['booking'] for k in ('area','desired_time','contact')))

    def test_migration_closes_unproved_window_and_legacy_completed_draft(self):
        self.approve_appointment_flow()
        self.accept_and_process(self.envelope('Book deep cleaning in Riverside tomorrow at 10 AM. Contact this number.'))
        appointment=self.engine.snapshot()['appointments'][0]
        draft={k:appointment[k] for k in ('service','area','desired_time','contact')}
        draft['_in_progress']=True
        import json
        with self.engine.db:
            self.engine.db.execute("UPDATE contacts SET booking=?,reply_permission_event_id='',reply_permission_basis='' WHERE phone=?",(json.dumps(draft),self.PHONE))
            self.engine.db.execute('ALTER TABLE contacts DROP COLUMN reply_permission_event_id')
            self.engine.db.execute('ALTER TABLE contacts DROP COLUMN reply_permission_basis')
        self.engine.close()
        self.engine=Engine(self.db_path,production=True,seed_demo=False,send_policy=self.config.send_policy())
        self.inbox=ServiceInbox(self.engine,self.provider,self.config)
        self.assertEqual(self.contact()['booking'],{})
        self.assertFalse(self.contact()['reply_permission_until'])
        self.assertEqual(len(self.engine.snapshot()['appointments']),1)

    def test_first_evidence_migration_closes_full_unmatched_legacy_draft(self):
        import json
        self.accept_and_process(self.envelope())
        draft={'service':'Deep cleaning','area':'Riverside','desired_time':'tomorrow at 10 AM','contact':self.PHONE,'_in_progress':True}
        with self.engine.db:
            self.engine.db.execute('UPDATE contacts SET booking=? WHERE phone=?',(json.dumps(draft),self.PHONE))
            self.engine.db.execute('ALTER TABLE contacts DROP COLUMN reply_permission_event_id')
            self.engine.db.execute('ALTER TABLE contacts DROP COLUMN reply_permission_basis')
        self.engine.close()
        self.engine=Engine(self.db_path,production=True,seed_demo=False,send_policy=self.config.send_policy())
        self.inbox=ServiceInbox(self.engine,self.provider,self.config)
        self.assertEqual(self.contact()['booking'],{})
        self.assertEqual(self.engine.snapshot()['appointments'],[])
        audit=next(a for a in self.engine.snapshot()['audit'] if a['action']=='legacy_booking_drafts_closed')
        self.assertEqual(audit['details']['completed_drafts_closed'],1)

    def test_startup_restricts_legacy_pending_stop_without_provider_read(self):
        self.accept_and_process(self.envelope())
        envelope=self.envelope('STOP')
        at=stamp()
        with self.engine.db:
            self.engine.db.execute('INSERT INTO provider_inbox(sid,phone,recipient,body,received_at,status,next_attempt_at) VALUES (?,?,?,?,?,?,?)',
                (envelope['sid'],self.PHONE,self.config.phone_number,'STOP',at,'pending',at))
        self.engine.close()
        self.engine=Engine(self.db_path,production=True,seed_demo=False,send_policy=self.config.send_policy())
        self.inbox=ServiceInbox(self.engine,self.provider,self.config)
        self.assertEqual(self.contact()['consent_status'],'revoked')
        self.assertTrue(all(q['status']=='canceled' for q in self.queue()))
        self.assertNotIn(envelope['sid'],self.provider.fetch_calls)

    def test_worker_shutdown_is_bounded_and_never_starts_send_after_closing(self):
        self.inbox.accept(self.envelope())
        entered,release=threading.Event(),threading.Event()
        original=self.provider.fetch_inbound
        def blocked_fetch(sid):
            entered.set()
            release.wait(2)
            return original(sid)
        self.provider.fetch_inbound=blocked_fetch
        worker=ServiceWorker(self.inbox,self.engine.outbox,self.provider)
        worker.start()
        self.assertTrue(entered.wait(1))
        self.assertFalse(worker.stop(timeout=.01))
        release.set()
        self.assertTrue(worker.stop(timeout=2))
        self.assertEqual(self.provider.send_calls,[])
        self.assertEqual(self.queue(),[])

    def enable_synthetic_send(self):
        # This changes only the policy exercised against RecordingProvider.
        # No credential, external transport, or actual Twilio account is used.
        self.config.live_enabled = True
        self.engine.outbox.policy = self.config.send_policy()

    def test_service_database_contains_no_seeded_demo_customer_conversations(self):
        state = self.engine.snapshot()
        self.assertEqual(state['contacts'], [])
        self.assertEqual(state['conversations'], [])
        self.assertEqual(self.queue(), [])

    def test_webhook_acceptance_is_durable_but_does_not_fetch_or_send(self):
        envelope = self.envelope()
        self.inbox.accept(envelope)
        self.assertEqual(self.provider.fetch_calls, [])
        self.assertEqual(self.provider.send_calls, [])
        self.assertEqual(self.queue(), [])
        self.engine.close()
        self.engine = Engine(self.db_path, production=True, seed_demo=False, send_policy=self.config.send_policy())
        self.inbox = ServiceInbox(self.engine, self.provider, self.config)
        self.inbox.process_one()
        self.assertEqual(self.provider.fetch_calls, [envelope['sid']])
        self.assertEqual(len(self.queue()), 1)
        self.assertEqual(self.queue()[0]['status'], 'queued')
        self.assertEqual(self.provider.send_calls, [])

    def test_duplicate_envelope_produces_only_one_queued_reply(self):
        envelope = self.envelope()
        self.inbox.accept(envelope)
        self.inbox.accept(copy.deepcopy(envelope))
        self.inbox.process_one()
        self.inbox.process_one()
        self.assertEqual(self.provider.fetch_calls, [envelope['sid']])
        self.assertEqual(len(self.queue()), 1)
        self.assertEqual(self.provider.send_calls, [])

    def test_stop_acceptance_and_suppression_roll_back_together_then_retry(self):
        self.accept_and_process(self.envelope())
        stop = self.envelope('STOP')
        with patch.object(self.inbox, '_restrict_stop_in_transaction', side_effect=RuntimeError('synthetic process interruption')):
            with self.assertRaises(RuntimeError):
                self.inbox.accept(stop)
        self.assertFalse(self.engine.db.execute('SELECT 1 FROM provider_inbox WHERE sid=?',(stop['sid'],)).fetchone())
        self.assertEqual(self.contact()['consent_status'], 'none')
        self.engine.close()
        self.engine = Engine(self.db_path, production=True, seed_demo=False, send_policy=self.config.send_policy())
        self.inbox = ServiceInbox(self.engine, self.provider, self.config)
        self.inbox.accept(stop)
        self.assertEqual(self.contact()['consent_status'], 'revoked')
        self.assertTrue(all(row['status'] == 'canceled' for row in self.queue()))
        self.assertEqual(self.provider.send_calls, [])
        self.assertNotIn(stop['sid'], self.provider.fetch_calls)

    def test_restart_retries_interrupted_read_only_proof_once_without_duplicate_reply(self):
        envelope = self.envelope()
        self.inbox.accept(envelope)
        self.provider.fetch_error = SystemExit('synthetic worker interruption')
        with self.assertRaises(SystemExit):
            self.inbox.process_one()
        self.engine.close()
        self.provider.fetch_error = None
        self.engine = Engine(self.db_path, production=True, seed_demo=False, send_policy=self.config.send_policy())
        self.inbox = ServiceInbox(self.engine, self.provider, self.config)
        self.inbox.process_one()
        self.inbox.process_one()
        self.assertEqual(self.provider.fetch_calls, [envelope['sid'], envelope['sid']])
        self.assertEqual(len(self.queue()), 1)
        self.assertEqual(self.provider.send_calls, [])

    def test_worker_requires_matching_provider_resource_for_every_identity_field(self):
        mismatches = [('verified', False), ('resource_id', 'SM' + 'f' * 32),
                      ('account_sid', 'AC' + 'f' * 32), ('phone', '+12025550999'),
                      ('to', '+12025550999'), ('body', 'START'), ('direction', 'outbound-api')]
        for field, bad in mismatches:
            with self.subTest(field=field):
                envelope = self.envelope()
                self.provider.proofs[envelope['sid']][field] = bad
                self.accept_and_process(envelope)
                self.assertEqual(self.queue(), [])
                self.assertEqual(self.provider.send_calls, [])

    def test_per_event_rule_validation_error_preserves_inbound_and_handoff_without_freezing_other_contacts(self):
        worker = ServiceWorker(self.inbox, self.engine.outbox, self.provider)
        for index, error_type in enumerate([ValueError, TypeError, KeyError]):
            with self.subTest(error_type=error_type.__name__):
                bad_phone = f'+120255502{index:02d}'
                next_phone = f'+120255503{index:02d}'
                envelope = self.envelope(phone=bad_phone)
                self.inbox.accept(envelope)
                before = len(self.queue())
                with patch.object(self.engine, '_process_event_in_transaction', side_effect=error_type('synthetic-sensitive-rule-error')):
                    result = worker.run_once()
                self.assertEqual(result['inbound']['status'], 'handoff')
                self.assertEqual(len(self.queue()), before)
                self.assertFalse(self.engine.outbox.is_paused())
                self.assertTrue(self.contact(bad_phone)['handoff'])
                conversation = next(c for c in self.engine.snapshot()['conversations'] if c['phone'] == bad_phone)
                inputs = [m for m in conversation['messages'] if m['direction'] == 'inbound']
                self.assertEqual(len(inputs), 1)
                self.assertEqual(inputs[0]['text'], envelope['body'])
                event = next(e for e in self.engine.snapshot()['events'] if e['event_id'] == 'twilio:' + envelope['sid'])
                self.assertEqual(event['status'], 'handoff')
                self.assertNotIn('synthetic-sensitive-rule-error', str(self.engine.snapshot()))
                self.inbox.accept(self.envelope(phone=next_phone))
                worker.run_once()
                self.assertEqual(len(self.queue()), before + 1)
                self.assertFalse(self.engine.outbox.is_paused())
        self.assertEqual(self.provider.send_calls, [])

    def approve_appointment_flow(self):
        business = self.engine.snapshot()['business']
        business['approved_faqs'].append({
            'topic': 'requests', 'answer': 'Collect an appointment request for owner review; availability is not confirmed.',
            'approved': True,
        })
        self.engine.save_business(business)

    def test_multiturn_booking_collects_fields_without_extending_original_service_reply_window(self):
        self.approve_appointment_flow()
        # Words inside an email are contact data, including words also used by
        # human-routing or service-intent rules. Neither can extend permission.
        cases = [(self.PHONE, 'synthetic.person@example.test'),
                 ('+12025550182', 'synthetic.service@example.test')]
        for phone, email in cases:
            with self.subTest(email=email):
                self.accept_and_process(self.envelope('I need a deep cleaning.', phone=phone))
                original_window = self.contact(phone)['reply_permission_until']
                original_inquiry = self.contact(phone)['last_service_inquiry_at']
                self.assertTrue(original_window)
                for field in ['Riverside', 'tomorrow at 10 AM', email]:
                    self.accept_and_process(self.envelope(field, phone=phone))
                    self.assertEqual(self.contact(phone)['reply_permission_until'], original_window)
                    self.assertEqual(self.contact(phone)['last_service_inquiry_at'], original_inquiry)
                requests = [a for a in self.engine.snapshot()['appointments'] if a['phone'] == phone]
                self.assertEqual(len(requests), 1)
                self.assertEqual(requests[0]['status'], 'pending_owner_confirmation')
                self.assertEqual(requests[0]['service'], 'Deep cleaning')
                self.assertEqual(requests[0]['area'], 'Riverside')
                self.assertEqual(requests[0]['desired_time'], 'tomorrow at 10 AM')
                self.assertEqual(requests[0]['contact'], email)
        self.assertTrue(all(row['status'] == 'queued' for row in self.queue()))
        self.assertEqual(self.provider.send_calls, [])

    def test_field_reply_after_original_booking_window_expires_cannot_renew_or_confirm_request(self):
        self.approve_appointment_flow()
        self.accept_and_process(self.envelope('I need a deep cleaning.'))
        original_window = self.contact()['reply_permission_until']
        original_inquiry = self.contact()['last_service_inquiry_at']
        later = datetime.fromisoformat(original_window.replace('Z', '+00:00')) + timedelta(minutes=1)
        before = len(self.queue())
        with patch('reception.engine.now_utc', return_value=later), \
             patch('reception.service.now_utc', return_value=later):
            self.accept_and_process(self.envelope('Riverside', occurred_at=later.isoformat()))
        self.assertEqual(self.contact()['reply_permission_until'], original_window)
        self.assertEqual(self.contact()['last_service_inquiry_at'], original_inquiry)
        self.assertEqual(len(self.queue()), before)
        self.assertEqual(self.engine.snapshot()['appointments'], [])
        self.assertTrue(self.contact()['handoff'])
        self.assertEqual(self.provider.send_calls, [])

    def test_provider_fetch_error_timeout_or_invalid_timestamp_never_queues_a_reply(self):
        for error in [TimeoutError('synthetic-secret-not-for-logs'),
                      SendRejected('provider_inbound_resource_mismatch'),
                      SendUncertain('transport_timeout')]:
            with self.subTest(error=type(error).__name__):
                self.provider.fetch_error = error
                self.accept_and_process(self.envelope())
                self.assertEqual(self.queue(), [])
        self.provider.fetch_error = None
        for stamp in [None, 'not-a-time', datetime.now().isoformat()]:
            with self.subTest(stamp=stamp):
                envelope = self.envelope()
                self.provider.proofs[envelope['sid']]['occurred_at'] = stamp
                self.accept_and_process(envelope)
                self.assertEqual(self.queue(), [])
        self.assertEqual(self.provider.send_calls, [])
        serialized = str(self.engine.snapshot())
        self.assertNotIn('synthetic-secret-not-for-logs', serialized)

    def test_stale_and_future_ordinary_messages_never_authorize_replies(self):
        for delta in [timedelta(hours=-1), timedelta(hours=1)]:
            with self.subTest(delta=delta):
                stamp = (datetime.now(timezone.utc) + delta).isoformat()
                self.accept_and_process(self.envelope(occurred_at=stamp))
                self.assertEqual(self.queue(), [])
        self.assertEqual(self.provider.send_calls, [])

    def test_stop_immediately_revokes_and_cancels_queued_replies_before_any_resource_fetch(self):
        self.accept_and_process(self.envelope())
        self.assertEqual(self.queue()[0]['status'], 'queued')
        calls_before = list(self.provider.fetch_calls)
        stop = self.envelope('STOP', opt_out_type='STOP')
        self.inbox.accept(stop)
        self.assertEqual(self.provider.fetch_calls, calls_before)
        self.assertEqual(self.contact()['consent_status'], 'revoked')
        self.assertTrue(all(row['status'] == 'canceled' for row in self.queue()))
        self.inbox.process_one()
        self.assertEqual(len(self.queue()), 1)
        self.assertEqual(self.provider.send_calls, [])

    def test_stale_stop_still_blocks_and_never_queues_duplicate_application_confirmation(self):
        stop = self.envelope('STOP', occurred_at=(datetime.now(timezone.utc) - timedelta(days=1)).isoformat())
        self.inbox.accept(stop)
        self.inbox.process_one()
        self.assertEqual(self.contact()['consent_status'], 'revoked')
        self.assertEqual(self.queue(), [])
        self.assertEqual(self.provider.send_calls, [])

    def test_start_acceptance_cannot_restore_before_fresh_verified_provider_proof(self):
        self.inbox.accept(self.envelope('STOP'))
        start = self.envelope('START', opt_out_type='START')
        self.inbox.accept(start)
        self.assertEqual(self.contact()['consent_status'], 'revoked')
        self.provider.proofs[start['sid']]['occurred_at'] = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        self.inbox.process_one()
        self.inbox.process_one()
        self.assertEqual(self.contact()['consent_status'], 'revoked')
        self.assertEqual(self.queue(), [])

    def test_dry_run_cannot_send_even_after_valid_resource_processed(self):
        self.accept_and_process(self.envelope())
        result = self.engine.outbox.run_once(self.provider)
        self.assertEqual(result['status'], 'paused')
        self.assertEqual(self.provider.send_calls, [])
        self.assertEqual(self.queue()[0]['status'], 'queued')

    def test_submission_unknown_outcome_is_persisted_and_never_retried_after_restart(self):
        self.accept_and_process(self.envelope())
        self.enable_synthetic_send()
        self.provider.send_error = SendUncertain('transport_timeout')
        result = self.engine.outbox.run_once(self.provider)
        self.assertEqual(result['status'], 'uncertain')
        self.assertEqual(len(self.provider.send_calls), 1)
        queue_id = self.queue()[0]['id']
        self.engine.close()
        self.engine = Engine(self.db_path, production=True, seed_demo=False, send_policy=self.config.send_policy())
        self.inbox = ServiceInbox(self.engine, self.provider, self.config)
        self.engine.outbox.run_once(self.provider)
        self.assertEqual(self.queue()[0]['status'], 'uncertain')
        self.assertEqual(len(self.provider.send_calls), 1)
        reconciliation = self.engine.outbox.reconcile(queue_id, self.provider)
        self.assertEqual(reconciliation['status'], 'blocked')
        self.assertEqual(self.provider.status_calls, [])
        self.assertEqual(len(self.provider.send_calls), 1)

    def test_callback_token_and_provider_sid_bind_one_submitted_item(self):
        self.accept_and_process(self.envelope())
        self.enable_synthetic_send()
        self.engine.outbox.run_once(self.provider)
        row = self.queue()[0]
        callback = self.provider.send_calls[0][2]
        query = parse_qs(urlsplit(callback).query)
        self.assertEqual(int(query['outbox_id'][0]), row['id'])
        token = query['token'][0]
        self.assertNotIn(token, str(self.engine.snapshot()))
        with self.assertRaises(ValueError):
            self.engine.outbox.apply_callback(row['id'], 'wrong-token', row['provider_sid'], 'delivered')
        self.assertEqual(self.queue()[0]['status'], 'accepted')
        self.engine.outbox.apply_callback(row['id'], token, row['provider_sid'], 'delivered')
        self.engine.outbox.apply_callback(row['id'], token, row['provider_sid'], 'sent')
        self.assertEqual(self.queue()[0]['status'], 'delivered')

    def test_worker_start_failure_does_not_join_an_unstarted_thread(self):
        worker = ServiceWorker(self.inbox, self.engine.outbox, self.provider)
        with patch('reception.service.threading.Thread.start', side_effect=RuntimeError('synthetic start failure')):
            with self.assertRaisesRegex(RuntimeError, 'synthetic start failure'):
                worker.start()
        self.assertIsNone(worker.thread)
        self.assertTrue(worker.stop(timeout=0.1))
        self.assertTrue(worker.stop_event.is_set())
        self.assertEqual(self.engine.db.execute('SELECT 1').fetchone()[0], 1)

    def test_interrupted_worker_start_reports_uncertain_drain_without_join_error(self):
        worker = ServiceWorker(self.inbox, self.engine.outbox, self.provider)
        with patch('reception.service.threading.Thread.start', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                worker.start()
        self.assertIsNotNone(worker.thread)
        self.assertFalse(worker.stop(timeout=0.1))
        self.assertEqual(self.engine.db.execute('SELECT 1').fetchone()[0], 1)


if __name__ == '__main__':
    unittest.main()
