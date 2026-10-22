import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { agentReply } from '../services/chat';
import { createTestHarness, type TestHarness } from '../testing/harness';
import { listen } from './listen';

const ORIGIN = 'https://harbor.example';
let h: TestHarness;
let server: { url: string; close(): Promise<void> };

beforeEach(async () => {
  h = await createTestHarness({ heartbeatMs: 50 });
  server = await listen(h.app, '127.0.0.1', 0);
});
afterEach(async () => {
  await server.close();
  h.close();
});

async function startChat() {
  const res = await fetch(`${server.url}/api/widget/b/harbor-home/conversations`, { method: 'POST', headers: { origin: ORIGIN } });
  return (await res.json()) as { conversationId: string; visitorToken: string };
}

function sendMessage(chat: { conversationId: string; visitorToken: string }, text: string) {
  return fetch(`${server.url}/api/widget/c/${chat.conversationId}/messages`, {
    method: 'POST',
    headers: { origin: ORIGIN, authorization: `Bearer ${chat.visitorToken}`, 'content-type': 'application/json' },
    body: JSON.stringify({ text }),
  });
}

interface OpenStream {
  status: number;
  until(predicate: (text: string) => boolean, timeoutMs?: number): Promise<string>;
  ended(timeoutMs?: number): Promise<boolean>;
  abort(): void;
}

async function openStream(path: string, headers: Record<string, string>): Promise<OpenStream> {
  const controller = new AbortController();
  const res = await fetch(`${server.url}${path}`, { headers, signal: controller.signal });
  const reader = res.body!.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let done = false;
  const readOnce = async (timeoutMs: number) => {
    const chunk = await Promise.race([
      reader.read(),
      new Promise<never>((_, reject) => setTimeout(() => reject(new Error(`timeout; received: ${buffer}`)), timeoutMs)),
    ]);
    if (chunk.done) done = true;
    else buffer += decoder.decode(chunk.value, { stream: true });
  };
  return {
    status: res.status,
    async until(predicate, timeoutMs = 2000) {
      const deadline = Date.now() + timeoutMs;
      while (!predicate(buffer)) {
        if (done) throw new Error(`stream ended; received: ${buffer}`);
        await readOnce(Math.max(1, deadline - Date.now()));
      }
      return buffer;
    },
    async ended(timeoutMs = 2000) {
      const deadline = Date.now() + timeoutMs;
      while (!done && Date.now() < deadline) await readOnce(Math.max(1, deadline - Date.now())).catch(() => undefined);
      return done;
    },
    abort: () => controller.abort(),
  };
}

const visitorHeaders = (token: string, extra: Record<string, string> = {}) => ({ origin: ORIGIN, authorization: `Bearer ${token}`, ...extra });

describe('widget SSE', () => {
  it('delivers agent replies live', async () => {
    const chat = await startChat();
    const stream = await openStream(`/api/widget/c/${chat.conversationId}/events`, visitorHeaders(chat.visitorToken));
    expect(stream.status).toBe(200);
    await stream.until((t) => t.includes('event: ready'));
    await agentReply(h.deps, { businessId: h.business.id, conversationId: chat.conversationId, userId: h.owner.id, text: 'Hi from Sam' });
    const text = await stream.until((t) => t.includes('Hi from Sam'));
    expect(text).toContain('event: message');
    expect(text).toContain('event: status');
    stream.abort();
  });

  it('replays messages after Last-Event-ID and sends heartbeats', async () => {
    const chat = await startChat();
    await sendMessage(chat, 'What are your hours?');
    const stream = await openStream(`/api/widget/c/${chat.conversationId}/events`, visitorHeaders(chat.visitorToken, { 'last-event-id': '0' }));
    const text = await stream.until((t) => t.includes('Our hours') && t.includes(': ping'));
    expect(text.match(/event: message/g)).toHaveLength(2);
    stream.abort();
  });

  it('requires the visitor token and an allowed origin', async () => {
    const chat = await startChat();
    expect((await fetch(`${server.url}/api/widget/c/${chat.conversationId}/events`, { headers: { origin: ORIGIN } })).status).toBe(401);
    const evil = await fetch(`${server.url}/api/widget/c/${chat.conversationId}/events`, {
      headers: { origin: 'https://evil.example', authorization: `Bearer ${chat.visitorToken}` },
    });
    expect(evil.status).toBe(403);
  });

  it('caps concurrent streams per conversation by closing the oldest', async () => {
    const chat = await startChat();
    const streams: OpenStream[] = [];
    for (let i = 0; i < 4; i++) {
      const s = await openStream(`/api/widget/c/${chat.conversationId}/events`, visitorHeaders(chat.visitorToken));
      await s.until((t) => t.includes('event: ready'));
      streams.push(s);
    }
    expect(await streams[0]!.ended()).toBe(true);
    expect(h.deps.sse.count(`visitor:${chat.conversationId}`)).toBe(3);
    for (const s of streams) s.abort();
  });

  it('cleans up listeners after many disconnects', async () => {
    const chat = await startChat();
    for (let i = 0; i < 100; i++) {
      const s = await openStream(`/api/widget/c/${chat.conversationId}/events`, visitorHeaders(chat.visitorToken));
      await s.until((t) => t.includes('event: ready'));
      s.abort();
    }
    await vi.waitFor(
      () => {
        expect(h.deps.bus.listenerCount()).toBe(0);
        expect(h.deps.sse.count()).toBe(0);
      },
      { timeout: 5000, interval: 50 },
    );
  });
});

describe('admin SSE', () => {
  it('requires a session and streams business events', async () => {
    expect((await fetch(`${server.url}/api/admin/events`)).status).toBe(401);
    const cookie = await h.login();
    const stream = await openStream('/api/admin/events', { cookie });
    await stream.until((t) => t.includes('event: ready'));
    const chat = await startChat();
    await sendMessage(chat, 'What are your hours?');
    const text = await stream.until((t) => t.includes('event: conversation') && t.includes('Our hours'));
    expect(text).toContain(chat.conversationId);
    stream.abort();
  });
});
