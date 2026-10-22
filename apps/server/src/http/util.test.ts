import { Hono } from 'hono';
import { describe, expect, it } from 'vitest';
import { clientIp } from './util';

function ipOf(trustProxy: boolean, xff: string | undefined, remote = '10.0.0.1'): Promise<string> {
  const app = new Hono();
  app.get('/', (c) => c.text(clientIp(c, trustProxy)));
  const headers: Record<string, string> = xff === undefined ? {} : { 'x-forwarded-for': xff };
  return Promise.resolve(app.request('/', { headers }, { incoming: { socket: { remoteAddress: remote } } } as never)).then((r) => r.text());
}

describe('clientIp', () => {
  it('uses the rightmost X-Forwarded-For hop when the proxy is trusted', async () => {
    expect(await ipOf(true, '6.6.6.6, 203.0.113.9')).toBe('203.0.113.9');
    expect(await ipOf(true, '203.0.113.9')).toBe('203.0.113.9');
  });

  it('skips empty trailing entries', async () => {
    expect(await ipOf(true, '6.6.6.6, 203.0.113.9, ,')).toBe('203.0.113.9');
  });

  it('ignores X-Forwarded-For when the proxy is not trusted', async () => {
    expect(await ipOf(false, '6.6.6.6, 203.0.113.9')).toBe('10.0.0.1');
  });

  it('falls back to the socket address when the header is missing or empty', async () => {
    expect(await ipOf(true, undefined)).toBe('10.0.0.1');
    expect(await ipOf(true, ' , ')).toBe('10.0.0.1');
  });
});
