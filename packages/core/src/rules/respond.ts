import {
  askFor,
  contactValue,
  extractContact,
  matchService,
  newDraft,
  nextMissingField,
  parseFieldValue,
  type FieldError,
} from '../booking';
import { t, type TemplateKey } from '../templates';
import type {
  BookingDraft,
  BookingRequestDraft,
  BusinessKnowledge,
  BusinessProfile,
  HandoffReason,
  Locale,
} from '../types';
import { classify, isExplicitQuestion, type Classification, type Topic } from './classify';
import { matchFaq } from './faqMatch';

export interface RulesOutcome {
  replies: string[];
  bookingDraft: BookingDraft | null;
  bookingRequest?: BookingRequestDraft;
  handoff?: { reason: HandoffReason };
}

const FIELD_ERROR_TEMPLATE: Record<FieldError, TemplateKey> = {
  invalid_contact: 'booking_invalid_contact',
  invalid_service: 'booking_invalid_service',
  invalid_text: 'booking_invalid_text',
};

export function respondWithRules(
  k: BusinessKnowledge,
  text: string,
  locale: Locale,
  draft: BookingDraft | null,
  now: Date,
): RulesOutcome {
  const c = classify(text);
  const services = k.profile.services;

  if (draft && c.wantsCancel && !isExplicitQuestion(text)) {
    return { replies: [t(locale, 'booking_cancelled')], bookingDraft: null };
  }
  if (c.wantsHuman) {
    return { replies: [t(locale, 'handoff')], bookingDraft: draft, handoff: { reason: 'visitor_request' } };
  }
  if (!draft && c.wantsCancel && !c.isQuestion) {
    return { replies: [t(locale, 'cancel_existing')], bookingDraft: null, handoff: { reason: 'visitor_request' } };
  }
  if (draft) {
    const awaited = k.bookingFields.find((d) => d.key === draft.awaiting) ?? nextMissingField(k.bookingFields, draft);
    const ask = awaited ? [askFor(awaited, locale, services)] : [];
    if (isExplicitQuestion(text)) {
      const answer = answerQuestion(k, text, c, locale);
      return { replies: [answer ?? t(locale, 'booking_question_unanswered'), ...ask], bookingDraft: refreshed(draft, now) };
    }
    if (c.isGreeting && !c.topics.length && !c.wantsBooking && !extractContact(text)) {
      return { replies: ask, bookingDraft: refreshed(draft, now) };
    }
    return bookingStep(k, draft, text, locale, now, !c.wantsBooking);
  }
  if (c.wantsBooking) return bookingStep(k, null, text, locale, now, false);

  const answer = answerQuestion(k, text, c, locale);
  if (answer) return { replies: [answer], bookingDraft: null };
  if (c.isGreeting) return { replies: [t(locale, 'greeting_menu')], bookingDraft: null };
  return { replies: [t(locale, 'unknown_question')], bookingDraft: null, handoff: { reason: 'unknown_question' } };
}

/** Copy of a draft kept for another turn: TTL restarts, the caller's draft is never mutated. */
function refreshed(draft: BookingDraft, now: Date): BookingDraft {
  return { ...draft, fields: { ...draft.fields }, updatedAt: now.toISOString() };
}

function answerQuestion(k: BusinessKnowledge, text: string, c: Classification, locale: Locale): string | null {
  const faq = matchFaq(text, k.faqs, k.profile.services.map((s) => s.name));
  if (faq) return faq.answer;
  const parts = c.topics
    .map((topic) => answerTopic(k.profile, topic, locale))
    .filter((part): part is string => part !== null);
  return parts.length ? parts.join('\n') : null;
}

function answerTopic(p: BusinessProfile, topic: Topic, locale: Locale): string | null {
  switch (topic) {
    case 'hours':
      return p.hours.trim() ? t(locale, 'answer_hours', { hours: p.hours.trim() }) : null;
    case 'areas':
      return p.serviceAreas.length ? t(locale, 'answer_areas', { areas: p.serviceAreas.join(', ') }) : null;
    case 'services':
      return p.services.length
        ? t(locale, 'answer_services', { list: p.services.map((s) => `${s.name} (${s.description})`).join('; ') })
        : null;
    case 'pricing': {
      const priced = p.services.filter((s) => s.pricing.trim());
      return priced.length
        ? t(locale, 'answer_pricing', { list: priced.map((s) => `${s.name}: ${s.pricing}`).join('; '), note: p.priceNote })
        : null;
    }
  }
}

function bookingStep(
  k: BusinessKnowledge,
  draft: BookingDraft | null,
  text: string,
  locale: Locale,
  now: Date,
  storeAnswer: boolean,
): RulesOutcome {
  const defs = k.bookingFields;
  const services = k.profile.services;
  const next: BookingDraft = draft ? { ...draft, fields: { ...draft.fields } } : newDraft(now);
  const awaited = next.awaiting ? defs.find((d) => d.key === next.awaiting) : undefined;

  if (awaited && storeAnswer) {
    const parsed = parseFieldValue(awaited, text, services);
    if (!parsed.ok) {
      return { replies: [t(locale, FIELD_ERROR_TEMPLATE[parsed.error]), askFor(awaited, locale, services)], bookingDraft: refreshed(next, now) };
    }
    next.fields[awaited.key] = parsed.value;
  } else {
    for (const def of defs) {
      if (next.fields[def.key]) continue;
      if (def.kind === 'contact') {
        const contact = extractContact(text);
        if (contact) next.fields[def.key] = contact;
      } else if (def.kind === 'service' && services.length) {
        const service = matchService(text, services);
        if (service) next.fields[def.key] = service;
      }
    }
  }

  next.updatedAt = now.toISOString();
  const missing = nextMissingField(defs, next);
  if (!missing) {
    return {
      replies: [t(locale, 'booking_submitted', { business: k.name, contact: contactValue(defs, next.fields) })],
      bookingDraft: null,
      bookingRequest: { fields: { ...next.fields } },
    };
  }
  next.awaiting = missing.key;
  return { replies: [askFor(missing, locale, services)], bookingDraft: next };
}
