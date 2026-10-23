import { getConnInfo } from '@hono/node-server/conninfo';
import type { Context } from 'hono';
import { HTTPException } from 'hono/http-exception';

export async function readJson(c: Context, opts: { optional?: boolean } = {}): Promise<unknown> {
  const text = await c.req.text();
  if (!text.trim()) {
    if (opts.optional) return {};
    throw new HTTPException(400, { message: 'A JSON body is required' });
  }
  try {
    return JSON.parse(text) as unknown;
  } catch {
    throw new HTTPException(400, { message: 'Invalid JSON body' });
  }
}

let proxyWarned = false;
export function resetProxyWarningForTests(): void {
  proxyWarned = false;
}

/**
 * X-Forwarded-For is only trusted when TRUST_PROXY=true. In that mode the RIGHTMOST non-empty entry is used:
 * it is the hop appended by our own reverse proxy, whereas everything to its left is client-controlled and
 * trivially spoofable. This requires that the proxy APPENDS the address it saw (e.g. nginx
 * `proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;`) and that the app is reachable ONLY through
 * that proxy; otherwise a client could connect directly and choose the value itself.
 */
export function clientIp(c: Context, trustProxy: boolean): string {
  if (!trustProxy && !proxyWarned && c.req.header('x-forwarded-for')) {
    proxyWarned = true;
    console.warn(
      'X-Forwarded-For received but TRUST_PROXY=false: rate limits use the proxy address. Set TRUST_PROXY=true when running behind a reverse proxy.',
    );
  }
  if (trustProxy) {
    const hops = (c.req.header('x-forwarded-for') ?? '')
      .split(',')
      .map((h) => h.trim())
      .filter(Boolean);
    const last = hops[hops.length - 1];
    if (last) return last;
  }
  try {
    return getConnInfo(c).remote.address ?? 'unknown';
  } catch {
    return 'unknown';
  }
}

export function parseEventId(value: string | undefined): number | null {
  if (!value || !/^\d{1,15}$/.test(value)) return null;
  return Number(value);
}
