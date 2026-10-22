import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { eq } from 'drizzle-orm';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { openDatabase } from './db/client';
import { bookingRequests } from './db/schema';
import { loadEnv, type Env } from './env';
import { startServer } from './server';
import { seedDemoBusiness } from './testing/fixtures';

let dir: string;
let env: Env;

beforeEach(async () => {
  dir = mkdtempSync(path.join(tmpdir(), 'reception-server-'));
  env = loadEnv({ APP_SECRET: 'p'.repeat(40), DATABASE_URL: `file:${path.join(dir, 'server.db')}` });
  const db = await openDatabase(env.DATABASE_URL);
  await seedDemoBusiness(db.db);
  db.close();
});
afterEach(() => {
  try {
    rmSync(dir, { recursive: true, force: true, maxRetries: 5 });
  } catch {
    // On Windows the native libsql connection keeps the .db/-wal/-shm handles until GC even after
    // close(), so removal can fail with EPERM/EBUSY. The dir lives in the OS temp folder; ignore.
  }
});

async function post(url: string, body?: unknown, token?: string) {
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'content-type': 'application/json', ...(token ? { authorization: `Bearer ${token}` } : {}) },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  return res.json() as Promise<Record<string, unknown>>;
}

describe('startServer', () => {
  it('continues a booking across a server restart', async () => {
    let server = await startServer(env, { mailer: null, port: 0 });
    const chat = (await post(`${server.url}/api/widget/b/harbor-home/conversations`)) as { conversationId: string; visitorToken: string };
    const say = (text: string) => post(`${server.url}/api/widget/c/${chat.conversationId}/messages`, { text }, chat.visitorToken);
    await say('I want to book a deep cleaning');
    await say('Saturday morning');
    await server.close();

    server = await startServer(env, { mailer: null, port: 0 });
    await say('Alex');
    const last = (await say('alex@example.com')) as { messages: { content: string }[] };
    expect(last.messages.at(-1)!.content).toContain('pending confirmation');
    const [booking] = await server.deps.db.select().from(bookingRequests).where(eq(bookingRequests.conversationId, chat.conversationId));
    expect(booking?.fields).toEqual({ service: 'Deep cleaning', preferred_time: 'Saturday morning', name: 'Alex', contact: 'alex@example.com' });
    await server.close();
  });
});
