import { t } from './templates';
import type { BookingDraft, BookingFieldDef, Locale, ServiceItem } from './types';

export const DRAFT_TTL_MS = 24 * 60 * 60 * 1000;
export const MAX_FIELD_CHARS = 200;

export type FieldError = 'invalid_contact' | 'invalid_service' | 'invalid_text';
export type FieldParse = { ok: true; value: string } | { ok: false; error: FieldError };

const EMAIL = /[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}/i;
const PHONE = /\+?\d[\d\s().-]{5,}\d/;

export function newDraft(now: Date): BookingDraft {
  return { fields: {}, awaiting: null, updatedAt: now.toISOString() };
}

export function isDraftExpired(draft: BookingDraft, now: Date): boolean {
  return now.getTime() - Date.parse(draft.updatedAt) > DRAFT_TTL_MS;
}

export function nextMissingField(defs: BookingFieldDef[], draft: BookingDraft): BookingFieldDef | null {
  return defs.find((d) => d.required && !draft.fields[d.key]?.trim()) ?? null;
}

export function isComplete(defs: BookingFieldDef[], draft: BookingDraft): boolean {
  return nextMissingField(defs, draft) === null;
}

export function extractContact(text: string): string | null {
  const email = EMAIL.exec(text);
  if (email) return email[0];
  const phone = PHONE.exec(text);
  if (phone && phone[0].replace(/\D/g, '').length >= 7) return phone[0].trim();
  return null;
}

function escapeRegExp(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

export function matchService(text: string, services: ServiceItem[]): string | null {
  const lower = text.toLowerCase().trim();
  const numbered = /^(\d{1,2})$/.exec(lower);
  if (numbered) return services[Number(numbered[1]) - 1]?.name ?? null;
  const full = services.find((s) => lower.includes(s.name.toLowerCase()));
  if (full) return full.name;
  const byHead = services.filter((s) => {
    const head = s.name.toLowerCase().split(/\s+/)[0] ?? '';
    return head.length >= 3 && new RegExp(`(^|[^a-z])${escapeRegExp(head)}([^a-z]|$)`).test(lower);
  });
  return byHead.length === 1 ? byHead[0]!.name : null;
}

export function parseFieldValue(def: BookingFieldDef, text: string, services: ServiceItem[]): FieldParse {
  const trimmed = text.trim();
  if (def.kind === 'contact') {
    const contact = extractContact(trimmed);
    return contact ? { ok: true, value: contact } : { ok: false, error: 'invalid_contact' };
  }
  if (def.kind === 'service' && services.length > 0) {
    const service = matchService(trimmed, services);
    return service ? { ok: true, value: service } : { ok: false, error: 'invalid_service' };
  }
  if (!trimmed || trimmed.length > MAX_FIELD_CHARS) return { ok: false, error: 'invalid_text' };
  return { ok: true, value: trimmed };
}

export function askFor(def: BookingFieldDef, locale: Locale, services: ServiceItem[]): string {
  if (def.kind === 'service' && services.length > 0) {
    return t(locale, 'booking_ask_service', {
      options: services.map((s, i) => `${i + 1}. ${s.name}`).join(', '),
    });
  }
  if (def.kind === 'contact') return t(locale, 'booking_ask_contact');
  return t(locale, 'booking_ask_field', { label: def.label[locale] });
}

/** Applies untrusted values (e.g. from an AI tool call): unknown keys and invalid values are rejected. */
export function setDraftFields(
  draft: BookingDraft,
  defs: BookingFieldDef[],
  values: Record<string, unknown>,
  services: ServiceItem[],
  now: Date,
): { draft: BookingDraft; rejected: string[] } {
  const fields = { ...draft.fields };
  const rejected: string[] = [];
  for (const [key, raw] of Object.entries(values)) {
    const def = defs.find((d) => d.key === key);
    if (!def || typeof raw !== 'string') {
      rejected.push(key);
      continue;
    }
    const parsed = parseFieldValue(def, raw, services);
    if (parsed.ok) fields[key] = parsed.value;
    else rejected.push(key);
  }
  return { draft: { fields, awaiting: draft.awaiting, updatedAt: now.toISOString() }, rejected };
}

export function contactValue(defs: BookingFieldDef[], fields: Record<string, string>): string {
  const def = defs.find((d) => d.kind === 'contact');
  return def ? (fields[def.key] ?? '') : '';
}
