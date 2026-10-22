import type { HttpBindings } from '@hono/node-server';
import type { ReplyModel } from '@reception/core';
import type { Hono } from 'hono';
import type { LanguageModelFactory } from '../ai/providers';
import type { Database } from '../db/client';
import { loadEnv, type Env } from '../env';
import { createApp } from '../http/app';
import { createAppDeps, type AppDeps } from '../http/deps';
import type { AppEnv } from '../http/types';
import { createMemoryMailer, type MailMessage, type Mailer } from '../mail/mailer';
import type { BusinessRow } from '../repos/business';
import { createUser, type UserRow } from '../repos/users';
import { seedDemoBusiness, testDatabase } from './fixtures';

export interface TestHarness {
  database: Database;
  deps: AppDeps;
  app: Hono<AppEnv>;
  business: BusinessRow;
  owner: UserRow;
  password: string;
  mailer: Mailer & { sent: MailMessage[] };
  env: Env;
  clock: { now: Date };
  request(path: string, init?: RequestInit, ip?: string): Promise<Response>;
  login(): Promise<string>;
  adminHeaders(cookie: string, extra?: Record<string, string>): Record<string, string>;
  close(): void;
}

export interface HarnessOptions {
  resolveModel?: (business: BusinessRow) => ReplyModel | null;
  languageModelFactory?: LanguageModelFactory;
  heartbeatMs?: number;
  env?: Record<string, string>;
  /** Pass null to run without a mailer (emailEnabled false). */
  mailer?: null;
}

export async function createTestHarness(opts: HarnessOptions = {}): Promise<TestHarness> {
  const database = await testDatabase();
  const business = await seedDemoBusiness(database.db);
  const password = 'correct horse battery staple';
  const owner = await createUser(database.db, { businessId: business.id, email: 'owner@harbor.example', password });
  const mailer = createMemoryMailer();
  const env = loadEnv({ APP_SECRET: 'test-secret-'.padEnd(40, 'x'), PUBLIC_ORIGIN: 'http://admin.test', NODE_ENV: 'test', ...opts.env });
  const clock = { now: new Date('2026-10-11T10:00:00Z') };
  const deps = createAppDeps({
    env,
    db: database.db,
    mailer: opts.mailer === null ? null : mailer,
    now: () => clock.now,
    resolveModel: opts.resolveModel ?? (() => null),
    ...(opts.languageModelFactory ? { languageModelFactory: opts.languageModelFactory } : {}),
    ...(opts.heartbeatMs ? { heartbeatMs: opts.heartbeatMs } : {}),
  });
  const app = createApp(deps);
  // getConnInfo reads the node socket from bindings; app.request needs it passed explicitly.
  const request = async (path: string, init: RequestInit = {}, ip = '203.0.113.7') =>
    app.request(path, init, { incoming: { socket: { remoteAddress: ip } } } as unknown as HttpBindings);
  const adminHeaders = (cookie: string, extra: Record<string, string> = {}) => ({
    cookie,
    origin: env.PUBLIC_ORIGIN,
    'content-type': 'application/json',
    ...extra,
  });
  async function login(): Promise<string> {
    const res = await request('/api/auth/login', {
      method: 'POST',
      headers: { origin: env.PUBLIC_ORIGIN, 'content-type': 'application/json' },
      body: JSON.stringify({ email: owner.email, password }),
    });
    if (res.status !== 200) throw new Error(`login failed with ${res.status}`);
    return res.headers.get('set-cookie')!.split(';')[0]!;
  }
  return { database, deps, app, business, owner, password, mailer, env, clock, request, login, adminHeaders, close: () => database.close() };
}
