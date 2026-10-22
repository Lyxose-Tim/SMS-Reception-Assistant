import { Hono, type MiddlewareHandler } from 'hono';
import { z } from 'zod';
import { parseOrThrow } from '../../domain/validation';
import { getBusinessById, getBusinessBySlug, listFaqs, type BusinessRow } from '../../repos/business';
import { getConversation, listMessages, verifyVisitorToken, VISITOR_VISIBLE_ROLES } from '../../repos/conversations';
import { convTopic, handleVisitorText, MAX_VISITOR_TEXT, offlineContactSchema, startConversation, submitOfflineContact, toPublicMessage } from '../../services/chat';
import type { AppDeps } from '../deps';
import { sseStream } from '../sse';
import type { AppEnv } from '../types';
import { clientIp, parseEventId, readJson } from '../util';

const messageSchema = z.object({ text: z.string().min(1).max(MAX_VISITOR_TEXT), locale: z.string().max(20).optional() }).strict();
const QUICK_QUESTIONS = 4;

function originAllowed(deps: AppDeps, business: BusinessRow, origin: string): boolean {
  return origin === deps.env.PUBLIC_ORIGIN || business.widget.allowedOrigins.includes(origin);
}

/** Per-business CORS: the widget may only be embedded on the merchant's allowed origins. */
export function corsGuard(deps: AppDeps): MiddlewareHandler<AppEnv> {
  return async (c, next) => {
    c.header('Vary', 'Origin');
    const origin = c.req.header('origin');
    if (origin) {
      if (!originAllowed(deps, c.get('business'), origin)) return c.json({ error: 'origin_not_allowed' }, 403);
      c.header('Access-Control-Allow-Origin', origin);
      c.header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS');
      c.header('Access-Control-Allow-Headers', 'Content-Type, Authorization, Last-Event-ID');
      c.header('Access-Control-Max-Age', '600');
    }
    if (c.req.method === 'OPTIONS') return c.body(null, 204);
    return next();
  };
}

function loadBusinessBySlug(deps: AppDeps): MiddlewareHandler<AppEnv> {
  return async (c, next) => {
    const business = await getBusinessBySlug(deps.db, c.req.param('slug') ?? '');
    if (!business) return c.json({ error: 'not_found' }, 404);
    c.set('business', business);
    return next();
  };
}

function loadConversation(deps: AppDeps): MiddlewareHandler<AppEnv> {
  return async (c, next) => {
    const conversation = await getConversation(deps.db, c.req.param('id') ?? '');
    const business = conversation ? await getBusinessById(deps.db, conversation.businessId) : null;
    if (!conversation || !business) return c.json({ error: 'not_found' }, 404);
    c.set('conversation', conversation);
    c.set('business', business);
    return next();
  };
}

/** The visitor token travels only in the Authorization header, never in a URL. */
const requireVisitorToken: MiddlewareHandler<AppEnv> = async (c, next) => {
  const header = c.req.header('authorization') ?? '';
  const token = header.startsWith('Bearer ') ? header.slice('Bearer '.length).trim() : '';
  if (!verifyVisitorToken(c.get('conversation'), token)) return c.json({ error: 'unauthorized' }, 401);
  return next();
};

export function widgetRoutes(deps: AppDeps) {
  const app = new Hono<AppEnv>();
  app.use('/b/:slug/*', loadBusinessBySlug(deps), corsGuard(deps));
  app.use('/c/:id/*', loadConversation(deps), corsGuard(deps), requireVisitorToken);

  app.get('/b/:slug/config', async (c) => {
    const business = c.get('business');
    const quickQuestions = (await listFaqs(deps.db, business.id))
      .filter((f) => f.enabled)
      .slice(0, QUICK_QUESTIONS)
      .map((f) => f.question);
    const { primaryColor, position, greeting } = business.widget;
    return c.json({
      business: { name: business.name, slug: business.slug },
      locale: business.locale,
      widget: { primaryColor, position, greeting },
      quickQuestions,
    });
  });

  app.post('/b/:slug/conversations', async (c) => {
    if (!deps.limiters.newConversation.hit(clientIp(c, deps.env.TRUST_PROXY))) return c.json({ error: 'rate_limited' }, 429);
    const { conversation, visitorToken } = await startConversation(deps, c.get('business'));
    return c.json({ conversationId: conversation.id, visitorToken }, 201);
  });

  app.get('/c/:id/messages', async (c) => {
    const conversation = c.get('conversation');
    const since = parseEventId(c.req.query('since'));
    const rows = await listMessages(deps.db, conversation.id, { ...(since !== null ? { afterId: since } : {}), roles: VISITOR_VISIBLE_ROLES, limit: 200 });
    return c.json({ messages: rows.map(toPublicMessage), status: conversation.status });
  });

  app.post('/c/:id/messages', async (c) => {
    const body = parseOrThrow(messageSchema, await readJson(c));
    const conversation = c.get('conversation');
    if (!deps.limiters.ipMessage.hit(clientIp(c, deps.env.TRUST_PROXY)) || !deps.limiters.visitorMessage.hit(conversation.id)) {
      return c.json({ error: 'rate_limited' }, 429);
    }
    const turn = await handleVisitorText(deps, conversation.id, body.text, body.locale ?? null, {
      ip: clientIp(c, deps.env.TRUST_PROXY),
    });
    void turn.notifications; // best-effort, never rejects
    return c.json({ messages: turn.messages, status: turn.status });
  });

  app.post('/c/:id/contact', async (c) => {
    const conversation = c.get('conversation');
    const body = parseOrThrow(offlineContactSchema, await readJson(c));
    if (!deps.limiters.contact.hit(conversation.id)) return c.json({ error: 'rate_limited' }, 429);
    const result = await submitOfflineContact(deps, conversation.id, body);
    void result.notifications;
    return c.json({ ok: true });
  });

  app.get('/c/:id/events', (c) => {
    const conversation = c.get('conversation');
    return sseStream(c, deps, {
      topic: convTopic(conversation.id),
      connectionKey: `visitor:${conversation.id}`,
      lastEventId: parseEventId(c.req.header('last-event-id')) ?? parseEventId(c.req.query('since')),
      replay: async (afterId) =>
        (await listMessages(deps.db, conversation.id, { afterId, roles: VISITOR_VISIBLE_ROLES, limit: 200 })).map((m) => ({
          type: 'message',
          id: String(m.id),
          data: toPublicMessage(m),
        })),
    });
  });

  return app;
}
