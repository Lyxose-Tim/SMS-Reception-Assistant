import { describe, expect, it } from 'vitest';
import { demoKnowledge } from '../demo';
import type { BookingDraft } from '../types';
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
