import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { agentReply, bizTopic, convTopic } from '../services/chat';
import { createTestHarness, type TestHarness } from '../testing/harness';
import { listen } from './listen';

const ORIGIN = 'https://harbor.example';
let h: TestHarness;
let server: { url: string; close(): Promise<void> };

// fetch() refuses these ports ("bad port"); an ephemeral port landing on one would make a test flake.
const FETCH_BLOCKED_PORTS = new Set([1719, 1720, 1723, 2049, 3659, 4045, 4190, 5060, 5061, 6000, 6566, 6665, 6666, 6667, 6668, 6669, 6679, 6697, 10080]);

async function listenOnFetchablePort(app: TestHarness['app']) {
  for (;;) {
    const candidate = await listen(app, '127.0.0.1', 0);
    if (!FETCH_BLOCKED_PORTS.has(Number(new URL(candidate.url).port))) return candidate;
    await candidate.close();
  }
}

beforeEach(async () => {
  h = await createTestHarness({ heartbeatMs: 50 });
  server = await listenOnFetchablePort(h.app);
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
  }, 20_000);

  it('sends X-Accel-Buffering: no so proxies do not buffer the stream', async () => {
    const chat = await startChat();
    const res = await fetch(`${server.url}/api/widget/c/${chat.conversationId}/events`, { headers: visitorHeaders(chat.visitorToken) });
    expect(res.headers.get('x-accel-buffering')).toBe('no');
    await res.body!.cancel();
  });

  it('falls back to ?since= when the Last-Event-ID header is invalid', async () => {
    const chat = await startChat();
    await sendMessage(chat, 'What are your hours?');
    const stream = await openStream(`/api/widget/c/${chat.conversationId}/events?since=0`, visitorHeaders(chat.visitorToken, { 'last-event-id': 'abc' }));
    const text = await stream.until((t) => t.includes('Our hours'));
    expect(text.match(/event: message/g)).toHaveLength(2);
    stream.abort();
  });

  it('dedupes live events only against what replay delivered', async () => {
    const chat = await startChat();
    await sendMessage(chat, 'What are your hours?');
    const stream = await openStream(`/api/widget/c/${chat.conversationId}/events`, visitorHeaders(chat.visitorToken, { 'last-event-id': '0' }));
    const replayed = await stream.until((t) => t.includes('Our hours'));
    const ids = [...replayed.matchAll(/^id: (\d+)$/gm)].map((m) => Number(m[1]));
    expect(ids.length).toBe(2);
    const max = Math.max(...ids);
    h.deps.bus.publish(convTopic(chat.conversationId), { type: 'message', id: String(ids[0]), data: { marker: 'DUP' } });
    h.deps.bus.publish(convTopic(chat.conversationId), { type: 'message', id: String(max + 1), data: { marker: 'NEW' } });
    const text = await stream.until((t) => t.includes('NEW'));
    expect(text).not.toContain('DUP');
    stream.abort();
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

  it('closes the admin stream when the session logs out', async () => {
    const cookie = await h.login();
    const stream = await openStream('/api/admin/events', { cookie });
    await stream.until((t) => t.includes('event: ready'));
    const out = await fetch(`${server.url}/api/auth/logout`, { method: 'POST', headers: { cookie, origin: h.env.PUBLIC_ORIGIN } });
    expect(out.status).toBe(200);
    expect(await stream.ended()).toBe(true);
    await vi.waitFor(() => expect(h.deps.sse.count()).toBe(0), { timeout: 3000, interval: 50 });
  });

  it('delivers out-of-order live ids (no global high-water dedupe)', async () => {
    const cookie = await h.login();
    const stream = await openStream('/api/admin/events', { cookie });
    await stream.until((t) => t.includes('event: ready'));
    h.deps.bus.publish(bizTopic(h.business.id), { type: 'message', id: '51', data: { marker: 'M51' } });
    h.deps.bus.publish(bizTopic(h.business.id), { type: 'message', id: '50', data: { marker: 'M50' } });
    h.deps.bus.publish(bizTopic(h.business.id), { type: 'message', id: 'not-a-number', data: { marker: 'MNAN' } });
    h.deps.bus.publish(bizTopic(h.business.id), { type: 'message', id: '52', data: { marker: 'M52' } });
    const text = await stream.until((t) => t.includes('M52'));
    for (const marker of ['M51', 'M50', 'MNAN']) expect(text).toContain(marker);
    stream.abort();
  });
});

describe('listen', () => {
  it('rejects when the port is already in use', async () => {
    const port = Number(new URL(server.url).port);
    await expect(listen(h.app, '127.0.0.1', port)).rejects.toThrow();
  });
});
