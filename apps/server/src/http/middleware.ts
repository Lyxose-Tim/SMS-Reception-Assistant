import type { MiddlewareHandler } from 'hono';
import { getCookie } from 'hono/cookie';
import type { Env } from '../env';
import { getSessionAuth } from '../repos/users';
import type { AppDeps } from './deps';
import type { AppEnv } from './types';

export const SESSION_COOKIE = 'rs_session';
const SAFE_METHODS = new Set(['GET', 'HEAD', 'OPTIONS']);

export function sessionCookieOptions(env: Env, maxAgeSeconds: number) {
  return {
    httpOnly: true,
    sameSite: 'Lax' as const,
    secure: env.PUBLIC_ORIGIN.startsWith('https://'),
    path: '/',
    maxAge: maxAgeSeconds,
  };
}

/** CSRF defense in addition to SameSite=Lax: state-changing admin requests must come from the admin origin. */
export function requireSameOrigin(deps: AppDeps): MiddlewareHandler<AppEnv> {
  return async (c, next) => {
    if (SAFE_METHODS.has(c.req.method)) return next();
    if (c.req.header('origin') !== deps.env.PUBLIC_ORIGIN) return c.json({ error: 'bad_origin' }, 403);
    return next();
  };
}

export function requireSession(deps: AppDeps): MiddlewareHandler<AppEnv> {
  return async (c, next) => {
    const auth = await getSessionAuth(deps.db, getCookie(c, SESSION_COOKIE) ?? '', deps.now());
    if (!auth) return c.json({ error: 'unauthorized' }, 401);
    c.set('auth', auth);
    return next();
  };
}
