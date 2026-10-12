import type { ChatMessage, Locale } from '@reception/core';
import { and, asc, desc, eq, gt, inArray, max, type SQL } from 'drizzle-orm';
import type { Db } from '../db/client';
import { contacts, conversations, messages, type MessageRole } from '../db/schema';
import { safeEqualHex, sha256 } from '../lib/crypto';

export type ConversationRow = typeof conversations.$inferSelect;
export type MessageRow = typeof messages.$inferSelect;
export type ContactRow = typeof contacts.$inferSelect;

export const VISITOR_VISIBLE_ROLES: readonly MessageRole[] = ['visitor', 'bot', 'agent'];

export interface ConversationListItem {
  conversation: ConversationRow;
  lastMessage: MessageRow | null;
  contact: ContactRow | null;
}

export async function insertConversation(
  db: Db,
  input: { businessId: string; visitorTokenHash: string; locale: Locale | null; now: Date },
): Promise<ConversationRow> {
  const [row] = await db
    .insert(conversations)
    .values({
      businessId: input.businessId,
      visitorTokenHash: input.visitorTokenHash,
      locale: input.locale,
      lastMessageAt: input.now,
      createdAt: input.now,
    })
    .returning();
  return row!;
}

export async function getConversation(db: Db, id: string): Promise<ConversationRow | null> {
  const [row] = await db.select().from(conversations).where(eq(conversations.id, id)).limit(1);
  return row ?? null;
}

export async function getConversationForBusiness(db: Db, businessId: string, id: string): Promise<ConversationRow | null> {
  const [row] = await db
    .select()
    .from(conversations)
    .where(and(eq(conversations.id, id), eq(conversations.businessId, businessId)))
    .limit(1);
  return row ?? null;
}

export function verifyVisitorToken(conversation: ConversationRow, token: string): boolean {
  if (!token) return false;
  return safeEqualHex(conversation.visitorTokenHash, sha256(token));
}

export async function listConversations(
  db: Db,
  businessId: string,
  opts: { filter?: 'all' | 'attention' | 'open'; limit?: number } = {},
): Promise<ConversationListItem[]> {
  const conditions: SQL[] = [eq(conversations.businessId, businessId)];
  if (opts.filter === 'attention') conditions.push(eq(conversations.needsAttention, true));
  if (opts.filter === 'open') conditions.push(inArray(conversations.status, ['bot', 'human']));
  const rows = await db
    .select({ conversation: conversations, contact: contacts })
    .from(conversations)
    .leftJoin(contacts, eq(contacts.id, conversations.contactId))
    .where(and(...conditions))
    .orderBy(desc(conversations.lastMessageAt))
    .limit(Math.min(opts.limit ?? 50, 200));
  // At most 200 small lookups; acceptable for a single-business MVP.
  return Promise.all(
    rows.map(async (r) => {
      const [lastMessage] = await db
        .select()
        .from(messages)
        .where(eq(messages.conversationId, r.conversation.id))
        .orderBy(desc(messages.id))
        .limit(1);
      return { conversation: r.conversation, contact: r.contact, lastMessage: lastMessage ?? null };
    }),
  );
}

export async function listMessages(
  db: Db,
  conversationId: string,
  opts: { afterId?: number; limit?: number; roles?: readonly MessageRole[] } = {},
): Promise<MessageRow[]> {
  const conditions: SQL[] = [eq(messages.conversationId, conversationId)];
  if (opts.afterId !== undefined) conditions.push(gt(messages.id, opts.afterId));
  if (opts.roles) conditions.push(inArray(messages.role, [...opts.roles]));
  return db
    .select()
    .from(messages)
    .where(and(...conditions))
    .orderBy(asc(messages.id))
    .limit(Math.min(opts.limit ?? 500, 1000));
}

export async function lastMessageId(db: Db, conversationId: string): Promise<number> {
  const [row] = await db.select({ value: max(messages.id) }).from(messages).where(eq(messages.conversationId, conversationId));
  return row?.value ?? 0;
}

export async function recentHistory(db: Db, conversationId: string, limit = 40): Promise<ChatMessage[]> {
  const rows = await db
    .select({ role: messages.role, content: messages.content })
    .from(messages)
    .where(and(eq(messages.conversationId, conversationId), inArray(messages.role, ['visitor', 'bot', 'agent'])))
    .orderBy(desc(messages.id))
    .limit(limit);
  return rows.reverse().map((r) => ({ role: r.role as ChatMessage['role'], content: r.content }));
}

export async function getContact(db: Db, id: string): Promise<ContactRow | null> {
  const [row] = await db.select().from(contacts).where(eq(contacts.id, id)).limit(1);
  return row ?? null;
}
