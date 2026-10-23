import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import type { Database } from '../db/client';
import { bookingRequests, messages, conversations, contacts } from '../db/schema';
import { randomToken, sha256 } from '../lib/crypto';
import { seedDemoBusiness, testDatabase } from '../testing/fixtures';
import { createBusiness, type BusinessRow } from './business';
import { decideBooking, getBookingForBusiness, listBookings } from './bookings';
import {
  getConversationForBusiness,
  insertConversation,
  lastMessageId,
  listConversations,
  listLatestMessages,
  listMessages,
  recentHistory,
  verifyVisitorToken,
} from './conversations';
import { eq } from 'drizzle-orm';

let database: Database;
let business: BusinessRow;
const t0 = new Date('2026-10-11T10:00:00Z');

beforeEach(async () => {
  database = await testDatabase();
  business = await seedDemoBusiness(database.db);
});
afterEach(() => database.close());

async function newConversation(at = t0) {
  const token = randomToken();
  const conversation = await insertConversation(database.db, { businessId: business.id, visitorTokenHash: sha256(token), locale: null, now: at });
  return { conversation, token };
}

async function addMessage(conversationId: string, role: 'visitor' | 'bot' | 'agent' | 'system', content: string) {
  await database.db.insert(messages).values({ conversationId, businessId: business.id, role, content });
}

describe('conversations', () => {
  it('verifies visitor tokens by hash', async () => {
    const { conversation, token } = await newConversation();
    expect(verifyVisitorToken(conversation, token)).toBe(true);
    expect(verifyVisitorToken(conversation, randomToken())).toBe(false);
    expect(verifyVisitorToken(conversation, '')).toBe(false);
  });

  it('scopes lookups by business', async () => {
    const { conversation } = await newConversation();
    const other = await createBusiness(database.db, { slug: 'other-biz', name: 'Other' });
    expect(await getConversationForBusiness(database.db, other.id, conversation.id)).toBeNull();
    expect((await getConversationForBusiness(database.db, business.id, conversation.id))?.id).toBe(conversation.id);
  });

  it('lists messages after an id and builds history without system messages', async () => {
    const { conversation } = await newConversation();
    await addMessage(conversation.id, 'visitor', 'hi');
    await addMessage(conversation.id, 'bot', 'hello');
    await addMessage(conversation.id, 'system', 'Visitor left contact details');
    await addMessage(conversation.id, 'agent', 'Sam here');
    const all = await listMessages(database.db, conversation.id);
    expect(all.map((m) => m.role)).toEqual(['visitor', 'bot', 'system', 'agent']);
    const after = await listMessages(database.db, conversation.id, { afterId: all[1]!.id, roles: ['visitor', 'bot', 'agent'] });
    expect(after.map((m) => m.content)).toEqual(['Sam here']);
    expect(await lastMessageId(database.db, conversation.id)).toBe(all[3]!.id);
    expect(await recentHistory(database.db, conversation.id)).toEqual([
      { role: 'visitor', content: 'hi' },
      { role: 'bot', content: 'hello' },
      { role: 'agent', content: 'Sam here' },
    ]);
  });

  it('limits history to the most recent messages', async () => {
    const { conversation } = await newConversation();
    for (let i = 0; i < 10; i++) await addMessage(conversation.id, 'visitor', `m${i}`);
    const history = await recentHistory(database.db, conversation.id, 3);
    expect(history.map((h) => h.content)).toEqual(['m7', 'm8', 'm9']);
  });

  it('clamps message limits: negative becomes 1, NaN falls back to the default', async () => {
    const { conversation } = await newConversation();
    for (let i = 0; i < 3; i++) await addMessage(conversation.id, 'visitor', `m${i}`);
    expect(await listMessages(database.db, conversation.id, { limit: -1 })).toHaveLength(1);
    expect(await listMessages(database.db, conversation.id, { limit: NaN })).toHaveLength(3);
  });

  it('clamps recentHistory limit to at least 1', async () => {
    const { conversation } = await newConversation();
    for (let i = 0; i < 3; i++) await addMessage(conversation.id, 'visitor', `m${i}`);
    expect(await recentHistory(database.db, conversation.id, 0)).toHaveLength(1);
  });

  it('lists conversations newest first with filters, last message and contact', async () => {
    const a = await newConversation(new Date('2026-10-11T10:00:00Z'));
    const b = await newConversation(new Date('2026-10-11T11:00:00Z'));
    await addMessage(a.conversation.id, 'visitor', 'first');
    await addMessage(a.conversation.id, 'bot', 'latest a');
    await addMessage(b.conversation.id, 'visitor', 'only b');
    const [contact] = await database.db.insert(contacts).values({ businessId: business.id, name: 'Alex', email: 'a@example.com' }).returning();
    await database.db.update(conversations).set({ needsAttention: true, contactId: contact!.id }).where(eq(conversations.id, a.conversation.id));
    await database.db.update(conversations).set({ status: 'closed' }).where(eq(conversations.id, b.conversation.id));

    const all = await listConversations(database.db, business.id);
    expect(all.map((i) => i.conversation.id)).toEqual([b.conversation.id, a.conversation.id]);
    expect(all[1]!.lastMessage?.content).toBe('latest a');
    expect(all[1]!.contact?.name).toBe('Alex');
    expect((await listConversations(database.db, business.id, { filter: 'attention' })).map((i) => i.conversation.id)).toEqual([a.conversation.id]);
    expect((await listConversations(database.db, business.id, { filter: 'open' })).map((i) => i.conversation.id)).toEqual([a.conversation.id]);
  });
});

describe('inbox listing', () => {
  it('excludes conversations with neither messages nor a contact', async () => {
    const empty = await newConversation(new Date('2026-10-11T12:00:00Z'));
    const withMessage = await newConversation(new Date('2026-10-11T11:00:00Z'));
    const withContact = await newConversation(new Date('2026-10-11T10:00:00Z'));
    await addMessage(withMessage.conversation.id, 'visitor', 'hi');
    const [contact] = await database.db.insert(contacts).values({ businessId: business.id, email: 'a@example.com' }).returning();
    await database.db.update(conversations).set({ contactId: contact!.id }).where(eq(conversations.id, withContact.conversation.id));
    const ids = (await listConversations(database.db, business.id)).map((i) => i.conversation.id);
    expect(ids).toEqual([withMessage.conversation.id, withContact.conversation.id]);
    expect(ids).not.toContain(empty.conversation.id);
  });

  it('pages with a (lastMessageAt, id) cursor', async () => {
    for (let i = 0; i < 4; i++) {
      const c = await newConversation(new Date(i < 2 ? '2026-10-11T10:00:00Z' : '2026-10-11T11:00:00Z'));
      await database.db.update(conversations).set({ lastMessageAt: c.conversation.lastMessageAt }).where(eq(conversations.id, c.conversation.id));
      await addMessage(c.conversation.id, 'visitor', `m${i}`);
    }
    const all = (await listConversations(database.db, business.id)).map((i) => i.conversation);
    expect(all).toHaveLength(4);
    const first = await listConversations(database.db, business.id, { limit: 3 });
    const cursor = first[2]!.conversation;
    const rest = await listConversations(database.db, business.id, { before: { at: cursor.lastMessageAt.getTime(), id: cursor.id } });
    expect([...first, ...rest].map((i) => i.conversation.id)).toEqual(all.map((c) => c.id));
    expect(rest).toHaveLength(1);
  });

  it('lists the latest messages before an id in ascending order', async () => {
    const { conversation } = await newConversation();
    for (let i = 0; i < 6; i++) await addMessage(conversation.id, 'visitor', `m${i}`);
    const latest = await listLatestMessages(database.db, conversation.id, { limit: 3 });
    expect(latest.map((m) => m.content)).toEqual(['m3', 'm4', 'm5']);
    const older = await listLatestMessages(database.db, conversation.id, { limit: 3, beforeId: latest[0]!.id });
    expect(older.map((m) => m.content)).toEqual(['m0', 'm1', 'm2']);
  });
});

describe('bookings', () => {
  it('decides a pending booking exactly once and scopes by business', async () => {
    const { conversation } = await newConversation();
    const [booking] = await database.db
      .insert(bookingRequests)
      .values({ businessId: business.id, conversationId: conversation.id, fields: { name: 'Alex' } })
      .returning();
    const other = await createBusiness(database.db, { slug: 'other-biz', name: 'Other' });
    expect(await getBookingForBusiness(database.db, other.id, booking!.id)).toBeNull();
    expect(await decideBooking(database.db, { businessId: other.id, bookingId: booking!.id, decision: 'confirmed', note: null, userId: 'u', now: t0 })).toBeNull();

    const decided = await decideBooking(database.db, { businessId: business.id, bookingId: booking!.id, decision: 'confirmed', note: 'See you Saturday', userId: 'u1', now: t0 });
    expect(decided).toMatchObject({ status: 'confirmed', ownerNote: 'See you Saturday', decidedBy: 'u1' });
    expect(decided?.decidedAt?.getTime()).toBe(t0.getTime());
    expect(await decideBooking(database.db, { businessId: business.id, bookingId: booking!.id, decision: 'declined', note: null, userId: 'u1', now: t0 })).toBeNull();

    expect((await listBookings(database.db, business.id, { status: 'pending' })).length).toBe(0);
    expect((await listBookings(database.db, business.id)).length).toBe(1);
  });
});
