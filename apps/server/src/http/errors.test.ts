import { Hono } from 'hono';
import { HTTPException } from 'hono/http-exception';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { handleError } from './errors';

function appWith(thrower: () => never) {
  const app = new Hono();
  app.onError(handleError);
  app.get('/', () => thrower());
  return app;
}

afterEach(() => vi.restoreAllMocks());

describe('handleError', () => {
  it('hides unexpected errors behind a generic 500 and does not log the error object', async () => {
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {});
    const err = Object.assign(new Error('boom'), { params: ['secret-bound-param'] });
    const res = await appWith(() => {
      throw err;
    }).request('/');
    expect(res.status).toBe(500);
    expect(await res.json()).toEqual({ error: 'internal' });
    expect(spy).toHaveBeenCalled();
    expect(spy.mock.calls.every((call) => call.every((arg) => typeof arg === 'string'))).toBe(true);
    expect(JSON.stringify(spy.mock.calls)).not.toContain('secret-bound-param');
  });

  it('returns a JSON body for plain HTTPExceptions', async () => {
    const res = await appWith(() => {
      throw new HTTPException(400, { message: 'Invalid JSON body' });
    }).request('/');
    expect(res.status).toBe(400);
    expect(await res.json()).toEqual({ error: 'Invalid JSON body' });
  });

  it('passes through the custom response carried by an HTTPException', async () => {
    const res = await appWith(() => {
      throw new HTTPException(401, { res: new Response('nope', { status: 401, headers: { 'www-authenticate': 'Basic' } }) });
    }).request('/');
    expect(res.status).toBe(401);
    expect(res.headers.get('www-authenticate')).toBe('Basic');
    expect(await res.text()).toBe('nope');
  });
});
