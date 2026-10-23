import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { mockLanguageModel } from '../../ai/testing';
import { createBusiness } from '../../repos/business';
import { createUser } from '../../repos/users';
import { createTestHarness, type TestHarness } from '../../testing/harness';

let h: TestHarness;
let cookie: string;
beforeEach(async () => {
  h = await createTestHarness({ languageModelFactory: () => mockLanguageModel({ text: 'OK' }) });
  cookie = await h.login();
});
afterEach(() => h.close());

const ORIGIN = 'https://harbor.example';
const get = (path: string, c = cookie) => h.request(`/api/admin${path}`, { headers: { cookie: c } });
const send = (method: string, path: string, body?: unknown, c = cookie) =>
  h.request(`/api/admin${path}`, { method, headers: h.adminHeaders(c), ...(body === undefined ? {} : { body: JSON.stringify(body) }) });

async function visitorChat(text: string) {
  const start = await h.request('/api/widget/b/harbor-home/conversations', { method: 'POST', headers: { origin: ORIGIN } });
  const chat = (await start.json()) as { conversationId: string; visitorToken: string };
  await h.request(`/api/widget/c/${chat.conversationId}/messages`, {
    method: 'POST',
    headers: { origin: ORIGIN, authorization: `Bearer ${chat.visitorToken}`, 'content-type': 'application/json' },
    body: JSON.stringify({ text }),
  });
  return chat;
}

describe('admin access control', () => {
  it('requires a session and the admin origin', async () => {
    expect((await h.request('/api/admin/conversations')).status).toBe(401);
    const noOrigin = await h.request('/api/admin/faqs', { method: 'POST', headers: { cookie, 'content-type': 'application/json' }, body: '{}' });
    expect(noOrigin.status).toBe(403);
  });

  it('isolates businesses', async () => {
    const chat = await visitorChat('What are your hours?');
    const other = await createBusiness(h.database.db, { slug: 'other-biz', name: 'Other' });
    await createUser(h.database.db, { businessId: other.id, email: 'other@other.example', password: 'another long password' });
    const res = await h.request('/api/auth/login', {
      method: 'POST',
      headers: { origin: h.env.PUBLIC_ORIGIN, 'content-type': 'application/json' },
      body: JSON.stringify({ email: 'other@other.example', password: 'another long password' }),
    });
    const otherCookie = res.headers.get('set-cookie')!.split(';')[0]!;
    expect((await get(`/conversations/${chat.conversationId}`, otherCookie)).status).toBe(404);
    expect((await send('POST', `/conversations/${chat.conversationId}/messages`, { text: 'hi' }, otherCookie)).status).toBe(404);
    expect(((await (await get('/conversations', otherCookie)).json()) as { conversations: unknown[] }).conversations).toHaveLength(0);
  });
});

describe('inbox', () => {
  it('lists, opens, takes over, replies and releases', async () => {
    const chat = await visitorChat('Do you clean boats?');
    const list = (await (await get('/conversations?filter=attention')).json()) as { conversations: { id: string; lastMessage: { role: string } }[] };
    expect(list.conversations.map((c) => c.id)).toEqual([chat.conversationId]);
    expect(list.conversations[0]!.lastMessage.role).toBe('bot');

    const detail = (await (await get(`/conversations/${chat.conversationId}`)).json()) as { messages: { role: string; meta: unknown }[]; conversation: { unreadCount: number } };
    expect(detail.messages.map((m) => m.role)).toEqual(['visitor', 'bot']);
    expect(detail.messages[1]!.meta).toMatchObject({ engine: 'rules', handoff: 'unknown_question' });
    expect(detail.conversation.unreadCount).toBe(0);

    expect(((await (await send('POST', `/conversations/${chat.conversationId}/takeover`)).json()) as { conversation: { status: string } }).conversation.status).toBe('human');
    const reply = await send('POST', `/conversations/${chat.conversationId}/messages`, { text: 'Hi, Sam here — we do not clean boats.' });
    expect(reply.status).toBe(201);
    const visitorView = await h.request(`/api/widget/c/${chat.conversationId}/messages`, { headers: { origin: ORIGIN, authorization: `Bearer ${chat.visitorToken}` } });
    expect(((await visitorView.json()) as { messages: { role: string }[] }).messages.map((m) => m.role)).toEqual(['visitor', 'bot', 'agent']);
    expect(((await (await send('POST', `/conversations/${chat.conversationId}/release`)).json()) as { conversation: { status: string } }).conversation.status).toBe('bot');
    expect((await send('POST', `/conversations/${chat.conversationId}/messages`, { text: '' })).status).toBe(422);
  });
});

describe('business and FAQs', () => {
  it('reads and patches the business with validation', async () => {
    const body = (await (await get('/business')).json()) as { business: Record<string, unknown>; limits: { totalChars: number } };
    expect(body.business).not.toHaveProperty('ai');
    expect(body.limits.totalChars).toBe(12000);
    const ok = await send('PATCH', '/business', { name: 'Harbor Home' });
    expect(((await ok.json()) as { business: { name: string } }).business.name).toBe('Harbor Home');
    const bad = await send('PATCH', '/business', { name: '' });
    expect(bad.status).toBe(422);
    expect(((await bad.json()) as { issues: { path: string }[] }).issues[0]!.path).toBe('name');
  });

  it('creates, updates and deletes FAQs within the budget', async () => {
    const created = await send('POST', '/faqs', { question: 'Do you clean windows?', answer: 'Yes, inside windows only.' });
    expect(created.status).toBe(201);
    const { faq } = (await created.json()) as { faq: { id: string } };
    expect((await send('PATCH', `/faqs/${faq.id}`, { enabled: false })).status).toBe(200);
    expect((await send('PATCH', `/faqs/${faq.id}`, { answer: 'a'.repeat(501) })).status).toBe(422);
    expect((await send('DELETE', `/faqs/${faq.id}`)).status).toBe(204);
    expect((await send('DELETE', `/faqs/${faq.id}`)).status).toBe(404);
    expect(((await (await get('/faqs')).json()) as { faqs: unknown[] }).faqs).toHaveLength(5);
  });
});

describe('bookings', () => {
  it('confirms a pending booking once', async () => {
    const chat = await visitorChat('I want to book a deep cleaning');
    for (const text of ['Saturday morning', 'Alex', 'alex@example.com']) {
      await h.request(`/api/widget/c/${chat.conversationId}/messages`, {
        method: 'POST',
        headers: { origin: ORIGIN, authorization: `Bearer ${chat.visitorToken}`, 'content-type': 'application/json' },
        body: JSON.stringify({ text }),
      });
    }
    const { bookings } = (await (await get('/bookings?status=pending')).json()) as { bookings: { id: string }[] };
    expect(bookings).toHaveLength(1);
    const confirmed = await send('POST', `/bookings/${bookings[0]!.id}/confirm`, { note: 'See you Saturday' });
    expect(((await confirmed.json()) as { booking: { status: string } }).booking.status).toBe('confirmed');
    expect((await send('POST', `/bookings/${bookings[0]!.id}/decline`, {})).status).toBe(409);
    expect((await send('POST', '/bookings/00000000-0000-0000-0000-000000000000/confirm', {})).status).toBe(404);
    expect((await get('/bookings?status=bogus')).status).toBe(422);
  });
});

describe('AI settings', () => {
  it('saves without ever returning the key and tests the connection', async () => {
    const saved = await send('PUT', '/ai', { provider: 'openai', model: 'gpt-x', apiKey: 'sk-test-12345678', enabled: true });
    const text = await saved.text();
    expect(saved.status).toBe(200);
    expect(text).not.toContain('sk-test');
    expect(JSON.parse(text)).toEqual({ ai: { provider: 'openai', model: 'gpt-x', baseUrl: null, keyLast4: '5678', hasKey: true, enabled: true } });
    expect(await (await get('/ai')).text()).not.toContain('sk-test');
    expect(await (await send('POST', '/ai/test')).json()).toEqual({ ok: true });
    expect((await send('PUT', '/ai', { provider: 'openai', model: '', enabled: true })).status).toBe(422);
  });
});

describe('role restrictions', () => {
  it('lets only owners change AI settings', async () => {
    await createUser(h.database.db, { businessId: h.business.id, email: 'agent@harbor.example', password: 'another long password', role: 'agent' });
    const res = await h.request('/api/auth/login', {
      method: 'POST',
      headers: { origin: h.env.PUBLIC_ORIGIN, 'content-type': 'application/json' },
      body: JSON.stringify({ email: 'agent@harbor.example', password: 'another long password' }),
    });
    const agentCookie = res.headers.get('set-cookie')!.split(';')[0]!;
    const put = await send('PUT', '/ai', { provider: 'openai', model: 'gpt-x', apiKey: 'sk-test-12345678', enabled: true }, agentCookie);
    expect(put.status).toBe(403);
    expect(await put.json()).toEqual({ error: 'forbidden' });
    expect((await send('POST', '/ai/test', undefined, agentCookie)).status).toBe(403);
    expect((await get('/conversations', agentCookie)).status).toBe(200);
  });
});
