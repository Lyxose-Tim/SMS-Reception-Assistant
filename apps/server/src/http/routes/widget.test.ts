import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { getConversation } from '../../repos/conversations';
import { createTestHarness, type TestHarness } from '../../testing/harness';

let h: TestHarness;
beforeEach(async () => {
  h = await createTestHarness();
});
afterEach(() => h.close());

const ORIGIN = 'https://harbor.example';

async function startChat(ip?: string) {
  const res = await h.request('/api/widget/b/harbor-home/conversations', { method: 'POST', headers: { origin: ORIGIN } }, ip);
  expect(res.status).toBe(201);
  return (await res.json()) as { conversationId: string; visitorToken: string };
}

function send(chat: { conversationId: string; visitorToken: string }, body: unknown, ip?: string) {
  return h.request(
    `/api/widget/c/${chat.conversationId}/messages`,
    { method: 'POST', headers: { origin: ORIGIN, authorization: `Bearer ${chat.visitorToken}`, 'content-type': 'application/json' }, body: JSON.stringify(body) },
    ip,
  );
}

describe('widget config and CORS', () => {
  it('returns the public config with CORS for an allowed origin', async () => {
    const res = await h.request('/api/widget/b/harbor-home/config', { headers: { origin: ORIGIN } });
    expect(res.status).toBe(200);
    expect(res.headers.get('access-control-allow-origin')).toBe(ORIGIN);
    expect(res.headers.get('vary')).toContain('Origin');
    const body = (await res.json()) as { business: { name: string }; quickQuestions: string[]; widget: Record<string, unknown> };
    expect(body.business.name).toBe('Harbor & Home Cleaning');
    expect(body.quickQuestions).toHaveLength(4);
    expect(body.widget).toEqual({ primaryColor: '#2563eb', position: 'right', greeting: expect.any(Object) });
    expect(JSON.stringify(body)).not.toContain('owner@harbor.example');
  });

  it('allows the admin origin and requests without an Origin header', async () => {
    expect((await h.request('/api/widget/b/harbor-home/config', { headers: { origin: h.env.PUBLIC_ORIGIN } })).status).toBe(200);
    expect((await h.request('/api/widget/b/harbor-home/config')).status).toBe(200);
  });

  it('rejects other origins and unknown businesses', async () => {
    expect((await h.request('/api/widget/b/harbor-home/config', { headers: { origin: 'https://evil.example' } })).status).toBe(403);
    expect((await h.request('/api/widget/b/nope/config', { headers: { origin: ORIGIN } })).status).toBe(404);
  });

  it('answers preflight requests without a token', async () => {
    const chat = await startChat();
    const res = await h.request(`/api/widget/c/${chat.conversationId}/messages`, {
      method: 'OPTIONS',
      headers: { origin: ORIGIN, 'access-control-request-method': 'POST' },
    });
    expect(res.status).toBe(204);
    expect(res.headers.get('access-control-allow-headers')).toContain('Authorization');
  });
});

describe('widget conversations', () => {
  it('runs a conversation with the visitor token', async () => {
    const chat = await startChat();
    const res = await send(chat, { text: 'What are your hours?', locale: 'en-US' });
    expect(res.status).toBe(200);
    const body = (await res.json()) as { messages: { role: string; content: string }[]; status: string };
    expect(body.status).toBe('bot');
    expect(body.messages.map((m) => m.role)).toEqual(['visitor', 'bot']);
    const list = await h.request(`/api/widget/c/${chat.conversationId}/messages?since=0`, {
      headers: { origin: ORIGIN, authorization: `Bearer ${chat.visitorToken}` },
    });
    expect(((await list.json()) as { messages: unknown[] }).messages).toHaveLength(2);
  });

  it('requires the right token', async () => {
    const chat = await startChat();
    const other = await startChat();
    expect((await send({ ...chat, visitorToken: other.visitorToken }, { text: 'hi' })).status).toBe(401);
    const missing = await h.request(`/api/widget/c/${chat.conversationId}/messages`, { headers: { origin: ORIGIN } });
    expect(missing.status).toBe(401);
  });

  it('validates message size', async () => {
    const chat = await startChat();
    expect((await send(chat, { text: 'x'.repeat(1001) })).status).toBe(422);
    expect((await send(chat, { text: '   ' })).status).toBe(422);
    expect((await send(chat, { text: 'hi', extra: true })).status).toBe(422);
  });

  it('rate limits messages per conversation', async () => {
    const chat = await startChat();
    for (let i = 0; i < 20; i++) expect((await send(chat, { text: 'What are your hours?' })).status).toBe(200);
    expect((await send(chat, { text: 'What are your hours?' })).status).toBe(429);
  });

  it('rate limits new conversations per IP', async () => {
    for (let i = 0; i < 20; i++) await startChat('192.0.2.50');
    const res = await h.request('/api/widget/b/harbor-home/conversations', { method: 'POST', headers: { origin: ORIGIN } }, '192.0.2.50');
    expect(res.status).toBe(429);
  });

  it('accepts offline contact details and emails the owner', async () => {
    const chat = await startChat();
    const res = await h.request(`/api/widget/c/${chat.conversationId}/contact`, {
      method: 'POST',
      headers: { origin: ORIGIN, authorization: `Bearer ${chat.visitorToken}`, 'content-type': 'application/json' },
      body: JSON.stringify({ name: 'Alex', email: 'alex@example.com', note: 'Call after 5pm' }),
    });
    expect(res.status).toBe(200);
    expect((await getConversation(h.database.db, chat.conversationId))?.needsAttention).toBe(true);
    await vi.waitFor(() => expect(h.mailer.sent.map((m) => m.to)).toEqual(['owner@harbor.example']));
    const bad = await h.request(`/api/widget/c/${chat.conversationId}/contact`, {
      method: 'POST',
      headers: { origin: ORIGIN, authorization: `Bearer ${chat.visitorToken}`, 'content-type': 'application/json' },
      body: JSON.stringify({ name: 'No contact' }),
    });
    expect(bad.status).toBe(422);
  });

  it('rejects oversized bodies', async () => {
    const chat = await startChat();
    expect((await send(chat, { text: 'x'.repeat(40 * 1024) })).status).toBe(413);
  });
});
