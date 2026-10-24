import { vi } from 'vitest';
import type { MessagesResult, PublicMessage, WidgetApi, WidgetConfig } from '../api';
import type { StreamOptions } from '../sse';
import { createSessionStore, type SessionStore, type StoredSession } from '../storage';

export const DEMO_CONFIG: WidgetConfig = {
  business: { name: 'Harbor & Home Cleaning', slug: 'harbor-home' },
  locale: 'en',
  widget: {
    primaryColor: '#2563eb',
    position: 'right',
    greeting: { en: 'Hi! How can we help you today?', 'zh-CN': '您好！有什么可以帮您？' },
  },
  quickQuestions: ['What are your hours?', 'Do you bring supplies?'],
};

let autoId = 10_000;
export function msg(role: PublicMessage['role'], content: string, id: number = autoId++): PublicMessage {
  return { id, role, content, createdAt: 1_760_000_000_000 + id };
}

export function fakeApi(overrides: Partial<WidgetApi> = {}) {
  let counter = 0;
  const defaults = {
    getConfig: vi.fn(async (): Promise<WidgetConfig> => DEMO_CONFIG),
    createConversation: vi.fn(async (): Promise<StoredSession> => {
      counter += 1;
      return { conversationId: `c${counter}`, visitorToken: `t${counter}` };
    }),
    listMessages: vi.fn(async (_s: StoredSession, _since: number): Promise<MessagesResult> => ({ messages: [], status: 'bot' })),
    sendMessage: vi.fn(
      async (_s: StoredSession, text: string): Promise<MessagesResult> => ({
        messages: [msg('visitor', text), msg('bot', `Echo: ${text}`)],
        status: 'bot',
      }),
    ),
    sendContact: vi.fn(async (): Promise<void> => undefined),
    eventsUrl: vi.fn((id: string) => `https://chat.example/api/widget/c/${id}/events`),
  };
  // Tests only override with vi.fn() mocks, so keep the mock-typed shape for `.mock` access.
  return { ...defaults, ...overrides } as typeof defaults;
}

export function memorySessions(): SessionStore {
  return createSessionStore('test', 'https://chat.example', () => null);
}

/** Stream stand-in that stays open until aborted; tests drive it via `calls`. */
export function controllableStream() {
  const calls: { options: StreamOptions; end(): void; fail(error: unknown): void }[] = [];
  const stream = (options: StreamOptions) =>
    new Promise<void>((resolve, reject) => {
      calls.push({ options, end: resolve, fail: reject });
      options.signal.addEventListener('abort', () => resolve(), { once: true });
    });
  return { stream, calls };
}
