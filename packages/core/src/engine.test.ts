import { describe, expect, it } from 'vitest';
import samples from '../evals/injection.json';
import { demoKnowledge } from './demo';
import { handleVisitorMessage } from './engine';
import type { BookingDraft, EngineContext, ModelInput, ModelOutput, ReplyModel } from './types';

const now = new Date('2026-10-10T12:00:00Z');

function ctx(overrides: Partial<EngineContext['conversation']> = {}, extra: Partial<EngineContext> = {}): EngineContext {
  return {
    business: demoKnowledge(),
    conversation: { status: 'bot', locale: null, bookingDraft: null, visitorMessageCount: 0, ...overrides },
    history: [],
    localeHint: 'en-US',
    now,
    ...extra,
  };
}

function fakeModel(impl: (input: ModelInput) => ModelOutput | Promise<ModelOutput>): ReplyModel & { calls: ModelInput[] } {
  const calls: ModelInput[] = [];
  return {
    calls,
    async generate(input) {
      calls.push(input);
      return impl(input);
    },
  };
}

const completeFields = { service: 'deep clean', preferred_time: 'Saturday 10am', name: 'Alex', contact: 'alex@example.com' };

describe('handleVisitorMessage: gating', () => {
  it('stays silent while a human has taken over', async () => {
    const draft: BookingDraft = { fields: { service: 'Deep cleaning' }, awaiting: 'name', updatedAt: now.toISOString() };
    const out = await handleVisitorMessage(ctx({ status: 'human', bookingDraft: draft }), 'hello?');
    expect(out.replies).toEqual([]);
    expect(out.bookingDraft).toEqual(draft);
    expect(out.meta.engine).toBe('none');
  });

  it('enforces the per-conversation message limit', async () => {
    const out = await handleVisitorMessage(ctx({ visitorMessageCount: 100 }), 'hello');
    expect(out.handoff).toEqual({ reason: 'message_limit' });
    expect(out.replies[0]).toContain('hello@harborclean.example');
  });

  it('drops an expired draft and says so', async () => {
    const stale: BookingDraft = { fields: { service: 'Deep cleaning' }, awaiting: 'name', updatedAt: '2026-10-08T12:00:00Z' };
    const out = await handleVisitorMessage(ctx({ bookingDraft: stale }), 'What are your hours?');
    expect(out.replies[0]).toBe('Your earlier booking details expired, so we will start again.');
    expect(out.replies[1]).toMatch(/^Our hours:/);
    expect(out.bookingDraft).toBeNull();
  });
});

describe('handleVisitorMessage: rules fallback', () => {
  it('uses rules when no model is configured', async () => {
    const out = await handleVisitorMessage(ctx(), 'What are your hours?');
    expect(out.meta).toMatchObject({ engine: 'rules', fallbackReason: 'no_model' });
    expect(out.locale).toBe('en');
  });

  it('keeps the conversation locale for an ambiguous message', async () => {
    const out = await handleVisitorMessage(ctx({ locale: 'zh-CN' }), 'hours?');
    expect(out.locale).toBe('zh-CN');
    expect(out.replies[0]).toMatch(/^营业时间：/);
  });

  it('continues a persisted draft (simulated restart between turns)', async () => {
    const first = await handleVisitorMessage(ctx(), 'I want to book a deep cleaning');
    const persisted = JSON.parse(JSON.stringify(first.bookingDraft)) as BookingDraft;
    const second = await handleVisitorMessage(ctx({ bookingDraft: persisted }), 'Saturday morning');
    expect(second.bookingDraft?.fields).toEqual({ service: 'Deep cleaning', preferred_time: 'Saturday morning' });
  });

  it('falls back on model errors', async () => {
    const model = fakeModel(() => {
      throw new Error('401 invalid key');
    });
    const out = await handleVisitorMessage(ctx(), 'What are your hours?', { model });
    expect(out.meta).toMatchObject({ engine: 'rules', fallbackReason: 'model_error' });
  });

  it('falls back on empty model replies', async () => {
    const model = fakeModel(() => ({ text: '   ', actions: [] }));
    const out = await handleVisitorMessage(ctx(), 'What are your hours?', { model });
    expect(out.meta.fallbackReason).toBe('empty_reply');
  });

  it('times out a hanging model, aborts it and falls back', async () => {
    const model = fakeModel(() => new Promise<ModelOutput>(() => {}));
    const out = await handleVisitorMessage(ctx(), 'What are your hours?', { model, timeoutMs: 20 });
    expect(out.meta).toMatchObject({ engine: 'rules', fallbackReason: 'timeout' });
    expect(model.calls[0]!.signal.aborted).toBe(true);
  });
});

describe('handleVisitorMessage: AI path', () => {
  it('returns model text and passes budgets to the model', async () => {
    const model = fakeModel(() => ({ text: 'We open at 8 AM.', actions: [] }));
    const out = await handleVisitorMessage(ctx(), 'When do you open on Monday?', { model });
    expect(out.replies).toEqual(['We open at 8 AM.']);
    expect(out.meta.engine).toBe('ai');
    expect(model.calls[0]!.maxOutputTokens).toBe(400);
    expect(model.calls[0]!.system).toContain('Reply in English');
  });

  it('asks the model to answer in Chinese for a Chinese question', async () => {
    const model = fakeModel(() => ({ text: '我们早上8点开门。', actions: [] }));
    const out = await handleVisitorMessage(ctx(), '周一几点开门？', { model });
    expect(out.locale).toBe('zh-CN');
    expect(model.calls[0]!.system).toContain('Reply in Simplified Chinese');
  });

  it('submits a complete booking with a fixed template and ignores model text', async () => {
    const model = fakeModel(() => ({
      text: 'Confirmed! Everything is free tomorrow.',
      actions: [{ type: 'update_booking_draft', fields: completeFields }, { type: 'submit_booking_request' }],
    }));
    const out = await handleVisitorMessage(ctx(), 'book me in', { model });
    expect(out.bookingRequest).toEqual({
      fields: { service: 'Deep cleaning', preferred_time: 'Saturday 10am', name: 'Alex', contact: 'alex@example.com' },
    });
    expect(out.bookingDraft).toBeNull();
    expect(out.replies).toHaveLength(1);
    expect(out.replies[0]).toContain('pending confirmation');
    expect(out.replies[0]).not.toContain('free');
  });

  it('refuses an incomplete submit and asks for the next field', async () => {
    const model = fakeModel(() => ({
      text: 'Booked!',
      actions: [{ type: 'update_booking_draft', fields: { service: 'deep' } }, { type: 'submit_booking_request' }],
    }));
    const out = await handleVisitorMessage(ctx(), 'deep clean please, book it', { model });
    expect(out.bookingRequest).toBeUndefined();
    expect(out.bookingDraft?.fields).toEqual({ service: 'Deep cleaning' });
    expect(out.replies).toEqual(['Could you share your preferred date and time?']);
  });

  it('stores validated draft fields from the model', async () => {
    const model = fakeModel(() => ({
      text: 'Great, what time works for you?',
      actions: [{ type: 'update_booking_draft', fields: { service: 'deep', contact: 'not-a-contact', evil: 'x' } }],
    }));
    const out = await handleVisitorMessage(ctx(), 'deep clean', { model });
    expect(out.bookingDraft?.fields).toEqual({ service: 'Deep cleaning' });
    expect(out.replies).toEqual(['Great, what time works for you?']);
  });

  it('hands off with a fixed template', async () => {
    const model = fakeModel(() => ({ text: 'sure', actions: [{ type: 'handoff_to_human', reason: 'not in info' }] }));
    const out = await handleVisitorMessage(ctx(), 'Do you clean boats?', { model });
    expect(out.handoff).toEqual({ reason: 'model_request' });
    expect(out.replies[0]).toMatch(/^I've asked a team member to join/);
  });

  it('caps very long model replies', async () => {
    const model = fakeModel(() => ({ text: 'x'.repeat(5000), actions: [] }));
    const out = await handleVisitorMessage(ctx(), 'tell me everything', { model });
    expect(out.replies[0]!.length).toBe(1200);
  });
});

describe('prompt-injection isolation', () => {
  it.each(samples.map((s) => [s.id, s.text] as const))('%s: visitor text never reaches the system prompt', async (_id, text) => {
    const model = fakeModel(() => ({ text: 'ok', actions: [] }));
    await handleVisitorMessage(ctx(), text, { model });
    const input = model.calls[0]!;
    expect(input.system).not.toContain(text);
    const last = input.messages.at(-1)!;
    expect(last.role).toBe('user');
    expect(last.content.endsWith('</visitor_message>')).toBe(true);
    expect(last.content.match(/<\/visitor_message>/g)).toHaveLength(1);
    expect(last.content.match(/<business_info>/g)).toBeNull();
  });
});
