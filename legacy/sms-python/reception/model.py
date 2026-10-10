"""Structured understanding adapter. The bundled adapter is deterministic, not AI.

Any future model adapter must return intents and candidate fields only. The domain
engine validates them against approved business facts and owns every send/consent
decision. This interface performs no network calls and cannot execute tools.
"""
from __future__ import annotations

import re
from typing import Protocol


class ModelError(RuntimeError):
    pass


class ModelTimeout(ModelError):
    pass


class ModelUnavailable(ModelError):
    pass


class UnderstandingAdapter(Protocol):
    def understand(self, text: str, business: dict, behavior: str = "normal") -> dict:
        """Return candidates only; never return permission to send or consent."""


class RuleMockAdapter:
    """Local deterministic parser with explicit simulated failure modes."""

    label = "Deterministic rules / simulated understanding (no live AI)"

    def understand(self, text: str, business: dict, behavior: str = "normal") -> dict:
        if behavior == "timeout":
            raise ModelTimeout("Simulated model timeout")
        if behavior == "unavailable":
            raise ModelUnavailable("Simulated model service unavailable")
        if behavior == "error":
            raise ModelError("Simulated model error")
        lower = text.lower().strip()
        malicious = bool(re.search(
            r"ignore(?:\s+(?:all|previous|your|the|earlier))*\s+(?:rules|instructions|policy|restrictions)|"
            r"system prompt|developer message|override (?:consent|opt.?out|policy)|"
            r"bypass (?:consent|restrictions|stop)|reveal .*(?:key|secret)|"
            r"pretend .*confirmed|mark .*confirmed", lower))
        human = bool(re.search(r"\b(?:human|agent|person|representative|manager)\b", lower))
        service = ""
        aliases = {
            "standard cleaning": r"\b(?:standard|regular)(?:\s+(?:house|home))?(?:\s+clean(?:ing)?)?\b",
            "deep cleaning": r"\bdeep(?:\s+(?:house|home))?\s+clean(?:ing)?\b|\bdeep\b",
            "move-out cleaning": r"\bmove[ -]?out(?:\s+clean(?:ing)?)?\b",
        }
        service_candidates = []
        for configured in business.get("services", []):
            name = configured["name"]
            pattern = aliases.get(name.lower(), re.escape(name.lower()))
            if re.search(pattern, lower):
                service_candidates.append(name)
        service = service_candidates[0] if service_candidates else ""
        unrecognized_service = ""
        if not service:
            requested = re.search(r"\b(?:for|is|need|offer|do|want|a|some)\s+(?:a\s+)?([a-z-]+(?:\s+[a-z-]+)?)\s+cleaning\b", lower)
            if requested:
                candidate = requested.group(1).strip()
                if candidate not in {"your", "our", "the", "home", "house", "residential"}:
                    unrecognized_service = candidate + " cleaning"
        area_candidates = [a for a in business.get("service_areas", []) if a.lower() in lower]
        area = area_candidates[0] if area_candidates else ""
        email_pattern = r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"
        phone_pattern = r"(?:\+?1[ -]?)?\(?\d{3}\)?[ -]?\d{3}[ -]?\d{4}\b"
        email = re.search(email_pattern, text)
        phone = re.search(phone_pattern, text)
        contact_candidates = set(value.lower() for value in re.findall(email_pattern, text))
        contact_candidates.update(re.sub(r"\D", "", value) for value in re.findall(phone_pattern, text))
        contact = email.group(0) if email else phone.group(0) if phone else ""
        if re.search(r"\b(?:this|same) (?:phone|number)\b|\btext me here\b", lower):
            contact = "__CURRENT_PHONE__"
            contact_candidates.add(contact)
        time_reference = (r"\b(?:next\s+)?(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
                          r"tomorrow|today|next week|\d{4}-\d{2}-\d{2})\b")
        time_match = re.search(time_reference + r"[^.!?;]*", text, re.I)
        desired = time_match.group(0).strip()[:160] if time_match else ""
        topics = []
        if re.search(r"\bhours?\b|\bopen\b|\bclose[ds]?\b|\bwhen .*available", lower):
            topics.append("hours")
        if re.search(r"service areas?|areas? .*serve|where .*serve|where .*work|locations?|coverage", lower):
            topics.append("areas")
        if re.search(r"\b(?:price|prices|pricing|cost|quote|rates)\b|how much", lower):
            topics.append("pricing")
        if re.search(r"what .*services|services .*offer|types? of cleaning|what .*cleaning.*offer", lower):
            topics.append("services")
        request_policy = bool(re.search(
            r"\bhow (?:do|can|should|would) (?:i|we|you).*\b(?:book|schedule|request|reserve)\b|"
            r"\bhow (?:to book|does (?:booking|the appointment process) work)\b|"
            r"\b(?:booking|appointment|reservation|request) (?:policy|policies|rules|process)\b|"
            r"\b(?:do|are|is|can)\b.*\b(?:appointments?|bookings?|requests?)\b.*\b(?:confirmed|confirmation|automatically)\b|"
            r"\b(?:what|which)\b.*\b(?:need|required)\b.*\b(?:book|appointment)\b|"
            r"\bdo i need an appointment\b", lower))
        if request_policy:
            topics.append("requests")
        # Multiple candidates or a negated candidate cannot establish a booking
        # value. Keep clear single-value corrections; ask a person to resolve
        # conflicting/negative wording rather than silently choose list order.
        booking_values_present = bool(service or area or desired or contact)
        ambiguous_booking = not topics and (
            len(set(service_candidates)) > 1 or len(set(area_candidates)) > 1 or
            len(re.findall(time_reference, text, re.I)) > 1 or
            len(contact_candidates) > 1 or
            booking_values_present and bool(re.search(r"\b(?:not|no|don't|do not|instead of|rather than)\b", lower)))
        mentioned_area = ""
        location = re.search(
            r"(?:serve|service|live in|located in|"
            r"clean(?:ing)?(?:\s+(?:my|our|the|a))?(?:\s+(?:home|house|property))?\s+in|"
            r"outside)\s+([A-Z][A-Za-z ]{1,35})", text)
        if location:
            mentioned_area = location.group(1).strip().rstrip(" ?.,")
        booking_intent = not request_policy and bool(re.search(r"\b(?:book|booking|schedule|appointment|reserve)\b|"
                                        r"\b(?:need|want|request) (?:a |some )?.*clean", lower))
        return {
            "intent": "human" if human else "booking" if booking_intent and not topics else "faq" if topics else "unknown",
            "topics": topics,
            "service": service,
            "unrecognized_service": unrecognized_service,
            "area": area,
            "desired_time": desired,
            "contact": contact,
            "mentioned_area": mentioned_area,
            "malicious": malicious,
            "new_booking": booking_intent and bool(re.search(r"\b(?:another|new|second|different)\b", lower)),
            "ambiguous_booking": ambiguous_booking,
        }
