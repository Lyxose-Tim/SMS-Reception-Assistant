"""One-operator authentication; sessions expire and restart invalidates them."""
import base64
import hashlib
import ipaddress
import re
import secrets
import threading
import time
from http.cookies import CookieError, SimpleCookie


def _password_parts(encoded):
    if not isinstance(encoded, str) or len(encoded) > 200:
        return None
    parts = encoded.split('$')
    if len(parts) != 6 or parts[:4] != ['scrypt', '16384', '8', '1']:
        return None
    decoded = []
    try:
        for value, length in zip(parts[4:], (16, 64)):
            raw = base64.b64decode(value.encode('ascii'), altchars=b'-_', validate=True)
            if len(raw) != length or base64.urlsafe_b64encode(raw).decode('ascii') != value:
                return None
            decoded.append(raw)
    except (ValueError, UnicodeError):
        return None
    return tuple(decoded)


def valid_password_hash(encoded):
    return _password_parts(encoded) is not None


def login_peer(peer, forwarded_values=(), trusted_proxy_cidrs=()):
    """Only an explicitly trusted single-hop proxy may supply the client IP."""
    try:
        address = ipaddress.ip_address(peer)
        trusted = any(address in ipaddress.ip_network(cidr, strict=True) for cidr in trusted_proxy_cidrs)
        if not trusted:
            return str(address)
        if len(forwarded_values) != 1 or ',' in forwarded_values[0]:
            raise ValueError()
        value = forwarded_values[0]
        if value != value.strip() or '%' in value:
            raise ValueError()
        return str(ipaddress.ip_address(value))
    except (ValueError, TypeError, AttributeError):
        raise ValueError('invalid_proxy_client_address') from None


def hash_password(password, salt=None):
    if not isinstance(password,str) or len(password)<12 or len(password)>256:
        raise ValueError('Use a password between 12 and 256 characters')
    salt=secrets.token_bytes(16) if salt is None else salt
    if not isinstance(salt, bytes) or len(salt) != 16:
        raise ValueError('Password salt must be exactly 16 bytes')
    digest=hashlib.scrypt(password.encode(),salt=salt,n=16384,r=8,p=1,maxmem=64*1024*1024)
    return 'scrypt$16384$8$1$'+base64.urlsafe_b64encode(salt).decode()+'$'+base64.urlsafe_b64encode(digest).decode()


def verify_password(password, encoded):
    parts = _password_parts(encoded)
    if not parts or not isinstance(password, str) or not 12 <= len(password) <= 256:
        return False
    try:
        result=hashlib.scrypt(password.encode(),salt=parts[0],n=16384,r=8,p=1,maxmem=64*1024*1024)
        return secrets.compare_digest(result,parts[1])
    except (ValueError,TypeError,UnicodeError):
        return False


class AuthSessions:
    def __init__(self,password_hash,ttl=3600):
        self.password_hash=password_hash
        self.ttl=ttl
        self.sessions={}
        self.failures={}
        self.lock=threading.Lock()

    def login(self,password,peer):
        with self.lock:
            now=time.time()
            recent_failures = {}
            for failed_peer, timestamps in self.failures.items():
                recent = [t for t in timestamps if t > now - 300]
                if recent:
                    recent_failures[failed_peer] = recent
            self.failures = recent_failures
            attempts=self.failures.get(peer,[])
            if len(attempts)>=5: return None,'login_rate_limited'
            if not verify_password(password,self.password_hash):
                attempts.append(now)
                self.failures[peer]=attempts
                return None,'invalid_credentials'
            self.sessions={k:v for k,v in self.sessions.items() if v['expires']>now}
            if len(self.sessions)>=100: return None,'session_limit'
            sid=secrets.token_urlsafe(32)
            self.sessions[sid]={'token':secrets.token_urlsafe(32),'expires':now+self.ttl}
            self.failures.pop(peer,None)
            return (sid,self.sessions[sid]['token']),''

    def get(self,cookie):
        if (not isinstance(cookie, str) or len(cookie) > 8192 or
                any(ord(c) < 32 or ord(c) == 127 for c in cookie) or
                len(re.findall(r'(?:^|;)\s*reception_session\s*=', cookie)) != 1):
            return None
        pair = re.compile(r"[!#$%&'*+\-.^_`|~0-9A-Za-z]+=(?:[\x21\x23-\x2B\x2D-\x3A\x3C-\x5B\x5D-\x7E]*|\"[\x21\x23-\x2B\x2D-\x3A\x3C-\x5B\x5D-\x7E]*\")")
        if any(not pair.fullmatch(part.strip()) for part in cookie.split(';')):
            return None
        try:
            parsed=SimpleCookie(); parsed.load(cookie or '')
            sid=parsed['reception_session'].value
            if not re.fullmatch(r'[A-Za-z0-9_-]{43}', sid): return None
        except (CookieError,KeyError,ValueError,TypeError): return None
        with self.lock:
            record=self.sessions.get(sid)
            if not record or record['expires']<=time.time():
                self.sessions.pop(sid,None); return None
            return {'sid':sid,**record}

    def logout(self,sid):
        with self.lock: self.sessions.pop(sid,None)
