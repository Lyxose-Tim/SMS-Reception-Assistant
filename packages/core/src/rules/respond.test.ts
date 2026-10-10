import { describe, expect, it } from 'vitest';
import { demoKnowledge } from '../demo';
import type { BookingDraft } from '../types';
import { t } from '../templates';
import { respondWithRules } from './respond';

const k = demoKnowledge();
const now = new Date('2026-10-10T12:00:00Z');

describe('respondWithRules: questions', () => {
  it('answers hours from the profile', () => {
    const out = respondWithRules(k, 'What are your hours?', 'en', null, now);
    expect(out.replies).toEqual([`Our hours: ${k.profile.hours}`]);
    expect(out.handoff).toBeUndefined();
  });

  it('answers a Chinese question with a Chinese template', () => {
    const out = respondWithRules(k, '你们几点开门？', 'zh-CN', null, now);
    expect(out.replies[0]).toBe(`营业时间：${k.profile.hours}`);
  });

  it('prefers a matching FAQ answer', () => {
    const out = respondWithRules(k, 'Do you bring supplies?', 'en', null, now);
    expect(out.replies).toEqual([k.faqs[0]!.answer]);
  });

  it('answers pricing with the price note', () => {
    const out = respondWithRules(k, 'How much does it cost?', 'en', null, now);
    expect(out.replies[0]).toContain('Deep cleaning: Starting at $220');
    expect(out.replies[0]).toContain(k.profile.priceNote);
  });

  it('shows the menu for a bare greeting', () => {
    expect(respondWithRules(k, 'hello', 'en', null, now).replies[0]).toMatch(/^Hi! I can help/);
  });

  it('hands off unknown questions', () => {
    const out = respondWithRules(k, 'Do you clean swimming pools?', 'en', null, now);
    expect(out.handoff).toEqual({ reason: 'unknown_question' });
    expect(out.replies[0]).toMatch(/passed your question to the team/);
  });

  it('hands off when the visitor asks for a person', () => {
    const out = respondWithRules(k, 'I want to talk to a real person', 'en', null, now);
    expect(out.handoff).toEqual({ reason: 'visitor_request' });
  });
});

describe('respondWithRules: booking', () => {
  it('collects all required fields across turns and submits', () => {
    let out = respondWithRules(k, "I'd like to book a deep cleaning", 'en', null, now);
    expect(out.bookingDraft?.fields).toEqual({ service: 'Deep cleaning' });
    expect(out.bookingDraft?.awaiting).toBe('preferred_time');
    expect(out.replies).toEqual(['Could you share your preferred date and time?']);

    out = respondWithRules(k, 'Saturday morning', 'en', out.bookingDraft, now);
    expect(out.replies).toEqual(['Could you share your name?']);

    out = respondWithRules(k, 'Alex', 'en', out.bookingDraft, now);
    expect(out.replies).toEqual(['What email address or phone number can we reach you at?']);

    out = respondWithRules(k, 'not sure', 'en', out.bookingDraft, now);
    expect(out.replies).toEqual([
      "That doesn't look like an email address or phone number.",
      'What email address or phone number can we reach you at?',
    ]);
    expect(out.bookingRequest).toBeUndefined();

    out = respondWithRules(k, 'alex@example.com', 'en', out.bookingDraft, now);
    expect(out.bookingDraft).toBeNull();
    expect(out.bookingRequest).toEqual({
      fields: { service: 'Deep cleaning', preferred_time: 'Saturday morning', name: 'Alex', contact: 'alex@example.com' },
    });
    expect(out.replies[0]).toContain('pending confirmation');
    expect(out.replies[0]).toContain('alex@example.com');
  });

  it('asks for the service list when the service is not given', () => {
    const out = respondWithRules(k, '我想预约', 'zh-CN', null, now);
    expect(out.replies[0]).toBe('您想预约哪项服务？可选：1. Standard cleaning, 2. Deep cleaning, 3. Move-out cleaning。');
    expect(out.bookingDraft?.awaiting).toBe('service');
  });

  it('answers a question mid-booking and re-asks the awaited field', () => {
    const draft: BookingDraft = { fields: { service: 'Deep cleaning', preferred_time: 'Sat' }, awaiting: 'name', updatedAt: now.toISOString() };
    const out = respondWithRules(k, 'Do you bring supplies?', 'en', draft, now);
    expect(out.replies).toEqual([k.faqs[0]!.answer, 'Could you share your name?']);
    expect(out.bookingDraft).toEqual(draft);
  });

  it('cancels an active draft', () => {
    const draft: BookingDraft = { fields: { service: 'Deep cleaning' }, awaiting: 'preferred_time', updatedAt: now.toISOString() };
    const out = respondWithRules(k, 'cancel', 'en', draft, now);
    expect(out.bookingDraft).toBeNull();
    expect(out.replies).toEqual(["No problem, I've cancelled this booking request."]);
  });
});

describe('respondWithRules: cancel without a draft', () => {
  it('hands off an existing-appointment cancel instead of starting a booking', () => {
    const out = respondWithRules(k, 'I want to cancel my appointment', 'en', null, now);
    expect(out.handoff).toEqual({ reason: 'visitor_request' });
    expect(out.bookingDraft).toBeNull();
    expect(out.replies).toEqual([t('en', 'cancel_existing')]);
  });

  it('does the same in Chinese', () => {
    const out = respondWithRules(k, '取消预约', 'zh-CN', null, now);
    expect(out.handoff).toEqual({ reason: 'visitor_request' });
    expect(out.bookingDraft).toBeNull();
    expect(out.replies).toEqual([t('zh-CN', 'cancel_existing')]);
  });

  it.each(['Can I cancel my appointment?', 'How do I cancel my booking?'])('does not start a booking from the question %j', (text) => {
    const out = respondWithRules(k, text, 'en', null, now);
    expect(out.replies).toEqual([t('en', 'cancel_existing')]);
    expect(out.handoff).toEqual({ reason: 'visitor_request' });
    expect(out.bookingDraft).toBeNull();
    expect(out.bookingRequest).toBeUndefined();
  });

  it('does not start a booking from an unpunctuated Chinese cancel question', () => {
    const out = respondWithRules(k, '可以取消预约吗', 'zh-CN', null, now);
    expect(out.replies).toEqual([t('zh-CN', 'cancel_existing')]);
    expect(out.handoff).toEqual({ reason: 'visitor_request' });
    expect(out.bookingDraft).toBeNull();
  });

  it('answers a cancel-flavoured question the knowledge base can answer, without handoff', () => {
    const out = respondWithRules(k, 'Can I cancel? What are your hours?', 'en', null, now);
    expect(out.replies).toEqual([`Our hours: ${k.profile.hours}`]);
    expect(out.handoff).toBeUndefined();
    expect(out.bookingDraft).toBeNull();
  });
});

describe('respondWithRules: active draft keeps junk out of fields', () => {
  const awaitingName = (): BookingDraft => ({
    fields: { service: 'Deep cleaning', preferred_time: 'Sat' },
    awaiting: 'name',
    updatedAt: '2026-10-09T08:00:00.000Z',
  });

  it('does not store an unanswerable question as the name', () => {
    const out = respondWithRules(k, 'Do you clean swimming pools?', 'en', awaitingName(), now);
    expect(out.replies).toEqual([t('en', 'booking_question_unanswered'), 'Could you share your name?']);
    expect(out.bookingDraft?.fields).toEqual({ service: 'Deep cleaning', preferred_time: 'Sat' });
    expect(out.bookingDraft?.awaiting).toBe('name');
    expect(out.bookingDraft?.updatedAt).toBe(now.toISOString());
  });

  it('does not store a greeting as the name', () => {
    const out = respondWithRules(k, 'hello', 'en', awaitingName(), now);
    expect(out.replies).toEqual(['Could you share your name?']);
    expect(out.bookingDraft?.fields).toEqual({ service: 'Deep cleaning', preferred_time: 'Sat' });
    expect(out.bookingDraft?.updatedAt).toBe(now.toISOString());
  });

  it('does not store restated booking intent as the name', () => {
    const out = respondWithRules(k, "I'd like to book a deep cleaning", 'en', awaitingName(), now);
    expect(out.bookingDraft?.fields).toEqual({ service: 'Deep cleaning', preferred_time: 'Sat' });
    expect(out.replies).toEqual(['Could you share your name?']);
  });

  it('opportunistically fills a contact from restated booking intent', () => {
    const draft: BookingDraft = {
      fields: { service: 'Deep cleaning', preferred_time: 'Sat', name: 'Alex' },
      awaiting: 'contact',
      updatedAt: now.toISOString(),
    };
    const out = respondWithRules(k, 'I want to book, my email is alex@example.com', 'en', draft, now);
    expect(out.bookingDraft).toBeNull();
    expect(out.bookingRequest?.fields.contact).toBe('alex@example.com');
  });

  it('treats a Chinese answer containing 几 as a field value, not a question', () => {
    const draft: BookingDraft = { fields: { service: 'Deep cleaning' }, awaiting: 'preferred_time', updatedAt: now.toISOString() };
    const out = respondWithRules(k, '周六几点都可以', 'zh-CN', draft, now);
    expect(out.bookingDraft?.fields.preferred_time).toBe('周六几点都可以');
    expect(out.replies).toEqual(['请提供您的姓名。']);
  });

  it('does not cancel the draft on an explicit question about cancelling', () => {
    const out = respondWithRules(k, 'how do I cancel?', 'en', awaitingName(), now);
    expect(out.bookingDraft).not.toBeNull();
    expect(out.bookingDraft?.fields).toEqual({ service: 'Deep cleaning', preferred_time: 'Sat' });
    expect(out.replies).toEqual([t('en', 'booking_question_unanswered'), 'Could you share your name?']);
  });

  it('does not store an unpunctuated Chinese question as the name', () => {
    const out = respondWithRules(k, '你们带清洁用品吗', 'zh-CN', awaitingName(), now);
    expect(out.bookingDraft?.fields).toEqual({ service: 'Deep cleaning', preferred_time: 'Sat' });
    expect(out.bookingDraft?.awaiting).toBe('name');
    expect(out.replies.at(-1)).toBe('请提供您的姓名。');
  });

  it('answers a punctuated Chinese hours question and re-asks the name', () => {
    const out = respondWithRules(k, '你们几点开门？', 'zh-CN', awaitingName(), now);
    expect(out.replies).toEqual([`营业时间：${k.profile.hours}`, '请提供您的姓名。']);
    expect(out.bookingDraft?.fields).toEqual({ service: 'Deep cleaning', preferred_time: 'Sat' });
  });

  it('does not cancel the draft on 取消吗？', () => {
    const out = respondWithRules(k, '取消吗？', 'zh-CN', awaitingName(), now);
    expect(out.bookingDraft).not.toBeNull();
    expect(out.bookingDraft?.fields).toEqual({ service: 'Deep cleaning', preferred_time: 'Sat' });
  });

  it('returns a refreshed copy of the draft on a human handoff', () => {
    const draft = awaitingName();
    const before = structuredClone(draft);
    const out = respondWithRules(k, 'I want to talk to a real person', 'en', draft, now);
    expect(out.handoff).toEqual({ reason: 'visitor_request' });
    expect(out.bookingDraft?.fields).toEqual(before.fields);
    expect(out.bookingDraft?.updatedAt).toBe(now.toISOString());
    expect(out.bookingDraft).not.toBe(draft);
    expect(draft).toEqual(before);
  });
});

describe('respondWithRules: draft TTL refresh', () => {
  const stale = (): BookingDraft => ({
    fields: { service: 'Deep cleaning', preferred_time: 'Sat', name: 'Alex' },
    awaiting: 'contact',
    updatedAt: '2026-10-09T08:00:00.000Z',
  });

  it('refreshes updatedAt on an invalid value without mutating the input', () => {
    const draft = stale();
    const before = structuredClone(draft);
    const out = respondWithRules(k, 'not sure', 'en', draft, now);
    expect(draft).toEqual(before);
    expect(out.bookingDraft).not.toBe(draft);
    expect(out.bookingDraft?.updatedAt).toBe(now.toISOString());
    expect(out.bookingDraft?.fields).toEqual(before.fields);
  });

  it('refreshes updatedAt on a mid-booking answer without mutating the input', () => {
    const draft = stale();
    const before = structuredClone(draft);
    const out = respondWithRules(k, 'Do you bring supplies?', 'en', draft, now);
    expect(draft).toEqual(before);
    expect(out.bookingDraft).not.toBe(draft);
    expect(out.bookingDraft?.updatedAt).toBe(now.toISOString());
    expect(out.bookingDraft?.awaiting).toBe('contact');
  });
});

describe('respondWithRules: Chinese booking', () => {
  it('collects all fields across turns and submits', () => {
    let out = respondWithRules(k, '我想预约', 'zh-CN', null, now);
    expect(out.replies[0]).toContain('您想预约哪项服务');

    out = respondWithRules(k, '2', 'zh-CN', out.bookingDraft, now);
    expect(out.bookingDraft?.fields).toEqual({ service: 'Deep cleaning' });
    expect(out.replies).toEqual(['请提供您的期望的日期和时间。']);

    out = respondWithRules(k, '周六上午', 'zh-CN', out.bookingDraft, now);
    expect(out.replies).toEqual(['请提供您的姓名。']);

    out = respondWithRules(k, '小李', 'zh-CN', out.bookingDraft, now);
    expect(out.replies).toEqual(['请留下可以联系到您的邮箱或电话。']);

    out = respondWithRules(k, '13800138000', 'zh-CN', out.bookingDraft, now);
    expect(out.bookingDraft).toBeNull();
    expect(out.bookingRequest).toEqual({
      fields: { service: 'Deep cleaning', preferred_time: '周六上午', name: '小李', contact: '13800138000' },
    });
    expect(out.replies[0]).toContain('正在等待确认');
  });
});
