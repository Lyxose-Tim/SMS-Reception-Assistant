import { eq } from 'drizzle-orm';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { auditLog, bookingRequests } from '../../db/schema';
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

  it('always varies on Origin, including Origin-less responses and 403s', async () => {
    const noOrigin = await h.request('/api/widget/b/harbor-home/config');
    expect(noOrigin.headers.get('vary')).toContain('Origin');
    const denied = await h.request('/api/widget/b/harbor-home/config', { headers: { origin: 'https://evil.example' } });
    expect(denied.status).toBe(403);
    expect(denied.headers.get('vary')).toContain('Origin');
    expect(denied.headers.get('access-control-allow-origin')).toBeNull();
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

describe('widget 404s are readable cross-origin', () => {
  const ELSEWHERE = 'https://elsewhere.example';
  const unknownConversation = () => `/api/widget/c/${crypto.randomUUID()}/messages`;

  function expectPublicCors(res: Response) {
    expect(res.headers.get('access-control-allow-origin')).toBe(ELSEWHERE);
    expect(res.headers.get('access-control-allow-methods')).toBe('GET, POST, OPTIONS');
    expect(res.headers.get('access-control-allow-headers')).toBe('Content-Type, Authorization, Last-Event-ID');
    expect(res.headers.get('access-control-max-age')).toBe('600');
    expect(res.headers.get('access-control-allow-credentials')).toBeNull();
    expect(res.headers.get('vary')).toContain('Origin');
  }

  it('answers the preflight for an unknown conversation with 204 and CORS', async () => {
    const res = await h.request(unknownConversation(), {
      method: 'OPTIONS',
      headers: { origin: ELSEWHERE, 'access-control-request-method': 'POST' },
    });
    expect(res.status).toBe(204);
    expectPublicCors(res);
  });

  it('returns a CORS-readable 404 for an unknown conversation', async () => {
    const res = await h.request(unknownConversation(), {
      method: 'POST',
      headers: { origin: ELSEWHERE, authorization: 'Bearer whatever', 'content-type': 'application/json' },
      body: JSON.stringify({ text: 'hi' }),
    });
    expect(res.status).toBe(404);
    expect(await res.json()).toEqual({ error: 'not_found' });
    expectPublicCors(res);
  });

  it('returns a CORS-readable 404 for an unknown business slug', async () => {
    const res = await h.request('/api/widget/b/nope/config', { headers: { origin: ELSEWHERE } });
    expect(res.status).toBe(404);
    expect(await res.json()).toEqual({ error: 'not_found' });
    expectPublicCors(res);
  });

  it('omits CORS headers on a 404 without an Origin header', async () => {
    const res = await h.request(unknownConversation());
    expect(res.status).toBe(404);
    expect(res.headers.get('access-control-allow-origin')).toBeNull();
    expect(res.headers.get('vary')).toContain('Origin');
  });

  it('still applies the per-business allow-list to a known conversation', async () => {
    const chat = await startChat();
    const res = await h.request(`/api/widget/c/${chat.conversationId}/messages`, {
      headers: { origin: ELSEWHERE, authorization: `Bearer ${chat.visitorToken}` },
    });
    expect(res.status).toBe(403);
    expect(res.headers.get('access-control-allow-origin')).toBeNull();
    const preflight = await h.request(`/api/widget/c/${chat.conversationId}/messages`, {
      method: 'OPTIONS',
      headers: { origin: ELSEWHERE, 'access-control-request-method': 'POST' },
    });
    expect(preflight.status).toBe(403);
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

  it('does not consume the contact limiter on invalid submissions', async () => {
    const chat = await startChat();
    const post = (body: unknown) =>
      h.request(`/api/widget/c/${chat.conversationId}/contact`, {
        method: 'POST',
        headers: { origin: ORIGIN, authorization: `Bearer ${chat.visitorToken}`, 'content-type': 'application/json' },
        body: JSON.stringify(body),
      });
    for (let i = 0; i < 6; i++) expect((await post({ name: 'x' })).status).toBe(422);
    expect((await post({ email: 'alex@example.com' })).status).toBe(200);
  });

  it('does not consume the conversation budget when the IP limit rejects a message', async () => {
    const chat = await startChat('192.0.2.60');
    for (let i = 0; i < 60; i++) h.deps.limiters.ipMessage.hit('192.0.2.60');
    for (let i = 0; i < 5; i++) expect((await send(chat, { text: 'What are your hours?' }, '192.0.2.60')).status).toBe(429);
    for (let i = 0; i < 20; i++) expect((await send(chat, { text: 'What are your hours?' }, '192.0.2.61')).status).toBe(200);
  });

  it('hides system messages from the visitor listing after an offline contact', async () => {
    const chat = await startChat();
    const headers = { origin: ORIGIN, authorization: `Bearer ${chat.visitorToken}`, 'content-type': 'application/json' };
    const contact = await h.request(`/api/widget/c/${chat.conversationId}/contact`, {
      method: 'POST',
      headers,
      body: JSON.stringify({ email: 'alex@example.com' }),
    });
    expect(contact.status).toBe(200);
    const list = await h.request(`/api/widget/c/${chat.conversationId}/messages?since=0`, { headers });
    const messages = ((await list.json()) as { messages: { role: string }[] }).messages;
    expect(messages.map((m) => m.role)).not.toContain('system');
  });

  it('rejects conversation requests from origins the business does not allow', async () => {
    const chat = await startChat();
    const res = await h.request(`/api/widget/c/${chat.conversationId}/messages`, {
      headers: { origin: 'https://other-shop.example', authorization: `Bearer ${chat.visitorToken}` },
    });
    expect(res.status).toBe(403);
  });

  it('rejects oversized bodies', async () => {
    const chat = await startChat();
    expect((await send(chat, { text: 'x'.repeat(40 * 1024) })).status).toBe(413);
  });
});

describe('booking and receipt throttling', () => {
  async function book(chat: { conversationId: string; visitorToken: string }, email: string, ip?: string): Promise<{ messages: { role: string; content: string }[] }> {
    let last: { messages: { role: string; content: string }[] } = { messages: [] };
    for (const text of ['I want to book a deep cleaning', 'Saturday morning', 'Alex', email]) {
      const res = await send(chat, { text }, ip);
      expect(res.status).toBe(200);
      last = (await res.json()) as typeof last;
    }
    return last;
  }
  const bookingCount = async () => (await h.database.db.select().from(bookingRequests)).length;
  const visitorMail = () => h.mailer.sent.filter((m) => m.to !== 'owner@harbor.example');
  const ownerMail = () => h.mailer.sent.filter((m) => m.to === 'owner@harbor.example');

  it('sends only one receipt per conversation per day', async () => {
    const chat = await startChat();
    await book(chat, 'one@example.com');
    await book(chat, 'two@example.com');
    await vi.waitFor(() => expect(ownerMail()).toHaveLength(2));
    expect(visitorMail().map((m) => m.to)).toEqual(['one@example.com']);
    expect(await bookingCount()).toBe(2);
    const rows = await h.database.db.select().from(auditLog).where(eq(auditLog.action, 'receipt_skipped'));
    expect(rows).toHaveLength(1);
  });

  it('refuses a 4th booking in one conversation with a fixed template and flags attention', async () => {
    const chat = await startChat();
    for (const email of ['a@example.com', 'b@example.com', 'c@example.com']) await book(chat, email);
    expect(await bookingCount()).toBe(3);
    const fourth = await book(chat, 'd@example.com');
    expect(await bookingCount()).toBe(3);
    expect(fourth.messages.at(-1)).toMatchObject({
      role: 'bot',
      content: "We've already received several booking requests from this chat. A team member will follow up with you.",
    });
    const conv = (await getConversation(h.database.db, chat.conversationId))!;
    expect(conv).toMatchObject({ needsAttention: true, attentionReason: 'booking_limit', bookingDraft: null });
    const rows = await h.database.db.select().from(auditLog).where(eq(auditLog.action, 'booking_limit'));
    expect(rows).toHaveLength(1);
  });

  it('sends at most 3 receipts per IP per hour across conversations', async () => {
    const ip = '192.0.2.88';
    for (let i = 0; i < 4; i++) await book(await startChat(ip), `v${i}@example.com`, ip);
    await vi.waitFor(() => expect(ownerMail()).toHaveLength(4));
    expect(visitorMail()).toHaveLength(3);
    expect(await bookingCount()).toBe(4);
  });
});
