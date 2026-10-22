import {
  extractContact,
  handleVisitorMessage,
  type ConversationStatus,
  type EngineResult,
  type HandoffReason,
  type ReplyModel,
} from '@reception/core';
import { and, eq, sql } from 'drizzle-orm';
import type { BatchItem } from 'drizzle-orm/batch';
import { z } from 'zod';
import type { Db } from '../db/client';
import { bookingRequests, contacts, conversations, messages, type MessageRole } from '../db/schema';
import { contactFromFields, type ContactFields } from '../domain/contact';
import { parseOrThrow, ValidationError } from '../domain/validation';
import type { EventBus } from '../lib/bus';
import { randomToken, sha256 } from '../lib/crypto';
import type { KeyedMutex } from '../lib/mutex';
import { auditInsert } from '../repos/audit';
import { getBookingForBusiness } from '../repos/bookings';
import { getBusinessById, loadKnowledge, type BusinessRow } from '../repos/business';
import {
  getConversation,
  getConversationForBusiness,
  insertConversation,
  lastMessageId,
  listMessages,
  recentHistory,
  type ConversationRow,
  type MessageRow,
} from '../repos/conversations';
import type { Notifier } from './notifier';

export interface ChatDeps {
  db: Db;
  bus: EventBus;
  notifier: Notifier;
  mutex: KeyedMutex;
  now: () => Date;
  resolveModel: (business: BusinessRow) => ReplyModel | null;
  modelTimeoutMs?: number;
}

export const MAX_VISITOR_TEXT = 1000;
export const MAX_AGENT_TEXT = 2000;
export const HARD_HANDOFFS: ReadonlySet<HandoffReason> = new Set<HandoffReason>(['visitor_request', 'message_limit']);

export const convTopic = (conversationId: string) => `conv:${conversationId}`;
export const bizTopic = (businessId: string) => `biz:${businessId}`;

type Batch = [BatchItem<'sqlite'>, ...BatchItem<'sqlite'>[]];

export interface PublicMessage {
  id: number;
  role: 'visitor' | 'bot' | 'agent';
  content: string;
  createdAt: number;
}

export interface AdminMessage {
  id: number;
  role: MessageRole;
  content: string;
  meta: Record<string, unknown> | null;
  createdAt: number;
}

export interface ConversationSummary {
  id: string;
  status: ConversationStatus;
  needsAttention: boolean;
  attentionReason: string | null;
  unreadCount: number;
  locale: string | null;
  contactId: string | null;
  lastMessageAt: number;
  createdAt: number;
}

export interface VisitorTurn {
  messages: PublicMessage[];
  status: ConversationStatus;
  notifications: Promise<void>;
}

export function toPublicMessage(m: MessageRow): PublicMessage {
  return { id: m.id, role: m.role as PublicMessage['role'], content: m.content, createdAt: m.createdAt.getTime() };
}

export function toAdminMessage(m: MessageRow): AdminMessage {
  return { id: m.id, role: m.role, content: m.content, meta: m.meta ?? null, createdAt: m.createdAt.getTime() };
}

export function conversationSummary(c: ConversationRow): ConversationSummary {
  return {
    id: c.id,
    status: c.status,
    needsAttention: c.needsAttention,
    attentionReason: c.attentionReason,
    unreadCount: c.unreadCount,
    locale: c.locale,
    contactId: c.contactId,
    lastMessageAt: c.lastMessageAt.getTime(),
    createdAt: c.createdAt.getTime(),
  };
}

function nonNullFields(contact: ContactFields): Partial<ContactFields> {
  return Object.fromEntries(Object.entries(contact).filter(([, v]) => v !== null)) as Partial<ContactFields>;
}

function publishMessages(deps: ChatDeps, conv: ConversationRow, rows: MessageRow[]): void {
  for (const m of rows) {
    if (m.role !== 'system') deps.bus.publish(convTopic(conv.id), { type: 'message', id: String(m.id), data: toPublicMessage(m) });
    deps.bus.publish(bizTopic(conv.businessId), { type: 'message', id: String(m.id), data: { conversationId: conv.id, message: toAdminMessage(m) } });
  }
}

function publishConversation(deps: ChatDeps, conv: ConversationRow, statusChanged: boolean): void {
  if (statusChanged) deps.bus.publish(convTopic(conv.id), { type: 'status', data: { status: conv.status } });
  deps.bus.publish(bizTopic(conv.businessId), { type: 'conversation', data: conversationSummary(conv) });
}

function botMeta(result: EngineResult): Record<string, unknown> {
  return {
    engine: result.meta.engine,
    ...(result.meta.fallbackReason ? { fallbackReason: result.meta.fallbackReason } : {}),
    ...(result.handoff ? { handoff: result.handoff.reason } : {}),
    ...(result.meta.knowledgeTruncated ? { knowledgeTruncated: true } : {}),
  };
}

export async function startConversation(deps: ChatDeps, business: BusinessRow): Promise<{ conversation: ConversationRow; visitorToken: string }> {
  const visitorToken = randomToken();
  const conversation = await insertConversation(deps.db, {
    businessId: business.id,
    visitorTokenHash: sha256(visitorToken),
    locale: null,
    now: deps.now(),
  });
  publishConversation(deps, conversation, false);
  return { conversation, visitorToken };
}

export async function handleVisitorText(deps: ChatDeps, conversationId: string, rawText: string, localeHint?: string | null): Promise<VisitorTurn> {
  const text = rawText.trim();
  if (!text) throw new ValidationError([{ path: 'text', message: 'Message is empty' }]);
  if (text.length > MAX_VISITOR_TEXT) {
    throw new ValidationError([{ path: 'text', message: `Messages are limited to ${MAX_VISITOR_TEXT} characters` }]);
  }

  return deps.mutex.run(conversationId, async () => {
    const conv = await getConversation(deps.db, conversationId);
    if (!conv) throw new ValidationError([{ path: 'conversationId', message: 'Unknown conversation' }]);
    const business = await getBusinessById(deps.db, conv.businessId);
    if (!business) throw new Error(`Business ${conv.businessId} not found`);
    const [knowledge, history, previousId] = await Promise.all([
      loadKnowledge(deps.db, business),
      recentHistory(deps.db, conv.id),
      lastMessageId(deps.db, conv.id),
    ]);

    const status: ConversationStatus = conv.status === 'closed' ? 'bot' : conv.status;
    const now = deps.now();
    const result = await handleVisitorMessage(
      {
        business: knowledge,
        conversation: { status, locale: conv.locale, bookingDraft: conv.bookingDraft, visitorMessageCount: conv.visitorMessageCount },
        history,
        localeHint: localeHint ?? null,
        now,
      },
      text,
      { model: deps.resolveModel(business), ...(deps.modelTimeoutMs ? { timeoutMs: deps.modelTimeoutMs } : {}) },
    );

    const handoff = result.handoff ?? null;
    const nextStatus: ConversationStatus = handoff && HARD_HANDOFFS.has(handoff.reason) ? 'human' : status;
    const needsAttention = conv.needsAttention || handoff !== null || nextStatus === 'human';
    const attentionReason = handoff?.reason ?? (needsAttention ? (conv.attentionReason ?? 'visitor_message') : null);

    const statements: BatchItem<'sqlite'>[] = [];
    let contactId = conv.contactId;
    let bookingId: string | null = null;
    if (result.bookingRequest) {
      const contact = contactFromFields(knowledge.bookingFields, result.bookingRequest.fields);
      if (contactId) {
        const provided = nonNullFields(contact);
        if (Object.keys(provided).length > 0) statements.push(deps.db.update(contacts).set(provided).where(eq(contacts.id, contactId)));
      } else {
        contactId = crypto.randomUUID();
        statements.push(deps.db.insert(contacts).values({ id: contactId, businessId: business.id, ...contact, createdAt: now }));
      }
      bookingId = crypto.randomUUID();
      statements.push(
        deps.db.insert(bookingRequests).values({
          id: bookingId,
          businessId: business.id,
          conversationId: conv.id,
          contactId,
          fields: result.bookingRequest.fields,
          status: 'pending',
          createdAt: now,
        }),
      );
      statements.push(auditInsert(deps.db, { businessId: business.id, actor: 'visitor', action: 'booking_requested', target: bookingId, now }));
    }
    statements.push(deps.db.insert(messages).values({ conversationId: conv.id, businessId: business.id, role: 'visitor', content: text, createdAt: now }));
    result.replies.forEach((reply, index) => {
      statements.push(
        deps.db.insert(messages).values({
          conversationId: conv.id,
          businessId: business.id,
          role: 'bot',
          content: reply,
          meta: index === 0 ? botMeta(result) : null,
          createdAt: now,
        }),
      );
    });
    statements.push(
      deps.db
        .update(conversations)
        .set({
          status: nextStatus,
          needsAttention,
          attentionReason,
          locale: result.locale,
          bookingDraft: result.bookingDraft,
          contactId,
          visitorMessageCount: sql`${conversations.visitorMessageCount} + 1`,
          unreadCount: sql`${conversations.unreadCount} + 1`,
          lastMessageAt: now,
        })
        .where(eq(conversations.id, conv.id)),
    );
    if (handoff) {
      statements.push(
        auditInsert(deps.db, { businessId: business.id, actor: 'visitor', action: 'handoff', target: conv.id, data: { reason: handoff.reason }, now }),
      );
    }
    await deps.db.batch(statements as Batch);

    const [updated, newMessages] = await Promise.all([getConversation(deps.db, conv.id), listMessages(deps.db, conv.id, { afterId: previousId })]);
    if (!updated) throw new Error('Conversation disappeared during a turn');
    publishMessages(deps, updated, newMessages);
    publishConversation(deps, updated, updated.status !== conv.status);

    const pending: Promise<void>[] = [];
    if (bookingId) {
      const booking = await getBookingForBusiness(deps.db, business.id, bookingId);
      if (booking) {
        deps.bus.publish(bizTopic(business.id), { type: 'booking', data: booking });
        pending.push(deps.notifier.bookingCreated({ business, conversation: updated, booking }));
      }
    }
    if (handoff && (!conv.needsAttention || (nextStatus === 'human' && conv.status !== 'human'))) {
      pending.push(deps.notifier.handoff({ business, conversation: updated, reason: handoff.reason, excerpt: text }));
    }
    return {
      messages: newMessages.filter((m) => m.role !== 'system').map(toPublicMessage),
      status: updated.status,
      notifications: Promise.all(pending).then(() => undefined),
    };
  });
}

export const offlineContactSchema = z
  .object({
    name: z.string().trim().max(100).optional(),
    email: z.email().max(200).optional(),
    phone: z
      .string()
      .trim()
      .max(40)
      .refine((v) => !v.includes('@') && extractContact(v) !== null, { error: 'Enter a valid phone number' })
      .optional(),
    note: z.string().trim().max(500).optional(),
  })
  .strict()
  .refine((v) => Boolean(v.email || v.phone), { error: 'Provide an email address or phone number', path: ['email'] });

export async function submitOfflineContact(deps: ChatDeps, conversationId: string, input: unknown): Promise<{ notifications: Promise<void> }> {
  const parsed = parseOrThrow(offlineContactSchema, input);
  return deps.mutex.run(conversationId, async () => {
    const conv = await getConversation(deps.db, conversationId);
    if (!conv) throw new ValidationError([{ path: 'conversationId', message: 'Unknown conversation' }]);
    const business = await getBusinessById(deps.db, conv.businessId);
    if (!business) throw new Error(`Business ${conv.businessId} not found`);
    const now = deps.now();
    const previousId = await lastMessageId(deps.db, conv.id);
    const contact = { name: parsed.name || null, email: parsed.email ?? null, phone: parsed.phone || null };
    const provided = nonNullFields(contact);
    const statements: BatchItem<'sqlite'>[] = [];
    let contactId = conv.contactId;
    if (contactId) {
      statements.push(deps.db.update(contacts).set(provided).where(eq(contacts.id, contactId)));
    } else {
      contactId = crypto.randomUUID();
      statements.push(deps.db.insert(contacts).values({ id: contactId, businessId: business.id, ...contact, createdAt: now }));
    }
    const summary = [contact.name, contact.email, contact.phone].filter(Boolean).join(' · ');
    statements.push(
      deps.db.insert(messages).values({
        conversationId: conv.id,
        businessId: business.id,
        role: 'system',
        content: `Contact details left: ${summary}${parsed.note ? ` — ${parsed.note}` : ''}`,
        createdAt: now,
      }),
    );
    statements.push(
      deps.db
        .update(conversations)
        .set({
          contactId,
          needsAttention: true,
          attentionReason: conv.status === 'human' && conv.attentionReason ? conv.attentionReason : 'offline_contact',
          lastMessageAt: now,
        })
        .where(eq(conversations.id, conv.id)),
    );
    statements.push(auditInsert(deps.db, { businessId: business.id, actor: 'visitor', action: 'offline_contact', target: conv.id, now }));
    await deps.db.batch(statements as Batch);

    const [updated, newMessages] = await Promise.all([getConversation(deps.db, conv.id), listMessages(deps.db, conv.id, { afterId: previousId })]);
    if (!updated) throw new Error('Conversation disappeared');
    publishMessages(deps, updated, newMessages);
    publishConversation(deps, updated, false);
    return { notifications: deps.notifier.offlineContact({ business, conversation: updated, contact, note: parsed.note ?? '' }) };
  });
}

export async function agentReply(
  deps: ChatDeps,
  input: { businessId: string; conversationId: string; userId: string; text: string },
): Promise<MessageRow | null> {
  const text = input.text.trim();
  if (!text) throw new ValidationError([{ path: 'text', message: 'Message is empty' }]);
  if (text.length > MAX_AGENT_TEXT) {
    throw new ValidationError([{ path: 'text', message: `Messages are limited to ${MAX_AGENT_TEXT} characters` }]);
  }
  return deps.mutex.run(input.conversationId, async () => {
    const conv = await getConversationForBusiness(deps.db, input.businessId, input.conversationId);
    if (!conv) return null;
    const now = deps.now();
    const previousId = await lastMessageId(deps.db, conv.id);
    await deps.db.batch([
      deps.db.insert(messages).values({
        conversationId: conv.id,
        businessId: conv.businessId,
        role: 'agent',
        content: text,
        meta: { userId: input.userId },
        createdAt: now,
      }),
      deps.db
        .update(conversations)
        .set({ status: 'human', needsAttention: false, attentionReason: null, unreadCount: 0, assignedUserId: input.userId, lastMessageAt: now })
        .where(eq(conversations.id, conv.id)),
      auditInsert(deps.db, { businessId: conv.businessId, actor: `user:${input.userId}`, action: 'agent_reply', target: conv.id, now }),
    ]);
    const [updated, newMessages] = await Promise.all([getConversation(deps.db, conv.id), listMessages(deps.db, conv.id, { afterId: previousId })]);
    if (!updated) return null;
    publishMessages(deps, updated, newMessages);
    publishConversation(deps, updated, updated.status !== conv.status);
    return newMessages.find((m) => m.role === 'agent') ?? null;
  });
}

const STATUS_ACTIONS = {
  takeover: (userId: string) => ({ status: 'human' as const, needsAttention: false, attentionReason: null, assignedUserId: userId }),
  release: () => ({ status: 'bot' as const, needsAttention: false, attentionReason: null, assignedUserId: null }),
  close: () => ({ status: 'closed' as const, needsAttention: false, attentionReason: null, assignedUserId: null }),
};

export type StatusAction = keyof typeof STATUS_ACTIONS;

export async function setConversationStatus(
  deps: ChatDeps,
  input: { businessId: string; conversationId: string; action: StatusAction; userId: string },
): Promise<ConversationRow | null> {
  return deps.mutex.run(input.conversationId, async () => {
    const conv = await getConversationForBusiness(deps.db, input.businessId, input.conversationId);
    if (!conv) return null;
    await deps.db.batch([
      deps.db.update(conversations).set(STATUS_ACTIONS[input.action](input.userId)).where(eq(conversations.id, conv.id)),
      auditInsert(deps.db, {
        businessId: conv.businessId,
        actor: `user:${input.userId}`,
        action: `conversation_${input.action}`,
        target: conv.id,
        now: deps.now(),
      }),
    ]);
    const updated = await getConversation(deps.db, conv.id);
    if (!updated) return null;
    publishConversation(deps, updated, updated.status !== conv.status);
    return updated;
  });
}

export async function markConversationRead(deps: ChatDeps, businessId: string, conversationId: string): Promise<boolean> {
  const rows = await deps.db
    .update(conversations)
    .set({ unreadCount: 0 })
    .where(and(eq(conversations.id, conversationId), eq(conversations.businessId, businessId)))
    .returning({ id: conversations.id });
  return rows.length > 0;
}
