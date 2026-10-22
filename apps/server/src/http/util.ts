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

/** X-Forwarded-For is only trusted when TRUST_PROXY=true (the app sits behind a known reverse proxy). */
export function clientIp(c: Context, trustProxy: boolean): string {
  if (trustProxy) {
    const first = c.req.header('x-forwarded-for')?.split(',')[0]?.trim();
    if (first) return first;
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
