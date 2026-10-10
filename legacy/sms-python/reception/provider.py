"""Official-SDK Twilio REST transport and signed webhook envelopes.

This module does not enable sending. The persistent outbox is the only caller
of ``send`` and owns consent, operator takeover, limits and emergency stops.
POST retries are disabled: a timeout or ambiguous response never authorizes a
second submission. Tests inject synthetic clients; importing this module makes
no network request and does not require an installed Twilio SDK.
"""
from datetime import datetime, timezone
import logging
import re
from urllib.parse import urlsplit


_ACCOUNT = re.compile(r"AC[0-9a-fA-F]{32}\Z")
_MESSAGE = re.compile(r"SM[0-9a-fA-F]{32}\Z")
_KEY = re.compile(r"SK[0-9a-fA-F]{32}\Z")
_E164 = re.compile(r"\+[1-9][0-9]{7,14}\Z")
SMS_STATUSES = frozenset({"accepted", "queued", "sending", "sent", "delivered",
                          "undelivered", "failed", "canceled", "scheduled"})


class SendRejected(Exception):
    """A definite local/4xx rejection; only an explicit 429 is retryable."""
    def __init__(self, code, retryable=False, http_status=None):
        self.code = _safe_code(code)
        self.retryable = bool(retryable)
        self.http_status = http_status if isinstance(http_status, int) else None
        super().__init__(self.code)


class SendUncertain(Exception):
    """Submission may have happened. Reconcile; never automatically resubmit."""
    def __init__(self, code):
        self.code = _safe_code(code)
        self.retryable = False
        super().__init__(self.code)


class WebhookRejected(ValueError):
    def __init__(self, reason, status_code=403):
        self.reason = self.code = _safe_code(reason)
        self.status_code = self.status = int(status_code)
        super().__init__(self.reason)


def _safe_code(code):
    # Never copy a provider exception message, URI, body, credential or phone.
    if isinstance(code, int) and not isinstance(code, bool):
        return str(code)
    if isinstance(code, str) and re.fullmatch(r"[a-z_][a-z0-9_]{0,63}|[0-9]{3,8}", code):
        return code
    return "provider_error"


def normalize_phone(value):
    """Accept an explicit international number; do not guess a country code."""
    if not isinstance(value, str):
        raise ValueError("invalid_phone")
    cleaned = re.sub(r"[\s().-]", "", value)
    if not _E164.fullmatch(cleaned):
        raise ValueError("international_e164_phone_required")
    return cleaned


def _canonical_base(value):
    if not isinstance(value, str):
        raise ValueError("canonical_https_url_required")
    parts = urlsplit(value)
    if (parts.scheme != "https" or not parts.hostname or parts.username or parts.password
            or parts.query or parts.fragment or any(char.isspace() for char in value)
            or "\\" in value):
        raise ValueError("canonical_https_url_required")
    try:
        parts.port
    except ValueError:
        raise ValueError("canonical_https_url_required") from None
    return value.rstrip("/")


def _attribute(resource, key, default=None):
    return resource.get(key, default) if isinstance(resource, dict) else getattr(resource, key, default)


def _error_code(value):
    if value in (None, ""):
        return None
    if isinstance(value, bool) or not re.fullmatch(r"[0-9]{1,8}", str(value)):
        return None
    return str(value)


class TwilioRESTProvider:
    def __init__(self, *, account_sid, auth_token, phone_number, public_base_url,
                 api_key_sid="", api_key_secret="", timeout_seconds=5, client=None):
        if not isinstance(account_sid, str) or not _ACCOUNT.fullmatch(account_sid):
            raise ValueError("invalid_account_sid")
        self.account_sid = account_sid
        self.phone_number = normalize_phone(phone_number)
        self.public_base_url = _canonical_base(public_base_url)
        try:
            timeout = float(timeout_seconds)
        except (ValueError, TypeError):
            raise ValueError("invalid_provider_timeout") from None
        if not 0 < timeout <= 30:
            raise ValueError("invalid_provider_timeout")
        if bool(api_key_sid) != bool(api_key_secret):
            raise ValueError("api_key_pair_required")
        if api_key_sid and not _KEY.fullmatch(api_key_sid):
            raise ValueError("invalid_api_key_sid")
        if not api_key_sid and not auth_token:
            raise ValueError("provider_credentials_required")
        self.timeout_seconds = timeout
        self.http_client = None
        if client is None:
            try:
                from twilio.http.http_client import TwilioHttpClient
                from twilio.rest import Client
            except ImportError:
                raise ValueError("official_twilio_sdk_required") from None
            # Use an isolated disabled logger. The SDK's request debug logging
            # contains phone/body/query data and must never reach application logs.
            sdk_logger = logging.Logger("harbor_twilio_transport_disabled")
            sdk_logger.disabled = True
            self.http_client = TwilioHttpClient(timeout=timeout, max_retries=0, logger=sdk_logger)
            username = api_key_sid or account_sid
            password = api_key_secret or auth_token
            client = Client(username, password, account_sid=account_sid,
                            http_client=self.http_client)
        self.client = client

    @staticmethod
    def _raise_provider_error(exc):
        try:
            from twilio.base.exceptions import TwilioRestException
        except ImportError:
            TwilioRestException = ()
        if TwilioRestException and isinstance(exc, TwilioRestException):
            status = getattr(exc, "status", None)
            code = getattr(exc, "code", None) or ("http_" + str(status))
            if isinstance(status, int) and 400 <= status < 500:
                raise SendRejected(code, retryable=status == 429, http_status=status) from None
            raise SendUncertain(code if isinstance(status, int) else "ambiguous_provider_response") from None
        # Socket/read/connect errors and any unknown exception after create may
        # have crossed the submission boundary. Do not infer rejection from text.
        try:
            from requests.exceptions import Timeout, ConnectionError
            if isinstance(exc, Timeout):
                raise SendUncertain("transport_timeout") from None
            if isinstance(exc, ConnectionError):
                raise SendUncertain("transport_connection_error") from None
        except ImportError:
            pass
        if isinstance(exc, TimeoutError):
            raise SendUncertain("transport_timeout") from None
        raise SendUncertain("ambiguous_provider_response") from None

    def send(self, to, body, callback_url):
        try:
            recipient = normalize_phone(to)
            if not isinstance(callback_url, str) or urlsplit(callback_url).fragment:
                raise ValueError()
            callback = _canonical_base(callback_url.split("?", 1)[0])
        except (ValueError, TypeError, AttributeError):
            raise SendRejected("invalid_send_parameters") from None
        base = urlsplit(self.public_base_url)
        target = urlsplit(callback)
        prefix = base.path.rstrip("/")
        if (target.scheme, target.netloc) != (base.scheme, base.netloc) or (prefix and not target.path.startswith(prefix + "/")):
            raise SendRejected("callback_origin_mismatch")
        if not isinstance(body, str) or not body.strip() or len(body) > 1600:
            raise SendRejected("invalid_message_body")
        try:
            result = self.client.messages.create(to=recipient, from_=self.phone_number,
                                                 body=body, status_callback=callback_url)
        except Exception as exc:
            self._raise_provider_error(exc)
        sid = _attribute(result, "sid")
        status = _attribute(result, "status")
        if not isinstance(sid, str) or not _MESSAGE.fullmatch(sid) or status not in SMS_STATUSES:
            raise SendUncertain("invalid_provider_create_response")
        return {"sid": sid, "status": status,
                "error_code": _error_code(_attribute(result, "error_code")),
                "num_segments": _attribute(result, "num_segments")}

    def _fetch_message(self, sid):
        if not isinstance(sid, str) or not _MESSAGE.fullmatch(sid):
            raise SendRejected("invalid_message_sid")
        try:
            return self.client.messages(sid).fetch()
        except Exception as exc:
            self._raise_provider_error(exc)

    def fetch_inbound(self, sid):
        result = self._fetch_message(sid)
        phone = _attribute(result, "from_")
        body = _attribute(result, "body")
        occurred = _attribute(result, "date_created")
        if (not isinstance(occurred, datetime) or occurred.tzinfo is None
                or _attribute(result, "sid") != sid
                or _attribute(result, "account_sid") != self.account_sid
                or _attribute(result, "to") != self.phone_number
                or _attribute(result, "direction") != "inbound"
                or not isinstance(phone, str) or not _E164.fullmatch(phone)
                or not isinstance(body, str) or len(body) > 1600
                or str(_attribute(result, "num_media", "0")) != "0"):
            raise SendRejected("provider_inbound_resource_mismatch")
        return {"verified": True, "resource_id": sid, "account_sid": self.account_sid,
                "phone": phone, "to": self.phone_number, "body": body,
                "direction": "inbound", "occurred_at": occurred.astimezone(timezone.utc).isoformat()}

    def fetch_status(self, sid):
        result = self._fetch_message(sid)
        recipient = _attribute(result, "to")
        status = _attribute(result, "status")
        if (_attribute(result, "sid") != sid or _attribute(result, "account_sid") != self.account_sid
                or _attribute(result, "from_") != self.phone_number
                or _attribute(result, "direction") != "outbound-api"
                or not isinstance(recipient, str) or not _E164.fullmatch(recipient)
                or status not in SMS_STATUSES):
            raise SendRejected("provider_outbound_resource_mismatch")
        return {"sid": sid, "status": status, "error_code": _error_code(_attribute(result, "error_code")),
                "from": self.phone_number, "to": recipient, "account_sid": self.account_sid,
                "body": _attribute(result, "body", ""), "verified": True,
                "num_segments": _attribute(result, "num_segments")}

    def lookup(self, sid):
        """Reconcile one known SID; never search ambiguous sends by body/time."""
        return self.fetch_status(sid)

    def read_only_readiness(self):
        try:
            account = self.client.api.accounts(self.account_sid).fetch()
            numbers = self.client.incoming_phone_numbers.list(phone_number=self.phone_number, limit=2)
        except Exception as exc:
            self._raise_provider_error(exc)
        if _attribute(account, "sid") != self.account_sid:
            raise SendRejected("readiness_account_mismatch")
        owned = [number for number in numbers
                 if _attribute(number, "phone_number") == self.phone_number
                 and _attribute(number, "account_sid") == self.account_sid]
        number = owned[0] if len(owned) == 1 else None
        capabilities = _attribute(number, "capabilities", {}) or {}
        account_type = _attribute(account, "type", "unknown")
        return {"account_type": account_type, "account_status": _attribute(account, "status", "unknown"),
                "is_trial": str(account_type).lower() == "trial", "owned_number": number is not None,
                "sms_capable": capabilities.get("sms") is True,
                "country": _attribute(number, "iso_country"),
                "geography_verified": bool(_attribute(number, "iso_country")),
                "geography_note": "If country metadata is unavailable, verify the number and intended destination countries in the authorized account; a +1 prefix is not a unique country.",
                "address_requirements": _attribute(number, "address_requirements"),
                "number_origin": _attribute(number, "origin"),
                "bundle_sid_present": bool(_attribute(number, "bundle_sid")),
                "registration_verified": False,
                "registration_note": "SMS capability does not verify A2P, toll-free, country-specific registration, destination permission or participant consent."}


class ServiceWebhookAdapter:
    """Validate the signed envelope quickly; resource proof is fetched later."""
    def __init__(self, cfg):
        self.cfg = cfg

    def _get(self, name):
        return self.cfg.get(name, "") if isinstance(self.cfg, dict) else getattr(self.cfg, name, "")

    @staticmethod
    def _value(params, key, default=""):
        try:
            values = params.getall(key)
        except AttributeError:
            try:
                values = params.getlist(key)
            except AttributeError:
                value = params.get(key, default)
                values = value if isinstance(value, list) else [value]
        if not values:
            return default
        if len(values) != 1 or not isinstance(values[0], str):
            raise WebhookRejected("ambiguous_form_parameter", 400)
        return values[0]

    def _validate_signature(self, path, signature, params):
        if not isinstance(signature, str) or not signature:
            raise WebhookRejected("invalid_signature")
        token = self._get("auth_token")
        account = self._get("account_sid")
        phone = self._get("phone_number")
        try:
            canonical = _canonical_base(self._get("public_base_url"))
            valid_cfg = bool(token) and bool(_ACCOUNT.fullmatch(account)) and bool(_E164.fullmatch(phone))
        except (ValueError, TypeError):
            valid_cfg = False
        if not valid_cfg:
            raise WebhookRejected("webhook_not_configured", 503)
        if (not isinstance(path, str) or not path.startswith("/") or path.startswith("//")
                or "#" in path or "\\" in path or any(c in path for c in "\r\n")):
            raise WebhookRejected("invalid_webhook_path", 400)
        try:
            from twilio.request_validator import RequestValidator
        except ImportError:
            raise WebhookRejected("official_twilio_sdk_required", 503) from None
        try:
            valid = RequestValidator(token).validate(canonical + path, params, signature)
        except (TypeError, ValueError, AttributeError):
            valid = False
        if not valid:
            raise WebhookRejected("invalid_signature")
        if self._value(params, "AccountSid") != account:
            raise WebhookRejected("account_mismatch")

    def validate_inbound(self, path, signature, params):
        self._validate_signature(path, signature, params)
        sid = self._value(params, "MessageSid")
        sender = self._value(params, "From")
        recipient = self._value(params, "To")
        body = self._value(params, "Body")
        optout = self._value(params, "OptOutType").upper()
        if not _MESSAGE.fullmatch(sid):
            raise WebhookRejected("invalid_message_sid", 400)
        if recipient != self._get("phone_number"):
            raise WebhookRejected("recipient_mismatch")
        if not _E164.fullmatch(sender):
            raise WebhookRejected("invalid_sender_phone", 400)
        if len(body) > 1600:
            raise WebhookRejected("message_body_too_long", 400)
        if self._value(params, "NumMedia", "0") != "0":
            raise WebhookRejected("sms_only_no_attachments", 400)
        if optout not in {"", "STOP", "START", "HELP"}:
            raise WebhookRejected("unknown_opt_out_type", 400)
        return {"sid": sid, "from": sender, "to": recipient, "body": body,
                "account_sid": self._get("account_sid"), "opt_out_type": optout}

    def validate_status(self, path, signature, params):
        self._validate_signature(path, signature, params)
        sid = self._value(params, "MessageSid")
        sender = self._value(params, "From")
        recipient = self._value(params, "To")
        status = self._value(params, "MessageStatus", self._value(params, "SmsStatus"))
        if not _MESSAGE.fullmatch(sid):
            raise WebhookRejected("invalid_message_sid", 400)
        if sender != self._get("phone_number"):
            raise WebhookRejected("sender_mismatch")
        # Twilio status callbacks contain a varying subset of standard fields;
        # official examples omit To. The signed callback nonce/outbox row binds
        # the intended recipient. If To is present it must match that row later.
        if recipient and not _E164.fullmatch(recipient):
            raise WebhookRejected("invalid_recipient_phone", 400)
        if status not in SMS_STATUSES:
            raise WebhookRejected("invalid_message_status", 400)
        if self._value(params, "SmsStatus", status) != status:
            raise WebhookRejected("conflicting_message_status", 400)
        error = self._value(params, "ErrorCode")
        if error and _error_code(error) is None:
            raise WebhookRejected("invalid_error_code", 400)
        return {"sid": sid, "from": sender, "to": recipient, "status": status,
                "error_code": _error_code(error), "account_sid": self._get("account_sid")}
