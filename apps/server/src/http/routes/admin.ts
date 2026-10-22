import { KNOWLEDGE_LIMITS } from '@reception/core';
import { Hono, type Context } from 'hono';
import { z } from 'zod';
import { publicAiSettings, saveAiSettings, testAiConnection } from '../../ai/settings';
import { knowledgeUsage, parseOrThrow } from '../../domain/validation';
import { writeAudit } from '../../repos/audit';
import { decideBooking, getBookingForBusiness, listBookings, listBookingsForConversation } from '../../repos/bookings';
import { createFaq, deleteFaq, getBusinessById, listFaqs, toFaq, updateBusiness, updateFaq, type BusinessRow } from '../../repos/business';
import { getContact, getConversationForBusiness, listConversations, listLatestMessages, type PageCursor } from '../../repos/conversations';
import {
  agentReply,
  bizTopic,
  conversationSummary,
  markConversationRead,
  MAX_AGENT_TEXT,
  publishConversation,
  setConversationStatus,
  toAdminMessage,
  type StatusAction,
} from '../../services/chat';
import { toBookingDto, toContactDto, toFaqDto } from '../../services/dto';
import type { AppDeps } from '../deps';
import { requireSameOrigin, requireSession } from '../middleware';
import { sseStream } from '../sse';
import type { AppEnv } from '../types';
import { readJson } from '../util';

const textSchema = z.object({ text: z.string().min(1).max(MAX_AGENT_TEXT) }).strict();
const decisionSchema = z.object({ note: z.string().trim().max(500).optional() }).strict();
const listFilterSchema = z.enum(['all', 'attention', 'open']).default('all');
const bookingStatusSchema = z.enum(['pending', 'confirmed', 'declined', 'cancelled']).optional();

const DEFAULT_PAGE = 50;
const MAX_PAGE = 100;
const DETAIL_MESSAGES = 200;
const DEFAULT_MESSAGE_PAGE = 100;

const wholeNumber = z.string().regex(/^\d{1,15}$/, { error: 'Must be a whole number' }).transform(Number);
const limitParam = (max: number, fallback: number) =>
  wholeNumber
    .pipe(z.number().min(1).max(max))
    .optional()
    .transform((v) => v ?? fallback);
/** "<epoch ms>:<id>", as returned in nextCursor. */
const cursorParam = z
  .string()
  .regex(/^\d{1,15}:[A-Za-z0-9-]{1,64}$/, { error: 'Malformed cursor' })
  .transform((v): PageCursor => {
    const i = v.indexOf(':');
    return { at: Number(v.slice(0, i)), id: v.slice(i + 1) };
  })
  .optional();
const conversationQuerySchema = z.object({ filter: listFilterSchema, limit: limitParam(MAX_PAGE, DEFAULT_PAGE), before: cursorParam });
const bookingQuerySchema = z.object({ status: bookingStatusSchema, limit: limitParam(MAX_PAGE, DEFAULT_PAGE), before: cursorParam });
const messagesQuerySchema = z.object({ limit: limitParam(DETAIL_MESSAGES, DEFAULT_MESSAGE_PAGE), before: wholeNumber.optional() });

const cursorOf = (at: Date, id: string) => `${at.getTime()}:${id}`;

export function publicBusiness(b: BusinessRow) {
  return {
    id: b.id,
    slug: b.slug,
    name: b.name,
    locale: b.locale,
    timezone: b.timezone,
    profile: b.profile,
    bookingFields: b.bookingFields,
    widget: b.widget,
    notify: b.notify,
  };
}

const notFound = (c: Context) => c.json({ error: 'not_found' }, 404);
const forbidden = (c: Context) => c.json({ error: 'forbidden' }, 403);

export function adminRoutes(deps: AppDeps) {
  const app = new Hono<AppEnv>();
  app.use('*', requireSameOrigin(deps), requireSession(deps));

  const businessId = (c: Context<AppEnv>) => c.get('auth').businessId;
  const userId = (c: Context<AppEnv>) => c.get('auth').user.id;
  const currentBusiness = async (c: Context<AppEnv>) => {
    const business = await getBusinessById(deps.db, businessId(c));
    if (!business) throw new Error('Session business no longer exists');
    return business;
  };

  // Conversations
  app.get('/conversations', async (c) => {
    const query = parseOrThrow(conversationQuerySchema, c.req.query());
    // One extra row tells whether another page exists.
    const items = await listConversations(deps.db, businessId(c), {
      filter: query.filter,
      limit: query.limit + 1,
      ...(query.before ? { before: query.before } : {}),
    });
    const page = items.slice(0, query.limit);
    const last = page.at(-1);
    return c.json({
      conversations: page.map((i) => ({
        ...conversationSummary(i.conversation),
        contact: i.contact ? toContactDto(i.contact) : null,
        lastMessage: i.lastMessage ? toAdminMessage(i.lastMessage) : null,
      })),
      nextCursor: items.length > query.limit && last ? cursorOf(last.conversation.lastMessageAt, last.conversation.id) : null,
    });
  });

  app.get('/conversations/:id', async (c) => {
    const found = await getConversationForBusiness(deps.db, businessId(c), c.req.param('id'));
    if (!found) return notFound(c);
    await markConversationRead(deps, businessId(c), found.id);
    const conversation = (await getConversationForBusiness(deps.db, businessId(c), found.id)) ?? { ...found, unreadCount: 0 };
    const [rows, bookings, contact, business] = await Promise.all([
      listLatestMessages(deps.db, conversation.id, { limit: DETAIL_MESSAGES + 1 }),
      listBookingsForConversation(deps.db, conversation.id),
      conversation.contactId ? getContact(deps.db, conversation.contactId) : Promise.resolve(null),
      currentBusiness(c),
    ]);
    // Lets other open dashboard tabs clear the unread badge.
    publishConversation(deps, conversation, false);
    return c.json({
      conversation: { ...conversationSummary(conversation), bookingDraft: conversation.bookingDraft },
      contact: contact && contact.businessId === conversation.businessId ? toContactDto(contact) : null,
      messages: rows.slice(-DETAIL_MESSAGES).map(toAdminMessage),
      hasMoreMessages: rows.length > DETAIL_MESSAGES,
      bookings: bookings.map((b) => toBookingDto(b, business.bookingFields)),
    });
  });

  app.get('/conversations/:id/messages', async (c) => {
    const query = parseOrThrow(messagesQuerySchema, c.req.query());
    const conversation = await getConversationForBusiness(deps.db, businessId(c), c.req.param('id'));
    if (!conversation) return notFound(c);
    const rows = await listLatestMessages(deps.db, conversation.id, {
      limit: query.limit + 1,
      ...(query.before !== undefined ? { beforeId: query.before } : {}),
    });
    return c.json({ messages: rows.slice(-query.limit).map(toAdminMessage), hasMore: rows.length > query.limit });
  });

  for (const action of ['takeover', 'release', 'close'] as const satisfies readonly StatusAction[]) {
    app.post(`/conversations/:id/${action}`, async (c) => {
      const updated = await setConversationStatus(deps, { businessId: businessId(c), conversationId: c.req.param('id'), action, userId: userId(c) });
      return updated ? c.json({ conversation: conversationSummary(updated) }) : notFound(c);
    });
  }

  app.post('/conversations/:id/messages', async (c) => {
    const body = parseOrThrow(textSchema, await readJson(c));
    const message = await agentReply(deps, { businessId: businessId(c), conversationId: c.req.param('id'), userId: userId(c), text: body.text });
    return message ? c.json({ message: toAdminMessage(message) }, 201) : notFound(c);
  });

  // Business profile & FAQs
  app.get('/business', async (c) => {
    const business = await currentBusiness(c);
    const faqRows = await listFaqs(deps.db, business.id);
    return c.json({
      business: publicBusiness(business),
      limits: KNOWLEDGE_LIMITS,
      emailEnabled: deps.emailEnabled,
      usage: knowledgeUsage(business.name, business.profile, faqRows.map(toFaq)),
    });
  });

  app.patch('/business', async (c) => {
    const updated = await updateBusiness(deps.db, businessId(c), await readJson(c), deps.now());
    if (!updated) return notFound(c);
    await writeAudit(deps.db, { businessId: updated.id, actor: `user:${userId(c)}`, action: 'business_updated', now: deps.now() });
    return c.json({ business: publicBusiness(updated) });
  });

  app.get('/faqs', async (c) => c.json({ faqs: (await listFaqs(deps.db, businessId(c))).map(toFaqDto) }));

  app.post('/faqs', async (c) => {
    const faq = await createFaq(deps.db, businessId(c), await readJson(c));
    return c.json({ faq: toFaqDto(faq) }, 201);
  });

  app.patch('/faqs/:id', async (c) => {
    const faq = await updateFaq(deps.db, businessId(c), c.req.param('id'), await readJson(c), deps.now());
    return faq ? c.json({ faq: toFaqDto(faq) }) : notFound(c);
  });

  app.delete('/faqs/:id', async (c) => ((await deleteFaq(deps.db, businessId(c), c.req.param('id'))) ? c.body(null, 204) : notFound(c)));

  // Bookings
  app.get('/bookings', async (c) => {
    const query = parseOrThrow(bookingQuerySchema, c.req.query());
    const [rows, business] = await Promise.all([
      listBookings(deps.db, businessId(c), {
        limit: query.limit + 1,
        ...(query.status ? { status: query.status } : {}),
        ...(query.before ? { before: query.before } : {}),
      }),
      currentBusiness(c),
    ]);
    const page = rows.slice(0, query.limit);
    const last = page.at(-1);
    return c.json({
      bookings: page.map((b) => toBookingDto(b, business.bookingFields)),
      nextCursor: rows.length > query.limit && last ? cursorOf(last.createdAt, last.id) : null,
    });
  });

  for (const [action, decision] of [
    ['confirm', 'confirmed'],
    ['decline', 'declined'],
  ] as const) {
    app.post(`/bookings/:id/${action}`, async (c) => {
      const body = parseOrThrow(decisionSchema, await readJson(c, { optional: true }));
      const existing = await getBookingForBusiness(deps.db, businessId(c), c.req.param('id'));
      if (!existing) return notFound(c);
      const now = deps.now();
      const booking = await decideBooking(deps.db, { businessId: businessId(c), bookingId: existing.id, decision, note: body.note ?? null, userId: userId(c), now });
      if (!booking) return c.json({ error: 'already_decided' }, 409);
      await writeAudit(deps.db, { businessId: booking.businessId, actor: `user:${userId(c)}`, action: `booking_${decision}`, target: booking.id, now });
      const dto = toBookingDto(booking, (await currentBusiness(c)).bookingFields);
      deps.bus.publish(bizTopic(booking.businessId), { type: 'booking', data: dto });
      return c.json({ booking: dto });
    });
  }

  // AI settings (the key is write-only)
  app.get('/ai', async (c) => c.json({ ai: publicAiSettings((await currentBusiness(c)).ai) }));

  app.put('/ai', async (c) => {
    if (c.get('auth').user.role !== 'owner') return forbidden(c);
    const business = await currentBusiness(c);
    const ai = await saveAiSettings(deps.db, business, await readJson(c), deps.secretKey, deps.now());
    await writeAudit(deps.db, { businessId: business.id, actor: `user:${userId(c)}`, action: 'ai_settings_updated', data: { provider: ai.provider, enabled: ai.enabled }, now: deps.now() });
    return c.json({ ai: publicAiSettings(ai) });
  });

  app.post('/ai/test', async (c) => {
    if (c.get('auth').user.role !== 'owner') return forbidden(c);
    return c.json(await testAiConnection(await currentBusiness(c), deps.secretKey, deps.languageModelFactory));
  });

  // Live events for the dashboard; no replay (the client refetches lists on reconnect).
  app.get('/events', (c) =>
    sseStream(c, deps, {
      topic: bizTopic(businessId(c)),
      connectionKey: `session:${c.get('auth').sessionId}`,
      lastEventId: null,
      replay: async () => [],
    }),
  );

  return app;
}
