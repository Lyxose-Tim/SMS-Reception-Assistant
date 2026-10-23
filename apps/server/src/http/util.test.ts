import { Hono } from 'hono';
import { describe, expect, it, vi } from 'vitest';
import { clientIp, resetProxyWarningForTests } from './util';

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

  it('warns once per process when X-Forwarded-For arrives while TRUST_PROXY is off', async () => {
    resetProxyWarningForTests();
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    try {
      await ipOf(false, undefined);
      expect(warn).not.toHaveBeenCalled();
      await ipOf(true, '6.6.6.6');
      expect(warn).not.toHaveBeenCalled();
      await ipOf(false, '6.6.6.6');
      await ipOf(false, '6.6.6.7');
      expect(warn).toHaveBeenCalledTimes(1);
      expect(String(warn.mock.calls[0]![0])).toBe(
        'X-Forwarded-For received but TRUST_PROXY=false: rate limits use the proxy address. Set TRUST_PROXY=true when running behind a reverse proxy.',
      );
    } finally {
      warn.mockRestore();
      resetProxyWarningForTests();
    }
  });

  it('falls back to the socket address when the header is missing or empty', async () => {
    expect(await ipOf(true, undefined)).toBe('10.0.0.1');
    expect(await ipOf(true, ' , ')).toBe('10.0.0.1');
  });
});
