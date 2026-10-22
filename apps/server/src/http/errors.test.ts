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

  it('logs only the name, SQL text and driver message for ORM query errors, never bound parameters', async () => {
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {});
    const cause = new Error('SQLITE_CONSTRAINT: UNIQUE constraint failed: users.email');
    const err = Object.assign(new Error('Failed query: insert into users (email) values (?)\nparams: victim@example.com,hunter2'), {
      name: 'DrizzleQueryError',
      query: 'insert into users (email) values (?)',
      params: ['victim@example.com', 'hunter2'],
      cause,
    });
    const res = await appWith(() => {
      throw err;
    }).request('/');
    expect(res.status).toBe(500);
    const logged = JSON.stringify(spy.mock.calls);
    expect(logged).toContain('DrizzleQueryError');
    expect(logged).toContain('insert into users (email) values (?)');
    expect(logged).toContain('UNIQUE constraint failed');
    expect(logged).not.toContain('victim@example.com');
    expect(logged).not.toContain('hunter2');
  });

  it('also treats any error carrying a query string as an ORM error', async () => {
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {});
    const err = Object.assign(new Error('select ... params: secret-value'), { query: 'select 1' });
    await appWith(() => {
      throw err;
    }).request('/');
    expect(JSON.stringify(spy.mock.calls)).not.toContain('secret-value');
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
