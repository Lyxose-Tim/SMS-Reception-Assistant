import { t } from './templates';
import type { BookingDraft, BookingFieldDef, Locale, ServiceItem } from './types';

export const DRAFT_TTL_MS = 24 * 60 * 60 * 1000;
export const MAX_FIELD_CHARS = 200;
/** Contact extraction only scans this many leading characters, bounding regex cost. */
export const MAX_SCAN_CHARS = 1000;

const MAX_EMAIL_CHARS = 254;
const MAX_PHONE_DIGITS = 15;

export type FieldError = 'invalid_contact' | 'invalid_service' | 'invalid_text';
export type FieldParse = { ok: true; value: string } | { ok: false; error: FieldError };

const EMAIL = /[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}/i;
const PHONE = /\+?\d[\d\s().-]{5,}\d/g;
// PHONE never contains "/", so slash dates (10/12/2026) never form a candidate in the first place.
const DATE_YMD = /^\d{4}[-.]\d{1,2}[-.]\d{1,2}/;
const DATE_DMY = /^\d{1,2}[-.]\d{1,2}[-.]\d{2,4}(\b|\s)/;
const TIME_SUFFIX = /^:\d/;

export function newDraft(now: Date): BookingDraft {
  return { fields: {}, awaiting: null, updatedAt: now.toISOString() };
}

export function isDraftExpired(draft: BookingDraft, now: Date): boolean {
  const updated = Date.parse(draft.updatedAt);
  if (Number.isNaN(updated)) return true;
  return now.getTime() - updated > DRAFT_TTL_MS;
}

export function nextMissingField(defs: BookingFieldDef[], draft: BookingDraft): BookingFieldDef | null {
  return defs.find((d) => d.required && !draft.fields[d.key]?.trim()) ?? null;
}

export function isComplete(defs: BookingFieldDef[], draft: BookingDraft): boolean {
  return nextMissingField(defs, draft) === null;
}

export function extractContact(text: string): string | null {
  const scanned = text.slice(0, MAX_SCAN_CHARS);
  const email = EMAIL.exec(scanned);
  if (email) return email[0].length <= MAX_EMAIL_CHARS ? email[0] : null;
  for (const match of scanned.matchAll(PHONE)) {
    const candidate = match[0].trim();
    const digits = candidate.replace(/\D/g, '').length;
    if (digits < 7 || digits > MAX_PHONE_DIGITS) continue;
    if (DATE_YMD.test(candidate) || DATE_DMY.test(candidate)) continue;
    if (TIME_SUFFIX.test(scanned.slice(match.index + match[0].length, match.index + match[0].length + 2))) continue;
    return candidate;
  }
  return null;
}

function escapeRegExp(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

export function matchService(text: string, services: ServiceItem[]): string | null {
  const lower = text.toLowerCase().trim();
  const numbered = /^(?:(?:service|option|number|no\.?|#)\s*)?(\d{1,2})$/.exec(lower);
  if (numbered) {
    const picked = services[Number(numbered[1]) - 1]?.name;
    return picked?.trim() ? picked : null;
  }
  const named = services.filter((s) => s.name.trim());
  let full: ServiceItem | null = null;
  for (const s of named) {
    const name = s.name.toLowerCase();
    if (lower.includes(name) && (!full || name.length > full.name.length)) full = s;
  }
  if (full) return full.name;
  const byHead = named.filter((s) => {
    const head = s.name.toLowerCase().split(/\s+/)[0] ?? '';
    return head.length >= 3 && new RegExp(`(^|[^a-z])${escapeRegExp(head)}([^a-z]|$)`).test(lower);
  });
  return byHead.length === 1 ? byHead[0]!.name : null;
}

export function parseFieldValue(def: BookingFieldDef, text: string, services: ServiceItem[]): FieldParse {
  const trimmed = text.trim();
  const listsServices = def.kind === 'service' && services.length > 0;
  if (trimmed.length > MAX_FIELD_CHARS) {
    if (def.kind === 'contact') return { ok: false, error: 'invalid_contact' };
    if (listsServices) return { ok: false, error: 'invalid_service' };
    return { ok: false, error: 'invalid_text' };
  }
  if (def.kind === 'contact') {
    const contact = extractContact(trimmed);
    return contact ? { ok: true, value: contact } : { ok: false, error: 'invalid_contact' };
  }
  if (listsServices) {
    const service = matchService(trimmed, services);
    return service ? { ok: true, value: service } : { ok: false, error: 'invalid_service' };
  }
  if (!trimmed) return { ok: false, error: 'invalid_text' };
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
