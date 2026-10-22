import type { ModelInput, ReplyModel } from '@reception/core';
import { eq } from 'drizzle-orm';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import type { Database } from '../db/client';
import { bookingRequests, contacts, conversations } from '../db/schema';
import { ValidationError } from '../domain/validation';
import { createEventBus, type BusEvent, type EventBus } from '../lib/bus';
import { createKeyedMutex } from '../lib/mutex';
import { createBusiness, type BusinessRow } from '../repos/business';
import { getConversation, listMessages } from '../repos/conversations';
import { seedDemoBusiness, testDatabase } from '../testing/fixtures';
import {
  agentReply,
  bizTopic,
  convTopic,
  handleVisitorText,
  markConversationRead,
  setConversationStatus,
  startConversation,
  submitOfflineContact,
  type ChatDeps,
} from './chat';
import type { Notifier } from './notifier';

let database: Database;
let business: BusinessRow;
let bus: EventBus;
let calls: { kind: string; input: unknown }[];
let model: ReplyModel | null;
const now = new Date('2026-10-11T10:00:00Z');

function deps(): ChatDeps {
  const notifier: Notifier = {
    async bookingCreated(input) {
      calls.push({ kind: 'booking', input });
    },
    async handoff(input) {
      calls.push({ kind: 'handoff', input });
    },
    async offlineContact(input) {
      calls.push({ kind: 'offline', input });
    },
  };
  return { db: database.db, bus, notifier, mutex: createKeyedMutex(), now: () => now, resolveModel: () => model };
}

beforeEach(async () => {
  database = await testDatabase();
  business = await seedDemoBusiness(database.db);
  bus = createEventBus();
  calls = [];
  model = null;
});
afterEach(() => database.close());

function capture(topic: string): BusEvent[] {
  const events: BusEvent[] = [];
  bus.subscribe(topic, (e) => events.push(e));
  return events;
}

async function start() {
  const d = deps();
  const { conversation, visitorToken } = await startConversation(d, business);
  return { d, id: conversation.id, visitorToken };
}

describe('handleVisitorText', () => {
  it('answers with rules, stores both messages and publishes events', async () => {
    const { d, id } = await start();
    const convEvents = capture(convTopic(id));
    const bizEvents = capture(bizTopic(business.id));
    const turn = await handleVisitorText(d, id, '  What are your hours?  ');
    expect(turn.messages.map((m) => m.role)).toEqual(['visitor', 'bot']);
    expect(turn.messages[0]!.content).toBe('What are your hours?');
    expect(turn.messages[1]!.content).toMatch(/^Our hours:/);
    expect(turn.status).toBe('bot');
    const conv = (await getConversation(database.db, id))!;
    expect(conv).toMatchObject({ visitorMessageCount: 1, unreadCount: 1, locale: 'en', needsAttention: false });
    expect(convEvents.filter((e) => e.type === 'message').map((e) => e.id)).toEqual(turn.messages.map((m) => String(m.id)));
    expect(bizEvents.map((e) => e.type)).toEqual(['message', 'message', 'conversation']);
    const [, bot] = await listMessages(database.db, id);
    expect(bot!.meta).toEqual({ engine: 'rules', fallbackReason: 'no_model' });
  });

  it('persists the booking draft between turns and creates a pending booking with a contact', async () => {
    const { d, id } = await start();
    for (const text of ['I want to book a deep cleaning', 'Saturday morning', 'Alex']) await handleVisitorText(d, id, text);
    expect((await getConversation(database.db, id))!.bookingDraft?.fields).toEqual({
      service: 'Deep cleaning',
      preferred_time: 'Saturday morning',
      name: 'Alex',
    });
    const bizEvents = capture(bizTopic(business.id));
    const turn = await handleVisitorText(d, id, 'alex@example.com');
    await turn.notifications;
    expect(turn.messages[1]!.content).toContain('pending confirmation');
    const [booking] = await database.db.select().from(bookingRequests).where(eq(bookingRequests.conversationId, id));
    expect(booking).toMatchObject({
      status: 'pending',
      fields: { service: 'Deep cleaning', preferred_time: 'Saturday morning', name: 'Alex', contact: 'alex@example.com' },
    });
    const conv = (await getConversation(database.db, id))!;
    expect(conv.bookingDraft).toBeNull();
    const [contact] = await database.db.select().from(contacts).where(eq(contacts.id, conv.contactId!));
    expect(contact).toMatchObject({ name: 'Alex', email: 'alex@example.com', phone: null });
    expect(calls.filter((c) => c.kind === 'booking')).toHaveLength(1);
    expect(bizEvents.some((e) => e.type === 'booking')).toBe(true);
  });

  it('soft handoff keeps the bot active and notifies once', async () => {
    const { d, id } = await start();
    const turn = await handleVisitorText(d, id, 'Do you clean swimming pools?');
    await turn.notifications;
    expect(turn.status).toBe('bot');
    expect((await getConversation(database.db, id))!).toMatchObject({ needsAttention: true, attentionReason: 'unknown_question' });
    const second = await handleVisitorText(d, id, 'Do you clean boats?');
    await second.notifications;
    expect(second.messages.map((m) => m.role)).toEqual(['visitor', 'bot']);
    expect(calls.filter((c) => c.kind === 'handoff')).toHaveLength(1);
  });

  it('keeps stored contact details when a booking supplies a different contact kind', async () => {
    const { d, id } = await start();
    await submitOfflineContact(d, id, { email: 'a@example.com' });
    for (const text of ['I want to book a deep cleaning', 'Saturday morning', 'Alex', '13800138000']) await handleVisitorText(d, id, text);
    const conv = (await getConversation(database.db, id))!;
    expect(await database.db.select().from(bookingRequests).where(eq(bookingRequests.conversationId, id))).toHaveLength(1);
    const [contact] = await database.db.select().from(contacts).where(eq(contacts.id, conv.contactId!));
    expect(contact).toMatchObject({ name: 'Alex', email: 'a@example.com', phone: '13800138000' });
  });

  it('notifies again when a soft handoff escalates to a hard handoff', async () => {
    const { d, id } = await start();
    await (await handleVisitorText(d, id, 'Do you clean boats?')).notifications;
    expect(calls.filter((c) => c.kind === 'handoff')).toHaveLength(1);
    const turn = await handleVisitorText(d, id, 'I want to talk to a real person');
    await turn.notifications;
    expect(turn.status).toBe('human');
    const handoffs = calls.filter((c) => c.kind === 'handoff');
    expect(handoffs).toHaveLength(2);
    expect(handoffs[1]!.input).toMatchObject({ reason: 'visitor_request' });
  });

  it('hard handoff silences the bot until released', async () => {
    const { d, id } = await start();
    const statusEvents = capture(convTopic(id));
    const turn = await handleVisitorText(d, id, 'I want to talk to a real person');
    expect(turn.status).toBe('human');
    expect(statusEvents.some((e) => e.type === 'status')).toBe(true);
    const silent = await handleVisitorText(d, id, 'hello?');
    expect(silent.messages.map((m) => m.role)).toEqual(['visitor']);
    expect((await getConversation(database.db, id))!.needsAttention).toBe(true);
    await setConversationStatus(d, { businessId: business.id, conversationId: id, action: 'release', userId: 'u1' });
    const back = await handleVisitorText(d, id, 'What are your hours?');
    expect(back.messages.map((m) => m.role)).toEqual(['visitor', 'bot']);
  });

  it('passes the count excluding the current message to the engine', async () => {
    const { d, id } = await start();
    await database.db.update(conversations).set({ visitorMessageCount: 99 }).where(eq(conversations.id, id));
    const ok = await handleVisitorText(d, id, 'What are your hours?');
    expect(ok.status).toBe('bot');
    const limited = await handleVisitorText(d, id, 'What are your hours?');
    expect(limited.status).toBe('human');
    expect((await getConversation(database.db, id))!.attentionReason).toBe('message_limit');
  });

  it('serializes concurrent messages in one conversation', async () => {
    const { d, id } = await start();
    await Promise.all([handleVisitorText(d, id, 'What are your hours?'), handleVisitorText(d, id, 'Do you bring supplies?')]);
    const rows = await listMessages(database.db, id);
    expect(rows.map((m) => m.role)).toEqual(['visitor', 'bot', 'visitor', 'bot']);
    expect((await getConversation(database.db, id))!.visitorMessageCount).toBe(2);
  });

  it('validates message text', async () => {
    const { d, id } = await start();
    await expect(handleVisitorText(d, id, '   ')).rejects.toBeInstanceOf(ValidationError);
    await expect(handleVisitorText(d, id, 'x'.repeat(1001))).rejects.toBeInstanceOf(ValidationError);
  });

  it('uses the AI model, records fallbacks and never sends system messages to it', async () => {
    const inputs: ModelInput[] = [];
    model = {
      async generate(input) {
        inputs.push(input);
        return { text: 'AI answer', actions: [] };
      },
    };
    const { d, id } = await start();
    await submitOfflineContact(d, id, { email: 'a@example.com', note: 'call me' });
    const turn = await handleVisitorText(d, id, 'Tell me about deep cleaning');
    expect(turn.messages[1]!.content).toBe('AI answer');
    expect(JSON.stringify(inputs[0]!.messages)).not.toContain('Contact details');
    model = {
      async generate() {
        throw new Error('401');
      },
    };
    await handleVisitorText(d, id, 'What are your hours?');
    const bot = (await listMessages(database.db, id)).filter((m) => m.role === 'bot').at(-1)!;
    expect(bot.meta).toEqual({ engine: 'rules', fallbackReason: 'model_error' });
  });
});

describe('offline contact', () => {
  it('stores the contact, flags the conversation and notifies', async () => {
    const { d, id } = await start();
    const res = await submitOfflineContact(d, id, { name: 'Alex', phone: '+1 202 555 0123', note: 'evenings' });
    await res.notifications;
    const conv = (await getConversation(database.db, id))!;
    expect(conv).toMatchObject({ needsAttention: true, attentionReason: 'offline_contact' });
    const [contact] = await database.db.select().from(contacts).where(eq(contacts.id, conv.contactId!));
    expect(contact).toMatchObject({ name: 'Alex', phone: '+1 202 555 0123' });
    expect((await listMessages(database.db, id)).map((m) => m.role)).toEqual(['system']);
    expect(calls.map((c) => c.kind)).toEqual(['offline']);
  });

  it('does not overwrite an existing hard attention reason', async () => {
    const { d, id } = await start();
    await handleVisitorText(d, id, 'I want to talk to a real person');
    await submitOfflineContact(d, id, { email: 'a@example.com' });
    expect((await getConversation(database.db, id))!).toMatchObject({ status: 'human', needsAttention: true, attentionReason: 'visitor_request' });
  });

  it('publishes the system message to the business topic only', async () => {
    const { d, id } = await start();
    const convEvents = capture(convTopic(id));
    const bizEvents = capture(bizTopic(business.id));
    await submitOfflineContact(d, id, { email: 'a@example.com' });
    expect(convEvents.filter((e) => e.type === 'message')).toEqual([]);
    expect(bizEvents.filter((e) => e.type === 'message')).toHaveLength(1);
  });

  it('requires an email or a valid phone number', async () => {
    const { d, id } = await start();
    await expect(submitOfflineContact(d, id, { name: 'Alex' })).rejects.toBeInstanceOf(ValidationError);
    await expect(submitOfflineContact(d, id, { phone: 'abc' })).rejects.toBeInstanceOf(ValidationError);
    await expect(submitOfflineContact(d, id, { email: 'not-an-email' })).rejects.toBeInstanceOf(ValidationError);
  });
});

describe('agent actions', () => {
  it('agent reply takes over, clears attention and resets unread', async () => {
    const { d, id } = await start();
    await handleVisitorText(d, id, 'Do you clean boats?');
    const events = capture(convTopic(id));
    const msg = await agentReply(d, { businessId: business.id, conversationId: id, userId: 'u1', text: 'Hi, Sam here!' });
    expect(msg).toMatchObject({ role: 'agent', content: 'Hi, Sam here!' });
    expect((await getConversation(database.db, id))!).toMatchObject({ status: 'human', needsAttention: false, unreadCount: 0 });
    expect(events.map((e) => e.type)).toEqual(['message', 'status']);
  });

  it('is scoped by business', async () => {
    const { d, id } = await start();
    const other = await createBusiness(database.db, { slug: 'other-biz', name: 'Other' });
    expect(await agentReply(d, { businessId: other.id, conversationId: id, userId: 'u', text: 'x' })).toBeNull();
    expect(await setConversationStatus(d, { businessId: other.id, conversationId: id, action: 'close', userId: 'u' })).toBeNull();
    expect(await markConversationRead(d, other.id, id)).toBe(false);
  });

  it('closes and reopens on a new visitor message', async () => {
    const { d, id } = await start();
    const closed = await setConversationStatus(d, { businessId: business.id, conversationId: id, action: 'close', userId: 'u' });
    expect(closed?.status).toBe('closed');
    const turn = await handleVisitorText(d, id, 'What are your hours?');
    expect(turn.status).toBe('bot');
    expect(turn.messages).toHaveLength(2);
  });

  it('publishes a status event when a closed conversation reopens', async () => {
    const { d, id } = await start();
    await setConversationStatus(d, { businessId: business.id, conversationId: id, action: 'close', userId: 'u' });
    const events = capture(convTopic(id));
    await handleVisitorText(d, id, 'What are your hours?');
    expect(events.filter((e) => e.type === 'status').map((e) => e.data)).toEqual([{ status: 'bot' }]);
  });

  it('close clears the assignee', async () => {
    const { d, id } = await start();
    const taken = await setConversationStatus(d, { businessId: business.id, conversationId: id, action: 'takeover', userId: 'u1' });
    expect(taken?.assignedUserId).toBe('u1');
    const closed = await setConversationStatus(d, { businessId: business.id, conversationId: id, action: 'close', userId: 'u1' });
    expect(closed).toMatchObject({ status: 'closed', assignedUserId: null });
  });

  it('marks a conversation read for the owning business', async () => {
    const { d, id } = await start();
    await handleVisitorText(d, id, 'What are your hours?');
    expect((await getConversation(database.db, id))!.unreadCount).toBe(1);
    expect(await markConversationRead(d, business.id, id)).toBe(true);
    expect((await getConversation(database.db, id))!.unreadCount).toBe(0);
  });

  it('validates agent text length', async () => {
    const { d, id } = await start();
    await expect(agentReply(d, { businessId: business.id, conversationId: id, userId: 'u', text: ' ' })).rejects.toBeInstanceOf(ValidationError);
    await expect(agentReply(d, { businessId: business.id, conversationId: id, userId: 'u', text: 'x'.repeat(2001) })).rejects.toBeInstanceOf(
      ValidationError,
    );
  });
});
