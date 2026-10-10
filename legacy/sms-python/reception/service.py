"""Durable signed inbox. Provider GET verification happens after empty TwiML.

STOP restricts immediately; no external action is taken by accept(). Other
business processing waits for an authoritative Message resource and timestamp.
"""
from datetime import timedelta
import hashlib
import json
import threading
from pathlib import Path
from .engine import STOP_WORDS, parse_time, now_utc, stamp


class ServiceInbox:
    def __init__(self, engine, provider, config):
        self.engine,self.provider,self.config=engine,provider,config
        self.worker_lock=threading.Lock()
        with engine._lock,engine.db:
            engine.db.executescript((Path(__file__).resolve().parents[1]/'migrations/002_service_inbox.sql').read_text())
            engine.db.execute("UPDATE provider_inbox SET status='pending' WHERE status='verifying'")
            pending=[dict(x) for x in engine.db.execute("SELECT * FROM provider_inbox WHERE status='pending'")]
        # A crash between durable acceptance and immediate suppression cannot
        # cause a pending STOP to be overlooked on restart.
        for row in pending:
            if self._is_stop(row): self._restrict_stop(row)

    @staticmethod
    def _is_stop(row):
        return row.get('opt_out_type')=='STOP' or row['body'].strip().upper() in STOP_WORDS

    def _event(self,row,at,proof=False):
        recognized_start=(row['body'].strip().upper() in {'START','UNSTOP'} and
            (self.config.optout_profile=='standard' or row.get('opt_out_type')=='START'))
        return {'event_id':'twilio:'+row['sid'],'kind':'sms','origin':'twilio','verified':True,
                'phone':row['phone'],'text':row['body'] or row.get('opt_out_type',''),'occurred_at':at,'provider_sid':row['sid'],
                'provider_managed_optout':True,'suppress_confirmation':True,
                'opt_out_type':row.get('opt_out_type',''),'provider_fresh':proof,
                'provider_keyword_verified':proof and recognized_start and self.config.optout_reviewed}

    def _restrict_stop(self,row):
        with self.engine.contact_barrier(row['phone']),self.engine._lock,self.engine.db:
            return self._restrict_stop_in_transaction(row)

    def _restrict_stop_in_transaction(self,row):
        event=self._event(row,row['received_at'])
        event['opt_out_type']='STOP'
        result=self.engine._process_event_in_transaction(self.engine._validate_event(event))
        self.engine.db.execute("UPDATE provider_inbox SET status='processed',result=?,error_code='' WHERE sid=?",
                               (json.dumps(result),row['sid']))
        return result

    def accept(self,envelope):
        row={'sid':envelope['sid'],'phone':envelope['from'],'recipient':envelope['to'],
             'body':envelope['body'],'opt_out_type':envelope.get('opt_out_type',''),
             'received_at':stamp()}
        sid_hash=hashlib.sha256(row['sid'].encode()).hexdigest()
        event_hash=hashlib.sha256(('twilio:'+row['sid']).encode()).hexdigest()
        with self.engine.contact_barrier(row['phone']),self.engine._lock,self.engine.db:
            if self.engine.closing:
                raise RuntimeError('service_closing')
            if (self.engine.db.execute('SELECT 1 FROM provider_inbox WHERE sid=?',(row['sid'],)).fetchone() or
                self.engine.db.execute('SELECT 1 FROM inbox_tombstones WHERE sid_hash=?',(sid_hash,)).fetchone() or
                self.engine.db.execute('SELECT 1 FROM privacy_dedup WHERE event_hash=?',(event_hash,)).fetchone()):
                return {'status':'duplicate','reason':'inbound_already_received'}
            self.engine.db.execute('''INSERT INTO provider_inbox
                (sid,phone,recipient,body,opt_out_type,received_at,status,next_attempt_at)
                VALUES (?,?,?,?,?,?,'pending',?)''',
                (row['sid'],row['phone'],row['recipient'],row['body'],row['opt_out_type'],row['received_at'],row['received_at']))
            if self._is_stop(row): self._restrict_stop_in_transaction(row)
        return {'status':'accepted','reason':'durable_inbound_saved'}

    def _proof(self,row,proof):
        expected={'resource_id':row['sid'],'account_sid':self.config.account_sid,
                  'phone':row['phone'],'to':row['recipient'],'body':row['body'],'direction':'inbound'}
        if not isinstance(proof,dict) or proof.get('verified') is not True or any(proof.get(k)!=v for k,v in expected.items()):
            return 'provider_resource_mismatch'
        try: age=(now_utc()-parse_time(proof['occurred_at'])).total_seconds()
        except (KeyError,ValueError,TypeError): return 'provider_timestamp_invalid'
        if age < -30 or age>self.config.inbound_max_age_seconds: return 'provider_event_expired'
        return ''

    def process_one(self):
        if self.engine.closing: return {'status':'closing'}
        if not self.worker_lock.acquire(blocking=False): return {'status':'busy'}
        try:
            with self.engine._lock,self.engine.db:
                record=self.engine.db.execute("SELECT * FROM provider_inbox WHERE status='pending' AND next_attempt_at<=? ORDER BY received_at LIMIT 1",(stamp(),)).fetchone()
                if not record: return {'status':'idle'}
                row=dict(record)
                self.engine.db.execute("UPDATE provider_inbox SET status='verifying',attempts=attempts+1 WHERE sid=?",(row['sid'],))
            if self._is_stop(row): return self._restrict_stop(row)
            try:
                if self.provider is None: raise RuntimeError('provider_unavailable')
                proof=self.provider.fetch_inbound(row['sid'])
            except Exception:
                attempts=row['attempts']+1
                status='pending' if attempts<3 else 'verification_failed'
                with self.engine._lock,self.engine.db:
                    self.engine.db.execute("UPDATE provider_inbox SET status=?,error_code='provider_verification_unavailable',next_attempt_at=? WHERE sid=?",
                        (status,stamp(now_utc()+timedelta(seconds=30*attempts)),row['sid']))
                    self.engine._audit(row['phone'],'twilio:'+row['sid'],'inbound_verification_deferred',{'attempt':attempts,'status':status})
                return {'status':status,'reason':'provider_verification_unavailable'}
            error=self._proof(row,proof)
            if error:
                with self.engine._lock,self.engine.db:
                    self.engine.db.execute("UPDATE provider_inbox SET status='rejected',error_code=? WHERE sid=?",(error,row['sid']))
                    self.engine._audit(None,'twilio:'+row['sid'],'inbound_proof_rejected',{'reason':error})
                return {'status':'rejected','reason':error}
            try:
                # Provider proof, service authority and outbound intent share one
                # commit. Engine's internal helper never commits this transaction.
                with self.engine.contact_barrier(row['phone']),self.engine._lock,self.engine.db:
                    current=self.engine.db.execute('SELECT status FROM provider_inbox WHERE sid=?',(row['sid'],)).fetchone()
                    if not current or current['status']!='verifying':
                        return {'status':'ignored','reason':'source_no_longer_pending'}
                    if self.engine.closing:
                        self.engine.db.execute("UPDATE provider_inbox SET status='pending' WHERE sid=?",(row['sid'],))
                        return {'status':'closing'}
                    self.engine.db.execute("UPDATE provider_inbox SET status='processed',resource_at=?,error_code='' WHERE sid=?",(stamp(parse_time(proof['occurred_at'])),row['sid']))
                    event=self.engine._validate_event(self._event(row,proof['occurred_at'],proof=True))
                    result=self.engine._process_event_in_transaction(event)
                    self.engine.db.execute("UPDATE provider_inbox SET result=? WHERE sid=?",(json.dumps(result),row['sid']))
            except (ValueError,TypeError,KeyError):
                # A bad individual message/configuration must not freeze every
                # customer's dispatch. Preserve the proven input for an operator.
                eid='twilio:'+row['sid']
                with self.engine.contact_barrier(row['phone']),self.engine._lock,self.engine.db:
                    if not self.engine.db.execute('SELECT 1 FROM provider_inbox WHERE sid=?',(row['sid'],)).fetchone():
                        return {'status':'ignored','reason':'source_unavailable'}
                    previous=self.engine.db.execute('SELECT result FROM events WHERE event_id=?',(eid,)).fetchone()
                    if previous:
                        result=json.loads(previous['result'])
                    else:
                        self.engine._contact(row['phone'])
                        inbound_id=self.engine._message(row['phone'],row['body'].strip(),'inbound','received',eid,at=proof['occurred_at'])
                        self.engine.db.execute('UPDATE messages SET provider_sid=? WHERE id=?',(row['sid'],inbound_id))
                        self.engine.db.execute('UPDATE contacts SET handoff=1,last_activity=? WHERE phone=?',(stamp(),row['phone']))
                        result={'status':'handoff','reason':'inbound_rule_validation_failed','sent':False}
                        self.engine.db.execute('INSERT INTO events(event_id,kind,phone,occurred_at,received_at,origin,status,result) VALUES (?,?,?,?,?,?,?,?)',
                            (eid,'sms',row['phone'],proof['occurred_at'],stamp(),'twilio','handoff',json.dumps(result)))
                        self.engine._audit(row['phone'],eid,'inbound_validation_handoff',{'reason':'rule_validation','no_send':True})
                    self.engine.db.execute("UPDATE provider_inbox SET status='processed',resource_at=?,result=?,error_code='' WHERE sid=?",
                        (stamp(parse_time(proof['occurred_at'])),json.dumps(result),row['sid']))
            return result
        finally: self.worker_lock.release()

    def snapshot(self):
        with self.engine._lock:
            counts={r['status']:r['n'] for r in self.engine.db.execute('SELECT status,count(*) n FROM provider_inbox GROUP BY status')}
            errors=[{'sid_masked':r['sid'][:2]+'…'+r['sid'][-4:],'status':r['status'],'error_code':r['error_code'],'received_at':r['received_at']}
                    for r in self.engine.db.execute("SELECT sid,status,error_code,received_at FROM provider_inbox WHERE status IN ('verification_failed','rejected') ORDER BY received_at DESC LIMIT 20")]
        return {'counts':counts,'errors':errors}


class ServiceWorker:
    def __init__(self,inbox,outbox,provider):
        self.inbox,self.outbox,self.provider=inbox,outbox,provider
        self.stop_event=threading.Event()
        self.thread=None
        self.closing=False

    def run_once(self):
        if self.closing or self.inbox.engine.closing:
            return {'inbound':{'status':'closing'},'outbound':{'status':'closing'}}
        incoming=self.inbox.process_one()
        if self.closing or self.inbox.engine.closing:
            return {'inbound':incoming,'outbound':{'status':'closing'}}
        outgoing=self.outbox.run_once(self.provider)
        return {'inbound':incoming,'outbound':outgoing}

    def start(self):
        if self.closing or self.inbox.engine.closing:
            raise RuntimeError('service_closing')
        if self.thread and self.thread.is_alive():
            return
        def run():
            while not self.stop_event.is_set():
                try: self.run_once()
                except Exception:
                    # Unknown worker errors pause dispatch, without exposing an
                    # exception containing provider parameters or credentials.
                    self.outbox.pause(True)
                self.stop_event.wait(0.5)
        thread=threading.Thread(target=run,name='service-worker',daemon=True)
        self.thread=thread
        try:
            thread.start()
        except BaseException as exc:
            self.closing=True
            self.stop_event.set()
            # Ordinary start failures create no native thread. An interruption
            # may leave startup in flight; retain it and fail closed on drain.
            if isinstance(exc,Exception) and thread.ident is None:
                self.thread=None
            raise

    def stop(self,timeout=16):
        self.closing=True
        self.inbox.engine.closing=True
        self.stop_event.set()
        if self.thread:
            try:
                self.thread.join(timeout=timeout)
            except RuntimeError:
                # Never turn an unstarted/uncertain thread into a second cleanup
                # exception or report that its resources have been drained.
                return False
        return self.thread is None or not self.thread.is_alive()
