import { describe, expect, it } from 'vitest';
import { ownerBookingEmail, ownerHandoffEmail, sanitizeField, visitorReceiptEmail } from './templates';

describe('sanitizeField', () => {
  it('removes links and control characters, collapses whitespace and caps length', () => {
    expect(sanitizeField('see https://evil.example/x and www.spam.example now')).toBe('see [link removed] and [link removed] now');
    expect(sanitizeField('a\u0000b\n\n c')).toBe('ab c');
    expect(sanitizeField('x'.repeat(500))).toHaveLength(200);
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

  it('builds the visitor receipt without links from visitor input', () => {
    const r = visitorReceiptEmail({ businessName: 'Harbor', fields }, 'en');
    expect(r.subject).toBe('Harbor: we received your booking request');
    expect(r.text).toContain('pending confirmation');
    expect(r.text).not.toContain('evil.example');
  });
});
