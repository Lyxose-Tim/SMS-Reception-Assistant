import { describe, expect, it } from 'vitest';
import { loadEnv } from './env';

const secret = 'x'.repeat(32);

describe('loadEnv', () => {
  it('applies defaults', () => {
    const env = loadEnv({ APP_SECRET: secret });
    expect(env).toMatchObject({
      NODE_ENV: 'development',
      HOST: '127.0.0.1',
      PORT: 8787,
      DATABASE_URL: 'file:./data/reception.db',
      PUBLIC_ORIGIN: 'http://127.0.0.1:8787',
      TRUST_PROXY: false,
    });
    expect(env.SMTP_URL).toBeUndefined();
  });

  it('requires a long APP_SECRET', () => {
    expect(() => loadEnv({})).toThrow(/APP_SECRET/);
    expect(() => loadEnv({ APP_SECRET: 'short' })).toThrow(/APP_SECRET/);
  });

  it('parses numbers, booleans and strips a trailing slash from PUBLIC_ORIGIN', () => {
    const env = loadEnv({ APP_SECRET: secret, PORT: '9000', TRUST_PROXY: 'true', PUBLIC_ORIGIN: 'https://chat.example.com/' });
    expect(env.PORT).toBe(9000);
    expect(env.TRUST_PROXY).toBe(true);
    expect(env.PUBLIC_ORIGIN).toBe('https://chat.example.com');
  });

  it('normalizes PUBLIC_ORIGIN to a canonical origin', () => {
    expect(loadEnv({ APP_SECRET: secret, PUBLIC_ORIGIN: 'https://Chat.Example.com:443/' }).PUBLIC_ORIGIN).toBe('https://chat.example.com');
    expect(loadEnv({ APP_SECRET: secret, PUBLIC_ORIGIN: 'http://Admin.Test:8080' }).PUBLIC_ORIGIN).toBe('http://admin.test:8080');
  });

  it('rejects a PUBLIC_ORIGIN with a query, hash or credentials', () => {
    for (const bad of ['https://x.example/?a=1', 'https://x.example/#frag', 'https://u:p@x.example', 'ftp://x.example', 'not a url']) {
      expect(() => loadEnv({ APP_SECRET: secret, PUBLIC_ORIGIN: bad })).toThrow(/PUBLIC_ORIGIN/);
    }
  });

  it('rejects a PUBLIC_ORIGIN with a path', () => {
    expect(() => loadEnv({ APP_SECRET: secret, PUBLIC_ORIGIN: 'https://x.example/admin' })).toThrow(/PUBLIC_ORIGIN/);
  });
});
