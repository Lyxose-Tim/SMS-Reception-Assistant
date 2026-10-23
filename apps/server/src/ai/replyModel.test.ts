import { demoKnowledge, handleVisitorMessage } from '@reception/core';
import { MockLanguageModelV4 } from 'ai/test';
import { describe, expect, it } from 'vitest';
import { createLanguageModel } from './providers';
import { createAiReplyModel } from './replyModel';
import { mockLanguageModel } from './testing';

const input = (signal = new AbortController().signal) => ({
  system: 'SYSTEM RULES',
  messages: [{ role: 'user' as const, content: '<visitor_message>hi</visitor_message>' }],
  signal,
  maxOutputTokens: 400,
});

describe('createAiReplyModel', () => {
  it('returns text and passes instructions, messages and the output cap', async () => {
    const model = mockLanguageModel({ text: 'Hello!' });
    const out = await createAiReplyModel(model).generate(input());
    expect(out).toEqual({ text: 'Hello!', actions: [] });
    const call = model.doGenerateCalls[0]!;
    expect(call.prompt[0]).toEqual({ role: 'system', content: 'SYSTEM RULES' });
    expect(JSON.stringify(call.prompt[1])).toContain('<visitor_message>hi</visitor_message>');
    expect(call.maxOutputTokens).toBe(400);
  });

  it('maps tool calls to core actions in order', async () => {
    const model = mockLanguageModel({
      calls: [
        { toolName: 'update_booking_draft', input: { fields: { service: 'deep', name: 'Alex' } } },
        { toolName: 'submit_booking_request', input: {} },
        { toolName: 'handoff_to_human', input: { reason: 'pets question' } },
      ],
    });
    const out = await createAiReplyModel(model).generate(input());
    expect(out.actions).toEqual([
      { type: 'update_booking_draft', fields: { service: 'deep', name: 'Alex' } },
      { type: 'submit_booking_request' },
      { type: 'handoff_to_human', reason: 'pets question' },
    ]);
  });

  it('drops tool calls whose input fails the schema', async () => {
    const model = mockLanguageModel({ calls: [{ toolName: 'update_booking_draft', input: { fields: 'not-an-object' } }], text: 'ok' });
    const out = await createAiReplyModel(model).generate(input());
    expect(out.actions).toEqual([]);
  });

  it('rejects when the signal aborts', async () => {
    const model = new MockLanguageModelV4({
      doGenerate: (options) =>
        new Promise((_, reject) => {
          const signal = options.abortSignal!;
          if (signal.aborted) return reject(signal.reason);
          signal.addEventListener('abort', () => reject(signal.reason));
        }),
    });
    const controller = new AbortController();
    const pending = createAiReplyModel(model).generate(input(controller.signal));
    controller.abort(new Error('timeout'));
    await expect(pending).rejects.toThrow();
  });

  it('drives a full booking through the core engine', async () => {
    const model = mockLanguageModel({
      calls: [
        {
          toolName: 'update_booking_draft',
          input: { fields: { service: 'deep clean', preferred_time: 'Sat 10am', name: 'Alex', contact: 'alex@example.com' } },
        },
        { toolName: 'submit_booking_request', input: {} },
      ],
    });
    const result = await handleVisitorMessage(
      {
        business: demoKnowledge(),
        conversation: { status: 'bot', locale: null, bookingDraft: null, visitorMessageCount: 0 },
        history: [],
        now: new Date('2026-10-11T10:00:00Z'),
      },
      'Book a deep clean Saturday 10am, Alex, alex@example.com',
      { model: createAiReplyModel(model) },
    );
    expect(result.meta.engine).toBe('ai');
    expect(result.bookingRequest?.fields.service).toBe('Deep cleaning');
    expect(result.replies[0]).toContain('pending confirmation');
  });
});

describe('createLanguageModel', () => {
  const provider = (m: unknown) => (m as { provider: string }).provider;

  it('builds a model for each provider', () => {
    expect(provider(createLanguageModel({ provider: 'openai', model: 'gpt-x', baseUrl: null }, 'k'))).toContain('openai');
    expect(provider(createLanguageModel({ provider: 'anthropic', model: 'claude-x', baseUrl: null }, 'k'))).toContain('anthropic');
    expect(provider(createLanguageModel({ provider: 'google', model: 'gemini-x', baseUrl: null }, 'k'))).toContain('google');
    expect(
      provider(createLanguageModel({ provider: 'openai-compatible', model: 'deepseek-chat', baseUrl: 'https://api.deepseek.example/v1' }, 'k')),
    ).toContain('custom');
  });

  it('requires a base URL for OpenAI-compatible providers', () => {
    expect(() => createLanguageModel({ provider: 'openai-compatible', model: 'm', baseUrl: null }, 'k')).toThrow(/base URL/i);
  });
});
