import { describe, expect, it } from 'vitest';
import {
  MAX_SCAN_CHARS,
  askFor,
  contactValue,
  extractContact,
  isComplete,
  isDraftExpired,
  matchService,
  newDraft,
  nextMissingField,
  parseFieldValue,
  setDraftFields,
} from './booking';
import { demoKnowledge } from './demo';

function timed<T>(fn: () => T): { result: T; ms: number } {
  const start = performance.now();
  const result = fn();
  return { result, ms: performance.now() - start };
}

const k = demoKnowledge();
const defs = k.bookingFields;
const services = k.profile.services;
const now = new Date('2026-10-10T12:00:00Z');
const def = (key: string) => defs.find((d) => d.key === key)!;

describe('extractContact', () => {
  it('finds emails and phone numbers', () => {
    expect(extractContact('reach me at alex@example.com thanks')).toBe('alex@example.com');
    expect(extractContact('call +1 (202) 555-0123')).toBe('+1 (202) 555-0123');
    expect(extractContact('电话 13800138000')).toBe('13800138000');
  });

  it('rejects short numbers and plain text', () => {
    expect(extractContact('I have 2 cats')).toBeNull();
    expect(extractContact('not sure')).toBeNull();
  });
});

describe('matchService', () => {
  it('matches by full name, distinctive first word or list number', () => {
    expect(matchService('Deep cleaning please', services)).toBe('Deep cleaning');
    expect(matchService('a deep clean', services)).toBe('Deep cleaning');
    expect(matchService('move-out', services)).toBe('Move-out cleaning');
    expect(matchService('2', services)).toBe('Deep cleaning');
  });

  it('returns null when nothing or an out-of-range number matches', () => {
    expect(matchService('window washing', services)).toBeNull();
    expect(matchService('9', services)).toBeNull();
  });
});

describe('parseFieldValue', () => {
  it('validates by field kind', () => {
    expect(parseFieldValue(def('contact'), 'alex@example.com', services)).toEqual({ ok: true, value: 'alex@example.com' });
    expect(parseFieldValue(def('contact'), 'nope', services)).toEqual({ ok: false, error: 'invalid_contact' });
    expect(parseFieldValue(def('service'), 'deep', services)).toEqual({ ok: true, value: 'Deep cleaning' });
    expect(parseFieldValue(def('service'), 'gardening', services)).toEqual({ ok: false, error: 'invalid_service' });
    expect(parseFieldValue(def('name'), '  Alex  ', services)).toEqual({ ok: true, value: 'Alex' });
    expect(parseFieldValue(def('name'), 'x'.repeat(201), services)).toEqual({ ok: false, error: 'invalid_text' });
  });

  it('accepts any service text when the business lists no services', () => {
    expect(parseFieldValue(def('service'), 'gardening', [])).toEqual({ ok: true, value: 'gardening' });
  });
});

describe('draft helpers', () => {
  it('walks required fields in order and ignores optional ones', () => {
    const draft = newDraft(now);
    expect(nextMissingField(defs, draft)?.key).toBe('service');
    draft.fields = { service: 'Deep cleaning', preferred_time: 'Sat', name: 'Alex' };
    expect(nextMissingField(defs, draft)?.key).toBe('contact');
    draft.fields.contact = 'alex@example.com';
    expect(isComplete(defs, draft)).toBe(true);
  });

  it('expires drafts after 24 hours', () => {
    const draft = newDraft(new Date('2026-10-09T11:59:00Z'));
    expect(isDraftExpired(draft, now)).toBe(true);
    expect(isDraftExpired(newDraft(new Date('2026-10-10T00:00:00Z')), now)).toBe(false);
  });

  it('setDraftFields validates values and rejects unknown keys', () => {
    const { draft, rejected } = setDraftFields(
      newDraft(new Date(0)),
      defs,
      { service: 'deep clean', contact: 'call me maybe', hacker: 'x', name: 'Alex' },
      services,
      now,
    );
    expect(draft.fields).toEqual({ service: 'Deep cleaning', name: 'Alex' });
    expect(rejected.sort()).toEqual(['contact', 'hacker']);
    expect(draft.updatedAt).toBe(now.toISOString());
  });

  it('contactValue reads the contact-kind field', () => {
    expect(contactValue(defs, { contact: 'a@b.co' })).toBe('a@b.co');
  });
});

describe('askFor', () => {
  it('lists services, asks for contact, or asks by label', () => {
    expect(askFor(def('service'), 'en', services)).toBe(
      'Which service would you like? Options: 1. Standard cleaning, 2. Deep cleaning, 3. Move-out cleaning.',
    );
    expect(askFor(def('contact'), 'zh-CN', services)).toBe('请留下可以联系到您的邮箱或电话。');
    expect(askFor(def('name'), 'zh-CN', services)).toBe('请提供您的姓名。');
  });
});

describe('hardening: length caps and bounded cost', () => {
  it('exports the scan cap', () => {
    expect(MAX_SCAN_CHARS).toBe(1000);
  });

  it('rejects a huge contact value quickly', () => {
    const { result, ms } = timed(() => parseFieldValue(def('contact'), 'a'.repeat(100000) + '@b.co', services));
    expect(result).toEqual({ ok: false, error: 'invalid_contact' });
    expect(ms).toBeLessThan(50);
  });

  it('rejects a huge service value before matching', () => {
    expect(parseFieldValue(def('service'), 'deep '.repeat(1000), services)).toEqual({ ok: false, error: 'invalid_service' });
  });

  it('extractContact scans a bounded prefix and stays fast', () => {
    const { result, ms } = timed(() => extractContact('x'.repeat(5000)));
    expect(result).toBeNull();
    expect(ms).toBeLessThan(50);
    expect(extractContact(' '.repeat(MAX_SCAN_CHARS) + 'alex@example.com')).toBeNull();
  });

  it('rejects emails longer than 254 chars', () => {
    expect(extractContact(`${'a'.repeat(250)}@example.com`)).toBeNull();
  });

  it('accepts a 200-char name and rejects 201', () => {
    expect(parseFieldValue(def('name'), 'x'.repeat(200), services)).toEqual({ ok: true, value: 'x'.repeat(200) });
    expect(parseFieldValue(def('name'), 'x'.repeat(201), services)).toEqual({ ok: false, error: 'invalid_text' });
  });
});

describe('hardening: phone false accepts', () => {
  it('rejects dates', () => {
    expect(extractContact('see you 2026-10-12')).toBeNull();
    expect(extractContact('10/12/2026')).toBeNull();
    expect(extractContact('2026/10/12')).toBeNull();
  });

  it('rejects more than 15 digits', () => {
    expect(extractContact('1234567890123456')).toBeNull();
  });

  it('still accepts real phones and emails', () => {
    expect(extractContact('call +1 (202) 555-0123')).toBe('+1 (202) 555-0123');
    expect(extractContact('13800138000')).toBe('13800138000');
    expect(extractContact('alex@example.com')).toBe('alex@example.com');
  });
});

describe('hardening: matchService', () => {
  const hair = [
    { name: 'Haircut', description: '', pricing: '' },
    { name: 'Haircut and color', description: '', pricing: '' },
  ];

  it('prefers the longest contained service name', () => {
    expect(matchService('haircut and color please', hair)).toBe('Haircut and color');
    expect(matchService('just a haircut', hair)).toBe('Haircut');
  });

  it('never matches blank-named services', () => {
    const blank = [
      { name: '', description: '', pricing: '' },
      { name: '   ', description: '', pricing: '' },
    ];
    expect(matchService('anything at all', blank)).toBeNull();
    expect(matchService('1', blank)).toBeNull();
    expect(matchService('x', [...blank, hair[0]!])).toBeNull();
  });
});

describe('hardening: isDraftExpired', () => {
  it('treats an unparseable updatedAt as expired', () => {
    expect(isDraftExpired({ fields: {}, awaiting: null, updatedAt: 'garbage' }, now)).toBe(true);
  });
});

describe('hardening: setDraftFields untrusted boundary', () => {
  it('rejects non-string values', () => {
    const { draft, rejected } = setDraftFields(
      newDraft(now),
      defs,
      { name: 123, contact: null, service: {}, preferred_time: ['x'] },
      services,
      now,
    );
    expect(draft.fields).toEqual({});
    expect(rejected.sort()).toEqual(['contact', 'name', 'preferred_time', 'service']);
  });

  it('preserves existing fields not mentioned and does not mutate the input', () => {
    const input = { fields: { name: 'Alex', service: 'Deep cleaning' }, awaiting: 'contact', updatedAt: new Date(0).toISOString() };
    const before = structuredClone(input);
    const { draft } = setDraftFields(input, defs, { contact: 'alex@example.com' }, services, now);
    expect(input).toEqual(before);
    expect(draft.fields).toEqual({ name: 'Alex', service: 'Deep cleaning', contact: 'alex@example.com' });
    expect(draft.fields).not.toBe(input.fields);
    expect(draft.awaiting).toBe('contact');
  });
});
