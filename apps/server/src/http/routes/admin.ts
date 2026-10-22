import { KNOWLEDGE_LIMITS } from '@reception/core';
import { Hono, type Context } from 'hono';
import { z } from 'zod';
import { publicAiSettings, saveAiSettings, testAiConnection } from '../../ai/settings';
import { parseOrThrow } from '../../domain/validation';
import { writeAudit } from '../../repos/audit';
import { decideBooking, getBookingForBusiness, listBookings, listBookingsForConversation } from '../../repos/bookings';
import { createFaq, deleteFaq, getBusinessById, listFaqs, updateBusiness, updateFaq, type BusinessRow } from '../../repos/business';
import { getContact, getConversationForBusiness, listConversations, listMessages } from '../../repos/conversations';
import {
  agentReply,
  bizTopic,
  conversationSummary,
  markConversationRead,
  MAX_AGENT_TEXT,
  setConversationStatus,
  toAdminMessage,
  type StatusAction,
} from '../../services/chat';
import type { AppDeps } from '../deps';
import { requireSameOrigin, requireSession } from '../middleware';
import { sseStream } from '../sse';
import type { AppEnv } from '../types';
import { readJson } from '../util';

const textSchema = z.object({ text: z.string().min(1).max(MAX_AGENT_TEXT) }).strict();
const decisionSchema = z.object({ note: z.string().trim().max(500).optional() }).strict();
const listFilterSchema = z.enum(['all', 'attention', 'open']).default('all');
const bookingStatusSchema = z.enum(['pending', 'confirmed', 'declined', 'cancelled']).optional();

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
    const filter = parseOrThrow(listFilterSchema, c.req.query('filter'));
    const items = await listConversations(deps.db, businessId(c), { filter });
    return c.json({
      conversations: items.map((i) => ({
        ...conversationSummary(i.conversation),
        contact: i.contact ? { id: i.contact.id, name: i.contact.name, email: i.contact.email, phone: i.contact.phone } : null,
        lastMessage: i.lastMessage ? toAdminMessage(i.lastMessage) : null,
      })),
    });
  });

  app.get('/conversations/:id', async (c) => {
    const conversation = await getConversationForBusiness(deps.db, businessId(c), c.req.param('id'));
    if (!conversation) return notFound(c);
    await markConversationRead(deps, businessId(c), conversation.id);
    const [rows, bookings, contact] = await Promise.all([
      listMessages(deps.db, conversation.id),
      listBookingsForConversation(deps.db, conversation.id),
      conversation.contactId ? getContact(deps.db, conversation.contactId) : Promise.resolve(null),
    ]);
    return c.json({
      conversation: { ...conversationSummary({ ...conversation, unreadCount: 0 }), bookingDraft: conversation.bookingDraft },
      contact,
      messages: rows.map(toAdminMessage),
      bookings,
    });
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
  app.get('/business', async (c) => c.json({ business: publicBusiness(await currentBusiness(c)), limits: KNOWLEDGE_LIMITS }));

  app.patch('/business', async (c) => {
    const updated = await updateBusiness(deps.db, businessId(c), await readJson(c), deps.now());
    if (!updated) return notFound(c);
    await writeAudit(deps.db, { businessId: updated.id, actor: `user:${userId(c)}`, action: 'business_updated', now: deps.now() });
    return c.json({ business: publicBusiness(updated) });
  });

  app.get('/faqs', async (c) => c.json({ faqs: await listFaqs(deps.db, businessId(c)) }));

  app.post('/faqs', async (c) => {
    const faq = await createFaq(deps.db, businessId(c), await readJson(c));
    return c.json({ faq }, 201);
  });

  app.patch('/faqs/:id', async (c) => {
    const faq = await updateFaq(deps.db, businessId(c), c.req.param('id'), await readJson(c), deps.now());
    return faq ? c.json({ faq }) : notFound(c);
  });

  app.delete('/faqs/:id', async (c) => ((await deleteFaq(deps.db, businessId(c), c.req.param('id'))) ? c.body(null, 204) : notFound(c)));

  // Bookings
  app.get('/bookings', async (c) => {
    const status = parseOrThrow(bookingStatusSchema, c.req.query('status'));
    return c.json({ bookings: await listBookings(deps.db, businessId(c), status ? { status } : {}) });
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
      deps.bus.publish(bizTopic(booking.businessId), { type: 'booking', data: booking });
      return c.json({ booking });
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
