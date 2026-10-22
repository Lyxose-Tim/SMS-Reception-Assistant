import { Hono } from 'hono';
import { deleteCookie, getCookie, setCookie } from 'hono/cookie';
import { z } from 'zod';
import { parseOrThrow } from '../../domain/validation';
import { hashPassword, verifyPassword } from '../../lib/crypto';
import { writeAudit } from '../../repos/audit';
import { getBusinessById } from '../../repos/business';
import { createSession, deleteSession, findUserByEmail, SESSION_TTL_MS } from '../../repos/users';
import type { AppDeps } from '../deps';
import { requireSameOrigin, requireSession, SESSION_COOKIE, sessionCookieOptions } from '../middleware';
import type { AppEnv } from '../types';
import { clientIp, readJson } from '../util';

const loginSchema = z.object({ email: z.email().max(200), password: z.string().min(1).max(200) }).strict();

let dummyHash: Promise<string> | null = null;
/** Verifying against a dummy hash keeps response time similar for unknown emails. */
const getDummyHash = () => (dummyHash ??= hashPassword('dummy-password-for-timing'));

export function authRoutes(deps: AppDeps) {
  const app = new Hono<AppEnv>();
  app.use('*', requireSameOrigin(deps));

  app.post('/login', async (c) => {
    const body = parseOrThrow(loginSchema, await readJson(c));
    const email = body.email.toLowerCase();
    if (!deps.limiters.login.hit(`${clientIp(c, deps.env.TRUST_PROXY)}|${email}`)) {
      return c.json({ error: 'too_many_attempts' }, 429);
    }
    const user = await findUserByEmail(deps.db, email);
    const valid = await verifyPassword(body.password, user?.passwordHash ?? (await getDummyHash()));
    if (!user || !valid) return c.json({ error: 'invalid_credentials' }, 401);
    const now = deps.now();
    const { token } = await createSession(deps.db, user.id, now);
    setCookie(c, SESSION_COOKIE, token, sessionCookieOptions(deps.env, SESSION_TTL_MS / 1000));
    await writeAudit(deps.db, { businessId: user.businessId, actor: `user:${user.id}`, action: 'login', now });
    const business = await getBusinessById(deps.db, user.businessId);
    return c.json({
      user: { id: user.id, email: user.email, role: user.role },
      business: business ? { id: business.id, slug: business.slug, name: business.name } : null,
    });
  });

  app.post('/logout', async (c) => {
    const token = getCookie(c, SESSION_COOKIE);
    if (token) await deleteSession(deps.db, token);
    deleteCookie(c, SESSION_COOKIE, { path: '/' });
    return c.json({ ok: true });
  });

  app.get('/me', requireSession(deps), async (c) => {
    const auth = c.get('auth');
    const business = await getBusinessById(deps.db, auth.businessId);
    return c.json({ user: auth.user, business: business ? { id: business.id, slug: business.slug, name: business.name } : null });
  });

  return app;
}
