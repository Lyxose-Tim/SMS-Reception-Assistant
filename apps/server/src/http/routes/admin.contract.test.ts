import { KNOWLEDGE_LIMITS, demoKnowledge } from '@reception/core';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { mockLanguageModel } from '../../ai/testing';
import { bookingRequests, messages } from '../../db/schema';
import { knowledgeUsage } from '../../domain/validation';
import type { BusEvent } from '../../lib/bus';
import { createBusiness, listFaqs } from '../../repos/business';
import { createUser } from '../../repos/users';
import { bizTopic } from '../../services/chat';
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

const BOOKING_KEYS = ['contact', 'conversationId', 'createdAt', 'decidedAt', 'fields', 'id', 'ownerNote', 'status'];
const FAQ_KEYS = ['answer', 'createdAt', 'enabled', 'id', 'question', 'sort', 'updatedAt'];
const CONTACT_KEYS = ['email', 'id', 'name', 'phone'];

async function visitorSend(chat: { conversationId: string; visitorToken: string }, text: string) {
  await h.request(`/api/widget/c/${chat.conversationId}/messages`, {
    method: 'POST',
    headers: { origin: ORIGIN, authorization: `Bearer ${chat.visitorToken}`, 'content-type': 'application/json' },
    body: JSON.stringify({ text }),
  });
}

async function startOnly() {
  const start = await h.request('/api/widget/b/harbor-home/conversations', { method: 'POST', headers: { origin: ORIGIN } });
  return (await start.json()) as { conversationId: string; visitorToken: string };
}

async function visitorChat(text: string) {
  const chat = await startOnly();
  await visitorSend(chat, text);
  return chat;
}

async function otherOwner() {
  const other = await createBusiness(h.database.db, { slug: 'other-biz', name: 'Other' });
  await createUser(h.database.db, { businessId: other.id, email: 'other@other.example', password: 'another long password' });
  const res = await h.request('/api/auth/login', {
    method: 'POST',
    headers: { origin: h.env.PUBLIC_ORIGIN, 'content-type': 'application/json' },
    body: JSON.stringify({ email: 'other@other.example', password: 'another long password' }),
  });
  return { other, cookie: res.headers.get('set-cookie')!.split(';')[0]! };
}

async function insertBooking(conversationId: string, createdAt: Date, fields: Record<string, string> = { name: 'Alex', contact: 'alex@example.com' }) {
  const [row] = await h.database.db
    .insert(bookingRequests)
    .values({ businessId: h.business.id, conversationId, fields, status: 'pending', createdAt, receiptSentAt: createdAt, decidedBy: 'someone' })
    .returning();
  return row!;
}

function captureBiz(): BusEvent[] {
  const events: BusEvent[] = [];
  h.deps.bus.subscribe(bizTopic(h.business.id), (e) => events.push(e));
  return events;
}

describe('admin isolation (foreign business gets 404)', () => {
  it('rejects every mutation and read on another business resources', async () => {
    const chat = await visitorChat('What are your hours?');
    const booking = await insertBooking(chat.conversationId, new Date('2026-10-11T10:00:00Z'));
    const [faq] = await listFaqs(h.database.db, h.business.id);
    const { cookie: b } = await otherOwner();
    for (const action of ['takeover', 'release', 'close']) {
      expect((await send('POST', `/conversations/${chat.conversationId}/${action}`, undefined, b)).status, action).toBe(404);
    }
    expect((await send('POST', `/bookings/${booking.id}/confirm`, {}, b)).status).toBe(404);
    expect((await send('POST', `/bookings/${booking.id}/decline`, {}, b)).status).toBe(404);
    expect((await send('PATCH', `/faqs/${faq!.id}`, { answer: 'hacked' }, b)).status).toBe(404);
    expect((await send('DELETE', `/faqs/${faq!.id}`, undefined, b)).status).toBe(404);
    expect((await get(`/conversations/${chat.conversationId}/messages`, b)).status).toBe(404);
    const detail = (await (await get(`/conversations/${chat.conversationId}`)).json()) as { conversation: { status: string }; bookings: { status: string }[] };
    expect(detail.conversation.status).toBe('bot');
    expect(detail.bookings[0]!.status).toBe('pending');
    expect((await listFaqs(h.database.db, h.business.id)).find((f) => f.id === faq!.id)?.answer).not.toBe('hacked');
  });
});

describe('DTOs', () => {
  it('never leaks raw rows or internal columns and uses epoch milliseconds', async () => {
    const chat = await visitorChat('What are your hours?');
    const booking = await insertBooking(chat.conversationId, new Date('2026-10-11T10:00:00Z'));
    const faqs = (await (await get('/faqs')).json()) as { faqs: Record<string, unknown>[] };
    expect(Object.keys(faqs.faqs[0]!).sort()).toEqual(FAQ_KEYS);
    expect(typeof faqs.faqs[0]!.createdAt).toBe('number');
    const created = (await (await send('POST', '/faqs', { question: 'Q?', answer: 'A.' })).json()) as { faq: Record<string, unknown> };
    expect(Object.keys(created.faq).sort()).toEqual(FAQ_KEYS);
    const patched = (await (await send('PATCH', `/faqs/${created.faq.id}`, { enabled: false })).json()) as { faq: Record<string, unknown> };
    expect(Object.keys(patched.faq).sort()).toEqual(FAQ_KEYS);

    const list = (await (await get('/bookings')).json()) as { bookings: Record<string, unknown>[] };
    expect(Object.keys(list.bookings[0]!).sort()).toEqual(BOOKING_KEYS);
    expect(list.bookings[0]).toMatchObject({
      id: booking.id,
      status: 'pending',
      createdAt: Date.parse('2026-10-11T10:00:00Z'),
      decidedAt: null,
      ownerNote: null,
      contact: { name: 'Alex', email: 'alex@example.com', phone: null },
    });

    const decided = (await (await send('POST', `/bookings/${booking.id}/confirm`, { note: 'ok' })).json()) as { booking: Record<string, unknown> };
    expect(Object.keys(decided.booking).sort()).toEqual(BOOKING_KEYS);
    expect(decided.booking.decidedAt).toBe(h.clock.now.getTime());

    const detail = (await (await get(`/conversations/${chat.conversationId}`)).json()) as { bookings: Record<string, unknown>[]; contact: unknown };
    expect(Object.keys(detail.bookings[0]!).sort()).toEqual(BOOKING_KEYS);
    expect(detail.contact).toBeNull();
  });

  it('exposes contacts as {id,name,email,phone} in list and detail, and booking events as DTOs', async () => {
    const events = captureBiz();
    const chat = await visitorChat('I want to book a deep cleaning');
    for (const text of ['Saturday morning', 'Alex', 'alex@example.com']) await visitorSend(chat, text);
    const list = (await (await get('/conversations')).json()) as { conversations: { contact: Record<string, unknown> | null }[] };
    expect(Object.keys(list.conversations[0]!.contact!).sort()).toEqual(CONTACT_KEYS);
    expect(list.conversations[0]!.contact).toMatchObject({ name: 'Alex', email: 'alex@example.com', phone: null });
    const detail = (await (await get(`/conversations/${chat.conversationId}`)).json()) as { contact: Record<string, unknown> };
    expect(Object.keys(detail.contact).sort()).toEqual(CONTACT_KEYS);

    const created = events.find((e) => e.type === 'booking')!.data as Record<string, unknown>;
    expect(Object.keys(created).sort()).toEqual(BOOKING_KEYS);
    expect(typeof created.createdAt).toBe('number');
    events.length = 0;
    await send('POST', `/bookings/${created.id}/confirm`, {});
    const decided = events.find((e) => e.type === 'booking')!.data as Record<string, unknown>;
    expect(Object.keys(decided).sort()).toEqual(BOOKING_KEYS);
    expect(decided.status).toBe('confirmed');
  });
});

describe('inbox hygiene', () => {
  it('hides started-but-unused conversations until the first visitor message and publishes nothing on start', async () => {
    const events = captureBiz();
    const chat = await startOnly();
    expect(events).toEqual([]);
    expect(((await (await get('/conversations')).json()) as { conversations: unknown[] }).conversations).toHaveLength(0);
    await visitorSend(chat, 'What are your hours?');
    const list = (await (await get('/conversations')).json()) as { conversations: { id: string }[] };
    expect(list.conversations.map((c) => c.id)).toEqual([chat.conversationId]);
    expect(events.some((e) => e.type === 'conversation')).toBe(true);
  });
});

describe('pagination', () => {
  it('pages conversations with a stable cursor, including ties on lastMessageAt', async () => {
    const ids: string[] = [];
    for (const text of ['What are your hours?', 'Where are you located?', 'What services do you offer?']) ids.push((await visitorChat(text)).conversationId);
    const full = (await (await get('/conversations')).json()) as { conversations: { id: string }[]; nextCursor: string | null };
    expect(full.conversations).toHaveLength(3);
    expect(full.nextCursor).toBeNull();
    const order = full.conversations.map((c) => c.id);
    expect([...order].sort()).toEqual([...ids].sort());

    const page1 = (await (await get('/conversations?limit=2')).json()) as { conversations: { id: string; lastMessageAt: number }[]; nextCursor: string | null };
    expect(page1.conversations.map((c) => c.id)).toEqual(order.slice(0, 2));
    expect(page1.nextCursor).toBe(`${page1.conversations[1]!.lastMessageAt}:${page1.conversations[1]!.id}`);
    const page2 = (await (await get(`/conversations?limit=2&before=${encodeURIComponent(page1.nextCursor!)}`)).json()) as {
      conversations: { id: string }[];
      nextCursor: string | null;
    };
    expect(page2.conversations.map((c) => c.id)).toEqual(order.slice(2));
    expect(page2.nextCursor).toBeNull();
  });

  it('validates limit and cursor with 422', async () => {
    for (const q of ['limit=0', 'limit=101', 'limit=abc', 'limit=1.5', 'before=garbage', 'before=12:', 'before=:abc', 'before=abc:def']) {
      expect((await get(`/conversations?${q}`)).status, q).toBe(422);
      expect((await get(`/bookings?${q}`)).status, `bookings ${q}`).toBe(422);
    }
    expect((await get('/conversations?limit=100')).status).toBe(200);
    const chat = await visitorChat('hello');
    expect((await get(`/conversations/${chat.conversationId}/messages?limit=201`)).status).toBe(422);
    expect((await get(`/conversations/${chat.conversationId}/messages?limit=0`)).status).toBe(422);
    expect((await get(`/conversations/${chat.conversationId}/messages?before=abc`)).status).toBe(422);
  });

  it('pages bookings by createdAt desc, id desc', async () => {
    const chat = await visitorChat('hello');
    const a = await insertBooking(chat.conversationId, new Date('2026-10-11T09:00:00Z'));
    const b = await insertBooking(chat.conversationId, new Date('2026-10-11T11:00:00Z'));
    const c = await insertBooking(chat.conversationId, new Date('2026-10-11T11:00:00Z'));
    const expected = [b, c]
      .sort((x, y) => (x.id < y.id ? 1 : -1))
      .concat(a)
      .map((r) => r.id);
    const page1 = (await (await get('/bookings?limit=2')).json()) as { bookings: { id: string; createdAt: number }[]; nextCursor: string | null };
    expect(page1.bookings.map((r) => r.id)).toEqual(expected.slice(0, 2));
    expect(page1.nextCursor).toBe(`${page1.bookings[1]!.createdAt}:${page1.bookings[1]!.id}`);
    const page2 = (await (await get(`/bookings?limit=2&before=${encodeURIComponent(page1.nextCursor!)}`)).json()) as { bookings: { id: string }[]; nextCursor: string | null };
    expect(page2.bookings.map((r) => r.id)).toEqual(expected.slice(2));
    expect(page2.nextCursor).toBeNull();
    expect(await (await get('/bookings?status=confirmed')).json()).toEqual({ bookings: [], nextCursor: null });
  });

  it('returns the latest 200 messages ascending and pages older ones through /messages', async () => {
    const chat = await startOnly();
    await h.database.db.insert(messages).values(
      Array.from({ length: 250 }, (_, i) => ({
        conversationId: chat.conversationId,
        businessId: h.business.id,
        role: 'visitor' as const,
        content: `m${i}`,
        createdAt: new Date('2026-10-11T10:00:00Z'),
      })),
    );
    const detail = (await (await get(`/conversations/${chat.conversationId}`)).json()) as { messages: { id: number; content: string }[]; hasMoreMessages: boolean };
    expect(detail.messages).toHaveLength(200);
    expect(detail.hasMoreMessages).toBe(true);
    expect(detail.messages[0]!.content).toBe('m50');
    expect(detail.messages.at(-1)!.content).toBe('m249');

    const older = (await (await get(`/conversations/${chat.conversationId}/messages?before=${detail.messages[0]!.id}&limit=30`)).json()) as {
      messages: { id: number; content: string }[];
      hasMore: boolean;
    };
    expect(older.messages.map((m) => m.content)).toEqual(Array.from({ length: 30 }, (_, i) => `m${20 + i}`));
    expect(older.hasMore).toBe(true);
    const oldest = (await (await get(`/conversations/${chat.conversationId}/messages?before=${older.messages[0]!.id}`)).json()) as {
      messages: { content: string }[];
      hasMore: boolean;
    };
    expect(oldest.messages).toHaveLength(20);
    expect(oldest.messages[0]!.content).toBe('m0');
    expect(oldest.hasMore).toBe(false);
  });

  it('reports hasMoreMessages false for short conversations', async () => {
    const chat = await visitorChat('What are your hours?');
    const detail = (await (await get(`/conversations/${chat.conversationId}`)).json()) as { messages: unknown[]; hasMoreMessages: boolean };
    expect(detail.messages).toHaveLength(2);
    expect(detail.hasMoreMessages).toBe(false);
  });
});

describe('dashboard data', () => {
  it('reports knowledge usage with the same accounting as the budget, and emailEnabled', async () => {
    const body = (await (await get('/business')).json()) as { emailEnabled: boolean; usage: { knowledgeChars: number; enabledFaqs: number } };
    const k = demoKnowledge();
    expect(body.usage).toEqual(knowledgeUsage(k.name, k.profile, k.faqs));
    expect(body.usage.enabledFaqs).toBe(k.faqs.filter((f) => f.enabled).length);
    expect(body.usage.knowledgeChars).toBeGreaterThan(0);
    expect(body.usage.knowledgeChars).toBeLessThanOrEqual(KNOWLEDGE_LIMITS.totalChars);
    expect(body.emailEnabled).toBe(true);
  });

  it('reports emailEnabled false without a mailer', async () => {
    h.close();
    h = await createTestHarness({ mailer: null });
    cookie = await h.login();
    expect(((await (await get('/business')).json()) as { emailEnabled: boolean }).emailEnabled).toBe(false);
  });

  it('publishes a conversation event with unreadCount 0 when a conversation is opened', async () => {
    const chat = await visitorChat('What are your hours?');
    const events = captureBiz();
    await get(`/conversations/${chat.conversationId}`);
    const ev = events.find((e) => e.type === 'conversation');
    expect(ev).toBeTruthy();
    expect(ev!.data).toMatchObject({ id: chat.conversationId, unreadCount: 0 });
  });
});
