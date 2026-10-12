import type { BookingDraft, BookingFieldDef, BusinessProfile, Locale } from '@reception/core';
import { index, integer, sqliteTable, text } from 'drizzle-orm/sqlite-core';

export const AI_PROVIDERS = ['openai', 'anthropic', 'google', 'openai-compatible'] as const;
export type AiProvider = (typeof AI_PROVIDERS)[number];

export interface WidgetSettings {
  primaryColor: string;
  position: 'left' | 'right';
  greeting: Record<Locale, string>;
  allowedOrigins: string[];
}

export interface AiSettings {
  provider: AiProvider | null;
  model: string;
  baseUrl: string | null;
  encryptedKey: string | null;
  keyLast4: string | null;
  enabled: boolean;
}

export interface NotifySettings {
  ownerEmail: string | null;
  visitorReceipts: boolean;
}

export type MessageRole = 'visitor' | 'bot' | 'agent' | 'system';

const uuid = () => text('id').primaryKey().$defaultFn(() => crypto.randomUUID());
const createdAt = () => integer('created_at', { mode: 'timestamp_ms' }).notNull().$defaultFn(() => new Date());

export const businesses = sqliteTable('businesses', {
  id: uuid(),
  slug: text('slug').notNull().unique(),
  name: text('name').notNull(),
  timezone: text('timezone').notNull().default('UTC'),
  locale: text('locale', { enum: ['en', 'zh-CN'] }).notNull().default('en'),
  profile: text('profile', { mode: 'json' }).$type<BusinessProfile>().notNull(),
  bookingFields: text('booking_fields', { mode: 'json' }).$type<BookingFieldDef[]>().notNull(),
  widget: text('widget', { mode: 'json' }).$type<WidgetSettings>().notNull(),
  ai: text('ai', { mode: 'json' }).$type<AiSettings>().notNull(),
  notify: text('notify', { mode: 'json' }).$type<NotifySettings>().notNull(),
  createdAt: createdAt(),
  updatedAt: integer('updated_at', { mode: 'timestamp_ms' }).notNull().$defaultFn(() => new Date()),
});

export const users = sqliteTable('users', {
  id: uuid(),
  businessId: text('business_id').notNull().references(() => businesses.id, { onDelete: 'cascade' }),
  email: text('email').notNull().unique(),
  passwordHash: text('password_hash').notNull(),
  role: text('role', { enum: ['owner', 'agent'] }).notNull().default('owner'),
  createdAt: createdAt(),
});

export const sessions = sqliteTable(
  'sessions',
  {
    id: text('id').primaryKey(), // sha256(token)
    userId: text('user_id').notNull().references(() => users.id, { onDelete: 'cascade' }),
    expiresAt: integer('expires_at', { mode: 'timestamp_ms' }).notNull(),
    createdAt: createdAt(),
  },
  (t) => [index('sessions_user_idx').on(t.userId)],
);

export const faqs = sqliteTable(
  'faqs',
  {
    id: uuid(),
    businessId: text('business_id').notNull().references(() => businesses.id, { onDelete: 'cascade' }),
    question: text('question').notNull(),
    answer: text('answer').notNull(),
    enabled: integer('enabled', { mode: 'boolean' }).notNull().default(true),
    sort: integer('sort').notNull().default(0),
    createdAt: createdAt(),
    updatedAt: integer('updated_at', { mode: 'timestamp_ms' }).notNull().$defaultFn(() => new Date()),
  },
  (t) => [index('faqs_business_idx').on(t.businessId, t.sort)],
);

export const contacts = sqliteTable('contacts', {
  id: uuid(),
  businessId: text('business_id').notNull().references(() => businesses.id, { onDelete: 'cascade' }),
  name: text('name'),
  email: text('email'),
  phone: text('phone'),
  createdAt: createdAt(),
});

export const conversations = sqliteTable(
  'conversations',
  {
    id: uuid(),
    businessId: text('business_id').notNull().references(() => businesses.id, { onDelete: 'cascade' }),
    contactId: text('contact_id').references(() => contacts.id, { onDelete: 'set null' }),
    channel: text('channel', { enum: ['web', 'sms'] }).notNull().default('web'),
    visitorTokenHash: text('visitor_token_hash').notNull().unique(),
    status: text('status', { enum: ['bot', 'human', 'closed'] }).notNull().default('bot'),
    needsAttention: integer('needs_attention', { mode: 'boolean' }).notNull().default(false),
    attentionReason: text('attention_reason'),
    assignedUserId: text('assigned_user_id'),
    locale: text('locale', { enum: ['en', 'zh-CN'] }),
    bookingDraft: text('booking_draft', { mode: 'json' }).$type<BookingDraft>(),
    visitorMessageCount: integer('visitor_message_count').notNull().default(0),
    unreadCount: integer('unread_count').notNull().default(0),
    lastMessageAt: integer('last_message_at', { mode: 'timestamp_ms' }).notNull().$defaultFn(() => new Date()),
    createdAt: createdAt(),
  },
  (t) => [index('conversations_business_idx').on(t.businessId, t.lastMessageAt)],
);

export const messages = sqliteTable(
  'messages',
  {
    id: integer('id').primaryKey({ autoIncrement: true }),
    conversationId: text('conversation_id').notNull().references(() => conversations.id, { onDelete: 'cascade' }),
    businessId: text('business_id').notNull(),
    role: text('role', { enum: ['visitor', 'bot', 'agent', 'system'] }).notNull(),
    content: text('content').notNull(),
    meta: text('meta', { mode: 'json' }).$type<Record<string, unknown>>(),
    createdAt: createdAt(),
  },
  (t) => [index('messages_conversation_idx').on(t.conversationId, t.id)],
);

export const bookingRequests = sqliteTable(
  'booking_requests',
  {
    id: uuid(),
    businessId: text('business_id').notNull().references(() => businesses.id, { onDelete: 'cascade' }),
    conversationId: text('conversation_id').notNull().references(() => conversations.id, { onDelete: 'cascade' }),
    contactId: text('contact_id').references(() => contacts.id, { onDelete: 'set null' }),
    fields: text('fields', { mode: 'json' }).$type<Record<string, string>>().notNull(),
    status: text('status', { enum: ['pending', 'confirmed', 'declined', 'cancelled'] }).notNull().default('pending'),
    ownerNote: text('owner_note'),
    receiptSentAt: integer('receipt_sent_at', { mode: 'timestamp_ms' }),
    decidedAt: integer('decided_at', { mode: 'timestamp_ms' }),
    decidedBy: text('decided_by'),
    createdAt: createdAt(),
  },
  (t) => [index('bookings_business_idx').on(t.businessId, t.createdAt)],
);

export const auditLog = sqliteTable('audit_log', {
  id: integer('id').primaryKey({ autoIncrement: true }),
  businessId: text('business_id'),
  actor: text('actor').notNull(),
  action: text('action').notNull(),
  target: text('target'),
  data: text('data', { mode: 'json' }).$type<Record<string, unknown>>(),
  createdAt: createdAt(),
});
