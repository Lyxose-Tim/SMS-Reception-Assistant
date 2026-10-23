import { describe, expect, it } from 'vitest';
import { ownerBookingEmail, ownerHandoffEmail, ownerOfflineContactEmail, sanitizeField, visitorReceiptEmail } from './templates';

describe('sanitizeField', () => {
  it('removes links and control characters, collapses whitespace and caps length', () => {
    expect(sanitizeField('see https://evil.example/x and www.spam.example now')).toBe('see [link removed] and [link removed] now');
    expect(sanitizeField('a\u0000b\n\n c')).toBe('ab c');
    expect(sanitizeField('x'.repeat(500))).toHaveLength(200);
  });

  it('removes control and zero-width characters before stripping links', () => {
    expect(sanitizeField('ht\u0000tps://evil.example')).not.toContain('evil.example');
    expect(sanitizeField('ht\u200Btps://evil.example/a')).not.toContain('evil.example');
    expect(sanitizeField('www\u202E.evil.example')).not.toContain('evil.example');
    expect(sanitizeField('a\uFEFFb\u2060c')).toBe('abc');
  });

  it('strips scheme urls, bare domains with a path and leaves normal text alone', () => {
    expect(sanitizeField('ftp://x.example/a')).toBe('[link removed]');
    expect(sanitizeField('evil.example/path ok')).toBe('[link removed] ok');
    expect(sanitizeField('Sat 10am')).toBe('Sat 10am');
    expect(sanitizeField('a@b.co')).toBe('a@b.co');
  });

  it('caps by code points without splitting surrogate pairs', () => {
    const out = sanitizeField('😀'.repeat(300));
    expect(Array.from(out)).toHaveLength(200);
    expect(out).not.toMatch(/[\uD800-\uDBFF](?![\uDC00-\uDFFF])/);
  });
});

describe('email templates', () => {
  const fields = [
    { label: 'service', value: 'Deep cleaning' },
    { label: 'name', value: 'Alex https://evil.example' },
  ];

  it('builds the owner booking email in English and Chinese', () => {
    const en = ownerBookingEmail({ businessName: 'Harbor', fields, adminUrl: 'https://x.example/admin' }, 'en');
    expect(en.subject).toBe('[Harbor] New booking request (pending)');
    expect(en.text).toContain('- service: Deep cleaning');
    expect(en.text).toContain('- name: Alex [link removed]');
    expect(en.text).toContain('https://x.example/admin');
    const zh = ownerBookingEmail({ businessName: 'Harbor', fields, adminUrl: 'https://x.example/admin' }, 'zh-CN');
    expect(zh.subject).toBe('【Harbor】新的预约申请（待确认）');
  });

  it('describes handoff reasons', () => {
    const en = ownerHandoffEmail({ businessName: 'Harbor', reason: 'visitor_request', excerpt: 'talk to a human please', adminUrl: 'u' }, 'en');
    expect(en.text).toContain('The visitor asked for a person');
    expect(en.text).toContain('talk to a human please');
  });

  it('keeps CR/LF out of subjects built from the business name', () => {
    const name = 'Harbor\r\nBcc: x';
    const subjects = [
      ownerBookingEmail({ businessName: name, fields, adminUrl: 'u' }, 'en').subject,
      ownerBookingEmail({ businessName: name, fields, adminUrl: 'u' }, 'zh-CN').subject,
      ownerHandoffEmail({ businessName: name, reason: 'visitor_request', excerpt: 'x', adminUrl: 'u' }, 'en').subject,
      ownerOfflineContactEmail({ businessName: name, contact: fields, note: '', adminUrl: 'u' }, 'zh-CN').subject,
      visitorReceiptEmail({ businessName: name, fields }, 'en').subject,
      visitorReceiptEmail({ businessName: name, fields }, 'zh-CN').subject,
    ];
    for (const subject of subjects) expect(subject).not.toMatch(/[\r\n]/);
  });

  it('builds the visitor receipt without links from visitor input', () => {
    const r = visitorReceiptEmail({ businessName: 'Harbor', fields }, 'en');
    expect(r.subject).toBe('Harbor: we received your booking request');
    expect(r.text).toContain('pending confirmation');
    expect(r.text).not.toContain('evil.example');
  });
});
