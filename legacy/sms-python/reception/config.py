"""Server-only configuration. No credentials are returned to the browser."""
from dataclasses import dataclass, field
import ipaddress
import os
from urllib.parse import urlsplit


def boolean(value):
    if str(value).lower() not in {'true', 'false', '1', '0', ''}:
        raise ValueError('Boolean configuration must be true or false')
    return str(value).lower() in {'true', '1'}


@dataclass
class ServiceConfig:
    mode: str = 'service'
    admin_password_hash: str = field(default='', repr=False)
    privacy_hmac_key: str = field(default='', repr=False)
    admin_origin: str = ''
    public_base_url: str = ''
    account_sid: str = field(default='', repr=False)
    auth_token: str = field(default='', repr=False)
    phone_number: str = ''
    api_key_sid: str = field(default='', repr=False)
    api_key_secret: str = field(default='', repr=False)
    live_enabled: bool = False
    allowed_recipients: tuple = ()
    recipient_policy: str = 'test_allowlist'
    inbound_service_approval_recorded: bool = False
    trusted_proxy_cidrs: tuple = ()
    max_messages_per_minute: int = 5
    max_messages_per_day: int = 40
    max_segments_per_message: int = 3
    max_daily_segments: int = 80
    reply_window_hours: int = 24
    request_timeout_seconds: int = 5
    session_ttl_seconds: int = 3600
    retention_days: int = 30
    emergency_stop_file: str = ''
    optout_profile: str = 'standard'
    optout_reviewed: bool = False
    readiness_approved: bool = False
    send_approval_recorded: bool = False
    inbound_max_age_seconds: int = 900

    @property
    def sender_number(self):
        return self.phone_number

    @classmethod
    def from_env(cls, env=None):
        e = os.environ if env is None else env
        mapping = {
            'mode':'APP_MODE', 'admin_password_hash':'ADMIN_PASSWORD_HASH', 'privacy_hmac_key':'PRIVACY_HMAC_KEY',
            'admin_origin':'ADMIN_ORIGIN', 'public_base_url':'TWILIO_PUBLIC_BASE_URL',
            'account_sid':'TWILIO_ACCOUNT_SID', 'auth_token':'TWILIO_AUTH_TOKEN',
            'phone_number':'TWILIO_PHONE_NUMBER', 'api_key_sid':'TWILIO_API_KEY_SID',
            'api_key_secret':'TWILIO_API_KEY_SECRET', 'emergency_stop_file':'EMERGENCY_STOP_FILE',
            'optout_profile':'TWILIO_OPT_OUT_PROFILE', 'recipient_policy':'RECIPIENT_POLICY'}
        kwargs={k:e[v] for k,v in mapping.items() if v in e}
        for key, name in {'live_enabled':'ENABLE_REAL_SEND','optout_reviewed':'TWILIO_OPT_OUT_REVIEWED',
                          'readiness_approved':'CLIENT_READINESS_APPROVED',
                          'send_approval_recorded':'TEST_SEND_APPROVAL_RECORDED',
                          'inbound_service_approval_recorded':'INBOUND_SERVICE_APPROVAL_RECORDED'}.items():
            kwargs[key]=boolean(e.get(name,'false'))
        for key in ('max_messages_per_minute','max_messages_per_day','max_segments_per_message',
                    'max_daily_segments','reply_window_hours','request_timeout_seconds',
                    'session_ttl_seconds','retention_days','inbound_max_age_seconds'):
            if key.upper() in e:
                try: kwargs[key]=int(e[key.upper()])
                except ValueError: raise ValueError('Numeric configuration must be an integer') from None
        kwargs['mode']=e.get('APP_MODE','demo')
        kwargs['allowed_recipients']=tuple(x.strip() for x in e.get('TEST_RECIPIENT_ALLOWLIST','').split(',') if x.strip())
        kwargs['trusted_proxy_cidrs']=tuple(x.strip() for x in e.get('TRUSTED_PROXY_CIDRS','').split(',') if x.strip())
        cfg=cls(**kwargs)
        cfg.validate()
        return cfg

    def validate(self):
        from .auth import valid_password_hash
        for name in ('live_enabled', 'optout_reviewed', 'readiness_approved',
                     'send_approval_recorded', 'inbound_service_approval_recorded'):
            if type(getattr(self, name)) is not bool:
                raise ValueError('Configuration approval flags must be boolean')
        if self.mode not in {'demo','service'}:
            raise ValueError('APP_MODE must be demo or service')
        if self.mode=='demo' and self.live_enabled:
            raise ValueError('Real sending is unavailable in demonstration mode')
        if self.mode=='service' and not valid_password_hash(self.admin_password_hash):
            raise ValueError('Service mode requires ADMIN_PASSWORD_HASH; use manage.py password-hash securely')
        if self.public_base_url:
            url=urlsplit(self.public_base_url)
            if url.scheme!='https' or not url.hostname or url.username or url.password or url.query or url.fragment or url.path not in ('','/'):
                raise ValueError('TWILIO_PUBLIC_BASE_URL must be a canonical HTTPS origin without credentials, path or query')
        if self.admin_origin:
            url=urlsplit(self.admin_origin)
            local=url.hostname in ('127.0.0.1','localhost')
            if url.scheme!='https' and not (url.scheme=='http' and local and not self.live_enabled):
                raise ValueError('ADMIN_ORIGIN requires HTTPS except local dry-run')
            if not url.hostname or url.username or url.password or url.path not in ('','/') or url.query or url.fragment:
                raise ValueError('ADMIN_ORIGIN must be a bare origin')
        if self.optout_profile not in {'standard','advanced'}:
            raise ValueError('Unknown Twilio opt-out profile')
        if self.recipient_policy not in {'test_allowlist','inbound_service'}:
            raise ValueError('RECIPIENT_POLICY must be test_allowlist or inbound_service')
        if not isinstance(self.trusted_proxy_cidrs, tuple):
            raise ValueError('TRUSTED_PROXY_CIDRS must contain explicit IP networks')
        try:
            for cidr in self.trusted_proxy_cidrs:
                if not isinstance(cidr, str) or '/' not in cidr:
                    raise ValueError()
                ipaddress.ip_network(cidr, strict=True)
        except ValueError:
            raise ValueError('TRUSTED_PROXY_CIDRS must contain valid canonical IP networks') from None
        bounds={'max_messages_per_minute':(1,30),'max_messages_per_day':(1,1000),
                'max_segments_per_message':(1,10),'max_daily_segments':(1,2000),
                'reply_window_hours':(1,24),'request_timeout_seconds':(1,15),
                'session_ttl_seconds':(60,86400),'retention_days':(1,365),
                'inbound_max_age_seconds':(60,3600)}
        for name,(low,high) in bounds.items():
            value=getattr(self,name)
            if type(value) is not int or not low<=value<=high:
                raise ValueError('Configuration limit out of range: '+name)
        import re
        if not isinstance(self.allowed_recipients, tuple) or any(not isinstance(x, str) or not re.fullmatch(r'\+[1-9]\d{7,14}',x) for x in self.allowed_recipients):
            raise ValueError('Allowlist requires explicit E.164 recipients')
        if self.live_enabled:
            if len(self.privacy_hmac_key)<32:
                raise ValueError('Live sending requires a stable server-only PRIVACY_HMAC_KEY of at least 32 characters')
            if not all((self.account_sid,self.auth_token,self.phone_number,self.public_base_url,self.admin_origin)):
                raise ValueError('Live sending requires complete server-only Twilio and HTTPS configuration')
            if self.recipient_policy == 'test_allowlist' and not self.allowed_recipients:
                raise ValueError('Live sending requires a test-recipient allowlist')
            approved = (self.send_approval_recorded if self.recipient_policy == 'test_allowlist'
                        else self.inbound_service_approval_recorded)
            if not all((self.optout_reviewed,self.readiness_approved,approved)):
                raise ValueError('Live sending requires recorded number/registration, opt-out and sending approvals')
        return self

    def send_policy(self):
        from .outbox import SendPolicy
        names=('live_enabled','allowed_recipients','max_messages_per_minute','max_messages_per_day',
               'max_segments_per_message','max_daily_segments','reply_window_hours',
               'emergency_stop_file','optout_profile','optout_reviewed','public_base_url',
               'recipient_policy','inbound_service_approval_recorded')
        return SendPolicy(sender_number=self.phone_number,**{k:getattr(self,k) for k in names})
