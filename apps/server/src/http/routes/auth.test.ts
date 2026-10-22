import { eq } from 'drizzle-orm';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { sessions } from '../../db/schema';
import { sha256 } from '../../lib/crypto';
import { createTestHarness, type TestHarness } from '../../testing/harness';

let h: TestHarness;
beforeEach(async () => {
  h = await createTestHarness();
});
afterEach(() => h.close());

const json = (body: unknown, origin: string | null, extra: Record<string, string> = {}) => ({
  method: 'POST',
  headers: { 'content-type': 'application/json', ...(origin ? { origin } : {}), ...extra },
  body: JSON.stringify(body),
});

describe('auth routes', () => {
  it('logs in with an HttpOnly Lax cookie and returns the business', async () => {
    const res = await h.request('/api/auth/login', json({ email: 'OWNER@harbor.example', password: h.password }, h.env.PUBLIC_ORIGIN));
    expect(res.status).toBe(200);
    const cookie = res.headers.get('set-cookie')!;
    expect(cookie).toMatch(/^rs_session=/);
    expect(cookie).toContain('HttpOnly');
    expect(cookie).toContain('SameSite=Lax');
    expect(cookie).not.toContain('Secure');
    expect(await res.json()).toMatchObject({ user: { email: 'owner@harbor.example', role: 'owner' }, business: { slug: 'harbor-home' } });
    const me = await h.request('/api/auth/me', { headers: { cookie: cookie.split(';')[0]! } });
    expect(me.status).toBe(200);
  });

  it('rejects wrong passwords and unknown users the same way', async () => {
    const wrong = await h.request('/api/auth/login', json({ email: 'owner@harbor.example', password: 'nope' }, h.env.PUBLIC_ORIGIN));
    const unknown = await h.request('/api/auth/login', json({ email: 'ghost@harbor.example', password: 'nope' }, h.env.PUBLIC_ORIGIN));
    expect(wrong.status).toBe(401);
    expect(unknown.status).toBe(401);
    expect(await wrong.json()).toEqual(await unknown.json());
  });

  it('rate limits login attempts per IP and email', async () => {
    for (let i = 0; i < 5; i++) {
      expect((await h.request('/api/auth/login', json({ email: 'owner@harbor.example', password: 'x' }, h.env.PUBLIC_ORIGIN))).status).toBe(401);
    }
    expect((await h.request('/api/auth/login', json({ email: 'owner@harbor.example', password: h.password }, h.env.PUBLIC_ORIGIN))).status).toBe(429);
    const otherIp = await h.request('/api/auth/login', json({ email: 'owner@harbor.example', password: h.password }, h.env.PUBLIC_ORIGIN), '198.51.100.9');
    expect(otherIp.status).toBe(200);
  });

  it('requires the admin origin for state-changing requests', async () => {
    expect((await h.request('/api/auth/login', json({ email: 'owner@harbor.example', password: h.password }, null))).status).toBe(403);
    expect((await h.request('/api/auth/login', json({ email: 'owner@harbor.example', password: h.password }, 'https://evil.example'))).status).toBe(403);
  });

  it('validates the body', async () => {
    const res = await h.request('/api/auth/login', json({ email: 'not-an-email' }, h.env.PUBLIC_ORIGIN));
    expect(res.status).toBe(422);
    const bad = await h.request('/api/auth/login', { method: 'POST', headers: { origin: h.env.PUBLIC_ORIGIN, 'content-type': 'application/json' }, body: '{' });
    expect(bad.status).toBe(400);
  });

  it('logs out and expires sessions', async () => {
    const cookie = await h.login();
    const out = await h.request('/api/auth/logout', { method: 'POST', headers: { cookie, origin: h.env.PUBLIC_ORIGIN } });
    expect(out.status).toBe(200);
    expect((await h.request('/api/auth/me', { headers: { cookie } })).status).toBe(401);

    const second = await h.login();
    h.clock.now = new Date(h.clock.now.getTime() + 8 * 24 * 60 * 60 * 1000);
    expect((await h.request('/api/auth/me', { headers: { cookie: second } })).status).toBe(401);
  });

  it('marks the cookie Secure when the public origin is https', async () => {
    const secure = await createTestHarness({ env: { PUBLIC_ORIGIN: 'https://chat.example.com' } });
    try {
      const res = await secure.request('/api/auth/login', json({ email: 'owner@harbor.example', password: secure.password }, 'https://chat.example.com'));
      expect(res.headers.get('set-cookie')).toContain('Secure');
    } finally {
      secure.close();
    }
  });

  it('clears the cookie with the same attributes on logout and requires the admin origin', async () => {
    const cookie = await h.login();
    const out = await h.request('/api/auth/logout', { method: 'POST', headers: { cookie, origin: h.env.PUBLIC_ORIGIN } });
    const set = out.headers.get('set-cookie')!;
    expect(set).toContain('Max-Age=0');
    expect(set).toContain('HttpOnly');
    expect(set).toContain('SameSite=Lax');
    expect(set).toContain('Path=/');
    expect((await h.request('/api/auth/logout', { method: 'POST', headers: { cookie } })).status).toBe(403);
  });

  it('deletes expired sessions of the user on login', async () => {
    const expiredId = sha256('expired-token');
    await h.database.db.insert(sessions).values({ id: expiredId, userId: h.owner.id, expiresAt: new Date(h.clock.now.getTime() - 1000), createdAt: new Date(h.clock.now.getTime() - 10_000) });
    await h.login();
    expect(await h.database.db.select().from(sessions).where(eq(sessions.id, expiredId))).toHaveLength(0);
  });

  it('rotates the presented session cookie on login', async () => {
    const old = await h.login();
    const res = await h.request('/api/auth/login', json({ email: 'owner@harbor.example', password: h.password }, h.env.PUBLIC_ORIGIN, { cookie: old }), '198.51.100.20');
    expect(res.status).toBe(200);
    const fresh = res.headers.get('set-cookie')!.split(';')[0]!;
    expect((await h.request('/api/auth/me', { headers: { cookie: old } })).status).toBe(401);
    expect((await h.request('/api/auth/me', { headers: { cookie: fresh } })).status).toBe(200);
  });

  it('limits failed logins per email regardless of source IP', async () => {
    for (let i = 0; i < 20; i++) {
      const res = await h.request('/api/auth/login', json({ email: 'owner@harbor.example', password: 'x' }, h.env.PUBLIC_ORIGIN), `198.51.100.${i + 1}`);
      expect(res.status).toBe(401);
    }
    const res = await h.request('/api/auth/login', json({ email: 'Owner@harbor.example', password: h.password }, h.env.PUBLIC_ORIGIN), '192.0.2.99');
    expect(res.status).toBe(429);
  });

  it('keys the IP limiter on the proxy-appended hop, not a spoofable leftmost X-Forwarded-For entry', async () => {
    const proxied = await createTestHarness({ env: { TRUST_PROXY: 'true' } });
    try {
      for (let i = 0; i < 5; i++) {
        const res = await proxied.request(
          '/api/auth/login',
          json({ email: 'owner@harbor.example', password: 'x' }, proxied.env.PUBLIC_ORIGIN, { 'x-forwarded-for': `10.9.8.${i}, 203.0.113.50` }),
        );
        expect(res.status).toBe(401);
      }
      const res = await proxied.request(
        '/api/auth/login',
        json({ email: 'owner@harbor.example', password: proxied.password }, proxied.env.PUBLIC_ORIGIN, { 'x-forwarded-for': '10.9.8.99, 203.0.113.50' }),
      );
      expect(res.status).toBe(429);
    } finally {
      proxied.close();
    }
  });

  it('serves a health check', async () => {
    expect(await (await h.request('/api/health')).json()).toEqual({ ok: true });
  });
});
