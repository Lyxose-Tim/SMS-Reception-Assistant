"""Signed, fail-closed Twilio intake. Never constructs a sending client.

The official SDK validates signatures. A trusted provider lookup is mandatory
for resource age and identity: signed form data alone provides no expiry proof.
No live provider lookup or external SMS transport is wired in this demo.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
import os
import re


class IntakeError(ValueError):
    def __init__(self, status, code):
        super().__init__(code)
        self.status = status
        self.code = code


@dataclass(frozen=True)
class WebhookSettings:
    auth_token: str = ""
    account_sid: str = ""
    phone_number: str = ""
    public_base_url: str = ""
    max_age_seconds: int = 300

    @classmethod
    def from_env(cls):
        return cls(
            os.environ.get("TWILIO_AUTH_TOKEN", ""),
            os.environ.get("TWILIO_ACCOUNT_SID", ""),
            os.environ.get("TWILIO_PHONE_NUMBER", ""),
            os.environ.get("TWILIO_PUBLIC_BASE_URL", ""),
        )


class TwilioAdapter:
    def __init__(self, settings=None, trusted_lookup=None):
        self.settings = settings or WebhookSettings.from_env()
        self.trusted_lookup = trusted_lookup

    def validate(self, path, signature, params):
        if not signature:
            raise IntakeError(403, "invalid_signature")
        cfg = self.settings
        if not all((cfg.auth_token, cfg.account_sid, cfg.phone_number, cfg.public_base_url)):
            raise IntakeError(503, "webhook_not_configured")
        if not cfg.public_base_url.startswith("https://"):
            raise IntakeError(503, "canonical_https_url_required")
        try:
            from twilio.request_validator import RequestValidator
        except ImportError:
            raise IntakeError(503, "official_twilio_sdk_required") from None
        # Use the configured canonical external URL, including the raw query.
        # Never trust Host or forwarded headers to reconstruct a signature URL.
        url = cfg.public_base_url.rstrip("/") + path
        if not RequestValidator(cfg.auth_token).validate(url, params, signature):
            raise IntakeError(403, "invalid_signature")
        if params.get("AccountSid") != cfg.account_sid or params.get("To") != cfg.phone_number:
            raise IntakeError(403, "account_or_recipient_mismatch")

    def event(self, path, signature, params):
        self.validate(path, signature, params)
        route = path.split("?", 1)[0]
        if route == "/webhooks/twilio/sms":
            sid, kind = params.get("MessageSid", ""), "sms"
            if not re.fullmatch(r"SM[0-9a-fA-F]{32}", sid):
                raise IntakeError(400, "invalid_message_sid")
        elif route == "/webhooks/twilio/dial-result":
            sid, kind = params.get("CallSid", ""), "missed_call"
            if not re.fullmatch(r"CA[0-9a-fA-F]{32}", sid):
                raise IntakeError(400, "invalid_call_sid")
            if not params.get("DialCallStatus"):
                raise IntakeError(400, "dial_result_required")
            if not re.fullmatch(r"CA[0-9a-fA-F]{32}", params.get("DialCallSid", "")):
                raise IntakeError(400, "dial_child_sid_required")
        else:
            raise IntakeError(404, "unknown_webhook")
        if self.trusted_lookup is None:
            raise IntakeError(503, "trusted_provider_lookup_required")
        try:
            proof = self.trusted_lookup(kind, sid, dict(params))
        except Exception:
            raise IntakeError(503, "provider_verification_unavailable") from None
        # Resolver must verify incoming message or parent/child dial resources,
        # including sender, recipient, account, body/status and parent linkage.
        if not isinstance(proof, dict) or proof.get("verified") is not True:
            raise IntakeError(403, "unverified_provider_resource")
        expected = {"account_sid": self.settings.account_sid,
                    "phone": params.get("From"), "to": self.settings.phone_number,
                    "resource_id": sid}
        if any(proof.get(key) != value for key, value in expected.items()):
            raise IntakeError(403, "provider_resource_mismatch")
        if kind == "sms" and (proof.get("direction") != "inbound" or proof.get("body") != params.get("Body", "")):
            raise IntakeError(403, "provider_message_mismatch")
        if kind == "missed_call" and (proof.get("dial_status") != params["DialCallStatus"] or
                                     proof.get("dial_sid") != params.get("DialCallSid") or
                                     proof.get("parent_call_sid") != sid):
            raise IntakeError(403, "provider_dial_mismatch")
        try:
            timestamp = proof["occurred_at"]
            if not isinstance(timestamp, str):
                raise ValueError()
            occurred = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            if occurred.tzinfo is None:
                raise ValueError()
            age = (datetime.now(timezone.utc) - occurred).total_seconds()
        except (ValueError, KeyError, TypeError):
            raise IntakeError(403, "provider_timestamp_required") from None
        text = params.get("Body", "")
        optout = params.get("OptOutType", "").upper()
        stop = kind == "sms" and (optout == "STOP" or text.strip().upper() in {"STOP", "STOPALL", "UNSUBSCRIBE", "CANCEL", "END", "QUIT", "REVOKE", "OPTOUT"})
        if age < -30 or (age > self.settings.max_age_seconds and not stop):
            raise IntakeError(409, "expired_provider_event")
        # An old STOP still suppresses future messages; it never sends a reply.
        event = {"event_id": "twilio:" + sid, "kind": kind, "phone": params["From"],
                 "origin": "twilio", "verified": True, "occurred_at": proof["occurred_at"]}
        if kind == "sms":
            event.update(text=text, provider_managed_optout=bool(optout), opt_out_type=optout,
                         suppress_confirmation=bool(optout) or age > self.settings.max_age_seconds)
            if optout in {"START", "STOP"}:
                event["text"] = optout
        else:
            event["dial_status"] = params["DialCallStatus"]
        return event
