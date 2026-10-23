# Plan 2 — Server (API, persistence, AI adapter) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `@reception/server`: a single-process Node HTTP server that persists conversations in SQLite, runs `@reception/core` for every visitor message (AI via the merchant's own key, rules fallback), exposes the widget API and the merchant admin API with live SSE updates, and emails the owner (and visitor receipts).

**Architecture:** `apps/server` in the existing npm-workspaces monorepo. Layers: env → db (Drizzle + libsql SQLite, WAL) → repos (data access, always scoped by `businessId`) → services (chat orchestration, notifier) → http (Hono routes: auth, widget, admin, SSE) → entry/CLI. Writes that span several statements use `db.batch` (atomic); each conversation's message handling is serialized with an in-process keyed mutex (no interactive transactions — see Global Constraints). The AI SDK lives only in `src/ai/` and implements core's `ReplyModel` port.

**Tech Stack:** Node 24, TypeScript 7, Hono 4.13 + @hono/node-server 2, drizzle-orm 0.45 + drizzle-kit 0.31 + @libsql/client 0.18, zod 4, ai 7 + @ai-sdk/{openai,anthropic,google,openai-compatible}, nodemailer 10, tsx 4, vitest 5.

This is plan 2 of 4 for `docs/superpowers/specs/2026-10-10-web-chat-mvp-design.md`. Plan 1 (`@reception/core`) is merged. Plans 3 (widget) and 4 (admin UI, demo, Docker) follow.

## Global Constraints

- Repo root `D:\SMS Reception Assistant` (Windows; Git Bash available). LF line endings. New package `apps/server` (`@reception/server`), depends on `@reception/core` (`"*"`, workspace).
- Run TS via `tsx` (dev and prod); bare `node` cannot resolve core's extensionless imports.
- **Verified library facts (do not use older APIs):**
  - ai v7: `generateText({ model, instructions, messages, tools, abortSignal, maxOutputTokens, maxRetries })` — `instructions` replaces deprecated `system`; tools use `tool({ description, inputSchema })`; tool calls are `result.toolCalls[i]` with `toolName`, `input`, optional `dynamic`/`invalid`; default is a single step. Test mock: `MockLanguageModelV4` from `ai/test`; its `doGenerate` returns `{ content: [{type:'text',text}, {type:'tool-call',toolCallId,toolName,input: JSON.stringify(obj)}], finishReason: { unified: 'tool-calls'|'stop', raw: undefined }, usage: { inputTokens: { total, noCache, cacheRead: undefined, cacheWrite: undefined }, outputTokens: { total, text, reasoning: undefined } }, warnings: [] }`.
  - Providers: `createOpenAI({apiKey, baseURL?})(id)`, `createAnthropic({apiKey})(id)`, `createGoogleGenerativeAI({apiKey})(id)`, `createOpenAICompatible({name, apiKey, baseURL})(id)`.
  - Hono: `streamSSE` (`hono/streaming`) with `stream.writeSSE({event,data,id})`, `stream.write(text)`, `stream.onAbort(cb)`, `stream.aborted`; `getConnInfo` (`@hono/node-server/conninfo`) throws without node bindings → tests pass `{ incoming: { socket: { remoteAddress } } }` as the 3rd arg of `app.request`.
  - libsql/drizzle: overlapping interactive transactions fail (`TRANSACTION_ACTIVE` / `SQLITE_BUSY`) even in one process → **never use `db.transaction`**; use `db.batch([...])` (atomic) and the per-conversation keyed mutex. Constraint error codes are on `error.cause.code`. `migrate()`'s folder is cwd-relative → resolve it from `import.meta.url`.
  - nodemailer 10 ships its own types (no `@types/nodemailer`).
- Database: `PRAGMA journal_mode=WAL` (file DBs), busy timeout 5000 ms (`createClient({ timeout: 5000 })`), `PRAGMA foreign_keys=ON`. No network IO (AI calls, email) between statements of one write.
- Every admin query is scoped by the session's `businessId`; a foreign id returns 404 (never 403/200).
- Rate limits (spec): visitor token 20 msgs/min; IP 60 msgs/min; IP 20 new conversations/hour; login 5 attempts / 5 min per IP+email; offline contact 5/hour per conversation. Visitor message ≤ 1000 chars; agent message ≤ 2000 chars. Request body ≤ 32 KB.
- Handoff policy (from Plan 1 final review): `visitor_request` and `message_limit` are **hard** (status → `human`, bot silent); `unknown_question` and `model_request` are **soft** (status stays `bot`, `needsAttention = true`, owner notified).
- `visitorMessageCount` passed to core is the count **before** the current message (excludes it). History passed to core contains only `visitor` / `bot` / `agent` roles.
- SSE: in-process bus `Map<topic, Set<listener>>`; unsubscribe in both `onAbort` and `finally`; heartbeat comment every 25 s; ≤ 3 concurrent streams per visitor conversation / admin session (oldest closed); replay via `Last-Event-ID` header or `?since=`. The visitor token is sent in the `Authorization: Bearer` header, **never in a URL**.
- Admin auth: DB-persisted sessions (token hashed with SHA-256), cookie `rs_session` HttpOnly, SameSite=Lax, Secure when `PUBLIC_ORIGIN` is https, 7-day TTL; state-changing admin and auth requests require `Origin === PUBLIC_ORIGIN`.
- BYOK: API keys encrypted with AES-256-GCM using a key derived (HKDF-SHA256) from `APP_SECRET` (≥ 32 chars); API responses expose only `keyLast4`/`hasKey`, never the key or ciphertext.
- Booking requests created by visitors/AI are always `pending`; only an authenticated owner/agent confirms or declines (only from `pending`).
- Visitor receipt email: at most once per booking (atomic claim), visitor-supplied text has URLs stripped and is length-capped, merchant toggle `notify.visitorReceipts`.
- Commit after every task; message ends with a blank line then `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` (verbatim, regardless of the implementing model).

---

## File Structure

```
apps/server/
  package.json  tsconfig.json  drizzle.config.ts  README.md
  drizzle/                         generated SQL migrations (committed)
  src/
    env.ts                         loadEnv(): zod-validated environment
    db/schema.ts                   Drizzle tables + JSON column types
    db/client.ts                   openDatabase(url): pragmas + migrate
    lib/crypto.ts                  scrypt passwords, tokens, sha256, AES-GCM secrets
    lib/rateLimit.ts               in-memory sliding-window limiter
    lib/bus.ts                     topic event bus
    lib/mutex.ts                   keyed async mutex
    lib/connections.ts             per-key connection cap registry
    domain/validation.ts           zod schemas, ValidationError, knowledge budget
    domain/contact.ts              contactFromFields
    repos/business.ts              business + FAQ data access
    repos/conversations.ts         conversations, messages, contacts reads
    repos/bookings.ts              booking requests
    repos/audit.ts                 audit log
    repos/users.ts                 users + sessions
    mail/mailer.ts                 Mailer interface, SMTP + memory mailers
    mail/templates.ts              en/zh email texts + sanitizeField
    services/notifier.ts           owner/visitor notifications (never throws)
    services/chat.ts               conversation orchestration around core
    ai/providers.ts                provider → LanguageModel factory
    ai/replyModel.ts               AI SDK → core ReplyModel adapter
    ai/settings.ts                 BYOK save/resolve/test
    http/types.ts                  Hono AppEnv
    http/deps.ts                   AppDeps, createAppDeps, limiters
    http/util.ts                   readJson, clientIp
    http/errors.ts                 onError handler
    http/middleware.ts             requireSession, requireSameOrigin
    http/sse.ts                    sseStream
    http/listen.ts                 listen(app, host, port) for tests and server.ts
    http/app.ts                    createApp
    http/routes/auth.ts|widget.ts|admin.ts
    testing/fixtures.ts            testDatabase, seedDemoBusiness
    testing/harness.ts             createTestHarness (app + login)
    server.ts                      startServer (used by index + tests)
    index.ts                       process entry
    cli.ts                         create-owner / seed-demo
    eval/promptEval.ts             live prompt-injection eval (needs a real key)
```

---

### Task 1: Server package, environment, schema, database client

**Files:**
- Create: `apps/server/package.json`, `apps/server/tsconfig.json`, `apps/server/drizzle.config.ts`
- Create: `apps/server/src/env.ts`, `apps/server/src/db/schema.ts`, `apps/server/src/db/client.ts`
- Create (generated): `apps/server/drizzle/*`
- Modify: root `package.json` (typecheck script), root `.gitignore` (data dir)
- Test: `apps/server/src/env.test.ts`, `apps/server/src/db/client.test.ts`

**Interfaces:**
- Produces: `loadEnv(source?): Env`, `Env`; all tables (`businesses`, `users`, `sessions`, `faqs`, `contacts`, `conversations`, `messages`, `bookingRequests`, `auditLog`); types `WidgetSettings`, `AiSettings`, `AiProvider`, `AI_PROVIDERS`, `NotifySettings`, `MessageRole`; `openDatabase(url): Promise<Database>`, `Database { db, client, close() }`, `Db`.

- [ ] **Step 1: Create the package**

`apps/server/package.json`:
```json
{
  "name": "@reception/server",
  "version": "0.1.0",
  "private": true,
  "type": "module",
  "scripts": {
    "dev": "tsx watch src/index.ts",
    "start": "tsx src/index.ts",
    "cli": "tsx src/cli.ts",
    "db:generate": "drizzle-kit generate",
    "eval:prompts": "tsx src/eval/promptEval.ts"
  },
  "dependencies": {
    "@reception/core": "*"
  }
}
```

`apps/server/tsconfig.json`:
```json
{
  "extends": "../../tsconfig.base.json",
  "include": ["src/**/*.ts", "drizzle.config.ts"]
}
```

`apps/server/drizzle.config.ts`:
```ts
import { defineConfig } from 'drizzle-kit';

export default defineConfig({
  dialect: 'sqlite',
  schema: './src/db/schema.ts',
  out: './drizzle',
});
```

Root `package.json` — change the `typecheck` script to:
```json
"typecheck": "tsc -p packages/core/tsconfig.json && tsc -p apps/server/tsconfig.json"
```

Root `.gitignore` — add a line `apps/server/data/`.

Run (registry is slow; allow ~10 min):
```bash
npm install -w @reception/server hono@^4.13 @hono/node-server@^2 drizzle-orm@^0.45 @libsql/client@^0.18 zod@^4 ai@^7 @ai-sdk/openai@^4 @ai-sdk/anthropic@^4 @ai-sdk/google@^4 @ai-sdk/openai-compatible@^3 nodemailer@^10 tsx@^4
npm install -w @reception/server -D drizzle-kit@^0.31
```

- [ ] **Step 2: Write failing tests**

`apps/server/src/env.test.ts`:
```ts
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

  it('rejects a PUBLIC_ORIGIN with a path', () => {
    expect(() => loadEnv({ APP_SECRET: secret, PUBLIC_ORIGIN: 'https://x.example/admin' })).toThrow(/PUBLIC_ORIGIN/);
  });
});
```

`apps/server/src/db/client.test.ts`:
```ts
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';
import { openDatabase, type Database } from './client';
import { auditLog } from './schema';

let opened: Database[] = [];
let dirs: string[] = [];

afterEach(() => {
  for (const d of opened) d.close();
  for (const dir of dirs) rmSync(dir, { recursive: true, force: true, maxRetries: 5 });
  opened = [];
  dirs = [];
});

async function open(url: string): Promise<Database> {
  const database = await openDatabase(url);
  opened.push(database);
  return database;
}

describe('openDatabase', () => {
  it('migrates an in-memory database', async () => {
    const { client } = await open(':memory:');
    const tables = await client.execute("SELECT name FROM sqlite_master WHERE type = 'table'");
    const names = tables.rows.map((r) => String(r.name));
    for (const t of ['businesses', 'users', 'sessions', 'faqs', 'contacts', 'conversations', 'messages', 'booking_requests', 'audit_log']) {
      expect(names).toContain(t);
    }
  });

  it('uses WAL for file databases and survives concurrent writes', async () => {
    const dir = mkdtempSync(path.join(tmpdir(), 'reception-db-'));
    dirs.push(dir);
    const { db, client } = await open(`file:${path.join(dir, 'nested', 'test.db')}`);
    const mode = await client.execute('PRAGMA journal_mode');
    expect(String(mode.rows[0]?.journal_mode)).toBe('wal');
    const fk = await client.execute('PRAGMA foreign_keys');
    expect(Number(fk.rows[0]?.foreign_keys)).toBe(1);

    await Promise.all(Array.from({ length: 50 }, (_, i) => db.insert(auditLog).values({ actor: 'test', action: `write-${i}` })));
    const count = await client.execute('SELECT COUNT(*) AS n FROM audit_log');
    expect(Number(count.rows[0]?.n)).toBe(50);
  });
});
```

- [ ] **Step 3: Run to verify RED**

Run: `npx vitest run apps/server` → Expected: FAIL (modules not found).

- [ ] **Step 4: Implement `env.ts`**

```ts
import { z } from 'zod';

const EnvSchema = z.object({
  NODE_ENV: z.enum(['development', 'test', 'production']).default('development'),
  HOST: z.string().min(1).default('127.0.0.1'),
  PORT: z.coerce.number().int().min(0).max(65535).default(8787),
  DATABASE_URL: z.string().min(1).default('file:./data/reception.db'),
  APP_SECRET: z.string({ error: 'APP_SECRET is required' }).min(32, { error: 'APP_SECRET must be at least 32 characters' }),
  PUBLIC_ORIGIN: z
    .string()
    .default('http://127.0.0.1:8787')
    .transform((v) => v.replace(/\/+$/, ''))
    .refine((v) => /^https?:\/\/[^/\s]+$/.test(v), { error: 'PUBLIC_ORIGIN must be an origin like https://chat.example.com' }),
  TRUST_PROXY: z
    .enum(['true', 'false'])
    .default('false')
    .transform((v) => v === 'true'),
  SMTP_URL: z.string().min(1).optional(),
  MAIL_FROM: z.string().min(1).optional(),
});

export type Env = z.infer<typeof EnvSchema>;

export function loadEnv(source: Record<string, string | undefined> = process.env): Env {
  const result = EnvSchema.safeParse(source);
  if (!result.success) {
    const details = result.error.issues.map((i) => `${i.path.join('.') || 'env'}: ${i.message}`).join('; ');
    throw new Error(`Invalid environment: ${details}`);
  }
  return result.data;
}
```

> The `APP_SECRET` error message must contain `APP_SECRET` in both the missing and too-short cases (the issue path is `APP_SECRET`, so the joined message contains it either way).

- [ ] **Step 5: Implement `db/schema.ts`**

```ts
import type { BookingDraft, BookingFieldDef, BusinessProfile, Locale } from '@reception/core';
import { index, integer, sqliteTable, text } from 'drizzle-orm/sqlite-core';

export const AI_PROVIDERS = ['openai', 'anthropic', 'google', 'openai-compatible'] as const;
export type AiProvider = (typeof AI_PROVIDERS)[number];

export interface WidgetSettings {
  primaryColor: string;
  position: 'left' | 'right';
  greeting: Record<Locale, string>;
  allowedOrigins: string[];
}

export interface AiSettings {
  provider: AiProvider | null;
  model: string;
  baseUrl: string | null;
  encryptedKey: string | null;
  keyLast4: string | null;
  enabled: boolean;
}

export interface NotifySettings {
  ownerEmail: string | null;
  visitorReceipts: boolean;
}

export type MessageRole = 'visitor' | 'bot' | 'agent' | 'system';

const uuid = () => text('id').primaryKey().$defaultFn(() => crypto.randomUUID());
const createdAt = () => integer('created_at', { mode: 'timestamp_ms' }).notNull().$defaultFn(() => new Date());

export const businesses = sqliteTable('businesses', {
  id: uuid(),
  slug: text('slug').notNull().unique(),
  name: text('name').notNull(),
  timezone: text('timezone').notNull().default('UTC'),
  locale: text('locale', { enum: ['en', 'zh-CN'] }).notNull().default('en'),
  profile: text('profile', { mode: 'json' }).$type<BusinessProfile>().notNull(),
  bookingFields: text('booking_fields', { mode: 'json' }).$type<BookingFieldDef[]>().notNull(),
  widget: text('widget', { mode: 'json' }).$type<WidgetSettings>().notNull(),
  ai: text('ai', { mode: 'json' }).$type<AiSettings>().notNull(),
  notify: text('notify', { mode: 'json' }).$type<NotifySettings>().notNull(),
  createdAt: createdAt(),
  updatedAt: integer('updated_at', { mode: 'timestamp_ms' }).notNull().$defaultFn(() => new Date()),
});

export const users = sqliteTable('users', {
  id: uuid(),
  businessId: text('business_id').notNull().references(() => businesses.id, { onDelete: 'cascade' }),
  email: text('email').notNull().unique(),
  passwordHash: text('password_hash').notNull(),
  role: text('role', { enum: ['owner', 'agent'] }).notNull().default('owner'),
  createdAt: createdAt(),
});

export const sessions = sqliteTable(
  'sessions',
  {
    id: text('id').primaryKey(), // sha256(token)
    userId: text('user_id').notNull().references(() => users.id, { onDelete: 'cascade' }),
    expiresAt: integer('expires_at', { mode: 'timestamp_ms' }).notNull(),
    createdAt: createdAt(),
  },
  (t) => [index('sessions_user_idx').on(t.userId)],
);

export const faqs = sqliteTable(
  'faqs',
  {
    id: uuid(),
    businessId: text('business_id').notNull().references(() => businesses.id, { onDelete: 'cascade' }),
    question: text('question').notNull(),
    answer: text('answer').notNull(),
    enabled: integer('enabled', { mode: 'boolean' }).notNull().default(true),
    sort: integer('sort').notNull().default(0),
    createdAt: createdAt(),
    updatedAt: integer('updated_at', { mode: 'timestamp_ms' }).notNull().$defaultFn(() => new Date()),
  },
  (t) => [index('faqs_business_idx').on(t.businessId, t.sort)],
);

export const contacts = sqliteTable('contacts', {
  id: uuid(),
  businessId: text('business_id').notNull().references(() => businesses.id, { onDelete: 'cascade' }),
  name: text('name'),
  email: text('email'),
  phone: text('phone'),
  createdAt: createdAt(),
});

export const conversations = sqliteTable(
  'conversations',
  {
    id: uuid(),
    businessId: text('business_id').notNull().references(() => businesses.id, { onDelete: 'cascade' }),
    contactId: text('contact_id').references(() => contacts.id, { onDelete: 'set null' }),
    channel: text('channel', { enum: ['web', 'sms'] }).notNull().default('web'),
    visitorTokenHash: text('visitor_token_hash').notNull().unique(),
    status: text('status', { enum: ['bot', 'human', 'closed'] }).notNull().default('bot'),
    needsAttention: integer('needs_attention', { mode: 'boolean' }).notNull().default(false),
    attentionReason: text('attention_reason'),
    assignedUserId: text('assigned_user_id'),
    locale: text('locale', { enum: ['en', 'zh-CN'] }),
    bookingDraft: text('booking_draft', { mode: 'json' }).$type<BookingDraft>(),
    visitorMessageCount: integer('visitor_message_count').notNull().default(0),
    unreadCount: integer('unread_count').notNull().default(0),
    lastMessageAt: integer('last_message_at', { mode: 'timestamp_ms' }).notNull().$defaultFn(() => new Date()),
    createdAt: createdAt(),
  },
  (t) => [index('conversations_business_idx').on(t.businessId, t.lastMessageAt)],
);

export const messages = sqliteTable(
  'messages',
  {
    id: integer('id').primaryKey({ autoIncrement: true }),
    conversationId: text('conversation_id').notNull().references(() => conversations.id, { onDelete: 'cascade' }),
    businessId: text('business_id').notNull(),
    role: text('role', { enum: ['visitor', 'bot', 'agent', 'system'] }).notNull(),
    content: text('content').notNull(),
    meta: text('meta', { mode: 'json' }).$type<Record<string, unknown>>(),
    createdAt: createdAt(),
  },
  (t) => [index('messages_conversation_idx').on(t.conversationId, t.id)],
);

export const bookingRequests = sqliteTable(
  'booking_requests',
  {
    id: uuid(),
    businessId: text('business_id').notNull().references(() => businesses.id, { onDelete: 'cascade' }),
    conversationId: text('conversation_id').notNull().references(() => conversations.id, { onDelete: 'cascade' }),
    contactId: text('contact_id').references(() => contacts.id, { onDelete: 'set null' }),
    fields: text('fields', { mode: 'json' }).$type<Record<string, string>>().notNull(),
    status: text('status', { enum: ['pending', 'confirmed', 'declined', 'cancelled'] }).notNull().default('pending'),
    ownerNote: text('owner_note'),
    receiptSentAt: integer('receipt_sent_at', { mode: 'timestamp_ms' }),
    decidedAt: integer('decided_at', { mode: 'timestamp_ms' }),
    decidedBy: text('decided_by'),
    createdAt: createdAt(),
  },
  (t) => [index('bookings_business_idx').on(t.businessId, t.createdAt)],
);

export const auditLog = sqliteTable('audit_log', {
  id: integer('id').primaryKey({ autoIncrement: true }),
  businessId: text('business_id'),
  actor: text('actor').notNull(),
  action: text('action').notNull(),
  target: text('target'),
  data: text('data', { mode: 'json' }).$type<Record<string, unknown>>(),
  createdAt: createdAt(),
});
```

- [ ] **Step 6: Implement `db/client.ts` and generate the migration**

```ts
import { mkdirSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createClient, type Client } from '@libsql/client';
import { drizzle, type LibSQLDatabase } from 'drizzle-orm/libsql';
import { migrate } from 'drizzle-orm/libsql/migrator';
import * as schema from './schema';

export type Db = LibSQLDatabase<typeof schema>;

export interface Database {
  db: Db;
  client: Client;
  close(): void;
}

/** Resolved from this file so it works regardless of the process cwd. */
export const MIGRATIONS_DIR = fileURLToPath(new URL('../../drizzle', import.meta.url));

export async function openDatabase(url: string): Promise<Database> {
  if (url.startsWith('file:')) {
    mkdirSync(path.dirname(path.resolve(url.slice('file:'.length))), { recursive: true });
  }
  const client = createClient({ url, timeout: 5000 });
  if (url !== ':memory:') await client.execute('PRAGMA journal_mode=WAL');
  await client.execute('PRAGMA foreign_keys=ON');
  const db = drizzle(client, { schema });
  await migrate(db, { migrationsFolder: MIGRATIONS_DIR });
  return { db, client, close: () => client.close() };
}
```

Generate the initial migration from `apps/server`:
```bash
cd apps/server && npx drizzle-kit generate --name init && cd ../..
```
Expected: `apps/server/drizzle/0000_init.sql` and `apps/server/drizzle/meta/*` created. Commit them.

- [ ] **Step 7: Verify GREEN**

Run: `npx vitest run apps/server` → PASS. Run: `npm run typecheck` → exit 0. Run: `npx vitest run` → all core + server tests PASS.

- [ ] **Step 8: Commit**

```bash
git add package.json package-lock.json .gitignore apps/server
git commit -m "feat(server): scaffold server package with env, schema and SQLite client"
```

---

### Task 2: Library utilities (crypto, rate limiter, event bus, keyed mutex, connection registry)

**Files:**
- Create: `apps/server/src/lib/{crypto,rateLimit,bus,mutex,connections}.ts`
- Test: `apps/server/src/lib/{crypto,rateLimit,bus,mutex,connections}.test.ts`

**Interfaces:**
- Produces:
  - `hashPassword(pw): Promise<string>`, `verifyPassword(pw, stored): Promise<boolean>`, `randomToken(bytes?): string`, `sha256(v): string` (hex), `safeEqualHex(a, b): boolean`, `deriveKey(secret, purpose): Buffer`, `encryptSecret(plain, key): string`, `decryptSecret(payload, key): string`
  - `createRateLimiter(limit, windowMs): RateLimiter` with `hit(key, now?): boolean` (true = allowed) and `size(): number`
  - `createEventBus(): EventBus` with `subscribe(topic, listener): () => void`, `publish(topic, event)`, `listenerCount(topic?)`; `BusEvent { type: string; id?: string; data: unknown }`
  - `createKeyedMutex(): KeyedMutex` with `run<T>(key, fn: () => Promise<T>): Promise<T>` and `size(): number`
  - `createConnectionRegistry(maxPerKey): ConnectionRegistry` with `add(key, close: () => void): () => void` and `count(key?): number`

- [ ] **Step 1: Write failing tests**

`apps/server/src/lib/crypto.test.ts`:
```ts
import { describe, expect, it } from 'vitest';
import { decryptSecret, deriveKey, encryptSecret, hashPassword, randomToken, safeEqualHex, sha256, verifyPassword } from './crypto';

describe('passwords', () => {
  it('hashes with a random salt and verifies', async () => {
    const a = await hashPassword('correct horse');
    const b = await hashPassword('correct horse');
    expect(a).toMatch(/^scrypt\$/);
    expect(a).not.toBe(b);
    expect(await verifyPassword('correct horse', a)).toBe(true);
    expect(await verifyPassword('wrong', a)).toBe(false);
  });

  it('rejects malformed stored hashes', async () => {
    expect(await verifyPassword('x', 'plain')).toBe(false);
    expect(await verifyPassword('x', 'scrypt$only')).toBe(false);
  });
});

describe('tokens and hashes', () => {
  it('creates url-safe random tokens', () => {
    const t = randomToken();
    expect(t).toMatch(/^[A-Za-z0-9_-]{40,}$/);
    expect(randomToken()).not.toBe(t);
  });

  it('hashes with sha256 hex and compares safely', () => {
    expect(sha256('abc')).toBe('ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad');
    expect(safeEqualHex(sha256('a'), sha256('a'))).toBe(true);
    expect(safeEqualHex(sha256('a'), sha256('b'))).toBe(false);
    expect(safeEqualHex('ab', 'abcd')).toBe(false);
  });
});

describe('secret encryption', () => {
  const key = deriveKey('x'.repeat(32), 'ai-api-keys');

  it('derives a stable 32-byte key per purpose', () => {
    expect(key).toHaveLength(32);
    expect(deriveKey('x'.repeat(32), 'ai-api-keys').equals(key)).toBe(true);
    expect(deriveKey('x'.repeat(32), 'other').equals(key)).toBe(false);
  });

  it('round-trips and never contains the plaintext', () => {
    const payload = encryptSecret('sk-test-1234567890', key);
    expect(payload.startsWith('v1.')).toBe(true);
    expect(payload).not.toContain('sk-test');
    expect(decryptSecret(payload, key)).toBe('sk-test-1234567890');
  });

  it('fails with the wrong key or a tampered payload', () => {
    const payload = encryptSecret('secret', key);
    expect(() => decryptSecret(payload, deriveKey('y'.repeat(32), 'ai-api-keys'))).toThrow();
    const parts = payload.split('.');
    parts[3] = Buffer.from('tampered').toString('base64url');
    expect(() => decryptSecret(parts.join('.'), key)).toThrow();
    expect(() => decryptSecret('garbage', key)).toThrow();
  });
});
```

`apps/server/src/lib/rateLimit.test.ts`:
```ts
import { describe, expect, it } from 'vitest';
import { createRateLimiter } from './rateLimit';

describe('createRateLimiter', () => {
  it('allows up to the limit within the window, per key', () => {
    const limiter = createRateLimiter(3, 1000);
    expect([1, 2, 3, 4].map(() => limiter.hit('a', 0))).toEqual([true, true, true, false]);
    expect(limiter.hit('b', 0)).toBe(true);
  });

  it('frees capacity after the window slides', () => {
    const limiter = createRateLimiter(2, 1000);
    limiter.hit('a', 0);
    limiter.hit('a', 500);
    expect(limiter.hit('a', 900)).toBe(false);
    expect(limiter.hit('a', 1001)).toBe(true);
  });

  it('prunes idle keys when the map grows large', () => {
    const limiter = createRateLimiter(1, 10);
    for (let i = 0; i < 10_001; i++) limiter.hit(`k${i}`, 0);
    limiter.hit('fresh', 1000);
    expect(limiter.size()).toBe(1);
  });
});
```

`apps/server/src/lib/bus.test.ts`:
```ts
import { describe, expect, it, vi } from 'vitest';
import { createEventBus } from './bus';

describe('createEventBus', () => {
  it('delivers to subscribers of a topic only', () => {
    const bus = createEventBus();
    const a = vi.fn();
    const b = vi.fn();
    bus.subscribe('t1', a);
    bus.subscribe('t2', b);
    bus.publish('t1', { type: 'x', data: 1 });
    expect(a).toHaveBeenCalledWith({ type: 'x', data: 1 });
    expect(b).not.toHaveBeenCalled();
  });

  it('unsubscribe is idempotent and empties the topic map', () => {
    const bus = createEventBus();
    const off = bus.subscribe('t', () => {});
    bus.subscribe('t', () => {});
    expect(bus.listenerCount('t')).toBe(2);
    off();
    off();
    expect(bus.listenerCount('t')).toBe(1);
    expect(bus.listenerCount()).toBe(1);
  });

  it('isolates a throwing subscriber', () => {
    const bus = createEventBus();
    const good = vi.fn();
    bus.subscribe('t', () => {
      throw new Error('boom');
    });
    bus.subscribe('t', good);
    expect(() => bus.publish('t', { type: 'x', data: null })).not.toThrow();
    expect(good).toHaveBeenCalledOnce();
  });
});
```

`apps/server/src/lib/mutex.test.ts`:
```ts
import { describe, expect, it } from 'vitest';
import { createKeyedMutex } from './mutex';

const delay = (ms: number) => new Promise((r) => setTimeout(r, ms));

describe('createKeyedMutex', () => {
  it('serializes work for the same key', async () => {
    const mutex = createKeyedMutex();
    const log: string[] = [];
    await Promise.all([
      mutex.run('a', async () => {
        log.push('1-start');
        await delay(20);
        log.push('1-end');
      }),
      mutex.run('a', async () => {
        log.push('2-start');
        log.push('2-end');
      }),
    ]);
    expect(log).toEqual(['1-start', '1-end', '2-start', '2-end']);
  });

  it('runs different keys concurrently', async () => {
    const mutex = createKeyedMutex();
    const log: string[] = [];
    await Promise.all([
      mutex.run('a', async () => {
        log.push('a-start');
        await delay(20);
        log.push('a-end');
      }),
      mutex.run('b', async () => {
        log.push('b');
      }),
    ]);
    expect(log.indexOf('b')).toBeLessThan(log.indexOf('a-end'));
  });

  it('releases after a failure and cleans up keys', async () => {
    const mutex = createKeyedMutex();
    await expect(mutex.run('a', async () => Promise.reject(new Error('x')))).rejects.toThrow('x');
    await expect(mutex.run('a', async () => 42)).resolves.toBe(42);
    expect(mutex.size()).toBe(0);
  });
});
```

`apps/server/src/lib/connections.test.ts`:
```ts
import { describe, expect, it, vi } from 'vitest';
import { createConnectionRegistry } from './connections';

describe('createConnectionRegistry', () => {
  it('closes the oldest connection beyond the cap', () => {
    const reg = createConnectionRegistry(2);
    const c1 = vi.fn();
    const c2 = vi.fn();
    const c3 = vi.fn();
    reg.add('k', c1);
    reg.add('k', c2);
    reg.add('k', c3);
    expect(c1).toHaveBeenCalledOnce();
    expect(c2).not.toHaveBeenCalled();
    expect(reg.count('k')).toBe(2);
  });

  it('removes entries on release, idempotently, and per key', () => {
    const reg = createConnectionRegistry(3);
    const off = reg.add('a', () => {});
    reg.add('b', () => {});
    off();
    off();
    expect(reg.count('a')).toBe(0);
    expect(reg.count()).toBe(1);
  });
});
```

- [ ] **Step 2: Run to verify RED** — `npx vitest run apps/server/src/lib` → FAIL (modules not found).

- [ ] **Step 3: Implement**

`apps/server/src/lib/crypto.ts`:
```ts
import {
  createCipheriv,
  createDecipheriv,
  createHash,
  hkdfSync,
  randomBytes,
  scrypt,
  timingSafeEqual,
} from 'node:crypto';

const SCRYPT_PARAMS = { N: 16384, r: 8, p: 1, maxmem: 64 * 1024 * 1024 };
const KEY_LENGTH = 64;

function scryptAsync(password: string, salt: Buffer): Promise<Buffer> {
  return new Promise((resolve, reject) => {
    scrypt(password, salt, KEY_LENGTH, SCRYPT_PARAMS, (err, key) => (err ? reject(err) : resolve(key)));
  });
}

export async function hashPassword(password: string): Promise<string> {
  const salt = randomBytes(16);
  const key = await scryptAsync(password, salt);
  return `scrypt$${salt.toString('base64url')}$${key.toString('base64url')}`;
}

export async function verifyPassword(password: string, stored: string): Promise<boolean> {
  const [scheme, salt, key] = stored.split('$');
  if (scheme !== 'scrypt' || !salt || !key) return false;
  const expected = Buffer.from(key, 'base64url');
  const actual = await scryptAsync(password, Buffer.from(salt, 'base64url'));
  return expected.length === actual.length && timingSafeEqual(expected, actual);
}

export function randomToken(bytes = 32): string {
  return randomBytes(bytes).toString('base64url');
}

export function sha256(value: string): string {
  return createHash('sha256').update(value).digest('hex');
}

export function safeEqualHex(a: string, b: string): boolean {
  const left = Buffer.from(a, 'hex');
  const right = Buffer.from(b, 'hex');
  return left.length === right.length && left.length > 0 && timingSafeEqual(left, right);
}

export function deriveKey(secret: string, purpose: string): Buffer {
  return Buffer.from(hkdfSync('sha256', secret, 'reception-assistant', purpose, 32));
}

export function encryptSecret(plain: string, key: Buffer): string {
  const iv = randomBytes(12);
  const cipher = createCipheriv('aes-256-gcm', key, iv);
  const ciphertext = Buffer.concat([cipher.update(plain, 'utf8'), cipher.final()]);
  return ['v1', iv.toString('base64url'), cipher.getAuthTag().toString('base64url'), ciphertext.toString('base64url')].join('.');
}

export function decryptSecret(payload: string, key: Buffer): string {
  const [version, iv, tag, ciphertext] = payload.split('.');
  if (version !== 'v1' || !iv || !tag || !ciphertext) throw new Error('Unsupported secret format');
  const decipher = createDecipheriv('aes-256-gcm', key, Buffer.from(iv, 'base64url'));
  decipher.setAuthTag(Buffer.from(tag, 'base64url'));
  return Buffer.concat([decipher.update(Buffer.from(ciphertext, 'base64url')), decipher.final()]).toString('utf8');
}
```

`apps/server/src/lib/rateLimit.ts`:
```ts
export interface RateLimiter {
  /** Records a hit and returns true when it is within the limit. */
  hit(key: string, now?: number): boolean;
  size(): number;
}

const PRUNE_THRESHOLD = 10_000;

export function createRateLimiter(limit: number, windowMs: number): RateLimiter {
  const hits = new Map<string, number[]>();
  return {
    hit(key, now = Date.now()) {
      const cutoff = now - windowMs;
      if (hits.size > PRUNE_THRESHOLD) {
        for (const [k, times] of hits) if (!times.some((t) => t > cutoff)) hits.delete(k);
      }
      const recent = (hits.get(key) ?? []).filter((t) => t > cutoff);
      if (recent.length >= limit) {
        hits.set(key, recent);
        return false;
      }
      recent.push(now);
      hits.set(key, recent);
      return true;
    },
    size: () => hits.size,
  };
}
```

`apps/server/src/lib/bus.ts`:
```ts
export interface BusEvent {
  type: string;
  /** Numeric message id as a string, used for SSE replay; omitted for non-message events. */
  id?: string;
  data: unknown;
}

export type Listener = (event: BusEvent) => void;

export interface EventBus {
  subscribe(topic: string, listener: Listener): () => void;
  publish(topic: string, event: BusEvent): void;
  listenerCount(topic?: string): number;
}

export function createEventBus(): EventBus {
  const topics = new Map<string, Set<Listener>>();
  return {
    subscribe(topic, listener) {
      let set = topics.get(topic);
      if (!set) {
        set = new Set();
        topics.set(topic, set);
      }
      set.add(listener);
      let active = true;
      return () => {
        if (!active) return;
        active = false;
        const current = topics.get(topic);
        if (!current) return;
        current.delete(listener);
        if (current.size === 0) topics.delete(topic);
      };
    },
    publish(topic, event) {
      const set = topics.get(topic);
      if (!set) return;
      for (const listener of [...set]) {
        try {
          listener(event);
        } catch (error) {
          // One broken subscriber (e.g. a closed stream) must not break the publisher or other subscribers.
          console.error('event listener failed', error);
        }
      }
    },
    listenerCount(topic) {
      if (topic !== undefined) return topics.get(topic)?.size ?? 0;
      let total = 0;
      for (const set of topics.values()) total += set.size;
      return total;
    },
  };
}
```

> The "isolates a throwing subscriber" test will print one `console.error` line; that is expected output for that test only. If you prefer pristine output, spy on `console.error` with `vi.spyOn(console, 'error').mockImplementation(() => {})` inside that test and assert it was called once.

`apps/server/src/lib/mutex.ts`:
```ts
export interface KeyedMutex {
  run<T>(key: string, fn: () => Promise<T>): Promise<T>;
  size(): number;
}

export function createKeyedMutex(): KeyedMutex {
  const tails = new Map<string, Promise<void>>();
  return {
    async run(key, fn) {
      const previous = tails.get(key) ?? Promise.resolve();
      let release!: () => void;
      const current = new Promise<void>((resolve) => {
        release = resolve;
      });
      const tail = previous.then(() => current);
      tails.set(key, tail);
      await previous;
      try {
        return await fn();
      } finally {
        release();
        if (tails.get(key) === tail) tails.delete(key);
      }
    },
    size: () => tails.size,
  };
}
```

`apps/server/src/lib/connections.ts`:
```ts
export interface ConnectionRegistry {
  /** Registers a connection; closes the oldest ones beyond the cap. Returns an idempotent release function. */
  add(key: string, close: () => void): () => void;
  count(key?: string): number;
}

export function createConnectionRegistry(maxPerKey: number): ConnectionRegistry {
  const byKey = new Map<string, Array<{ close: () => void }>>();
  return {
    add(key, close) {
      const list = byKey.get(key) ?? [];
      const entry = { close };
      list.push(entry);
      byKey.set(key, list);
      while (list.length > maxPerKey) list.shift()?.close();
      let active = true;
      return () => {
        if (!active) return;
        active = false;
        const current = byKey.get(key);
        if (!current) return;
        const index = current.indexOf(entry);
        if (index >= 0) current.splice(index, 1);
        if (current.length === 0) byKey.delete(key);
      };
    },
    count(key) {
      if (key !== undefined) return byKey.get(key)?.length ?? 0;
      let total = 0;
      for (const list of byKey.values()) total += list.length;
      return total;
    },
  };
}
```

- [ ] **Step 4: Verify GREEN** — `npx vitest run apps/server` PASS; `npm run typecheck` exit 0.

- [ ] **Step 5: Commit**

```bash
git add apps/server/src/lib
git commit -m "feat(server): add crypto, rate limiter, event bus, mutex and connection registry"
```

---

### Task 3: Validation, business/FAQ repository, test fixtures

**Files:**
- Create: `apps/server/src/domain/validation.ts`, `apps/server/src/repos/business.ts`, `apps/server/src/testing/fixtures.ts`
- Test: `apps/server/src/repos/business.test.ts`

**Interfaces:**
- Consumes: `Db`, tables; core `DEFAULT_BOOKING_FIELDS`, `KNOWLEDGE_LIMITS`, `MAX_NAME_CHARS`, `validateKnowledge`, `demoKnowledge`, types.
- Produces:
  - `ValidationError { issues: {path, message}[] }`, `parseOrThrow(schema, value)`, schemas `profileSchema`, `bookingFieldsSchema`, `widgetSchema`, `notifySchema`, `faqInputSchema`, `businessPatchSchema`, `localeSchema`; `assertKnowledgeBudget(name, profile, faqs)`
  - `BusinessRow`, `FaqRow`, `CreateBusinessInput`, `emptyProfile()`, `defaultWidget()`, `defaultAi()`, `defaultNotify()`, `createBusiness(db, input)`, `getBusinessById(db, id)`, `getBusinessBySlug(db, slug)`, `listFaqs(db, businessId)`, `toFaq(row)`, `loadKnowledge(db, business)`, `updateBusiness(db, businessId, patch, now)`, `createFaq(db, businessId, input)`, `updateFaq(db, businessId, faqId, patch, now)`, `deleteFaq(db, businessId, faqId)`
  - `testDatabase()`, `seedDemoBusiness(db, overrides?)`

- [ ] **Step 1: Write failing tests** — `apps/server/src/repos/business.test.ts`:
```ts
import { demoKnowledge } from '@reception/core';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import type { Database } from '../db/client';
import { ValidationError } from '../domain/validation';
import { seedDemoBusiness, testDatabase } from '../testing/fixtures';
import {
  createBusiness,
  createFaq,
  deleteFaq,
  getBusinessBySlug,
  listFaqs,
  loadKnowledge,
  updateBusiness,
  updateFaq,
  type BusinessRow,
} from './business';

let database: Database;
let business: BusinessRow;
const now = new Date('2026-10-11T10:00:00Z');

beforeEach(async () => {
  database = await testDatabase();
  business = await seedDemoBusiness(database.db);
});
afterEach(() => database.close());

async function expectValidation(promise: Promise<unknown>, path: string) {
  const error = await promise.then(
    () => null,
    (e: unknown) => e,
  );
  expect(error).toBeInstanceOf(ValidationError);
  expect((error as ValidationError).issues.map((i) => i.path)).toContain(path);
}

describe('business repository', () => {
  it('seeds the demo business and loads its knowledge', async () => {
    const k = await loadKnowledge(database.db, business);
    const demo = demoKnowledge();
    expect(k.name).toBe(demo.name);
    expect(k.profile).toEqual(demo.profile);
    expect(k.faqs.map((f) => f.question)).toEqual(demo.faqs.map((f) => f.question));
    expect(k.bookingFields.map((f) => f.key)).toEqual(['service', 'preferred_time', 'name', 'contact', 'notes']);
    expect((await getBusinessBySlug(database.db, 'harbor-home'))?.id).toBe(business.id);
    expect(await getBusinessBySlug(database.db, 'nope')).toBeNull();
  });

  it('only loads enabled FAQs into knowledge, in sort order', async () => {
    const [first] = await listFaqs(database.db, business.id);
    await updateFaq(database.db, business.id, first!.id, { enabled: false }, now);
    const k = await loadKnowledge(database.db, business);
    expect(k.faqs).toHaveLength(4);
    expect(k.faqs.some((f) => f.id === first!.id)).toBe(false);
  });

  it('rejects invalid slugs and duplicate slugs', async () => {
    await expectValidation(createBusiness(database.db, { slug: 'Bad Slug', name: 'X' }), 'slug');
    await expect(createBusiness(database.db, { slug: 'harbor-home', name: 'X' })).rejects.toThrow();
  });

  it('validates business patches', async () => {
    await expectValidation(updateBusiness(database.db, business.id, { name: 'n'.repeat(101) }, now), 'name');
    await expectValidation(
      updateBusiness(database.db, business.id, { widget: { primaryColor: 'red', position: 'right', greeting: { en: '', 'zh-CN': '' }, allowedOrigins: [] } }, now),
      'widget.primaryColor',
    );
    await expectValidation(
      updateBusiness(database.db, business.id, { widget: { primaryColor: '#112233', position: 'right', greeting: { en: '', 'zh-CN': '' }, allowedOrigins: ['https://x.example/path'] } }, now),
      'widget.allowedOrigins.0',
    );
    await expectValidation(
      updateBusiness(database.db, business.id, { bookingFields: [{ key: 'Bad Key', label: { en: 'a', 'zh-CN': 'a' }, kind: 'text', required: true }] }, now),
      'bookingFields.0.key',
    );
    await expectValidation(updateBusiness(database.db, business.id, { unknown: 1 }, now), '');
  });

  it('trims service names and saves valid patches', async () => {
    const k = demoKnowledge();
    const updated = await updateBusiness(
      database.db,
      business.id,
      { name: 'Harbor Home', profile: { ...k.profile, services: [{ name: '  Window cleaning ', description: 'Windows', pricing: '' }] } },
      now,
    );
    expect(updated?.name).toBe('Harbor Home');
    expect(updated?.profile.services[0]?.name).toBe('Window cleaning');
    expect(updated?.updatedAt.getTime()).toBe(now.getTime());
  });

  it('enforces FAQ limits and the shared knowledge budget', async () => {
    await expectValidation(createFaq(database.db, business.id, { question: 'q'.repeat(201), answer: 'a' }), 'question');
    await expectValidation(createFaq(database.db, business.id, { question: 'q', answer: 'a'.repeat(501) }), 'answer');
    for (let i = 0; i < 21; i++) {
      await createFaq(database.db, business.id, { question: `Question ${i}?`, answer: 'a'.repeat(470) });
    }
    // Budget ≈ 122 reserve + ~680 profile + ~580 demo FAQs + 21 × (12 + 470 + 8) ≈ 11.7k ≤ 12000;
    // one more ~517-char entry pushes it over. (26 enabled FAQs, under the 30 limit.)
    await expectValidation(createFaq(database.db, business.id, { question: 'One more?', answer: 'a'.repeat(500) }), 'knowledge');
  });

  it('limits enabled FAQs to 30', async () => {
    for (let i = 0; i < 25; i++) await createFaq(database.db, business.id, { question: `Q${i}?`, answer: 'Short.' });
    await expectValidation(createFaq(database.db, business.id, { question: 'Q31?', answer: 'Short.' }), 'faqs');
    await expect(createFaq(database.db, business.id, { question: 'Disabled?', answer: 'Short.', enabled: false })).resolves.toBeTruthy();
  });

  it('scopes FAQ updates and deletes by business', async () => {
    const other = await createBusiness(database.db, { slug: 'other-biz', name: 'Other' });
    const [faq] = await listFaqs(database.db, business.id);
    expect(await updateFaq(database.db, other.id, faq!.id, { answer: 'hacked' }, now)).toBeNull();
    expect(await deleteFaq(database.db, other.id, faq!.id)).toBe(false);
    expect(await deleteFaq(database.db, business.id, faq!.id)).toBe(true);
    expect(await listFaqs(database.db, business.id)).toHaveLength(4);
  });
});
```

- [ ] **Step 2: Run to verify RED** — `npx vitest run apps/server/src/repos` → FAIL.

- [ ] **Step 3: Implement `domain/validation.ts`**

```ts
import { KNOWLEDGE_LIMITS, MAX_NAME_CHARS, validateKnowledge, type BusinessProfile, type Faq } from '@reception/core';
import { z } from 'zod';

export interface ValidationIssue {
  path: string;
  message: string;
}

export class ValidationError extends Error {
  constructor(public readonly issues: ValidationIssue[]) {
    super('Validation failed');
    this.name = 'ValidationError';
  }
}

export function parseOrThrow<S extends z.ZodType>(schema: S, value: unknown): z.output<S> {
  const result = schema.safeParse(value);
  if (!result.success) {
    throw new ValidationError(result.error.issues.map((i) => ({ path: i.path.join('.'), message: i.message })));
  }
  return result.data;
}

const text = (max: number) => z.string().trim().max(max);

export const localeSchema = z.enum(['en', 'zh-CN']);

export const serviceSchema = z.object({
  name: z.string().trim().min(1).max(100),
  description: text(300),
  pricing: text(200),
});

export const profileSchema = z.object({
  about: text(1000),
  hours: text(300),
  serviceAreas: z.array(z.string().trim().min(1).max(60)).max(30),
  services: z.array(serviceSchema).max(20),
  priceNote: text(300),
  contactEmail: z.union([z.literal(''), z.email()]),
  contactPhone: text(40),
  address: text(200),
});

export const bookingFieldSchema = z.object({
  key: z.string().regex(/^[a-z_]{1,32}$/, { error: 'Use 1-32 lowercase letters or underscores' }),
  label: z.object({ en: z.string().trim().min(1).max(60), 'zh-CN': z.string().trim().min(1).max(60) }),
  kind: z.enum(['text', 'contact', 'service']),
  required: z.boolean(),
});

export const bookingFieldsSchema = z
  .array(bookingFieldSchema)
  .min(1)
  .max(10)
  .refine((fields) => new Set(fields.map((f) => f.key)).size === fields.length, { error: 'Booking field keys must be unique' })
  .refine((fields) => fields.some((f) => f.required), { error: 'At least one booking field must be required' });

const originSchema = z
  .string()
  .trim()
  .refine((v) => /^https?:\/\/[^/\s?#]+$/.test(v), { error: 'Use an origin like https://example.com (no path or trailing slash)' });

export const widgetSchema = z.object({
  primaryColor: z.string().regex(/^#[0-9a-fA-F]{6}$/, { error: 'Use a hex color like #2563eb' }),
  position: z.enum(['left', 'right']),
  greeting: z.object({ en: text(300), 'zh-CN': text(300) }),
  allowedOrigins: z.array(originSchema).max(10),
});

export const notifySchema = z.object({
  ownerEmail: z.union([z.null(), z.email()]),
  visitorReceipts: z.boolean(),
});

export const faqInputSchema = z
  .object({
    question: z.string().trim().min(1).max(KNOWLEDGE_LIMITS.faqQuestionChars),
    answer: z.string().trim().min(1).max(KNOWLEDGE_LIMITS.faqAnswerChars),
    enabled: z.boolean().default(true),
    sort: z.number().int().min(0).max(1000).default(0),
  })
  .strict();

export const faqPatchSchema = z
  .object({
    question: z.string().trim().min(1).max(KNOWLEDGE_LIMITS.faqQuestionChars),
    answer: z.string().trim().min(1).max(KNOWLEDGE_LIMITS.faqAnswerChars),
    enabled: z.boolean(),
    sort: z.number().int().min(0).max(1000),
  })
  .partial()
  .strict();

export const businessPatchSchema = z
  .object({
    name: z.string().trim().min(1).max(MAX_NAME_CHARS),
    locale: localeSchema,
    timezone: z.string().trim().min(1).max(60),
    profile: profileSchema,
    widget: widgetSchema,
    notify: notifySchema,
    bookingFields: bookingFieldsSchema,
  })
  .partial()
  .strict();

/** Same budget the core prompt builder uses, so saved knowledge is never truncated. */
export function assertKnowledgeBudget(name: string, profile: BusinessProfile, faqs: Faq[]): void {
  const issues = validateKnowledge(profile, faqs, name);
  if (issues.length === 0) return;
  throw new ValidationError(
    issues.map((i) => ({
      path: i.path,
      message: i.code === 'too_many' ? `At most ${i.limit} enabled FAQs are allowed` : `Too long: ${i.actual} of ${i.limit} characters`,
    })),
  );
}
```

> `validateKnowledge` returns paths like `name`, `profile`, `faqs`, `faqs.3.answer`, `knowledge` — the tests rely on those.

- [ ] **Step 4: Implement `repos/business.ts`**

```ts
import { DEFAULT_BOOKING_FIELDS, type BusinessKnowledge, type BusinessProfile, type Faq, type Locale } from '@reception/core';
import { and, asc, eq } from 'drizzle-orm';
import type { Db } from '../db/client';
import { businesses, faqs, type AiSettings, type NotifySettings, type WidgetSettings } from '../db/schema';
import {
  assertKnowledgeBudget,
  businessPatchSchema,
  faqInputSchema,
  faqPatchSchema,
  parseOrThrow,
  profileSchema,
  ValidationError,
} from '../domain/validation';

export type BusinessRow = typeof businesses.$inferSelect;
export type FaqRow = typeof faqs.$inferSelect;

export const emptyProfile = (): BusinessProfile => ({
  about: '',
  hours: '',
  serviceAreas: [],
  services: [],
  priceNote: '',
  contactEmail: '',
  contactPhone: '',
  address: '',
});

export const defaultWidget = (): WidgetSettings => ({
  primaryColor: '#2563eb',
  position: 'right',
  greeting: { en: 'Hi! How can we help you today?', 'zh-CN': '您好！有什么可以帮您？' },
  allowedOrigins: [],
});

export const defaultAi = (): AiSettings => ({
  provider: null,
  model: '',
  baseUrl: null,
  encryptedKey: null,
  keyLast4: null,
  enabled: false,
});

export const defaultNotify = (): NotifySettings => ({ ownerEmail: null, visitorReceipts: true });

export interface CreateBusinessInput {
  slug: string;
  name: string;
  locale?: Locale;
  profile?: BusinessProfile;
  faqs?: { question: string; answer: string; enabled: boolean }[];
  widget?: Partial<WidgetSettings>;
  notify?: Partial<NotifySettings>;
}

export function toFaq(row: FaqRow): Faq {
  return { id: row.id, question: row.question, answer: row.answer, enabled: row.enabled };
}

export async function createBusiness(db: Db, input: CreateBusinessInput): Promise<BusinessRow> {
  if (!/^[a-z0-9-]{3,40}$/.test(input.slug)) {
    throw new ValidationError([{ path: 'slug', message: 'Use 3-40 lowercase letters, digits or hyphens' }]);
  }
  const profile = parseOrThrow(profileSchema, input.profile ?? emptyProfile());
  const faqInputs = (input.faqs ?? []).map((f) => parseOrThrow(faqInputSchema, f));
  assertKnowledgeBudget(
    input.name,
    profile,
    faqInputs.map((f, i) => ({ id: String(i), question: f.question, answer: f.answer, enabled: f.enabled })),
  );
  const id = crypto.randomUUID();
  const businessInsert = db
    .insert(businesses)
    .values({
      id,
      slug: input.slug,
      name: input.name.trim(),
      locale: input.locale ?? 'en',
      profile,
      bookingFields: DEFAULT_BOOKING_FIELDS.map((f) => ({ ...f, label: { ...f.label } })),
      widget: { ...defaultWidget(), ...input.widget },
      ai: defaultAi(),
      notify: { ...defaultNotify(), ...input.notify },
    })
    .returning();
  if (faqInputs.length === 0) {
    const [row] = await businessInsert;
    return row!;
  }
  const faqInsert = db
    .insert(faqs)
    .values(faqInputs.map((f, i) => ({ businessId: id, question: f.question, answer: f.answer, enabled: f.enabled, sort: i })));
  const [rows] = await db.batch([businessInsert, faqInsert]);
  return rows[0]!;
}

export async function getBusinessById(db: Db, id: string): Promise<BusinessRow | null> {
  const [row] = await db.select().from(businesses).where(eq(businesses.id, id)).limit(1);
  return row ?? null;
}

export async function getBusinessBySlug(db: Db, slug: string): Promise<BusinessRow | null> {
  const [row] = await db.select().from(businesses).where(eq(businesses.slug, slug)).limit(1);
  return row ?? null;
}

export async function listFaqs(db: Db, businessId: string): Promise<FaqRow[]> {
  return db.select().from(faqs).where(eq(faqs.businessId, businessId)).orderBy(asc(faqs.sort), asc(faqs.createdAt));
}

export async function loadKnowledge(db: Db, business: BusinessRow): Promise<BusinessKnowledge> {
  const rows = await listFaqs(db, business.id);
  return {
    name: business.name,
    profile: business.profile,
    faqs: rows.filter((r) => r.enabled).map(toFaq),
    bookingFields: business.bookingFields,
  };
}

export async function updateBusiness(db: Db, businessId: string, patch: unknown, now: Date): Promise<BusinessRow | null> {
  const parsed = parseOrThrow(businessPatchSchema, patch);
  const current = await getBusinessById(db, businessId);
  if (!current) return null;
  const faqRows = await listFaqs(db, businessId);
  assertKnowledgeBudget(parsed.name ?? current.name, parsed.profile ?? current.profile, faqRows.map(toFaq));
  const [row] = await db
    .update(businesses)
    .set({ ...parsed, updatedAt: now })
    .where(eq(businesses.id, businessId))
    .returning();
  return row ?? null;
}

export async function createFaq(db: Db, businessId: string, input: unknown): Promise<FaqRow> {
  const parsed = parseOrThrow(faqInputSchema, input);
  const business = await getBusinessById(db, businessId);
  if (!business) throw new ValidationError([{ path: 'businessId', message: 'Unknown business' }]);
  const existing = (await listFaqs(db, businessId)).map(toFaq);
  assertKnowledgeBudget(business.name, business.profile, [
    ...existing,
    { id: 'new', question: parsed.question, answer: parsed.answer, enabled: parsed.enabled },
  ]);
  const [row] = await db.insert(faqs).values({ businessId, ...parsed }).returning();
  return row!;
}

export async function updateFaq(db: Db, businessId: string, faqId: string, patch: unknown, now: Date): Promise<FaqRow | null> {
  const parsed = parseOrThrow(faqPatchSchema, patch);
  const business = await getBusinessById(db, businessId);
  if (!business) return null;
  const rows = await listFaqs(db, businessId);
  const target = rows.find((r) => r.id === faqId);
  if (!target) return null;
  const merged = rows.map((r) => (r.id === faqId ? toFaq({ ...r, ...parsed }) : toFaq(r)));
  assertKnowledgeBudget(business.name, business.profile, merged);
  const [row] = await db
    .update(faqs)
    .set({ ...parsed, updatedAt: now })
    .where(and(eq(faqs.id, faqId), eq(faqs.businessId, businessId)))
    .returning();
  return row ?? null;
}

export async function deleteFaq(db: Db, businessId: string, faqId: string): Promise<boolean> {
  const rows = await db.delete(faqs).where(and(eq(faqs.id, faqId), eq(faqs.businessId, businessId))).returning({ id: faqs.id });
  return rows.length > 0;
}
```

> `updateBusiness` with `{ unknown: 1 }` fails `.strict()` with an `unrecognized_keys` issue whose path is `[]` → `''` (the test expects `''`).

- [ ] **Step 5: Implement `testing/fixtures.ts`**

```ts
import { demoKnowledge } from '@reception/core';
import { openDatabase, type Database, type Db } from '../db/client';
import { createBusiness, type BusinessRow, type CreateBusinessInput } from '../repos/business';

export function testDatabase(): Promise<Database> {
  return openDatabase(':memory:');
}

export function seedDemoBusiness(db: Db, overrides: Partial<CreateBusinessInput> = {}): Promise<BusinessRow> {
  const k = demoKnowledge();
  return createBusiness(db, {
    slug: 'harbor-home',
    name: k.name,
    profile: k.profile,
    faqs: k.faqs.map(({ question, answer, enabled }) => ({ question, answer, enabled })),
    widget: { allowedOrigins: ['https://harbor.example'] },
    notify: { ownerEmail: 'owner@harbor.example', visitorReceipts: true },
    ...overrides,
  });
}
```

- [ ] **Step 6: Verify GREEN** — `npx vitest run apps/server` PASS; `npm run typecheck` exit 0. If the FAQ budget test's arithmetic is off by a few FAQs (budget includes `HEADER_RESERVE` and per-entry overhead), adjust the loop count so the 20 inserts succeed and the next 500-char FAQ fails — do not change the limits.

- [ ] **Step 7: Commit**

```bash
git add apps/server/src/domain apps/server/src/repos apps/server/src/testing
git commit -m "feat(server): add validation and business/FAQ repository"
```

---

### Task 4: Conversation, message, booking, contact and audit repositories

**Files:**
- Create: `apps/server/src/repos/conversations.ts`, `apps/server/src/repos/bookings.ts`, `apps/server/src/repos/audit.ts`, `apps/server/src/domain/contact.ts`
- Test: `apps/server/src/repos/conversations.test.ts`, `apps/server/src/domain/contact.test.ts`

**Interfaces:**
- Consumes: tables, `Db`, `sha256`, `safeEqualHex`, fixtures, core `contactValue`, `ChatMessage`, `BookingFieldDef`.
- Produces:
  - `ConversationRow`, `MessageRow`, `ContactRow`, `VISITOR_VISIBLE_ROLES` (`['visitor','bot','agent']`)
  - `insertConversation(db, { businessId, visitorTokenHash, locale, now }): Promise<ConversationRow>`
  - `getConversation(db, id)`, `getConversationForBusiness(db, businessId, id)`, `verifyVisitorToken(conversation, token): boolean`
  - `listConversations(db, businessId, { filter?: 'all'|'attention'|'open'; limit? }): Promise<ConversationListItem[]>` where `ConversationListItem = { conversation, lastMessage: MessageRow | null, contact: ContactRow | null }`
  - `listMessages(db, conversationId, { afterId?, limit?, roles? }): Promise<MessageRow[]>` (ascending id)
  - `lastMessageId(db, conversationId): Promise<number>` (0 when none)
  - `recentHistory(db, conversationId, limit = 40): Promise<ChatMessage[]>` (oldest first, visitor/bot/agent only)
  - `getContact(db, id)`
  - `BookingRow`, `listBookings(db, businessId, { status?, limit? })`, `getBookingForBusiness(db, businessId, id)`, `listBookingsForConversation(db, conversationId)`, `decideBooking(db, { businessId, bookingId, decision: 'confirmed'|'declined', note, userId, now }): Promise<BookingRow | null>` (only from `pending`)
  - `AuditEntry`, `auditInsert(db, entry)` (insert builder, usable in `db.batch`), `writeAudit(db, entry): Promise<void>`
  - `contactFromFields(defs, fields): { name: string | null; email: string | null; phone: string | null }`

- [ ] **Step 1: Write failing tests**

`apps/server/src/domain/contact.test.ts`:
```ts
import { DEFAULT_BOOKING_FIELDS } from '@reception/core';
import { describe, expect, it } from 'vitest';
import { contactFromFields } from './contact';

describe('contactFromFields', () => {
  it('splits email and phone contacts', () => {
    expect(contactFromFields(DEFAULT_BOOKING_FIELDS, { name: 'Alex', contact: 'alex@example.com' })).toEqual({
      name: 'Alex',
      email: 'alex@example.com',
      phone: null,
    });
    expect(contactFromFields(DEFAULT_BOOKING_FIELDS, { name: '小李', contact: '13800138000' })).toEqual({
      name: '小李',
      email: null,
      phone: '13800138000',
    });
  });

  it('returns nulls when fields are missing', () => {
    expect(contactFromFields(DEFAULT_BOOKING_FIELDS, {})).toEqual({ name: null, email: null, phone: null });
  });
});
```

`apps/server/src/repos/conversations.test.ts`:
```ts
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import type { Database } from '../db/client';
import { bookingRequests, messages, conversations, contacts } from '../db/schema';
import { randomToken, sha256 } from '../lib/crypto';
import { seedDemoBusiness, testDatabase } from '../testing/fixtures';
import { createBusiness, type BusinessRow } from './business';
import { decideBooking, getBookingForBusiness, listBookings } from './bookings';
import {
  getConversationForBusiness,
  insertConversation,
  lastMessageId,
  listConversations,
  listMessages,
  recentHistory,
  verifyVisitorToken,
} from './conversations';
import { eq } from 'drizzle-orm';

let database: Database;
let business: BusinessRow;
const t0 = new Date('2026-10-11T10:00:00Z');

beforeEach(async () => {
  database = await testDatabase();
  business = await seedDemoBusiness(database.db);
});
afterEach(() => database.close());

async function newConversation(at = t0) {
  const token = randomToken();
  const conversation = await insertConversation(database.db, { businessId: business.id, visitorTokenHash: sha256(token), locale: null, now: at });
  return { conversation, token };
}

async function addMessage(conversationId: string, role: 'visitor' | 'bot' | 'agent' | 'system', content: string) {
  await database.db.insert(messages).values({ conversationId, businessId: business.id, role, content });
}

describe('conversations', () => {
  it('verifies visitor tokens by hash', async () => {
    const { conversation, token } = await newConversation();
    expect(verifyVisitorToken(conversation, token)).toBe(true);
    expect(verifyVisitorToken(conversation, randomToken())).toBe(false);
    expect(verifyVisitorToken(conversation, '')).toBe(false);
  });

  it('scopes lookups by business', async () => {
    const { conversation } = await newConversation();
    const other = await createBusiness(database.db, { slug: 'other-biz', name: 'Other' });
    expect(await getConversationForBusiness(database.db, other.id, conversation.id)).toBeNull();
    expect((await getConversationForBusiness(database.db, business.id, conversation.id))?.id).toBe(conversation.id);
  });

  it('lists messages after an id and builds history without system messages', async () => {
    const { conversation } = await newConversation();
    await addMessage(conversation.id, 'visitor', 'hi');
    await addMessage(conversation.id, 'bot', 'hello');
    await addMessage(conversation.id, 'system', 'Visitor left contact details');
    await addMessage(conversation.id, 'agent', 'Sam here');
    const all = await listMessages(database.db, conversation.id);
    expect(all.map((m) => m.role)).toEqual(['visitor', 'bot', 'system', 'agent']);
    const after = await listMessages(database.db, conversation.id, { afterId: all[1]!.id, roles: ['visitor', 'bot', 'agent'] });
    expect(after.map((m) => m.content)).toEqual(['Sam here']);
    expect(await lastMessageId(database.db, conversation.id)).toBe(all[3]!.id);
    expect(await recentHistory(database.db, conversation.id)).toEqual([
      { role: 'visitor', content: 'hi' },
      { role: 'bot', content: 'hello' },
      { role: 'agent', content: 'Sam here' },
    ]);
  });

  it('limits history to the most recent messages', async () => {
    const { conversation } = await newConversation();
    for (let i = 0; i < 10; i++) await addMessage(conversation.id, 'visitor', `m${i}`);
    const history = await recentHistory(database.db, conversation.id, 3);
    expect(history.map((h) => h.content)).toEqual(['m7', 'm8', 'm9']);
  });

  it('lists conversations newest first with filters, last message and contact', async () => {
    const a = await newConversation(new Date('2026-10-11T10:00:00Z'));
    const b = await newConversation(new Date('2026-10-11T11:00:00Z'));
    await addMessage(a.conversation.id, 'visitor', 'first');
    await addMessage(a.conversation.id, 'bot', 'latest a');
    const [contact] = await database.db.insert(contacts).values({ businessId: business.id, name: 'Alex', email: 'a@example.com' }).returning();
    await database.db.update(conversations).set({ needsAttention: true, contactId: contact!.id }).where(eq(conversations.id, a.conversation.id));
    await database.db.update(conversations).set({ status: 'closed' }).where(eq(conversations.id, b.conversation.id));

    const all = await listConversations(database.db, business.id);
    expect(all.map((i) => i.conversation.id)).toEqual([b.conversation.id, a.conversation.id]);
    expect(all[1]!.lastMessage?.content).toBe('latest a');
    expect(all[1]!.contact?.name).toBe('Alex');
    expect((await listConversations(database.db, business.id, { filter: 'attention' })).map((i) => i.conversation.id)).toEqual([a.conversation.id]);
    expect((await listConversations(database.db, business.id, { filter: 'open' })).map((i) => i.conversation.id)).toEqual([a.conversation.id]);
  });
});

describe('bookings', () => {
  it('decides a pending booking exactly once and scopes by business', async () => {
    const { conversation } = await newConversation();
    const [booking] = await database.db
      .insert(bookingRequests)
      .values({ businessId: business.id, conversationId: conversation.id, fields: { name: 'Alex' } })
      .returning();
    const other = await createBusiness(database.db, { slug: 'other-biz', name: 'Other' });
    expect(await getBookingForBusiness(database.db, other.id, booking!.id)).toBeNull();
    expect(await decideBooking(database.db, { businessId: other.id, bookingId: booking!.id, decision: 'confirmed', note: null, userId: 'u', now: t0 })).toBeNull();

    const decided = await decideBooking(database.db, { businessId: business.id, bookingId: booking!.id, decision: 'confirmed', note: 'See you Saturday', userId: 'u1', now: t0 });
    expect(decided).toMatchObject({ status: 'confirmed', ownerNote: 'See you Saturday', decidedBy: 'u1' });
    expect(decided?.decidedAt?.getTime()).toBe(t0.getTime());
    expect(await decideBooking(database.db, { businessId: business.id, bookingId: booking!.id, decision: 'declined', note: null, userId: 'u1', now: t0 })).toBeNull();

    expect((await listBookings(database.db, business.id, { status: 'pending' })).length).toBe(0);
    expect((await listBookings(database.db, business.id)).length).toBe(1);
  });
});
```

- [ ] **Step 2: Run to verify RED** — `npx vitest run apps/server` → FAIL.

- [ ] **Step 3: Implement**

`apps/server/src/domain/contact.ts`:
```ts
import { contactValue, type BookingFieldDef } from '@reception/core';

export interface ContactFields {
  name: string | null;
  email: string | null;
  phone: string | null;
}

export function contactFromFields(defs: BookingFieldDef[], fields: Record<string, string>): ContactFields {
  const contact = contactValue(defs, fields).trim();
  const name = fields.name?.trim() || null;
  if (!contact) return { name, email: null, phone: null };
  return contact.includes('@') ? { name, email: contact, phone: null } : { name, email: null, phone: contact };
}
```

`apps/server/src/repos/audit.ts`:
```ts
import type { Db } from '../db/client';
import { auditLog } from '../db/schema';

export interface AuditEntry {
  businessId: string | null;
  /** 'visitor', 'system' or `user:<id>` */
  actor: string;
  action: string;
  target?: string | null;
  data?: Record<string, unknown> | null;
  now?: Date;
}

export function auditInsert(db: Db, entry: AuditEntry) {
  return db.insert(auditLog).values({
    businessId: entry.businessId,
    actor: entry.actor,
    action: entry.action,
    target: entry.target ?? null,
    data: entry.data ?? null,
    ...(entry.now ? { createdAt: entry.now } : {}),
  });
}

export async function writeAudit(db: Db, entry: AuditEntry): Promise<void> {
  await auditInsert(db, entry);
}
```

`apps/server/src/repos/conversations.ts`:
```ts
import type { ChatMessage, Locale } from '@reception/core';
import { and, asc, desc, eq, gt, inArray, max, type SQL } from 'drizzle-orm';
import type { Db } from '../db/client';
import { contacts, conversations, messages, type MessageRole } from '../db/schema';
import { safeEqualHex, sha256 } from '../lib/crypto';

export type ConversationRow = typeof conversations.$inferSelect;
export type MessageRow = typeof messages.$inferSelect;
export type ContactRow = typeof contacts.$inferSelect;

export const VISITOR_VISIBLE_ROLES: readonly MessageRole[] = ['visitor', 'bot', 'agent'];

export interface ConversationListItem {
  conversation: ConversationRow;
  lastMessage: MessageRow | null;
  contact: ContactRow | null;
}

export async function insertConversation(
  db: Db,
  input: { businessId: string; visitorTokenHash: string; locale: Locale | null; now: Date },
): Promise<ConversationRow> {
  const [row] = await db
    .insert(conversations)
    .values({
      businessId: input.businessId,
      visitorTokenHash: input.visitorTokenHash,
      locale: input.locale,
      lastMessageAt: input.now,
      createdAt: input.now,
    })
    .returning();
  return row!;
}

export async function getConversation(db: Db, id: string): Promise<ConversationRow | null> {
  const [row] = await db.select().from(conversations).where(eq(conversations.id, id)).limit(1);
  return row ?? null;
}

export async function getConversationForBusiness(db: Db, businessId: string, id: string): Promise<ConversationRow | null> {
  const [row] = await db
    .select()
    .from(conversations)
    .where(and(eq(conversations.id, id), eq(conversations.businessId, businessId)))
    .limit(1);
  return row ?? null;
}

export function verifyVisitorToken(conversation: ConversationRow, token: string): boolean {
  if (!token) return false;
  return safeEqualHex(conversation.visitorTokenHash, sha256(token));
}

export async function listConversations(
  db: Db,
  businessId: string,
  opts: { filter?: 'all' | 'attention' | 'open'; limit?: number } = {},
): Promise<ConversationListItem[]> {
  const conditions: SQL[] = [eq(conversations.businessId, businessId)];
  if (opts.filter === 'attention') conditions.push(eq(conversations.needsAttention, true));
  if (opts.filter === 'open') conditions.push(inArray(conversations.status, ['bot', 'human']));
  const rows = await db
    .select({ conversation: conversations, contact: contacts })
    .from(conversations)
    .leftJoin(contacts, eq(contacts.id, conversations.contactId))
    .where(and(...conditions))
    .orderBy(desc(conversations.lastMessageAt))
    .limit(Math.min(opts.limit ?? 50, 200));
  // At most 200 small lookups; acceptable for a single-business MVP.
  return Promise.all(
    rows.map(async (r) => {
      const [lastMessage] = await db
        .select()
        .from(messages)
        .where(eq(messages.conversationId, r.conversation.id))
        .orderBy(desc(messages.id))
        .limit(1);
      return { conversation: r.conversation, contact: r.contact, lastMessage: lastMessage ?? null };
    }),
  );
}

export async function listMessages(
  db: Db,
  conversationId: string,
  opts: { afterId?: number; limit?: number; roles?: readonly MessageRole[] } = {},
): Promise<MessageRow[]> {
  const conditions: SQL[] = [eq(messages.conversationId, conversationId)];
  if (opts.afterId !== undefined) conditions.push(gt(messages.id, opts.afterId));
  if (opts.roles) conditions.push(inArray(messages.role, [...opts.roles]));
  return db
    .select()
    .from(messages)
    .where(and(...conditions))
    .orderBy(asc(messages.id))
    .limit(Math.min(opts.limit ?? 500, 1000));
}

export async function lastMessageId(db: Db, conversationId: string): Promise<number> {
  const [row] = await db.select({ value: max(messages.id) }).from(messages).where(eq(messages.conversationId, conversationId));
  return row?.value ?? 0;
}

export async function recentHistory(db: Db, conversationId: string, limit = 40): Promise<ChatMessage[]> {
  const rows = await db
    .select({ role: messages.role, content: messages.content })
    .from(messages)
    .where(and(eq(messages.conversationId, conversationId), inArray(messages.role, ['visitor', 'bot', 'agent'])))
    .orderBy(desc(messages.id))
    .limit(limit);
  return rows.reverse().map((r) => ({ role: r.role as ChatMessage['role'], content: r.content }));
}

export async function getContact(db: Db, id: string): Promise<ContactRow | null> {
  const [row] = await db.select().from(contacts).where(eq(contacts.id, id)).limit(1);
  return row ?? null;
}
```

`apps/server/src/repos/bookings.ts`:
```ts
import { and, desc, eq, type SQL } from 'drizzle-orm';
import type { Db } from '../db/client';
import { bookingRequests } from '../db/schema';

export type BookingRow = typeof bookingRequests.$inferSelect;
export type BookingStatus = BookingRow['status'];

export async function listBookings(
  db: Db,
  businessId: string,
  opts: { status?: BookingStatus; limit?: number } = {},
): Promise<BookingRow[]> {
  const conditions: SQL[] = [eq(bookingRequests.businessId, businessId)];
  if (opts.status) conditions.push(eq(bookingRequests.status, opts.status));
  return db
    .select()
    .from(bookingRequests)
    .where(and(...conditions))
    .orderBy(desc(bookingRequests.createdAt))
    .limit(Math.min(opts.limit ?? 100, 500));
}

export async function getBookingForBusiness(db: Db, businessId: string, id: string): Promise<BookingRow | null> {
  const [row] = await db
    .select()
    .from(bookingRequests)
    .where(and(eq(bookingRequests.id, id), eq(bookingRequests.businessId, businessId)))
    .limit(1);
  return row ?? null;
}

export async function listBookingsForConversation(db: Db, conversationId: string): Promise<BookingRow[]> {
  return db
    .select()
    .from(bookingRequests)
    .where(eq(bookingRequests.conversationId, conversationId))
    .orderBy(desc(bookingRequests.createdAt));
}

/** Only a pending request can be decided; returns null when not found, foreign, or already decided. */
export async function decideBooking(
  db: Db,
  input: { businessId: string; bookingId: string; decision: 'confirmed' | 'declined'; note: string | null; userId: string; now: Date },
): Promise<BookingRow | null> {
  const [row] = await db
    .update(bookingRequests)
    .set({ status: input.decision, ownerNote: input.note, decidedAt: input.now, decidedBy: input.userId })
    .where(
      and(
        eq(bookingRequests.id, input.bookingId),
        eq(bookingRequests.businessId, input.businessId),
        eq(bookingRequests.status, 'pending'),
      ),
    )
    .returning();
  return row ?? null;
}
```

- [ ] **Step 4: Verify GREEN** — `npx vitest run apps/server` PASS; `npm run typecheck` exit 0.

- [ ] **Step 5: Commit**

```bash
git add apps/server/src/repos apps/server/src/domain
git commit -m "feat(server): add conversation, message, booking and audit repositories"
```

---
### Task 5: Mailer, email templates and notifier

**Files:**
- Create: `apps/server/src/mail/mailer.ts`, `apps/server/src/mail/templates.ts`, `apps/server/src/services/notifier.ts`
- Test: `apps/server/src/mail/templates.test.ts`, `apps/server/src/services/notifier.test.ts`

**Interfaces:**
- Consumes: `Db`, `bookingRequests`, `writeAudit`, `contactFromFields`, `BusinessRow`, `ConversationRow`, `BookingRow`, core `HandoffReason`, `Locale`.
- Produces:
  - `MailMessage { to; subject; text }`, `Mailer { send(m): Promise<void> }`, `createSmtpMailer(smtpUrl, from)`, `createMemoryMailer(): Mailer & { sent: MailMessage[] }`
  - `sanitizeField(text)`, `MAX_EMAIL_FIELD_CHARS = 200`, `FieldLine { label; value }`, `EmailContent { subject; text }`, `ownerBookingEmail`, `ownerHandoffEmail`, `ownerOfflineContactEmail`, `visitorReceiptEmail`
  - `Notifier { bookingCreated({business, conversation, booking}); handoff({business, conversation, reason, excerpt}); offlineContact({business, conversation, contact, note}) }` — every method resolves (never rejects); `createNotifier({ db, mailer, publicOrigin, now })`; `adminConversationUrl(publicOrigin, conversationId)`

- [ ] **Step 1: Write failing tests**

`apps/server/src/mail/templates.test.ts`:
```ts
import { describe, expect, it } from 'vitest';
import { ownerBookingEmail, ownerHandoffEmail, sanitizeField, visitorReceiptEmail } from './templates';

describe('sanitizeField', () => {
  it('removes links and control characters, collapses whitespace and caps length', () => {
    expect(sanitizeField('see https://evil.example/x and www.spam.example now')).toBe('see [link removed] and [link removed] now');
    expect(sanitizeField('a\u0000b\n\n c')).toBe('ab c');
    expect(sanitizeField('x'.repeat(500))).toHaveLength(200);
  });
});

describe('email templates', () => {
  const fields = [
    { label: 'service', value: 'Deep cleaning' },
    { label: 'name', value: 'Alex https://evil.example' },
  ];

  it('builds the owner booking email in English and Chinese', () => {
    const en = ownerBookingEmail({ businessName: 'Harbor', fields, adminUrl: 'https://x.example/admin' }, 'en');
    expect(en.subject).toBe('[Harbor] New booking request (pending)');
    expect(en.text).toContain('- service: Deep cleaning');
    expect(en.text).toContain('- name: Alex [link removed]');
    expect(en.text).toContain('https://x.example/admin');
    const zh = ownerBookingEmail({ businessName: 'Harbor', fields, adminUrl: 'https://x.example/admin' }, 'zh-CN');
    expect(zh.subject).toBe('【Harbor】新的预约申请（待确认）');
  });

  it('describes handoff reasons', () => {
    const en = ownerHandoffEmail({ businessName: 'Harbor', reason: 'visitor_request', excerpt: 'talk to a human please', adminUrl: 'u' }, 'en');
    expect(en.text).toContain('The visitor asked for a person');
    expect(en.text).toContain('talk to a human please');
  });

  it('builds the visitor receipt without links from visitor input', () => {
    const r = visitorReceiptEmail({ businessName: 'Harbor', fields }, 'en');
    expect(r.subject).toBe('Harbor: we received your booking request');
    expect(r.text).toContain('pending confirmation');
    expect(r.text).not.toContain('evil.example');
  });
});
```

`apps/server/src/services/notifier.test.ts`:
```ts
import { and, eq } from 'drizzle-orm';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import type { Database } from '../db/client';
import { auditLog, bookingRequests } from '../db/schema';
import { sha256 } from '../lib/crypto';
import { createMemoryMailer, type Mailer } from '../mail/mailer';
import type { BookingRow } from '../repos/bookings';
import { updateBusiness, type BusinessRow } from '../repos/business';
import { insertConversation, type ConversationRow } from '../repos/conversations';
import { seedDemoBusiness, testDatabase } from '../testing/fixtures';
import { createNotifier } from './notifier';

let database: Database;
let business: BusinessRow;
let conversation: ConversationRow;
const now = new Date('2026-10-11T10:00:00Z');

beforeEach(async () => {
  database = await testDatabase();
  business = await seedDemoBusiness(database.db);
  conversation = await insertConversation(database.db, { businessId: business.id, visitorTokenHash: sha256('t'), locale: 'en', now });
});
afterEach(() => database.close());

async function makeBooking(fields: Record<string, string>): Promise<BookingRow> {
  const [row] = await database.db.insert(bookingRequests).values({ businessId: business.id, conversationId: conversation.id, fields }).returning();
  return row!;
}

const fields = { service: 'Deep cleaning', preferred_time: 'Sat 10am', name: 'Alex', contact: 'alex@example.com' };

describe('notifier', () => {
  it('emails the owner and sends one visitor receipt per booking', async () => {
    const mailer = createMemoryMailer();
    const notifier = createNotifier({ db: database.db, mailer, publicOrigin: 'https://chat.example', now: () => now });
    const booking = await makeBooking(fields);
    await notifier.bookingCreated({ business, conversation, booking });
    await notifier.bookingCreated({ business, conversation, booking });
    const owner = mailer.sent.filter((m) => m.to === 'owner@harbor.example');
    const visitor = mailer.sent.filter((m) => m.to === 'alex@example.com');
    expect(owner).toHaveLength(2);
    expect(owner[0]!.text).toContain(`https://chat.example/admin/#/conversations/${conversation.id}`);
    expect(visitor).toHaveLength(1);
    const [stored] = await database.db.select().from(bookingRequests).where(eq(bookingRequests.id, booking.id));
    expect(stored!.receiptSentAt?.getTime()).toBe(now.getTime());
  });

  it('skips receipts when disabled or when the contact is a phone number', async () => {
    const mailer = createMemoryMailer();
    const notifier = createNotifier({ db: database.db, mailer, publicOrigin: 'https://chat.example', now: () => now });
    await notifier.bookingCreated({ business, conversation, booking: await makeBooking({ ...fields, contact: '13800138000' }) });
    const updated = (await updateBusiness(database.db, business.id, { notify: { ownerEmail: null, visitorReceipts: false } }, now))!;
    await notifier.bookingCreated({ business: updated, conversation, booking: await makeBooking(fields) });
    expect(mailer.sent.map((m) => m.to)).toEqual(['owner@harbor.example']);
  });

  it('never throws and records failures in the audit log', async () => {
    const failing: Mailer = {
      async send() {
        throw new Error('SMTP down');
      },
    };
    const notifier = createNotifier({ db: database.db, mailer: failing, publicOrigin: 'https://chat.example', now: () => now });
    await expect(notifier.handoff({ business, conversation, reason: 'unknown_question', excerpt: 'boats?' })).resolves.toBeUndefined();
    const rows = await database.db.select().from(auditLog).where(and(eq(auditLog.action, 'email_failed'), eq(auditLog.target, 'owner_handoff')));
    expect(rows).toHaveLength(1);
  });

  it('does nothing without a mailer', async () => {
    const notifier = createNotifier({ db: database.db, mailer: null, publicOrigin: 'https://chat.example', now: () => now });
    await expect(
      notifier.offlineContact({ business, conversation, contact: { name: 'A', email: 'a@example.com', phone: null }, note: '' }),
    ).resolves.toBeUndefined();
  });
});
```

- [ ] **Step 2: Run to verify RED** — `npx vitest run apps/server/src/mail apps/server/src/services` → FAIL.

- [ ] **Step 3: Implement**

`apps/server/src/mail/mailer.ts`:
```ts
import { createTransport } from 'nodemailer';

export interface MailMessage {
  to: string;
  subject: string;
  text: string;
}

export interface Mailer {
  send(message: MailMessage): Promise<void>;
}

/** smtpUrl like smtps://user:pass@smtp.example.com:465 */
export function createSmtpMailer(smtpUrl: string, from: string): Mailer {
  const transport = createTransport(smtpUrl);
  return {
    async send(message) {
      await transport.sendMail({ from, to: message.to, subject: message.subject, text: message.text });
    },
  };
}

export function createMemoryMailer(): Mailer & { sent: MailMessage[] } {
  const sent: MailMessage[] = [];
  return {
    sent,
    async send(message) {
      sent.push(message);
    },
  };
}
```

`apps/server/src/mail/templates.ts`:
```ts
import type { HandoffReason, Locale } from '@reception/core';

export const MAX_EMAIL_FIELD_CHARS = 200;
const URL_PATTERN = /\b(?:https?:\/\/|www\.)\S+/gi;
// eslint-disable-next-line no-control-regex
const CONTROL_CHARS = /[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F]/g;

export interface EmailContent {
  subject: string;
  text: string;
}

export interface FieldLine {
  label: string;
  value: string;
}

/** For visitor-supplied text in emails: no links, no control characters, one line, capped. */
export function sanitizeField(value: string): string {
  const clean = value.replace(URL_PATTERN, '[link removed]').replace(CONTROL_CHARS, '').replace(/\s+/g, ' ').trim();
  return clean.length > MAX_EMAIL_FIELD_CHARS ? `${clean.slice(0, MAX_EMAIL_FIELD_CHARS - 1)}…` : clean;
}

const list = (fields: FieldLine[]) => fields.map((f) => `- ${f.label}: ${sanitizeField(f.value)}`).join('\n');

const HANDOFF_REASONS: Record<Locale, Record<HandoffReason, string>> = {
  en: {
    visitor_request: 'The visitor asked for a person',
    unknown_question: "The assistant couldn't answer a question",
    model_request: "The assistant couldn't answer a question",
    message_limit: 'The conversation reached its message limit',
  },
  'zh-CN': {
    visitor_request: '访客要求人工服务',
    unknown_question: '助手无法回答访客的问题',
    model_request: '助手无法回答访客的问题',
    message_limit: '对话已达到消息上限',
  },
};

export function ownerBookingEmail(input: { businessName: string; fields: FieldLine[]; adminUrl: string }, locale: Locale): EmailContent {
  return locale === 'zh-CN'
    ? {
        subject: `【${input.businessName}】新的预约申请（待确认）`,
        text: `收到一条新的预约申请，请在后台确认或拒绝：\n\n${list(input.fields)}\n\n查看：${input.adminUrl}`,
      }
    : {
        subject: `[${input.businessName}] New booking request (pending)`,
        text: `A visitor submitted a booking request. Please confirm or decline it in the dashboard:\n\n${list(input.fields)}\n\nOpen: ${input.adminUrl}`,
      };
}

export function ownerHandoffEmail(
  input: { businessName: string; reason: HandoffReason; excerpt: string; adminUrl: string },
  locale: Locale,
): EmailContent {
  const reason = HANDOFF_REASONS[locale][input.reason];
  return locale === 'zh-CN'
    ? {
        subject: `【${input.businessName}】有访客需要人工处理`,
        text: `${reason}。\n\n访客消息：${sanitizeField(input.excerpt)}\n\n查看：${input.adminUrl}`,
      }
    : {
        subject: `[${input.businessName}] A visitor needs your attention`,
        text: `${reason}.\n\nVisitor message: ${sanitizeField(input.excerpt)}\n\nOpen: ${input.adminUrl}`,
      };
}

export function ownerOfflineContactEmail(
  input: { businessName: string; contact: FieldLine[]; note: string; adminUrl: string },
  locale: Locale,
): EmailContent {
  const note = input.note ? sanitizeField(input.note) : '';
  return locale === 'zh-CN'
    ? {
        subject: `【${input.businessName}】访客留下了联系方式`,
        text: `访客留下了联系方式，请尽快跟进：\n\n${list(input.contact)}${note ? `\n\n留言：${note}` : ''}\n\n查看：${input.adminUrl}`,
      }
    : {
        subject: `[${input.businessName}] A visitor left their contact details`,
        text: `A visitor left their contact details. Please follow up:\n\n${list(input.contact)}${note ? `\n\nNote: ${note}` : ''}\n\nOpen: ${input.adminUrl}`,
      };
}

export function visitorReceiptEmail(input: { businessName: string; fields: FieldLine[] }, locale: Locale): EmailContent {
  return locale === 'zh-CN'
    ? {
        subject: `${input.businessName}：我们已收到您的预约申请`,
        text: `感谢您的预约申请，目前正在等待确认，${input.businessName}会与您联系确认。\n\n${list(input.fields)}\n\n这是一封自动发送的邮件。`,
      }
    : {
        subject: `${input.businessName}: we received your booking request`,
        text: `Thanks for your request. It is pending confirmation — ${input.businessName} will contact you to confirm.\n\n${list(input.fields)}\n\nThis is an automated message.`,
      };
}
```

`apps/server/src/services/notifier.ts`:
```ts
import type { HandoffReason, Locale } from '@reception/core';
import { and, eq, isNull } from 'drizzle-orm';
import type { Db } from '../db/client';
import { bookingRequests } from '../db/schema';
import { contactFromFields, type ContactFields } from '../domain/contact';
import type { MailMessage, Mailer } from '../mail/mailer';
import { ownerBookingEmail, ownerHandoffEmail, ownerOfflineContactEmail, visitorReceiptEmail, type FieldLine } from '../mail/templates';
import { writeAudit } from '../repos/audit';
import type { BookingRow } from '../repos/bookings';
import type { BusinessRow } from '../repos/business';
import type { ConversationRow } from '../repos/conversations';

export interface Notifier {
  bookingCreated(input: { business: BusinessRow; conversation: ConversationRow; booking: BookingRow }): Promise<void>;
  handoff(input: { business: BusinessRow; conversation: ConversationRow; reason: HandoffReason; excerpt: string }): Promise<void>;
  offlineContact(input: { business: BusinessRow; conversation: ConversationRow; contact: ContactFields; note: string }): Promise<void>;
}

export interface NotifierDeps {
  db: Db;
  mailer: Mailer | null;
  publicOrigin: string;
  now: () => Date;
}

export function adminConversationUrl(publicOrigin: string, conversationId: string): string {
  return `${publicOrigin}/admin/#/conversations/${conversationId}`;
}

export function createNotifier(deps: NotifierDeps): Notifier {
  async function deliver(business: BusinessRow, kind: string, message: MailMessage): Promise<void> {
    if (!deps.mailer) return;
    try {
      await deps.mailer.send(message);
      await writeAudit(deps.db, { businessId: business.id, actor: 'system', action: 'email_sent', target: kind, now: deps.now() });
    } catch (error) {
      await writeAudit(deps.db, {
        businessId: business.id,
        actor: 'system',
        action: 'email_failed',
        target: kind,
        data: { error: error instanceof Error ? error.message.slice(0, 200) : 'unknown error' },
        now: deps.now(),
      }).catch(() => undefined);
    }
  }

  /** Notifications are best-effort: they must never fail the caller's request. */
  async function safely(work: () => Promise<void>): Promise<void> {
    try {
      await work();
    } catch (error) {
      console.error('notification failed', error);
    }
  }

  function fieldLines(business: BusinessRow, fields: Record<string, string>, locale: Locale): FieldLine[] {
    return business.bookingFields.filter((d) => fields[d.key]).map((d) => ({ label: d.label[locale], value: fields[d.key]! }));
  }

  return {
    bookingCreated: ({ business, conversation, booking }) =>
      safely(async () => {
        const tasks: Promise<void>[] = [];
        const adminUrl = adminConversationUrl(deps.publicOrigin, conversation.id);
        if (business.notify.ownerEmail) {
          const content = ownerBookingEmail(
            { businessName: business.name, fields: fieldLines(business, booking.fields, business.locale), adminUrl },
            business.locale,
          );
          tasks.push(deliver(business, 'owner_booking', { to: business.notify.ownerEmail, ...content }));
        }
        const { email } = contactFromFields(business.bookingFields, booking.fields);
        if (email && business.notify.visitorReceipts && deps.mailer) {
          // Atomic claim: at most one receipt per booking even if this runs twice.
          const claimed = await deps.db
            .update(bookingRequests)
            .set({ receiptSentAt: deps.now() })
            .where(and(eq(bookingRequests.id, booking.id), isNull(bookingRequests.receiptSentAt)))
            .returning({ id: bookingRequests.id });
          if (claimed.length > 0) {
            const locale = conversation.locale ?? business.locale;
            const content = visitorReceiptEmail({ businessName: business.name, fields: fieldLines(business, booking.fields, locale) }, locale);
            tasks.push(deliver(business, 'visitor_receipt', { to: email, ...content }));
          }
        }
        await Promise.all(tasks);
      }),

    handoff: ({ business, conversation, reason, excerpt }) =>
      safely(async () => {
        if (!business.notify.ownerEmail) return;
        const content = ownerHandoffEmail(
          { businessName: business.name, reason, excerpt, adminUrl: adminConversationUrl(deps.publicOrigin, conversation.id) },
          business.locale,
        );
        await deliver(business, 'owner_handoff', { to: business.notify.ownerEmail, ...content });
      }),

    offlineContact: ({ business, conversation, contact, note }) =>
      safely(async () => {
        if (!business.notify.ownerEmail) return;
        const labels =
          business.locale === 'zh-CN' ? { name: '姓名', email: '邮箱', phone: '电话' } : { name: 'name', email: 'email', phone: 'phone' };
        const lines: FieldLine[] = (['name', 'email', 'phone'] as const)
          .filter((k) => contact[k])
          .map((k) => ({ label: labels[k], value: contact[k]! }));
        const content = ownerOfflineContactEmail(
          { businessName: business.name, contact: lines, note, adminUrl: adminConversationUrl(deps.publicOrigin, conversation.id) },
          business.locale,
        );
        await deliver(business, 'owner_offline_contact', { to: business.notify.ownerEmail, ...content });
      }),
  };
}
```

- [ ] **Step 4: Verify GREEN** — `npx vitest run apps/server` PASS; `npm run typecheck` exit 0.

- [ ] **Step 5: Commit**

```bash
git add apps/server/src/mail apps/server/src/services
git commit -m "feat(server): add mailer, bilingual email templates and best-effort notifier"
```

---

### Task 6: AI adapter and bring-your-own-key settings

**Files:**
- Create: `apps/server/src/ai/providers.ts`, `apps/server/src/ai/replyModel.ts`, `apps/server/src/ai/settings.ts`, `apps/server/src/ai/testing.ts` (test helper)
- Test: `apps/server/src/ai/replyModel.test.ts`, `apps/server/src/ai/settings.test.ts`

**Interfaces:**
- Consumes: core `ReplyModel`, `ModelAction`, `handleVisitorMessage`, `demoKnowledge`; `AiSettings`, `AI_PROVIDERS`, `businesses`; `encryptSecret`, `decryptSecret`, `deriveKey`; `ValidationError`, `parseOrThrow`; `BusinessRow`, `getBusinessById`; fixtures.
- Produces:
  - `ProviderConfig { provider: AiProvider; model: string; baseUrl: string | null }`, `LanguageModelFactory = (config, apiKey) => LanguageModel`, `createLanguageModel` (default factory)
  - `REPLY_TOOLS`, `createAiReplyModel(model: LanguageModel): ReplyModel`
  - `aiSettingsInputSchema`, `PublicAiSettings`, `publicAiSettings(ai)`, `saveAiSettings(db, business, input, key, now): Promise<AiSettings>`, `resolveReplyModel(business, key, factory?): ReplyModel | null`, `AiTestResult`, `testAiConnection(business, key, factory?, timeoutMs?)`
  - test helper `mockLanguageModel({ text?, calls?, error? })`

- [ ] **Step 1: Create the mock helper** — `apps/server/src/ai/testing.ts`:
```ts
import { MockLanguageModelV4 } from 'ai/test';

const USAGE = {
  inputTokens: { total: 10, noCache: 10, cacheRead: undefined, cacheWrite: undefined },
  outputTokens: { total: 5, text: 5, reasoning: undefined },
};

export interface MockReply {
  text?: string;
  calls?: { toolName: string; input: unknown }[];
  error?: Error;
}

/** AI SDK v7 mock model. `doGenerateCalls` records the prompt each call received. */
export function mockLanguageModel(reply: MockReply): MockLanguageModelV4 {
  return new MockLanguageModelV4({
    doGenerate: async () => {
      if (reply.error) throw reply.error;
      const content = [
        ...(reply.text ? [{ type: 'text' as const, text: reply.text }] : []),
        ...(reply.calls ?? []).map((c, i) => ({
          type: 'tool-call' as const,
          toolCallId: `call-${i}`,
          toolName: c.toolName,
          input: JSON.stringify(c.input),
        })),
      ];
      return {
        content,
        finishReason: { unified: reply.calls?.length ? ('tool-calls' as const) : ('stop' as const), raw: undefined },
        usage: USAGE,
        warnings: [],
      };
    },
  });
}
```

- [ ] **Step 2: Write failing tests**

`apps/server/src/ai/replyModel.test.ts`:
```ts
import { demoKnowledge, handleVisitorMessage } from '@reception/core';
import { MockLanguageModelV4 } from 'ai/test';
import { describe, expect, it } from 'vitest';
import { createLanguageModel } from './providers';
import { createAiReplyModel } from './replyModel';
import { mockLanguageModel } from './testing';

const input = (signal = new AbortController().signal) => ({
  system: 'SYSTEM RULES',
  messages: [{ role: 'user' as const, content: '<visitor_message>hi</visitor_message>' }],
  signal,
  maxOutputTokens: 400,
});

describe('createAiReplyModel', () => {
  it('returns text and passes instructions, messages and the output cap', async () => {
    const model = mockLanguageModel({ text: 'Hello!' });
    const out = await createAiReplyModel(model).generate(input());
    expect(out).toEqual({ text: 'Hello!', actions: [] });
    const call = model.doGenerateCalls[0]!;
    expect(call.prompt[0]).toEqual({ role: 'system', content: 'SYSTEM RULES' });
    expect(JSON.stringify(call.prompt[1])).toContain('<visitor_message>hi</visitor_message>');
    expect(call.maxOutputTokens).toBe(400);
  });

  it('maps tool calls to core actions in order', async () => {
    const model = mockLanguageModel({
      calls: [
        { toolName: 'update_booking_draft', input: { fields: { service: 'deep', name: 'Alex' } } },
        { toolName: 'submit_booking_request', input: {} },
        { toolName: 'handoff_to_human', input: { reason: 'pets question' } },
      ],
    });
    const out = await createAiReplyModel(model).generate(input());
    expect(out.actions).toEqual([
      { type: 'update_booking_draft', fields: { service: 'deep', name: 'Alex' } },
      { type: 'submit_booking_request' },
      { type: 'handoff_to_human', reason: 'pets question' },
    ]);
  });

  it('drops tool calls whose input fails the schema', async () => {
    const model = mockLanguageModel({ calls: [{ toolName: 'update_booking_draft', input: { fields: 'not-an-object' } }], text: 'ok' });
    const out = await createAiReplyModel(model).generate(input());
    expect(out.actions).toEqual([]);
  });

  it('rejects when the signal aborts', async () => {
    const model = new MockLanguageModelV4({
      doGenerate: (options) =>
        new Promise((_, reject) => {
          const signal = options.abortSignal!;
          if (signal.aborted) return reject(signal.reason);
          signal.addEventListener('abort', () => reject(signal.reason));
        }),
    });
    const controller = new AbortController();
    const pending = createAiReplyModel(model).generate(input(controller.signal));
    controller.abort(new Error('timeout'));
    await expect(pending).rejects.toThrow();
  });

  it('drives a full booking through the core engine', async () => {
    const model = mockLanguageModel({
      calls: [
        {
          toolName: 'update_booking_draft',
          input: { fields: { service: 'deep clean', preferred_time: 'Sat 10am', name: 'Alex', contact: 'alex@example.com' } },
        },
        { toolName: 'submit_booking_request', input: {} },
      ],
    });
    const result = await handleVisitorMessage(
      {
        business: demoKnowledge(),
        conversation: { status: 'bot', locale: null, bookingDraft: null, visitorMessageCount: 0 },
        history: [],
        now: new Date('2026-10-11T10:00:00Z'),
      },
      'Book a deep clean Saturday 10am, Alex, alex@example.com',
      { model: createAiReplyModel(model) },
    );
    expect(result.meta.engine).toBe('ai');
    expect(result.bookingRequest?.fields.service).toBe('Deep cleaning');
    expect(result.replies[0]).toContain('pending confirmation');
  });
});

describe('createLanguageModel', () => {
  const provider = (m: unknown) => (m as { provider: string }).provider;

  it('builds a model for each provider', () => {
    expect(provider(createLanguageModel({ provider: 'openai', model: 'gpt-x', baseUrl: null }, 'k'))).toContain('openai');
    expect(provider(createLanguageModel({ provider: 'anthropic', model: 'claude-x', baseUrl: null }, 'k'))).toContain('anthropic');
    expect(provider(createLanguageModel({ provider: 'google', model: 'gemini-x', baseUrl: null }, 'k'))).toContain('google');
    expect(
      provider(createLanguageModel({ provider: 'openai-compatible', model: 'deepseek-chat', baseUrl: 'https://api.deepseek.example/v1' }, 'k')),
    ).toContain('custom');
  });

  it('requires a base URL for OpenAI-compatible providers', () => {
    expect(() => createLanguageModel({ provider: 'openai-compatible', model: 'm', baseUrl: null }, 'k')).toThrow(/base URL/i);
  });
});
```

`apps/server/src/ai/settings.test.ts`:
```ts
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import type { Database } from '../db/client';
import { ValidationError } from '../domain/validation';
import { deriveKey } from '../lib/crypto';
import { getBusinessById, type BusinessRow } from '../repos/business';
import { seedDemoBusiness, testDatabase } from '../testing/fixtures';
import type { LanguageModelFactory } from './providers';
import { publicAiSettings, resolveReplyModel, saveAiSettings, testAiConnection } from './settings';
import { mockLanguageModel } from './testing';

let database: Database;
let business: BusinessRow;
const key = deriveKey('s'.repeat(32), 'ai-api-keys');
const now = new Date('2026-10-11T10:00:00Z');
const apiKey = 'sk-live-abcdefgh12345678';

beforeEach(async () => {
  database = await testDatabase();
  business = await seedDemoBusiness(database.db);
});
afterEach(() => database.close());

const reload = async () => (await getBusinessById(database.db, business.id))!;

describe('AI settings', () => {
  it('encrypts the key and exposes only the last four characters', async () => {
    await saveAiSettings(database.db, business, { provider: 'openai', model: 'gpt-x', apiKey, enabled: true }, key, now);
    const row = await reload();
    expect(publicAiSettings(row.ai)).toEqual({ provider: 'openai', model: 'gpt-x', baseUrl: null, keyLast4: '5678', hasKey: true, enabled: true });
    const raw = await database.client.execute('SELECT ai FROM businesses');
    expect(String(raw.rows[0]?.ai)).not.toContain(apiKey);
  });

  it('keeps the key when omitted and clears it with null', async () => {
    await saveAiSettings(database.db, business, { provider: 'openai', model: 'gpt-x', apiKey, enabled: false }, key, now);
    await saveAiSettings(database.db, await reload(), { provider: 'openai', model: 'gpt-y', enabled: false }, key, now);
    expect((await reload()).ai.keyLast4).toBe('5678');
    await saveAiSettings(database.db, await reload(), { provider: 'openai', model: 'gpt-y', apiKey: null, enabled: false }, key, now);
    expect(publicAiSettings((await reload()).ai).hasKey).toBe(false);
  });

  it('validates enabling and provider requirements', async () => {
    await expect(saveAiSettings(database.db, business, { provider: 'openai', model: 'gpt-x', enabled: true }, key, now)).rejects.toBeInstanceOf(
      ValidationError,
    );
    await expect(
      saveAiSettings(database.db, business, { provider: 'openai-compatible', model: 'm', apiKey, enabled: false }, key, now),
    ).rejects.toBeInstanceOf(ValidationError);
    await expect(
      saveAiSettings(database.db, business, { provider: 'openai', model: 'gpt-x', enabled: false, extra: 1 }, key, now),
    ).rejects.toBeInstanceOf(ValidationError);
  });

  it('resolves a reply model only when enabled with a decryptable key', async () => {
    const factory: LanguageModelFactory = () => mockLanguageModel({ text: 'hi' });
    expect(resolveReplyModel(business, key, factory)).toBeNull();
    await saveAiSettings(database.db, business, { provider: 'openai', model: 'gpt-x', apiKey, enabled: true }, key, now);
    const row = await reload();
    const model = resolveReplyModel(row, key, factory);
    expect(model).not.toBeNull();
    await expect(
      model!.generate({ system: 's', messages: [{ role: 'user', content: 'x' }], signal: new AbortController().signal, maxOutputTokens: 10 }),
    ).resolves.toEqual({ text: 'hi', actions: [] });
    expect(resolveReplyModel(row, deriveKey('t'.repeat(32), 'ai-api-keys'), factory)).toBeNull();
  });

  it('tests the connection and redacts the key from errors', async () => {
    await saveAiSettings(database.db, business, { provider: 'openai', model: 'gpt-x', apiKey, enabled: false }, key, now);
    const row = await reload();
    expect(await testAiConnection(row, key, () => mockLanguageModel({ text: 'OK' }))).toEqual({ ok: true });
    const failing = await testAiConnection(row, key, () => mockLanguageModel({ error: new Error(`401 invalid key ${apiKey}`) }));
    expect(failing.ok).toBe(false);
    expect(JSON.stringify(failing)).not.toContain(apiKey);
    expect(JSON.stringify(failing)).toContain('***');
  });
});
```

- [ ] **Step 3: Run to verify RED** — `npx vitest run apps/server/src/ai` → FAIL.

- [ ] **Step 4: Implement**

`apps/server/src/ai/providers.ts`:
```ts
import { createAnthropic } from '@ai-sdk/anthropic';
import { createGoogleGenerativeAI } from '@ai-sdk/google';
import { createOpenAI } from '@ai-sdk/openai';
import { createOpenAICompatible } from '@ai-sdk/openai-compatible';
import type { LanguageModel } from 'ai';
import type { AiProvider } from '../db/schema';

export interface ProviderConfig {
  provider: AiProvider;
  model: string;
  baseUrl: string | null;
}

export type LanguageModelFactory = (config: ProviderConfig, apiKey: string) => LanguageModel;

export const createLanguageModel: LanguageModelFactory = (config, apiKey) => {
  switch (config.provider) {
    case 'openai':
      return createOpenAI({ apiKey, ...(config.baseUrl ? { baseURL: config.baseUrl } : {}) })(config.model);
    case 'anthropic':
      return createAnthropic({ apiKey })(config.model);
    case 'google':
      return createGoogleGenerativeAI({ apiKey })(config.model);
    case 'openai-compatible':
      if (!config.baseUrl) throw new Error('A base URL is required for OpenAI-compatible providers');
      return createOpenAICompatible({ name: 'custom', apiKey, baseURL: config.baseUrl })(config.model);
  }
};
```

`apps/server/src/ai/replyModel.ts`:
```ts
import type { ModelAction, ReplyModel } from '@reception/core';
import { generateText, tool, type LanguageModel } from 'ai';
import { z } from 'zod';

/**
 * Tools have no `execute`: the calls are returned to the core engine, which validates them
 * and produces every commitment (booking submitted, handoff) from fixed templates.
 */
export const REPLY_TOOLS = {
  update_booking_draft: tool({
    description: 'Save booking details the visitor has provided. Use the booking field keys listed in the instructions as keys.',
    inputSchema: z.object({ fields: z.record(z.string(), z.string().max(200)) }),
  }),
  submit_booking_request: tool({
    description: 'Submit the booking request after every required booking field has been saved.',
    inputSchema: z.object({}),
  }),
  handoff_to_human: tool({
    description: 'Ask a team member to take over when the business information does not answer the question or the visitor asks for a person.',
    inputSchema: z.object({ reason: z.string().max(200) }),
  }),
};

export function createAiReplyModel(model: LanguageModel): ReplyModel {
  return {
    async generate(input) {
      const result = await generateText({
        model,
        instructions: input.system,
        messages: input.messages,
        tools: REPLY_TOOLS,
        abortSignal: input.signal,
        maxOutputTokens: input.maxOutputTokens,
        maxRetries: 0,
      });
      const actions: ModelAction[] = [];
      for (const call of result.toolCalls) {
        if (call.dynamic || (call as { invalid?: boolean }).invalid) continue;
        if (call.toolName === 'update_booking_draft') actions.push({ type: 'update_booking_draft', fields: call.input.fields });
        else if (call.toolName === 'submit_booking_request') actions.push({ type: 'submit_booking_request' });
        else if (call.toolName === 'handoff_to_human') actions.push({ type: 'handoff_to_human', reason: call.input.reason });
      }
      return { text: result.text, actions };
    },
  };
}
```

> If TS cannot narrow `call.input` by `toolName`, re-validate instead: `const parsed = REPLY_TOOLS.update_booking_draft.inputSchema` is a zod schema — use the same `z.object(...)` schemas (export them as constants) with `safeParse(call.input)` and skip failures. Record the choice in the report.

`apps/server/src/ai/settings.ts`:
```ts
import type { ReplyModel } from '@reception/core';
import { generateText } from 'ai';
import { eq } from 'drizzle-orm';
import { z } from 'zod';
import type { Db } from '../db/client';
import { AI_PROVIDERS, businesses, type AiProvider, type AiSettings } from '../db/schema';
import { parseOrThrow, ValidationError, type ValidationIssue } from '../domain/validation';
import { decryptSecret, encryptSecret } from '../lib/crypto';
import type { BusinessRow } from '../repos/business';
import { createLanguageModel, type LanguageModelFactory } from './providers';
import { createAiReplyModel } from './replyModel';

export const aiSettingsInputSchema = z
  .object({
    provider: z.enum(AI_PROVIDERS).nullable(),
    model: z.string().trim().max(100),
    baseUrl: z.union([z.null(), z.url()]).default(null),
    /** string = set, null = clear, omitted = keep */
    apiKey: z.union([z.null(), z.string().trim().min(8).max(500)]).optional(),
    enabled: z.boolean(),
  })
  .strict();

export interface PublicAiSettings {
  provider: AiProvider | null;
  model: string;
  baseUrl: string | null;
  keyLast4: string | null;
  hasKey: boolean;
  enabled: boolean;
}

export function publicAiSettings(ai: AiSettings): PublicAiSettings {
  return { provider: ai.provider, model: ai.model, baseUrl: ai.baseUrl, keyLast4: ai.keyLast4, hasKey: ai.encryptedKey !== null, enabled: ai.enabled };
}

export async function saveAiSettings(db: Db, business: BusinessRow, input: unknown, key: Buffer, now: Date): Promise<AiSettings> {
  const parsed = parseOrThrow(aiSettingsInputSchema, input);
  let encryptedKey = business.ai.encryptedKey;
  let keyLast4 = business.ai.keyLast4;
  if (parsed.apiKey === null) {
    encryptedKey = null;
    keyLast4 = null;
  } else if (typeof parsed.apiKey === 'string') {
    encryptedKey = encryptSecret(parsed.apiKey, key);
    keyLast4 = parsed.apiKey.slice(-4);
  }
  const issues: ValidationIssue[] = [];
  if (parsed.provider === 'openai-compatible' && !parsed.baseUrl) {
    issues.push({ path: 'baseUrl', message: 'A base URL is required for OpenAI-compatible providers' });
  }
  if (parsed.enabled) {
    if (!parsed.provider) issues.push({ path: 'provider', message: 'Choose a provider before enabling AI' });
    if (!parsed.model) issues.push({ path: 'model', message: 'Enter a model name before enabling AI' });
    if (!encryptedKey) issues.push({ path: 'apiKey', message: 'Add an API key before enabling AI' });
  }
  if (issues.length > 0) throw new ValidationError(issues);
  const ai: AiSettings = { provider: parsed.provider, model: parsed.model, baseUrl: parsed.baseUrl, encryptedKey, keyLast4, enabled: parsed.enabled };
  await db.update(businesses).set({ ai, updatedAt: now }).where(eq(businesses.id, business.id));
  return ai;
}

function decryptApiKey(ai: AiSettings, key: Buffer): string | null {
  if (!ai.encryptedKey) return null;
  try {
    return decryptSecret(ai.encryptedKey, key);
  } catch {
    return null;
  }
}

export function resolveReplyModel(business: BusinessRow, key: Buffer, factory: LanguageModelFactory = createLanguageModel): ReplyModel | null {
  const { ai } = business;
  if (!ai.enabled || !ai.provider || !ai.model) return null;
  const apiKey = decryptApiKey(ai, key);
  if (!apiKey) return null;
  try {
    return createAiReplyModel(factory({ provider: ai.provider, model: ai.model, baseUrl: ai.baseUrl }, apiKey));
  } catch {
    return null;
  }
}

export type AiTestResult = { ok: true } | { ok: false; error: string };

/** Works before AI is enabled so the owner can verify a key first. */
export async function testAiConnection(
  business: BusinessRow,
  key: Buffer,
  factory: LanguageModelFactory = createLanguageModel,
  timeoutMs = 10_000,
): Promise<AiTestResult> {
  const { ai } = business;
  if (!ai.provider || !ai.model) return { ok: false, error: 'Choose a provider and model first.' };
  const apiKey = decryptApiKey(ai, key);
  if (!apiKey) return { ok: false, error: 'No usable API key is saved.' };
  try {
    const model = factory({ provider: ai.provider, model: ai.model, baseUrl: ai.baseUrl }, apiKey);
    await generateText({ model, prompt: 'Reply with the single word OK.', maxOutputTokens: 16, maxRetries: 0, abortSignal: AbortSignal.timeout(timeoutMs) });
    return { ok: true };
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    return { ok: false, error: message.split(apiKey).join('***').slice(0, 300) };
  }
}
```

- [ ] **Step 5: Verify GREEN** — `npx vitest run apps/server` PASS; `npm run typecheck` exit 0.

- [ ] **Step 6: Commit**

```bash
git add apps/server/src/ai
git commit -m "feat(server): add AI SDK reply model adapter and encrypted BYOK settings"
```

---

### Task 7: Chat service (core engine orchestration and persistence)

**Files:**
- Create: `apps/server/src/services/chat.ts`
- Test: `apps/server/src/services/chat.test.ts`

**Interfaces:**
- Consumes: core `handleVisitorMessage`, `extractContact`, `EngineResult`, `HandoffReason`, `ConversationStatus`, `ReplyModel`; repos (business, conversations, bookings, audit); `contactFromFields`; `ValidationError`, `parseOrThrow`; `EventBus`; `KeyedMutex`; `randomToken`, `sha256`; `Notifier`.
- Produces:
  - `ChatDeps { db; bus; notifier; mutex; now; resolveModel(business): ReplyModel | null; modelTimeoutMs? }`
  - `MAX_VISITOR_TEXT = 1000`, `MAX_AGENT_TEXT = 2000`, `HARD_HANDOFFS`
  - `convTopic(id)`, `bizTopic(id)`
  - `PublicMessage { id; role: 'visitor'|'bot'|'agent'; content; createdAt: number }`, `toPublicMessage(row)`; `AdminMessage { id; role; content; meta; createdAt: number }`, `toAdminMessage(row)`; `ConversationSummary`, `conversationSummary(row)`
  - `startConversation(deps, business): Promise<{ conversation; visitorToken }>`
  - `handleVisitorText(deps, conversationId, text, localeHint?): Promise<VisitorTurn>`; `VisitorTurn = { messages: PublicMessage[]; status: ConversationStatus; notifications: Promise<void> }`
  - `submitOfflineContact(deps, conversationId, input: unknown): Promise<{ notifications: Promise<void> }>`
  - `agentReply(deps, { businessId, conversationId, userId, text }): Promise<MessageRow | null>`
  - `setConversationStatus(deps, { businessId, conversationId, action: 'takeover'|'release'|'close', userId }): Promise<ConversationRow | null>`
  - `markConversationRead(deps, businessId, conversationId): Promise<boolean>`
- Events (consumed by SSE in Task 11):
  - topic `conv:<id>`: `{ type: 'message', id: String(messageId), data: PublicMessage }` (never `system` messages); `{ type: 'status', data: { status } }` when the status changes
  - topic `biz:<businessId>`: `{ type: 'message', id, data: { conversationId, message: AdminMessage } }`; `{ type: 'conversation', data: ConversationSummary }`; `{ type: 'booking', data: BookingRow }`

Behavior rules:
- All work for one conversation runs inside `deps.mutex.run(conversationId, ...)`.
- A `closed` conversation reopens as `bot` when the visitor writes again.
- Handoff: hard (`visitor_request`, `message_limit`) → `status = 'human'`; soft → status unchanged. Any handoff sets `needsAttention = true`, `attentionReason = reason`. A visitor message while `human` also sets `needsAttention` (reason `visitor_message` unless one exists). The owner is emailed about a handoff only when the conversation did not already need attention (avoids email floods).
- All writes of one turn go into one `db.batch` (contact, booking, messages, conversation update, audit). Core runs before the batch; notifications after it.
- Only the first bot reply of a turn stores `meta` (`engine`, optional `fallbackReason`, `handoff`, `knowledgeTruncated`).

- [ ] **Step 1: Write failing tests** — `apps/server/src/services/chat.test.ts`:
```ts
import type { ModelInput, ReplyModel } from '@reception/core';
import { eq } from 'drizzle-orm';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import type { Database } from '../db/client';
import { bookingRequests, contacts, conversations } from '../db/schema';
import { ValidationError } from '../domain/validation';
import { createEventBus, type BusEvent, type EventBus } from '../lib/bus';
import { createKeyedMutex } from '../lib/mutex';
import { createBusiness, type BusinessRow } from '../repos/business';
import { getConversation, listMessages } from '../repos/conversations';
import { seedDemoBusiness, testDatabase } from '../testing/fixtures';
import {
  agentReply,
  bizTopic,
  convTopic,
  handleVisitorText,
  markConversationRead,
  setConversationStatus,
  startConversation,
  submitOfflineContact,
  type ChatDeps,
} from './chat';
import type { Notifier } from './notifier';

let database: Database;
let business: BusinessRow;
let bus: EventBus;
let calls: { kind: string; input: unknown }[];
let model: ReplyModel | null;
const now = new Date('2026-10-11T10:00:00Z');

function deps(): ChatDeps {
  const notifier: Notifier = {
    async bookingCreated(input) {
      calls.push({ kind: 'booking', input });
    },
    async handoff(input) {
      calls.push({ kind: 'handoff', input });
    },
    async offlineContact(input) {
      calls.push({ kind: 'offline', input });
    },
  };
  return { db: database.db, bus, notifier, mutex: createKeyedMutex(), now: () => now, resolveModel: () => model };
}

beforeEach(async () => {
  database = await testDatabase();
  business = await seedDemoBusiness(database.db);
  bus = createEventBus();
  calls = [];
  model = null;
});
afterEach(() => database.close());

function capture(topic: string): BusEvent[] {
  const events: BusEvent[] = [];
  bus.subscribe(topic, (e) => events.push(e));
  return events;
}

async function start() {
  const d = deps();
  const { conversation, visitorToken } = await startConversation(d, business);
  return { d, id: conversation.id, visitorToken };
}

describe('handleVisitorText', () => {
  it('answers with rules, stores both messages and publishes events', async () => {
    const { d, id } = await start();
    const convEvents = capture(convTopic(id));
    const bizEvents = capture(bizTopic(business.id));
    const turn = await handleVisitorText(d, id, '  What are your hours?  ');
    expect(turn.messages.map((m) => m.role)).toEqual(['visitor', 'bot']);
    expect(turn.messages[0]!.content).toBe('What are your hours?');
    expect(turn.messages[1]!.content).toMatch(/^Our hours:/);
    expect(turn.status).toBe('bot');
    const conv = (await getConversation(database.db, id))!;
    expect(conv).toMatchObject({ visitorMessageCount: 1, unreadCount: 1, locale: 'en', needsAttention: false });
    expect(convEvents.filter((e) => e.type === 'message').map((e) => e.id)).toEqual(turn.messages.map((m) => String(m.id)));
    expect(bizEvents.map((e) => e.type)).toEqual(['message', 'message', 'conversation']);
    const [, bot] = await listMessages(database.db, id);
    expect(bot!.meta).toEqual({ engine: 'rules', fallbackReason: 'no_model' });
  });

  it('persists the booking draft between turns and creates a pending booking with a contact', async () => {
    const { d, id } = await start();
    for (const text of ['I want to book a deep cleaning', 'Saturday morning', 'Alex']) await handleVisitorText(d, id, text);
    expect((await getConversation(database.db, id))!.bookingDraft?.fields).toEqual({
      service: 'Deep cleaning',
      preferred_time: 'Saturday morning',
      name: 'Alex',
    });
    const bizEvents = capture(bizTopic(business.id));
    const turn = await handleVisitorText(d, id, 'alex@example.com');
    await turn.notifications;
    expect(turn.messages[1]!.content).toContain('pending confirmation');
    const [booking] = await database.db.select().from(bookingRequests).where(eq(bookingRequests.conversationId, id));
    expect(booking).toMatchObject({
      status: 'pending',
      fields: { service: 'Deep cleaning', preferred_time: 'Saturday morning', name: 'Alex', contact: 'alex@example.com' },
    });
    const conv = (await getConversation(database.db, id))!;
    expect(conv.bookingDraft).toBeNull();
    const [contact] = await database.db.select().from(contacts).where(eq(contacts.id, conv.contactId!));
    expect(contact).toMatchObject({ name: 'Alex', email: 'alex@example.com', phone: null });
    expect(calls.filter((c) => c.kind === 'booking')).toHaveLength(1);
    expect(bizEvents.some((e) => e.type === 'booking')).toBe(true);
  });

  it('soft handoff keeps the bot active and notifies once', async () => {
    const { d, id } = await start();
    const turn = await handleVisitorText(d, id, 'Do you clean swimming pools?');
    await turn.notifications;
    expect(turn.status).toBe('bot');
    expect((await getConversation(database.db, id))!).toMatchObject({ needsAttention: true, attentionReason: 'unknown_question' });
    const second = await handleVisitorText(d, id, 'Do you clean boats?');
    await second.notifications;
    expect(second.messages.map((m) => m.role)).toEqual(['visitor', 'bot']);
    expect(calls.filter((c) => c.kind === 'handoff')).toHaveLength(1);
  });

  it('hard handoff silences the bot until released', async () => {
    const { d, id } = await start();
    const statusEvents = capture(convTopic(id));
    const turn = await handleVisitorText(d, id, 'I want to talk to a real person');
    expect(turn.status).toBe('human');
    expect(statusEvents.some((e) => e.type === 'status')).toBe(true);
    const silent = await handleVisitorText(d, id, 'hello?');
    expect(silent.messages.map((m) => m.role)).toEqual(['visitor']);
    expect((await getConversation(database.db, id))!.needsAttention).toBe(true);
    await setConversationStatus(d, { businessId: business.id, conversationId: id, action: 'release', userId: 'u1' });
    const back = await handleVisitorText(d, id, 'What are your hours?');
    expect(back.messages.map((m) => m.role)).toEqual(['visitor', 'bot']);
  });

  it('passes the count excluding the current message to the engine', async () => {
    const { d, id } = await start();
    await database.db.update(conversations).set({ visitorMessageCount: 99 }).where(eq(conversations.id, id));
    const ok = await handleVisitorText(d, id, 'What are your hours?');
    expect(ok.status).toBe('bot');
    const limited = await handleVisitorText(d, id, 'What are your hours?');
    expect(limited.status).toBe('human');
    expect((await getConversation(database.db, id))!.attentionReason).toBe('message_limit');
  });

  it('serializes concurrent messages in one conversation', async () => {
    const { d, id } = await start();
    await Promise.all([handleVisitorText(d, id, 'What are your hours?'), handleVisitorText(d, id, 'Do you bring supplies?')]);
    const rows = await listMessages(database.db, id);
    expect(rows.map((m) => m.role)).toEqual(['visitor', 'bot', 'visitor', 'bot']);
    expect((await getConversation(database.db, id))!.visitorMessageCount).toBe(2);
  });

  it('validates message text', async () => {
    const { d, id } = await start();
    await expect(handleVisitorText(d, id, '   ')).rejects.toBeInstanceOf(ValidationError);
    await expect(handleVisitorText(d, id, 'x'.repeat(1001))).rejects.toBeInstanceOf(ValidationError);
  });

  it('uses the AI model, records fallbacks and never sends system messages to it', async () => {
    const inputs: ModelInput[] = [];
    model = {
      async generate(input) {
        inputs.push(input);
        return { text: 'AI answer', actions: [] };
      },
    };
    const { d, id } = await start();
    await submitOfflineContact(d, id, { email: 'a@example.com', note: 'call me' });
    const turn = await handleVisitorText(d, id, 'Tell me about deep cleaning');
    expect(turn.messages[1]!.content).toBe('AI answer');
    expect(JSON.stringify(inputs[0]!.messages)).not.toContain('Contact details');
    model = {
      async generate() {
        throw new Error('401');
      },
    };
    await handleVisitorText(d, id, 'What are your hours?');
    const bot = (await listMessages(database.db, id)).filter((m) => m.role === 'bot').at(-1)!;
    expect(bot.meta).toEqual({ engine: 'rules', fallbackReason: 'model_error' });
  });
});

describe('offline contact', () => {
  it('stores the contact, flags the conversation and notifies', async () => {
    const { d, id } = await start();
    const res = await submitOfflineContact(d, id, { name: 'Alex', phone: '+1 202 555 0123', note: 'evenings' });
    await res.notifications;
    const conv = (await getConversation(database.db, id))!;
    expect(conv).toMatchObject({ needsAttention: true, attentionReason: 'offline_contact' });
    const [contact] = await database.db.select().from(contacts).where(eq(contacts.id, conv.contactId!));
    expect(contact).toMatchObject({ name: 'Alex', phone: '+1 202 555 0123' });
    expect((await listMessages(database.db, id)).map((m) => m.role)).toEqual(['system']);
    expect(calls.map((c) => c.kind)).toEqual(['offline']);
  });

  it('requires an email or a valid phone number', async () => {
    const { d, id } = await start();
    await expect(submitOfflineContact(d, id, { name: 'Alex' })).rejects.toBeInstanceOf(ValidationError);
    await expect(submitOfflineContact(d, id, { phone: 'abc' })).rejects.toBeInstanceOf(ValidationError);
    await expect(submitOfflineContact(d, id, { email: 'not-an-email' })).rejects.toBeInstanceOf(ValidationError);
  });
});

describe('agent actions', () => {
  it('agent reply takes over, clears attention and resets unread', async () => {
    const { d, id } = await start();
    await handleVisitorText(d, id, 'Do you clean boats?');
    const events = capture(convTopic(id));
    const msg = await agentReply(d, { businessId: business.id, conversationId: id, userId: 'u1', text: 'Hi, Sam here!' });
    expect(msg).toMatchObject({ role: 'agent', content: 'Hi, Sam here!' });
    expect((await getConversation(database.db, id))!).toMatchObject({ status: 'human', needsAttention: false, unreadCount: 0 });
    expect(events.map((e) => e.type)).toEqual(['message', 'status']);
  });

  it('is scoped by business', async () => {
    const { d, id } = await start();
    const other = await createBusiness(database.db, { slug: 'other-biz', name: 'Other' });
    expect(await agentReply(d, { businessId: other.id, conversationId: id, userId: 'u', text: 'x' })).toBeNull();
    expect(await setConversationStatus(d, { businessId: other.id, conversationId: id, action: 'close', userId: 'u' })).toBeNull();
    expect(await markConversationRead(d, other.id, id)).toBe(false);
  });

  it('closes and reopens on a new visitor message', async () => {
    const { d, id } = await start();
    const closed = await setConversationStatus(d, { businessId: business.id, conversationId: id, action: 'close', userId: 'u' });
    expect(closed?.status).toBe('closed');
    const turn = await handleVisitorText(d, id, 'What are your hours?');
    expect(turn.status).toBe('bot');
    expect(turn.messages).toHaveLength(2);
  });

  it('validates agent text length', async () => {
    const { d, id } = await start();
    await expect(agentReply(d, { businessId: business.id, conversationId: id, userId: 'u', text: ' ' })).rejects.toBeInstanceOf(ValidationError);
    await expect(agentReply(d, { businessId: business.id, conversationId: id, userId: 'u', text: 'x'.repeat(2001) })).rejects.toBeInstanceOf(
      ValidationError,
    );
  });
});
```

- [ ] **Step 2: Run to verify RED** — `npx vitest run apps/server/src/services/chat.test.ts` → FAIL.

- [ ] **Step 3: Implement `apps/server/src/services/chat.ts`**

```ts
import {
  extractContact,
  handleVisitorMessage,
  type ConversationStatus,
  type EngineResult,
  type HandoffReason,
  type ReplyModel,
} from '@reception/core';
import { and, eq, sql } from 'drizzle-orm';
import type { BatchItem } from 'drizzle-orm/batch';
import { z } from 'zod';
import type { Db } from '../db/client';
import { bookingRequests, contacts, conversations, messages, type MessageRole } from '../db/schema';
import { contactFromFields } from '../domain/contact';
import { parseOrThrow, ValidationError } from '../domain/validation';
import type { EventBus } from '../lib/bus';
import { randomToken, sha256 } from '../lib/crypto';
import type { KeyedMutex } from '../lib/mutex';
import { auditInsert } from '../repos/audit';
import { getBookingForBusiness } from '../repos/bookings';
import { getBusinessById, loadKnowledge, type BusinessRow } from '../repos/business';
import {
  getConversation,
  getConversationForBusiness,
  insertConversation,
  lastMessageId,
  listMessages,
  recentHistory,
  type ConversationRow,
  type MessageRow,
} from '../repos/conversations';
import type { Notifier } from './notifier';

export interface ChatDeps {
  db: Db;
  bus: EventBus;
  notifier: Notifier;
  mutex: KeyedMutex;
  now: () => Date;
  resolveModel: (business: BusinessRow) => ReplyModel | null;
  modelTimeoutMs?: number;
}

export const MAX_VISITOR_TEXT = 1000;
export const MAX_AGENT_TEXT = 2000;
export const HARD_HANDOFFS: ReadonlySet<HandoffReason> = new Set<HandoffReason>(['visitor_request', 'message_limit']);

export const convTopic = (conversationId: string) => `conv:${conversationId}`;
export const bizTopic = (businessId: string) => `biz:${businessId}`;

type Batch = [BatchItem<'sqlite'>, ...BatchItem<'sqlite'>[]];

export interface PublicMessage {
  id: number;
  role: 'visitor' | 'bot' | 'agent';
  content: string;
  createdAt: number;
}

export interface AdminMessage {
  id: number;
  role: MessageRole;
  content: string;
  meta: Record<string, unknown> | null;
  createdAt: number;
}

export interface ConversationSummary {
  id: string;
  status: ConversationStatus;
  needsAttention: boolean;
  attentionReason: string | null;
  unreadCount: number;
  locale: string | null;
  contactId: string | null;
  lastMessageAt: number;
  createdAt: number;
}

export interface VisitorTurn {
  messages: PublicMessage[];
  status: ConversationStatus;
  notifications: Promise<void>;
}

export function toPublicMessage(m: MessageRow): PublicMessage {
  return { id: m.id, role: m.role as PublicMessage['role'], content: m.content, createdAt: m.createdAt.getTime() };
}

export function toAdminMessage(m: MessageRow): AdminMessage {
  return { id: m.id, role: m.role, content: m.content, meta: m.meta ?? null, createdAt: m.createdAt.getTime() };
}

export function conversationSummary(c: ConversationRow): ConversationSummary {
  return {
    id: c.id,
    status: c.status,
    needsAttention: c.needsAttention,
    attentionReason: c.attentionReason,
    unreadCount: c.unreadCount,
    locale: c.locale,
    contactId: c.contactId,
    lastMessageAt: c.lastMessageAt.getTime(),
    createdAt: c.createdAt.getTime(),
  };
}

function publishMessages(deps: ChatDeps, conv: ConversationRow, rows: MessageRow[]): void {
  for (const m of rows) {
    if (m.role !== 'system') deps.bus.publish(convTopic(conv.id), { type: 'message', id: String(m.id), data: toPublicMessage(m) });
    deps.bus.publish(bizTopic(conv.businessId), { type: 'message', id: String(m.id), data: { conversationId: conv.id, message: toAdminMessage(m) } });
  }
}

function publishConversation(deps: ChatDeps, conv: ConversationRow, statusChanged: boolean): void {
  if (statusChanged) deps.bus.publish(convTopic(conv.id), { type: 'status', data: { status: conv.status } });
  deps.bus.publish(bizTopic(conv.businessId), { type: 'conversation', data: conversationSummary(conv) });
}

function botMeta(result: EngineResult): Record<string, unknown> {
  return {
    engine: result.meta.engine,
    ...(result.meta.fallbackReason ? { fallbackReason: result.meta.fallbackReason } : {}),
    ...(result.handoff ? { handoff: result.handoff.reason } : {}),
    ...(result.meta.knowledgeTruncated ? { knowledgeTruncated: true } : {}),
  };
}

export async function startConversation(deps: ChatDeps, business: BusinessRow): Promise<{ conversation: ConversationRow; visitorToken: string }> {
  const visitorToken = randomToken();
  const conversation = await insertConversation(deps.db, {
    businessId: business.id,
    visitorTokenHash: sha256(visitorToken),
    locale: null,
    now: deps.now(),
  });
  publishConversation(deps, conversation, false);
  return { conversation, visitorToken };
}

export async function handleVisitorText(deps: ChatDeps, conversationId: string, rawText: string, localeHint?: string | null): Promise<VisitorTurn> {
  const text = rawText.trim();
  if (!text) throw new ValidationError([{ path: 'text', message: 'Message is empty' }]);
  if (text.length > MAX_VISITOR_TEXT) {
    throw new ValidationError([{ path: 'text', message: `Messages are limited to ${MAX_VISITOR_TEXT} characters` }]);
  }

  return deps.mutex.run(conversationId, async () => {
    const conv = await getConversation(deps.db, conversationId);
    if (!conv) throw new ValidationError([{ path: 'conversationId', message: 'Unknown conversation' }]);
    const business = await getBusinessById(deps.db, conv.businessId);
    if (!business) throw new Error(`Business ${conv.businessId} not found`);
    const [knowledge, history, previousId] = await Promise.all([
      loadKnowledge(deps.db, business),
      recentHistory(deps.db, conv.id),
      lastMessageId(deps.db, conv.id),
    ]);

    const status: ConversationStatus = conv.status === 'closed' ? 'bot' : conv.status;
    const now = deps.now();
    const result = await handleVisitorMessage(
      {
        business: knowledge,
        conversation: { status, locale: conv.locale, bookingDraft: conv.bookingDraft, visitorMessageCount: conv.visitorMessageCount },
        history,
        localeHint: localeHint ?? null,
        now,
      },
      text,
      { model: deps.resolveModel(business), ...(deps.modelTimeoutMs ? { timeoutMs: deps.modelTimeoutMs } : {}) },
    );

    const handoff = result.handoff ?? null;
    const nextStatus: ConversationStatus = handoff && HARD_HANDOFFS.has(handoff.reason) ? 'human' : status;
    const needsAttention = conv.needsAttention || handoff !== null || nextStatus === 'human';
    const attentionReason = handoff?.reason ?? (needsAttention ? (conv.attentionReason ?? 'visitor_message') : null);

    const statements: BatchItem<'sqlite'>[] = [];
    let contactId = conv.contactId;
    let bookingId: string | null = null;
    if (result.bookingRequest) {
      const contact = contactFromFields(knowledge.bookingFields, result.bookingRequest.fields);
      if (contactId) {
        statements.push(deps.db.update(contacts).set(contact).where(eq(contacts.id, contactId)));
      } else {
        contactId = crypto.randomUUID();
        statements.push(deps.db.insert(contacts).values({ id: contactId, businessId: business.id, ...contact, createdAt: now }));
      }
      bookingId = crypto.randomUUID();
      statements.push(
        deps.db.insert(bookingRequests).values({
          id: bookingId,
          businessId: business.id,
          conversationId: conv.id,
          contactId,
          fields: result.bookingRequest.fields,
          status: 'pending',
          createdAt: now,
        }),
      );
      statements.push(auditInsert(deps.db, { businessId: business.id, actor: 'visitor', action: 'booking_requested', target: bookingId, now }));
    }
    statements.push(deps.db.insert(messages).values({ conversationId: conv.id, businessId: business.id, role: 'visitor', content: text, createdAt: now }));
    result.replies.forEach((reply, index) => {
      statements.push(
        deps.db.insert(messages).values({
          conversationId: conv.id,
          businessId: business.id,
          role: 'bot',
          content: reply,
          meta: index === 0 ? botMeta(result) : null,
          createdAt: now,
        }),
      );
    });
    statements.push(
      deps.db
        .update(conversations)
        .set({
          status: nextStatus,
          needsAttention,
          attentionReason,
          locale: result.locale,
          bookingDraft: result.bookingDraft,
          contactId,
          visitorMessageCount: sql`${conversations.visitorMessageCount} + 1`,
          unreadCount: sql`${conversations.unreadCount} + 1`,
          lastMessageAt: now,
        })
        .where(eq(conversations.id, conv.id)),
    );
    if (handoff) {
      statements.push(
        auditInsert(deps.db, { businessId: business.id, actor: 'visitor', action: 'handoff', target: conv.id, data: { reason: handoff.reason }, now }),
      );
    }
    await deps.db.batch(statements as Batch);

    const [updated, newMessages] = await Promise.all([getConversation(deps.db, conv.id), listMessages(deps.db, conv.id, { afterId: previousId })]);
    if (!updated) throw new Error('Conversation disappeared during a turn');
    publishMessages(deps, updated, newMessages);
    publishConversation(deps, updated, updated.status !== conv.status);

    const pending: Promise<void>[] = [];
    if (bookingId) {
      const booking = await getBookingForBusiness(deps.db, business.id, bookingId);
      if (booking) {
        deps.bus.publish(bizTopic(business.id), { type: 'booking', data: booking });
        pending.push(deps.notifier.bookingCreated({ business, conversation: updated, booking }));
      }
    }
    if (handoff && !conv.needsAttention) {
      pending.push(deps.notifier.handoff({ business, conversation: updated, reason: handoff.reason, excerpt: text }));
    }
    return {
      messages: newMessages.filter((m) => m.role !== 'system').map(toPublicMessage),
      status: updated.status,
      notifications: Promise.all(pending).then(() => undefined),
    };
  });
}

const offlineContactSchema = z
  .object({
    name: z.string().trim().max(100).optional(),
    email: z.email().max(200).optional(),
    phone: z
      .string()
      .trim()
      .max(40)
      .refine((v) => !v.includes('@') && extractContact(v) !== null, { error: 'Enter a valid phone number' })
      .optional(),
    note: z.string().trim().max(500).optional(),
  })
  .strict()
  .refine((v) => Boolean(v.email || v.phone), { error: 'Provide an email address or phone number', path: ['email'] });

export async function submitOfflineContact(deps: ChatDeps, conversationId: string, input: unknown): Promise<{ notifications: Promise<void> }> {
  const parsed = parseOrThrow(offlineContactSchema, input);
  return deps.mutex.run(conversationId, async () => {
    const conv = await getConversation(deps.db, conversationId);
    if (!conv) throw new ValidationError([{ path: 'conversationId', message: 'Unknown conversation' }]);
    const business = await getBusinessById(deps.db, conv.businessId);
    if (!business) throw new Error(`Business ${conv.businessId} not found`);
    const now = deps.now();
    const previousId = await lastMessageId(deps.db, conv.id);
    const contact = { name: parsed.name || null, email: parsed.email ?? null, phone: parsed.phone || null };
    const provided = Object.fromEntries(Object.entries(contact).filter(([, v]) => v !== null));
    const statements: BatchItem<'sqlite'>[] = [];
    let contactId = conv.contactId;
    if (contactId) {
      statements.push(deps.db.update(contacts).set(provided).where(eq(contacts.id, contactId)));
    } else {
      contactId = crypto.randomUUID();
      statements.push(deps.db.insert(contacts).values({ id: contactId, businessId: business.id, ...contact, createdAt: now }));
    }
    const summary = [contact.name, contact.email, contact.phone].filter(Boolean).join(' · ');
    statements.push(
      deps.db.insert(messages).values({
        conversationId: conv.id,
        businessId: business.id,
        role: 'system',
        content: `Contact details left: ${summary}${parsed.note ? ` — ${parsed.note}` : ''}`,
        createdAt: now,
      }),
    );
    statements.push(
      deps.db
        .update(conversations)
        .set({ contactId, needsAttention: true, attentionReason: 'offline_contact', lastMessageAt: now })
        .where(eq(conversations.id, conv.id)),
    );
    statements.push(auditInsert(deps.db, { businessId: business.id, actor: 'visitor', action: 'offline_contact', target: conv.id, now }));
    await deps.db.batch(statements as Batch);

    const [updated, newMessages] = await Promise.all([getConversation(deps.db, conv.id), listMessages(deps.db, conv.id, { afterId: previousId })]);
    if (!updated) throw new Error('Conversation disappeared');
    publishMessages(deps, updated, newMessages);
    publishConversation(deps, updated, false);
    return { notifications: deps.notifier.offlineContact({ business, conversation: updated, contact, note: parsed.note ?? '' }) };
  });
}

export async function agentReply(
  deps: ChatDeps,
  input: { businessId: string; conversationId: string; userId: string; text: string },
): Promise<MessageRow | null> {
  const text = input.text.trim();
  if (!text) throw new ValidationError([{ path: 'text', message: 'Message is empty' }]);
  if (text.length > MAX_AGENT_TEXT) {
    throw new ValidationError([{ path: 'text', message: `Messages are limited to ${MAX_AGENT_TEXT} characters` }]);
  }
  return deps.mutex.run(input.conversationId, async () => {
    const conv = await getConversationForBusiness(deps.db, input.businessId, input.conversationId);
    if (!conv) return null;
    const now = deps.now();
    const previousId = await lastMessageId(deps.db, conv.id);
    await deps.db.batch([
      deps.db.insert(messages).values({
        conversationId: conv.id,
        businessId: conv.businessId,
        role: 'agent',
        content: text,
        meta: { userId: input.userId },
        createdAt: now,
      }),
      deps.db
        .update(conversations)
        .set({ status: 'human', needsAttention: false, attentionReason: null, unreadCount: 0, assignedUserId: input.userId, lastMessageAt: now })
        .where(eq(conversations.id, conv.id)),
      auditInsert(deps.db, { businessId: conv.businessId, actor: `user:${input.userId}`, action: 'agent_reply', target: conv.id, now }),
    ]);
    const [updated, newMessages] = await Promise.all([getConversation(deps.db, conv.id), listMessages(deps.db, conv.id, { afterId: previousId })]);
    if (!updated) return null;
    publishMessages(deps, updated, newMessages);
    publishConversation(deps, updated, updated.status !== conv.status);
    return newMessages.find((m) => m.role === 'agent') ?? null;
  });
}

const STATUS_ACTIONS = {
  takeover: (userId: string) => ({ status: 'human' as const, needsAttention: false, attentionReason: null, assignedUserId: userId }),
  release: () => ({ status: 'bot' as const, needsAttention: false, attentionReason: null, assignedUserId: null }),
  close: () => ({ status: 'closed' as const, needsAttention: false, attentionReason: null }),
};

export type StatusAction = keyof typeof STATUS_ACTIONS;

export async function setConversationStatus(
  deps: ChatDeps,
  input: { businessId: string; conversationId: string; action: StatusAction; userId: string },
): Promise<ConversationRow | null> {
  return deps.mutex.run(input.conversationId, async () => {
    const conv = await getConversationForBusiness(deps.db, input.businessId, input.conversationId);
    if (!conv) return null;
    await deps.db.batch([
      deps.db.update(conversations).set(STATUS_ACTIONS[input.action](input.userId)).where(eq(conversations.id, conv.id)),
      auditInsert(deps.db, {
        businessId: conv.businessId,
        actor: `user:${input.userId}`,
        action: `conversation_${input.action}`,
        target: conv.id,
        now: deps.now(),
      }),
    ]);
    const updated = await getConversation(deps.db, conv.id);
    if (!updated) return null;
    publishConversation(deps, updated, updated.status !== conv.status);
    return updated;
  });
}

export async function markConversationRead(deps: ChatDeps, businessId: string, conversationId: string): Promise<boolean> {
  const rows = await deps.db
    .update(conversations)
    .set({ unreadCount: 0 })
    .where(and(eq(conversations.id, conversationId), eq(conversations.businessId, businessId)))
    .returning({ id: conversations.id });
  return rows.length > 0;
}
```

> If `BatchItem` is not exported from `drizzle-orm/batch` in 0.45, locate it (`grep -r "export type BatchItem" node_modules/drizzle-orm`) and import from there — keep the batching design.
> The phone refine rejects `abc` and accepts `+1 202 555 0123` (core: ≥ 7 digits, not a date/time).

- [ ] **Step 4: Verify GREEN** — focused `npx vitest run apps/server/src/services/chat.test.ts`, then `npx vitest run`; `npm run typecheck` exit 0.

- [ ] **Step 5: Commit**

```bash
git add apps/server/src/services/chat.ts apps/server/src/services/chat.test.ts
git commit -m "feat(server): orchestrate conversations around the core engine with atomic writes"
```

---

### Task 8: HTTP foundation, users/sessions and auth routes

**Files:**
- Create: `apps/server/src/repos/users.ts`, `apps/server/src/http/types.ts`, `apps/server/src/http/deps.ts`, `apps/server/src/http/util.ts`, `apps/server/src/http/errors.ts`, `apps/server/src/http/middleware.ts`, `apps/server/src/http/routes/auth.ts`, `apps/server/src/http/app.ts`, `apps/server/src/testing/harness.ts`
- Test: `apps/server/src/http/routes/auth.test.ts`

**Interfaces:**
- Consumes: `Env`, `loadEnv`, `Db`, tables, crypto, limiter, bus, mutex, connections, `ChatDeps`, `createNotifier`, `createMemoryMailer`, `Mailer`, `createLanguageModel`, `LanguageModelFactory`, `resolveReplyModel`, `ValidationError`, `parseOrThrow`, `writeAudit`, `getBusinessById`, fixtures.
- Produces:
  - `UserRow`, `SESSION_TTL_MS` (7 days), `AuthContext { user: { id; email; role }; businessId; sessionId }`, `createUser(db, { businessId, email, password, role? })`, `findUserByEmail(db, email)`, `createSession(db, userId, now): Promise<{ token; expiresAt }>`, `getSessionAuth(db, token, now): Promise<AuthContext | null>`, `deleteSession(db, token)`
  - `AppEnv` (`Variables: { auth; business; conversation }`)
  - `Limiters`, `createLimiters()`, `AppDeps extends ChatDeps { env; secretKey; limiters; sse; heartbeatMs; languageModelFactory }`, `createAppDeps(input)`
  - `readJson(c, opts?)`, `clientIp(c, trustProxy)`, `parseEventId(value)`
  - `handleError(err, c)`
  - `SESSION_COOKIE = 'rs_session'`, `requireSession(deps)`, `requireSameOrigin(deps)`, `sessionCookieOptions(env, maxAgeSeconds)`
  - `authRoutes(deps)`, `createApp(deps)`
  - `createTestHarness(opts?)` → `{ database, deps, app, business, owner, password, mailer, env, clock, request(path, init?, ip?), login(), adminHeaders(cookie, extra?), close() }`

- [ ] **Step 1: Write failing tests** — `apps/server/src/http/routes/auth.test.ts`:
```ts
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
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
    const res = await secure.request('/api/auth/login', json({ email: 'owner@harbor.example', password: secure.password }, 'https://chat.example.com'));
    expect(res.headers.get('set-cookie')).toContain('Secure');
    secure.close();
  });

  it('serves a health check', async () => {
    expect(await (await h.request('/api/health')).json()).toEqual({ ok: true });
  });
});
```

- [ ] **Step 2: Run to verify RED** — `npx vitest run apps/server/src/http` → FAIL.

- [ ] **Step 3: Implement `repos/users.ts`**

```ts
import { eq } from 'drizzle-orm';
import type { Db } from '../db/client';
import { sessions, users } from '../db/schema';
import { ValidationError } from '../domain/validation';
import { hashPassword, randomToken, sha256 } from '../lib/crypto';

export type UserRow = typeof users.$inferSelect;
export const SESSION_TTL_MS = 7 * 24 * 60 * 60 * 1000;
export const MIN_PASSWORD_LENGTH = 10;

export interface AuthContext {
  user: { id: string; email: string; role: UserRow['role'] };
  businessId: string;
  /** sha256 of the session token */
  sessionId: string;
}

export async function createUser(
  db: Db,
  input: { businessId: string; email: string; password: string; role?: UserRow['role'] },
): Promise<UserRow> {
  if (input.password.length < MIN_PASSWORD_LENGTH) {
    throw new ValidationError([{ path: 'password', message: `Use at least ${MIN_PASSWORD_LENGTH} characters` }]);
  }
  const [row] = await db
    .insert(users)
    .values({
      businessId: input.businessId,
      email: input.email.trim().toLowerCase(),
      passwordHash: await hashPassword(input.password),
      role: input.role ?? 'owner',
    })
    .returning();
  return row!;
}

export async function findUserByEmail(db: Db, email: string): Promise<UserRow | null> {
  const [row] = await db.select().from(users).where(eq(users.email, email.trim().toLowerCase())).limit(1);
  return row ?? null;
}

export async function createSession(db: Db, userId: string, now: Date): Promise<{ token: string; expiresAt: Date }> {
  const token = randomToken();
  const expiresAt = new Date(now.getTime() + SESSION_TTL_MS);
  await db.insert(sessions).values({ id: sha256(token), userId, expiresAt, createdAt: now });
  return { token, expiresAt };
}

export async function getSessionAuth(db: Db, token: string, now: Date): Promise<AuthContext | null> {
  if (!token) return null;
  const id = sha256(token);
  const [row] = await db
    .select({ session: sessions, user: users })
    .from(sessions)
    .innerJoin(users, eq(users.id, sessions.userId))
    .where(eq(sessions.id, id))
    .limit(1);
  if (!row) return null;
  if (row.session.expiresAt.getTime() <= now.getTime()) {
    await db.delete(sessions).where(eq(sessions.id, id));
    return null;
  }
  return { user: { id: row.user.id, email: row.user.email, role: row.user.role }, businessId: row.user.businessId, sessionId: id };
}

export async function deleteSession(db: Db, token: string): Promise<void> {
  await db.delete(sessions).where(eq(sessions.id, sha256(token)));
}
```

- [ ] **Step 4: Implement the HTTP foundation**

`apps/server/src/http/types.ts`:
```ts
import type { BusinessRow } from '../repos/business';
import type { ConversationRow } from '../repos/conversations';
import type { AuthContext } from '../repos/users';

export interface AppEnv {
  Variables: {
    auth: AuthContext;
    business: BusinessRow;
    conversation: ConversationRow;
  };
}
```

`apps/server/src/http/deps.ts`:
```ts
import type { ReplyModel } from '@reception/core';
import { createLanguageModel, type LanguageModelFactory } from '../ai/providers';
import { resolveReplyModel } from '../ai/settings';
import type { Db } from '../db/client';
import type { Env } from '../env';
import { createEventBus } from '../lib/bus';
import { createConnectionRegistry, type ConnectionRegistry } from '../lib/connections';
import { deriveKey } from '../lib/crypto';
import { createKeyedMutex } from '../lib/mutex';
import { createRateLimiter, type RateLimiter } from '../lib/rateLimit';
import type { Mailer } from '../mail/mailer';
import type { BusinessRow } from '../repos/business';
import type { ChatDeps } from '../services/chat';
import { createNotifier } from '../services/notifier';

export interface Limiters {
  login: RateLimiter;
  visitorMessage: RateLimiter;
  ipMessage: RateLimiter;
  newConversation: RateLimiter;
  contact: RateLimiter;
}

export function createLimiters(): Limiters {
  return {
    login: createRateLimiter(5, 5 * 60_000),
    visitorMessage: createRateLimiter(20, 60_000),
    ipMessage: createRateLimiter(60, 60_000),
    newConversation: createRateLimiter(20, 60 * 60_000),
    contact: createRateLimiter(5, 60 * 60_000),
  };
}

export const MAX_STREAMS_PER_KEY = 3;
export const HEARTBEAT_MS = 25_000;

export interface AppDeps extends ChatDeps {
  env: Env;
  secretKey: Buffer;
  limiters: Limiters;
  sse: ConnectionRegistry;
  heartbeatMs: number;
  languageModelFactory: LanguageModelFactory;
}

export interface CreateAppDepsInput {
  env: Env;
  db: Db;
  mailer: Mailer | null;
  now?: () => Date;
  languageModelFactory?: LanguageModelFactory;
  resolveModel?: (business: BusinessRow) => ReplyModel | null;
  heartbeatMs?: number;
  modelTimeoutMs?: number;
}

export function createAppDeps(input: CreateAppDepsInput): AppDeps {
  const now = input.now ?? (() => new Date());
  const secretKey = deriveKey(input.env.APP_SECRET, 'ai-api-keys');
  const languageModelFactory = input.languageModelFactory ?? createLanguageModel;
  return {
    env: input.env,
    db: input.db,
    bus: createEventBus(),
    notifier: createNotifier({ db: input.db, mailer: input.mailer, publicOrigin: input.env.PUBLIC_ORIGIN, now }),
    mutex: createKeyedMutex(),
    now,
    secretKey,
    languageModelFactory,
    resolveModel: input.resolveModel ?? ((business) => resolveReplyModel(business, secretKey, languageModelFactory)),
    limiters: createLimiters(),
    sse: createConnectionRegistry(MAX_STREAMS_PER_KEY),
    heartbeatMs: input.heartbeatMs ?? HEARTBEAT_MS,
    ...(input.modelTimeoutMs ? { modelTimeoutMs: input.modelTimeoutMs } : {}),
  };
}
```

`apps/server/src/http/util.ts`:
```ts
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
```

`apps/server/src/http/errors.ts`:
```ts
import type { Context } from 'hono';
import { HTTPException } from 'hono/http-exception';
import { ValidationError } from '../domain/validation';

export function handleError(err: Error, c: Context): Response {
  if (err instanceof HTTPException) return c.json({ error: err.message || 'http_error' }, err.status);
  if (err instanceof ValidationError) return c.json({ error: 'validation', issues: err.issues }, 422);
  console.error(err);
  return c.json({ error: 'internal' }, 500);
}
```

`apps/server/src/http/middleware.ts`:
```ts
import type { MiddlewareHandler } from 'hono';
import { getCookie } from 'hono/cookie';
import type { Env } from '../env';
import { getSessionAuth } from '../repos/users';
import type { AppDeps } from './deps';
import type { AppEnv } from './types';

export const SESSION_COOKIE = 'rs_session';
const SAFE_METHODS = new Set(['GET', 'HEAD', 'OPTIONS']);

export function sessionCookieOptions(env: Env, maxAgeSeconds: number) {
  return {
    httpOnly: true,
    sameSite: 'Lax' as const,
    secure: env.PUBLIC_ORIGIN.startsWith('https://'),
    path: '/',
    maxAge: maxAgeSeconds,
  };
}

/** CSRF defense in addition to SameSite=Lax: state-changing admin requests must come from the admin origin. */
export function requireSameOrigin(deps: AppDeps): MiddlewareHandler<AppEnv> {
  return async (c, next) => {
    if (SAFE_METHODS.has(c.req.method)) return next();
    if (c.req.header('origin') !== deps.env.PUBLIC_ORIGIN) return c.json({ error: 'bad_origin' }, 403);
    return next();
  };
}

export function requireSession(deps: AppDeps): MiddlewareHandler<AppEnv> {
  return async (c, next) => {
    const auth = await getSessionAuth(deps.db, getCookie(c, SESSION_COOKIE) ?? '', deps.now());
    if (!auth) return c.json({ error: 'unauthorized' }, 401);
    c.set('auth', auth);
    return next();
  };
}
```

`apps/server/src/http/routes/auth.ts`:
```ts
import { Hono } from 'hono';
import { deleteCookie, getCookie, setCookie } from 'hono/cookie';
import { z } from 'zod';
import { parseOrThrow } from '../../domain/validation';
import { hashPassword, verifyPassword } from '../../lib/crypto';
import { writeAudit } from '../../repos/audit';
import { getBusinessById } from '../../repos/business';
import { createSession, deleteSession, findUserByEmail, SESSION_TTL_MS } from '../../repos/users';
import type { AppDeps } from '../deps';
import { requireSameOrigin, requireSession, SESSION_COOKIE, sessionCookieOptions } from '../middleware';
import type { AppEnv } from '../types';
import { clientIp, readJson } from '../util';

const loginSchema = z.object({ email: z.email().max(200), password: z.string().min(1).max(200) }).strict();

let dummyHash: Promise<string> | null = null;
/** Verifying against a dummy hash keeps response time similar for unknown emails. */
const getDummyHash = () => (dummyHash ??= hashPassword('dummy-password-for-timing'));

export function authRoutes(deps: AppDeps) {
  const app = new Hono<AppEnv>();
  app.use('*', requireSameOrigin(deps));

  app.post('/login', async (c) => {
    const body = parseOrThrow(loginSchema, await readJson(c));
    const email = body.email.toLowerCase();
    if (!deps.limiters.login.hit(`${clientIp(c, deps.env.TRUST_PROXY)}|${email}`)) {
      return c.json({ error: 'too_many_attempts' }, 429);
    }
    const user = await findUserByEmail(deps.db, email);
    const valid = await verifyPassword(body.password, user?.passwordHash ?? (await getDummyHash()));
    if (!user || !valid) return c.json({ error: 'invalid_credentials' }, 401);
    const now = deps.now();
    const { token } = await createSession(deps.db, user.id, now);
    setCookie(c, SESSION_COOKIE, token, sessionCookieOptions(deps.env, SESSION_TTL_MS / 1000));
    await writeAudit(deps.db, { businessId: user.businessId, actor: `user:${user.id}`, action: 'login', now });
    const business = await getBusinessById(deps.db, user.businessId);
    return c.json({
      user: { id: user.id, email: user.email, role: user.role },
      business: business ? { id: business.id, slug: business.slug, name: business.name } : null,
    });
  });

  app.post('/logout', async (c) => {
    const token = getCookie(c, SESSION_COOKIE);
    if (token) await deleteSession(deps.db, token);
    deleteCookie(c, SESSION_COOKIE, { path: '/' });
    return c.json({ ok: true });
  });

  app.get('/me', requireSession(deps), async (c) => {
    const auth = c.get('auth');
    const business = await getBusinessById(deps.db, auth.businessId);
    return c.json({ user: auth.user, business: business ? { id: business.id, slug: business.slug, name: business.name } : null });
  });

  return app;
}
```

`apps/server/src/http/app.ts`:
```ts
import { Hono } from 'hono';
import { bodyLimit } from 'hono/body-limit';
import type { AppDeps } from './deps';
import { handleError } from './errors';
import { authRoutes } from './routes/auth';
import type { AppEnv } from './types';

export const MAX_BODY_BYTES = 32 * 1024;

export function createApp(deps: AppDeps): Hono<AppEnv> {
  const app = new Hono<AppEnv>();
  app.onError(handleError);
  app.use('/api/*', bodyLimit({ maxSize: MAX_BODY_BYTES, onError: (c) => c.json({ error: 'payload_too_large' }, 413) }));
  app.get('/api/health', (c) => c.json({ ok: true }));
  app.route('/api/auth', authRoutes(deps));
  return app;
}
```

`apps/server/src/testing/harness.ts`:
```ts
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
    mailer,
    now: () => clock.now,
    resolveModel: opts.resolveModel ?? (() => null),
    ...(opts.languageModelFactory ? { languageModelFactory: opts.languageModelFactory } : {}),
    ...(opts.heartbeatMs ? { heartbeatMs: opts.heartbeatMs } : {}),
  });
  const app = createApp(deps);
  // getConnInfo reads the node socket from bindings; app.request needs it passed explicitly.
  const request = async (path: string, init: RequestInit = {}, ip = '203.0.113.7') =>
    app.request(path, init, { incoming: { socket: { remoteAddress: ip } } } as never);
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
```

- [ ] **Step 5: Verify GREEN** — `npx vitest run apps/server` PASS; `npm run typecheck` exit 0.

- [ ] **Step 6: Commit**

```bash
git add apps/server/src/repos/users.ts apps/server/src/http apps/server/src/testing/harness.ts
git commit -m "feat(server): add HTTP foundation, DB sessions and admin auth routes"
```

---

### Task 9: Widget API routes

**Files:**
- Create: `apps/server/src/http/routes/widget.ts`
- Modify: `apps/server/src/http/app.ts` (register `/api/widget`)
- Test: `apps/server/src/http/routes/widget.test.ts`

**Interfaces:**
- Consumes: `AppDeps`, `AppEnv`, `readJson`, `clientIp`, `parseEventId`, `getBusinessBySlug`, `getBusinessById`, `listFaqs`, `getConversation`, `listMessages`, `verifyVisitorToken`, `VISITOR_VISIBLE_ROLES`, chat service (`startConversation`, `handleVisitorText`, `submitOfflineContact`, `toPublicMessage`, `MAX_VISITOR_TEXT`).
- Produces (paths are under `/api/widget`; **deviation from the spec's paths**, which collided — Plan 3's widget uses these):
  - `GET  /b/:slug/config` → `{ business: { name, slug }, locale, widget: { primaryColor, position, greeting }, quickQuestions: string[] (≤ 4) }`
  - `POST /b/:slug/conversations` → 201 `{ conversationId, visitorToken }`
  - `GET  /c/:id/messages?since=<id>` (Bearer) → `{ messages: PublicMessage[], status }`
  - `POST /c/:id/messages` `{ text, locale? }` (Bearer) → `{ messages: PublicMessage[], status }`
  - `POST /c/:id/contact` `{ name?, email?, phone?, note? }` (Bearer) → `{ ok: true }`
  - `widgetRoutes(deps)`; `corsGuard(deps)` (exported for Task 11)
- CORS: a request with an `Origin` header must match `business.widget.allowedOrigins` or `PUBLIC_ORIGIN`, else 403; allowed origins are echoed with `Vary: Origin`, methods `GET, POST, OPTIONS`, headers `Content-Type, Authorization, Last-Event-ID`, max-age 600. `OPTIONS` → 204 (before auth). Requests without `Origin` (non-browser) pass CORS but still need the Bearer token on `/c/*`.

- [ ] **Step 1: Write failing tests** — `apps/server/src/http/routes/widget.test.ts`:
```ts
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { getConversation } from '../../repos/conversations';
import { createTestHarness, type TestHarness } from '../../testing/harness';

let h: TestHarness;
beforeEach(async () => {
  h = await createTestHarness();
});
afterEach(() => h.close());

const ORIGIN = 'https://harbor.example';

async function startChat(ip?: string) {
  const res = await h.request('/api/widget/b/harbor-home/conversations', { method: 'POST', headers: { origin: ORIGIN } }, ip);
  expect(res.status).toBe(201);
  return (await res.json()) as { conversationId: string; visitorToken: string };
}

function send(chat: { conversationId: string; visitorToken: string }, body: unknown, ip?: string) {
  return h.request(
    `/api/widget/c/${chat.conversationId}/messages`,
    { method: 'POST', headers: { origin: ORIGIN, authorization: `Bearer ${chat.visitorToken}`, 'content-type': 'application/json' }, body: JSON.stringify(body) },
    ip,
  );
}

describe('widget config and CORS', () => {
  it('returns the public config with CORS for an allowed origin', async () => {
    const res = await h.request('/api/widget/b/harbor-home/config', { headers: { origin: ORIGIN } });
    expect(res.status).toBe(200);
    expect(res.headers.get('access-control-allow-origin')).toBe(ORIGIN);
    expect(res.headers.get('vary')).toContain('Origin');
    const body = (await res.json()) as { business: { name: string }; quickQuestions: string[]; widget: Record<string, unknown> };
    expect(body.business.name).toBe('Harbor & Home Cleaning');
    expect(body.quickQuestions).toHaveLength(4);
    expect(body.widget).toEqual({ primaryColor: '#2563eb', position: 'right', greeting: expect.any(Object) });
    expect(JSON.stringify(body)).not.toContain('owner@harbor.example');
  });

  it('allows the admin origin and requests without an Origin header', async () => {
    expect((await h.request('/api/widget/b/harbor-home/config', { headers: { origin: h.env.PUBLIC_ORIGIN } })).status).toBe(200);
    expect((await h.request('/api/widget/b/harbor-home/config')).status).toBe(200);
  });

  it('rejects other origins and unknown businesses', async () => {
    expect((await h.request('/api/widget/b/harbor-home/config', { headers: { origin: 'https://evil.example' } })).status).toBe(403);
    expect((await h.request('/api/widget/b/nope/config', { headers: { origin: ORIGIN } })).status).toBe(404);
  });

  it('answers preflight requests without a token', async () => {
    const chat = await startChat();
    const res = await h.request(`/api/widget/c/${chat.conversationId}/messages`, {
      method: 'OPTIONS',
      headers: { origin: ORIGIN, 'access-control-request-method': 'POST' },
    });
    expect(res.status).toBe(204);
    expect(res.headers.get('access-control-allow-headers')).toContain('Authorization');
  });
});

describe('widget conversations', () => {
  it('runs a conversation with the visitor token', async () => {
    const chat = await startChat();
    const res = await send(chat, { text: 'What are your hours?', locale: 'en-US' });
    expect(res.status).toBe(200);
    const body = (await res.json()) as { messages: { role: string; content: string }[]; status: string };
    expect(body.status).toBe('bot');
    expect(body.messages.map((m) => m.role)).toEqual(['visitor', 'bot']);
    const list = await h.request(`/api/widget/c/${chat.conversationId}/messages?since=0`, {
      headers: { origin: ORIGIN, authorization: `Bearer ${chat.visitorToken}` },
    });
    expect(((await list.json()) as { messages: unknown[] }).messages).toHaveLength(2);
  });

  it('requires the right token', async () => {
    const chat = await startChat();
    const other = await startChat();
    expect((await send({ ...chat, visitorToken: other.visitorToken }, { text: 'hi' })).status).toBe(401);
    const missing = await h.request(`/api/widget/c/${chat.conversationId}/messages`, { headers: { origin: ORIGIN } });
    expect(missing.status).toBe(401);
  });

  it('validates message size', async () => {
    const chat = await startChat();
    expect((await send(chat, { text: 'x'.repeat(1001) })).status).toBe(422);
    expect((await send(chat, { text: '   ' })).status).toBe(422);
    expect((await send(chat, { text: 'hi', extra: true })).status).toBe(422);
  });

  it('rate limits messages per conversation', async () => {
    const chat = await startChat();
    for (let i = 0; i < 20; i++) expect((await send(chat, { text: 'What are your hours?' })).status).toBe(200);
    expect((await send(chat, { text: 'What are your hours?' })).status).toBe(429);
  });

  it('rate limits new conversations per IP', async () => {
    for (let i = 0; i < 20; i++) await startChat('192.0.2.50');
    const res = await h.request('/api/widget/b/harbor-home/conversations', { method: 'POST', headers: { origin: ORIGIN } }, '192.0.2.50');
    expect(res.status).toBe(429);
  });

  it('accepts offline contact details and emails the owner', async () => {
    const chat = await startChat();
    const res = await h.request(`/api/widget/c/${chat.conversationId}/contact`, {
      method: 'POST',
      headers: { origin: ORIGIN, authorization: `Bearer ${chat.visitorToken}`, 'content-type': 'application/json' },
      body: JSON.stringify({ name: 'Alex', email: 'alex@example.com', note: 'Call after 5pm' }),
    });
    expect(res.status).toBe(200);
    expect((await getConversation(h.database.db, chat.conversationId))?.needsAttention).toBe(true);
    await vi.waitFor(() => expect(h.mailer.sent.map((m) => m.to)).toEqual(['owner@harbor.example']));
    const bad = await h.request(`/api/widget/c/${chat.conversationId}/contact`, {
      method: 'POST',
      headers: { origin: ORIGIN, authorization: `Bearer ${chat.visitorToken}`, 'content-type': 'application/json' },
      body: JSON.stringify({ name: 'No contact' }),
    });
    expect(bad.status).toBe(422);
  });

  it('rejects oversized bodies', async () => {
    const chat = await startChat();
    expect((await send(chat, { text: 'x'.repeat(40 * 1024) })).status).toBe(413);
  });
});
```

- [ ] **Step 2: Run to verify RED** — `npx vitest run apps/server/src/http/routes/widget.test.ts` → FAIL (404s).

- [ ] **Step 3: Implement `apps/server/src/http/routes/widget.ts`**

```ts
import { Hono, type MiddlewareHandler } from 'hono';
import { z } from 'zod';
import { parseOrThrow } from '../../domain/validation';
import { getBusinessById, getBusinessBySlug, listFaqs, type BusinessRow } from '../../repos/business';
import { getConversation, listMessages, verifyVisitorToken, VISITOR_VISIBLE_ROLES } from '../../repos/conversations';
import { handleVisitorText, MAX_VISITOR_TEXT, startConversation, submitOfflineContact, toPublicMessage } from '../../services/chat';
import type { AppDeps } from '../deps';
import type { AppEnv } from '../types';
import { clientIp, parseEventId, readJson } from '../util';

const messageSchema = z.object({ text: z.string().min(1).max(MAX_VISITOR_TEXT), locale: z.string().max(20).optional() }).strict();
const QUICK_QUESTIONS = 4;

function originAllowed(deps: AppDeps, business: BusinessRow, origin: string): boolean {
  return origin === deps.env.PUBLIC_ORIGIN || business.widget.allowedOrigins.includes(origin);
}

/** Per-business CORS: the widget may only be embedded on the merchant's allowed origins. */
export function corsGuard(deps: AppDeps): MiddlewareHandler<AppEnv> {
  return async (c, next) => {
    const origin = c.req.header('origin');
    if (origin) {
      if (!originAllowed(deps, c.get('business'), origin)) return c.json({ error: 'origin_not_allowed' }, 403);
      c.header('Access-Control-Allow-Origin', origin);
      c.header('Vary', 'Origin');
      c.header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS');
      c.header('Access-Control-Allow-Headers', 'Content-Type, Authorization, Last-Event-ID');
      c.header('Access-Control-Max-Age', '600');
    }
    if (c.req.method === 'OPTIONS') return c.body(null, 204);
    return next();
  };
}

function loadBusinessBySlug(deps: AppDeps): MiddlewareHandler<AppEnv> {
  return async (c, next) => {
    const business = await getBusinessBySlug(deps.db, c.req.param('slug') ?? '');
    if (!business) return c.json({ error: 'not_found' }, 404);
    c.set('business', business);
    return next();
  };
}

function loadConversation(deps: AppDeps): MiddlewareHandler<AppEnv> {
  return async (c, next) => {
    const conversation = await getConversation(deps.db, c.req.param('id') ?? '');
    const business = conversation ? await getBusinessById(deps.db, conversation.businessId) : null;
    if (!conversation || !business) return c.json({ error: 'not_found' }, 404);
    c.set('conversation', conversation);
    c.set('business', business);
    return next();
  };
}

/** The visitor token travels only in the Authorization header, never in a URL. */
const requireVisitorToken: MiddlewareHandler<AppEnv> = async (c, next) => {
  const header = c.req.header('authorization') ?? '';
  const token = header.startsWith('Bearer ') ? header.slice('Bearer '.length).trim() : '';
  if (!verifyVisitorToken(c.get('conversation'), token)) return c.json({ error: 'unauthorized' }, 401);
  return next();
};

export function widgetRoutes(deps: AppDeps) {
  const app = new Hono<AppEnv>();
  app.use('/b/:slug/*', loadBusinessBySlug(deps), corsGuard(deps));
  app.use('/c/:id/*', loadConversation(deps), corsGuard(deps), requireVisitorToken);

  app.get('/b/:slug/config', async (c) => {
    const business = c.get('business');
    const quickQuestions = (await listFaqs(deps.db, business.id))
      .filter((f) => f.enabled)
      .slice(0, QUICK_QUESTIONS)
      .map((f) => f.question);
    const { primaryColor, position, greeting } = business.widget;
    return c.json({
      business: { name: business.name, slug: business.slug },
      locale: business.locale,
      widget: { primaryColor, position, greeting },
      quickQuestions,
    });
  });

  app.post('/b/:slug/conversations', async (c) => {
    if (!deps.limiters.newConversation.hit(clientIp(c, deps.env.TRUST_PROXY))) return c.json({ error: 'rate_limited' }, 429);
    const { conversation, visitorToken } = await startConversation(deps, c.get('business'));
    return c.json({ conversationId: conversation.id, visitorToken }, 201);
  });

  app.get('/c/:id/messages', async (c) => {
    const conversation = c.get('conversation');
    const since = parseEventId(c.req.query('since'));
    const rows = await listMessages(deps.db, conversation.id, { ...(since !== null ? { afterId: since } : {}), roles: VISITOR_VISIBLE_ROLES, limit: 200 });
    return c.json({ messages: rows.map(toPublicMessage), status: conversation.status });
  });

  app.post('/c/:id/messages', async (c) => {
    const body = parseOrThrow(messageSchema, await readJson(c));
    const conversation = c.get('conversation');
    if (!deps.limiters.visitorMessage.hit(conversation.id) || !deps.limiters.ipMessage.hit(clientIp(c, deps.env.TRUST_PROXY))) {
      return c.json({ error: 'rate_limited' }, 429);
    }
    const turn = await handleVisitorText(deps, conversation.id, body.text, body.locale ?? null);
    void turn.notifications; // best-effort, never rejects
    return c.json({ messages: turn.messages, status: turn.status });
  });

  app.post('/c/:id/contact', async (c) => {
    const conversation = c.get('conversation');
    const body = await readJson(c);
    if (!deps.limiters.contact.hit(conversation.id)) return c.json({ error: 'rate_limited' }, 429);
    const result = await submitOfflineContact(deps, conversation.id, body);
    void result.notifications;
    return c.json({ ok: true });
  });

  return app;
}
```

Register in `apps/server/src/http/app.ts` (after the auth route):
```ts
import { widgetRoutes } from './routes/widget';
// ...
app.route('/api/widget', widgetRoutes(deps));
```

> If Hono does not expose `:slug`/`:id` params inside `app.use('/b/:slug/*', ...)` middleware, switch those middlewares to route-level handlers (e.g. `app.on(['GET','POST','OPTIONS'], '/b/:slug/*', ...)`) — keep the order: load → CORS → token. Also ensure `OPTIONS` reaches the middleware (Hono returns 404 for unmatched methods otherwise): if needed add `app.options('/b/:slug/*', (c) => c.body(null, 204))` and `app.options('/c/:id/*', ...)` after the middleware.

- [ ] **Step 4: Verify GREEN** — focused, then `npx vitest run`; `npm run typecheck` exit 0.

- [ ] **Step 5: Commit**

```bash
git add apps/server/src/http
git commit -m "feat(server): add widget API with per-business CORS, visitor tokens and rate limits"
```

---

### Task 10: Admin API routes

**Files:**
- Create: `apps/server/src/http/routes/admin.ts`
- Modify: `apps/server/src/http/app.ts` (register `/api/admin`)
- Test: `apps/server/src/http/routes/admin.test.ts`

**Interfaces:**
- Consumes: `requireSameOrigin`, `requireSession`, `readJson`, repos (business, conversations, bookings, audit, users), chat service, AI settings, core `KNOWLEDGE_LIMITS`.
- Produces (`/api/admin`, all require a session; non-GET also require `Origin === PUBLIC_ORIGIN`; foreign ids → 404):
  - `GET /conversations?filter=all|attention|open` → `{ conversations: (ConversationSummary & { contact, lastMessage: AdminMessage | null })[] }`
  - `GET /conversations/:id` → `{ conversation: ConversationSummary & { bookingDraft }, contact, messages: AdminMessage[], bookings }` (marks read)
  - `POST /conversations/:id/takeover|release|close` → `{ conversation }`
  - `POST /conversations/:id/messages` `{ text }` → 201 `{ message: AdminMessage }`
  - `GET /business` → `{ business: PublicBusiness, limits }`; `PATCH /business` → `{ business }`
  - `GET /faqs` → `{ faqs }`; `POST /faqs` → 201 `{ faq }`; `PATCH /faqs/:id` → `{ faq }`; `DELETE /faqs/:id` → 204
  - `GET /bookings?status=` → `{ bookings }`; `POST /bookings/:id/confirm|decline` `{ note? }` → `{ booking }`, 409 `{ error: 'already_decided' }`
  - `GET /ai` → `{ ai: PublicAiSettings }`; `PUT /ai` → `{ ai }`; `POST /ai/test` → `AiTestResult`
  - `adminRoutes(deps)`, `publicBusiness(row)` (everything except `ai`)

- [ ] **Step 1: Write failing tests** — `apps/server/src/http/routes/admin.test.ts`:
```ts
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { mockLanguageModel } from '../../ai/testing';
import { createBusiness } from '../../repos/business';
import { createUser } from '../../repos/users';
import { createTestHarness, type TestHarness } from '../../testing/harness';

let h: TestHarness;
let cookie: string;
beforeEach(async () => {
  h = await createTestHarness({ languageModelFactory: () => mockLanguageModel({ text: 'OK' }) });
  cookie = await h.login();
});
afterEach(() => h.close());

const ORIGIN = 'https://harbor.example';
const get = (path: string, c = cookie) => h.request(`/api/admin${path}`, { headers: { cookie: c } });
const send = (method: string, path: string, body?: unknown, c = cookie) =>
  h.request(`/api/admin${path}`, { method, headers: h.adminHeaders(c), ...(body === undefined ? {} : { body: JSON.stringify(body) }) });

async function visitorChat(text: string) {
  const start = await h.request('/api/widget/b/harbor-home/conversations', { method: 'POST', headers: { origin: ORIGIN } });
  const chat = (await start.json()) as { conversationId: string; visitorToken: string };
  await h.request(`/api/widget/c/${chat.conversationId}/messages`, {
    method: 'POST',
    headers: { origin: ORIGIN, authorization: `Bearer ${chat.visitorToken}`, 'content-type': 'application/json' },
    body: JSON.stringify({ text }),
  });
  return chat;
}

describe('admin access control', () => {
  it('requires a session and the admin origin', async () => {
    expect((await h.request('/api/admin/conversations')).status).toBe(401);
    const noOrigin = await h.request('/api/admin/faqs', { method: 'POST', headers: { cookie, 'content-type': 'application/json' }, body: '{}' });
    expect(noOrigin.status).toBe(403);
  });

  it('isolates businesses', async () => {
    const chat = await visitorChat('What are your hours?');
    const other = await createBusiness(h.database.db, { slug: 'other-biz', name: 'Other' });
    await createUser(h.database.db, { businessId: other.id, email: 'other@other.example', password: 'another long password' });
    const res = await h.request('/api/auth/login', {
      method: 'POST',
      headers: { origin: h.env.PUBLIC_ORIGIN, 'content-type': 'application/json' },
      body: JSON.stringify({ email: 'other@other.example', password: 'another long password' }),
    });
    const otherCookie = res.headers.get('set-cookie')!.split(';')[0]!;
    expect((await get(`/conversations/${chat.conversationId}`, otherCookie)).status).toBe(404);
    expect((await send('POST', `/conversations/${chat.conversationId}/messages`, { text: 'hi' }, otherCookie)).status).toBe(404);
    expect(((await (await get('/conversations', otherCookie)).json()) as { conversations: unknown[] }).conversations).toHaveLength(0);
  });
});

describe('inbox', () => {
  it('lists, opens, takes over, replies and releases', async () => {
    const chat = await visitorChat('Do you clean boats?');
    const list = (await (await get('/conversations?filter=attention')).json()) as { conversations: { id: string; lastMessage: { role: string } }[] };
    expect(list.conversations.map((c) => c.id)).toEqual([chat.conversationId]);
    expect(list.conversations[0]!.lastMessage.role).toBe('bot');

    const detail = (await (await get(`/conversations/${chat.conversationId}`)).json()) as { messages: { role: string; meta: unknown }[]; conversation: { unreadCount: number } };
    expect(detail.messages.map((m) => m.role)).toEqual(['visitor', 'bot']);
    expect(detail.messages[1]!.meta).toMatchObject({ engine: 'rules', handoff: 'unknown_question' });
    expect(detail.conversation.unreadCount).toBe(0);

    expect(((await (await send('POST', `/conversations/${chat.conversationId}/takeover`)).json()) as { conversation: { status: string } }).conversation.status).toBe('human');
    const reply = await send('POST', `/conversations/${chat.conversationId}/messages`, { text: 'Hi, Sam here — we do not clean boats.' });
    expect(reply.status).toBe(201);
    const visitorView = await h.request(`/api/widget/c/${chat.conversationId}/messages`, { headers: { origin: ORIGIN, authorization: `Bearer ${chat.visitorToken}` } });
    expect(((await visitorView.json()) as { messages: { role: string }[] }).messages.map((m) => m.role)).toEqual(['visitor', 'bot', 'agent']);
    expect(((await (await send('POST', `/conversations/${chat.conversationId}/release`)).json()) as { conversation: { status: string } }).conversation.status).toBe('bot');
    expect((await send('POST', `/conversations/${chat.conversationId}/messages`, { text: '' })).status).toBe(422);
  });
});

describe('business and FAQs', () => {
  it('reads and patches the business with validation', async () => {
    const body = (await (await get('/business')).json()) as { business: Record<string, unknown>; limits: { totalChars: number } };
    expect(body.business).not.toHaveProperty('ai');
    expect(body.limits.totalChars).toBe(12000);
    const ok = await send('PATCH', '/business', { name: 'Harbor Home' });
    expect(((await ok.json()) as { business: { name: string } }).business.name).toBe('Harbor Home');
    const bad = await send('PATCH', '/business', { name: '' });
    expect(bad.status).toBe(422);
    expect(((await bad.json()) as { issues: { path: string }[] }).issues[0]!.path).toBe('name');
  });

  it('creates, updates and deletes FAQs within the budget', async () => {
    const created = await send('POST', '/faqs', { question: 'Do you clean windows?', answer: 'Yes, inside windows only.' });
    expect(created.status).toBe(201);
    const { faq } = (await created.json()) as { faq: { id: string } };
    expect((await send('PATCH', `/faqs/${faq.id}`, { enabled: false })).status).toBe(200);
    expect((await send('PATCH', `/faqs/${faq.id}`, { answer: 'a'.repeat(501) })).status).toBe(422);
    expect((await send('DELETE', `/faqs/${faq.id}`)).status).toBe(204);
    expect((await send('DELETE', `/faqs/${faq.id}`)).status).toBe(404);
    expect(((await (await get('/faqs')).json()) as { faqs: unknown[] }).faqs).toHaveLength(5);
  });
});

describe('bookings', () => {
  it('confirms a pending booking once', async () => {
    const chat = await visitorChat('I want to book a deep cleaning');
    for (const text of ['Saturday morning', 'Alex', 'alex@example.com']) {
      await h.request(`/api/widget/c/${chat.conversationId}/messages`, {
        method: 'POST',
        headers: { origin: ORIGIN, authorization: `Bearer ${chat.visitorToken}`, 'content-type': 'application/json' },
        body: JSON.stringify({ text }),
      });
    }
    const { bookings } = (await (await get('/bookings?status=pending')).json()) as { bookings: { id: string }[] };
    expect(bookings).toHaveLength(1);
    const confirmed = await send('POST', `/bookings/${bookings[0]!.id}/confirm`, { note: 'See you Saturday' });
    expect(((await confirmed.json()) as { booking: { status: string } }).booking.status).toBe('confirmed');
    expect((await send('POST', `/bookings/${bookings[0]!.id}/decline`, {})).status).toBe(409);
    expect((await send('POST', '/bookings/00000000-0000-0000-0000-000000000000/confirm', {})).status).toBe(404);
    expect((await get('/bookings?status=bogus')).status).toBe(422);
  });
});

describe('AI settings', () => {
  it('saves without ever returning the key and tests the connection', async () => {
    const saved = await send('PUT', '/ai', { provider: 'openai', model: 'gpt-x', apiKey: 'sk-test-12345678', enabled: true });
    const text = await saved.text();
    expect(saved.status).toBe(200);
    expect(text).not.toContain('sk-test');
    expect(JSON.parse(text)).toEqual({ ai: { provider: 'openai', model: 'gpt-x', baseUrl: null, keyLast4: '5678', hasKey: true, enabled: true } });
    expect(await (await get('/ai')).text()).not.toContain('sk-test');
    expect(await (await send('POST', '/ai/test')).json()).toEqual({ ok: true });
    expect((await send('PUT', '/ai', { provider: 'openai', model: '', enabled: true })).status).toBe(422);
  });
});
```

- [ ] **Step 2: Run to verify RED** — `npx vitest run apps/server/src/http/routes/admin.test.ts` → FAIL.

- [ ] **Step 3: Implement `apps/server/src/http/routes/admin.ts`**

```ts
import { KNOWLEDGE_LIMITS } from '@reception/core';
import { Hono, type Context } from 'hono';
import { z } from 'zod';
import { publicAiSettings, saveAiSettings, testAiConnection } from '../../ai/settings';
import { parseOrThrow } from '../../domain/validation';
import { writeAudit } from '../../repos/audit';
import { decideBooking, getBookingForBusiness, listBookings, listBookingsForConversation } from '../../repos/bookings';
import { createFaq, deleteFaq, getBusinessById, listFaqs, updateBusiness, updateFaq, type BusinessRow } from '../../repos/business';
import { getContact, getConversationForBusiness, listConversations, listMessages } from '../../repos/conversations';
import {
  agentReply,
  bizTopic,
  conversationSummary,
  markConversationRead,
  MAX_AGENT_TEXT,
  setConversationStatus,
  toAdminMessage,
  type StatusAction,
} from '../../services/chat';
import type { AppDeps } from '../deps';
import { requireSameOrigin, requireSession } from '../middleware';
import type { AppEnv } from '../types';
import { readJson } from '../util';

const textSchema = z.object({ text: z.string().min(1).max(MAX_AGENT_TEXT) }).strict();
const decisionSchema = z.object({ note: z.string().trim().max(500).optional() }).strict();
const listFilterSchema = z.enum(['all', 'attention', 'open']).default('all');
const bookingStatusSchema = z.enum(['pending', 'confirmed', 'declined', 'cancelled']).optional();

export function publicBusiness(b: BusinessRow) {
  return {
    id: b.id,
    slug: b.slug,
    name: b.name,
    locale: b.locale,
    timezone: b.timezone,
    profile: b.profile,
    bookingFields: b.bookingFields,
    widget: b.widget,
    notify: b.notify,
  };
}

const notFound = (c: Context) => c.json({ error: 'not_found' }, 404);

export function adminRoutes(deps: AppDeps) {
  const app = new Hono<AppEnv>();
  app.use('*', requireSameOrigin(deps), requireSession(deps));

  const businessId = (c: Context<AppEnv>) => c.get('auth').businessId;
  const userId = (c: Context<AppEnv>) => c.get('auth').user.id;
  const currentBusiness = async (c: Context<AppEnv>) => {
    const business = await getBusinessById(deps.db, businessId(c));
    if (!business) throw new Error('Session business no longer exists');
    return business;
  };

  // Conversations
  app.get('/conversations', async (c) => {
    const filter = parseOrThrow(listFilterSchema, c.req.query('filter'));
    const items = await listConversations(deps.db, businessId(c), { filter });
    return c.json({
      conversations: items.map((i) => ({
        ...conversationSummary(i.conversation),
        contact: i.contact ? { id: i.contact.id, name: i.contact.name, email: i.contact.email, phone: i.contact.phone } : null,
        lastMessage: i.lastMessage ? toAdminMessage(i.lastMessage) : null,
      })),
    });
  });

  app.get('/conversations/:id', async (c) => {
    const conversation = await getConversationForBusiness(deps.db, businessId(c), c.req.param('id'));
    if (!conversation) return notFound(c);
    await markConversationRead(deps, businessId(c), conversation.id);
    const [rows, bookings, contact] = await Promise.all([
      listMessages(deps.db, conversation.id),
      listBookingsForConversation(deps.db, conversation.id),
      conversation.contactId ? getContact(deps.db, conversation.contactId) : Promise.resolve(null),
    ]);
    return c.json({
      conversation: { ...conversationSummary({ ...conversation, unreadCount: 0 }), bookingDraft: conversation.bookingDraft },
      contact,
      messages: rows.map(toAdminMessage),
      bookings,
    });
  });

  for (const action of ['takeover', 'release', 'close'] as const satisfies readonly StatusAction[]) {
    app.post(`/conversations/:id/${action}`, async (c) => {
      const updated = await setConversationStatus(deps, { businessId: businessId(c), conversationId: c.req.param('id'), action, userId: userId(c) });
      return updated ? c.json({ conversation: conversationSummary(updated) }) : notFound(c);
    });
  }

  app.post('/conversations/:id/messages', async (c) => {
    const body = parseOrThrow(textSchema, await readJson(c));
    const message = await agentReply(deps, { businessId: businessId(c), conversationId: c.req.param('id'), userId: userId(c), text: body.text });
    return message ? c.json({ message: toAdminMessage(message) }, 201) : notFound(c);
  });

  // Business profile & FAQs
  app.get('/business', async (c) => c.json({ business: publicBusiness(await currentBusiness(c)), limits: KNOWLEDGE_LIMITS }));

  app.patch('/business', async (c) => {
    const updated = await updateBusiness(deps.db, businessId(c), await readJson(c), deps.now());
    if (!updated) return notFound(c);
    await writeAudit(deps.db, { businessId: updated.id, actor: `user:${userId(c)}`, action: 'business_updated', now: deps.now() });
    return c.json({ business: publicBusiness(updated) });
  });

  app.get('/faqs', async (c) => c.json({ faqs: await listFaqs(deps.db, businessId(c)) }));

  app.post('/faqs', async (c) => {
    const faq = await createFaq(deps.db, businessId(c), await readJson(c));
    return c.json({ faq }, 201);
  });

  app.patch('/faqs/:id', async (c) => {
    const faq = await updateFaq(deps.db, businessId(c), c.req.param('id'), await readJson(c), deps.now());
    return faq ? c.json({ faq }) : notFound(c);
  });

  app.delete('/faqs/:id', async (c) => ((await deleteFaq(deps.db, businessId(c), c.req.param('id'))) ? c.body(null, 204) : notFound(c)));

  // Bookings
  app.get('/bookings', async (c) => {
    const status = parseOrThrow(bookingStatusSchema, c.req.query('status'));
    return c.json({ bookings: await listBookings(deps.db, businessId(c), status ? { status } : {}) });
  });

  for (const [action, decision] of [
    ['confirm', 'confirmed'],
    ['decline', 'declined'],
  ] as const) {
    app.post(`/bookings/:id/${action}`, async (c) => {
      const body = parseOrThrow(decisionSchema, await readJson(c, { optional: true }));
      const existing = await getBookingForBusiness(deps.db, businessId(c), c.req.param('id'));
      if (!existing) return notFound(c);
      const now = deps.now();
      const booking = await decideBooking(deps.db, { businessId: businessId(c), bookingId: existing.id, decision, note: body.note ?? null, userId: userId(c), now });
      if (!booking) return c.json({ error: 'already_decided' }, 409);
      await writeAudit(deps.db, { businessId: booking.businessId, actor: `user:${userId(c)}`, action: `booking_${decision}`, target: booking.id, now });
      deps.bus.publish(bizTopic(booking.businessId), { type: 'booking', data: booking });
      return c.json({ booking });
    });
  }

  // AI settings (the key is write-only)
  app.get('/ai', async (c) => c.json({ ai: publicAiSettings((await currentBusiness(c)).ai) }));

  app.put('/ai', async (c) => {
    const business = await currentBusiness(c);
    const ai = await saveAiSettings(deps.db, business, await readJson(c), deps.secretKey, deps.now());
    await writeAudit(deps.db, { businessId: business.id, actor: `user:${userId(c)}`, action: 'ai_settings_updated', data: { provider: ai.provider, enabled: ai.enabled }, now: deps.now() });
    return c.json({ ai: publicAiSettings(ai) });
  });

  app.post('/ai/test', async (c) => c.json(await testAiConnection(await currentBusiness(c), deps.secretKey, deps.languageModelFactory)));

  return app;
}
```

Register in `apps/server/src/http/app.ts`:
```ts
import { adminRoutes } from './routes/admin';
// ...
app.route('/api/admin', adminRoutes(deps));
```

> `parseOrThrow(listFilterSchema, undefined)` yields `'all'` via `.default`. If the `satisfies` on the `for…of` tuple confuses TS 7, drop it — `StatusAction` is still enforced by `setConversationStatus`'s parameter type.

- [ ] **Step 4: Verify GREEN** — focused, then `npx vitest run`; `npm run typecheck` exit 0.

- [ ] **Step 5: Commit**

```bash
git add apps/server/src/http
git commit -m "feat(server): add admin API for inbox, knowledge, bookings and AI settings"
```

---

### Task 11: Server-Sent Events (widget + admin)

**Files:**
- Create: `apps/server/src/http/sse.ts`, `apps/server/src/http/listen.ts`
- Modify: `apps/server/src/http/routes/widget.ts` (add `GET /c/:id/events`), `apps/server/src/http/routes/admin.ts` (add `GET /events`)
- Test: `apps/server/src/http/sse.test.ts`

**Interfaces:**
- Consumes: `EventBus`, `BusEvent`, `ConnectionRegistry`, `AppDeps.heartbeatMs`, `parseEventId`, `listMessages`, `VISITOR_VISIBLE_ROLES`, `toPublicMessage`, `convTopic`, `bizTopic`.
- Produces:
  - `sseStream(c, deps, { topic, connectionKey, lastEventId, replay(afterId): Promise<BusEvent[]> }): Response`
  - `listen(app, hostname, port): Promise<{ url: string; close(): Promise<void> }>` (used by tests and Task 12)
  - Stream format: first `event: ready`; then `event: <type>` / `data: <json>` / `id: <messageId>` for message events; heartbeat comment `: ping` every `heartbeatMs` when idle.
  - Widget: `GET /api/widget/c/:id/events` (Bearer; `Last-Event-ID` header or `?since=` replays visitor-visible messages after that id); connection key `visitor:<conversationId>`.
  - Admin: `GET /api/admin/events` (session cookie); no replay (the dashboard refetches lists on reconnect); connection key `session:<sessionId>`.

Guarantees: subscribe **before** replay and drop live events whose numeric id ≤ last sent id (no gaps, no duplicates); unsubscribe and unregister in `finally` **and** on abort (idempotent); a 4th stream for the same key closes the oldest.

- [ ] **Step 1: Write failing tests** — `apps/server/src/http/sse.test.ts`:
```ts
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { agentReply } from '../services/chat';
import { createTestHarness, type TestHarness } from '../testing/harness';
import { listen } from './listen';

const ORIGIN = 'https://harbor.example';
let h: TestHarness;
let server: { url: string; close(): Promise<void> };

beforeEach(async () => {
  h = await createTestHarness({ heartbeatMs: 50 });
  server = await listen(h.app, '127.0.0.1', 0);
});
afterEach(async () => {
  await server.close();
  h.close();
});

async function startChat() {
  const res = await fetch(`${server.url}/api/widget/b/harbor-home/conversations`, { method: 'POST', headers: { origin: ORIGIN } });
  return (await res.json()) as { conversationId: string; visitorToken: string };
}

function sendMessage(chat: { conversationId: string; visitorToken: string }, text: string) {
  return fetch(`${server.url}/api/widget/c/${chat.conversationId}/messages`, {
    method: 'POST',
    headers: { origin: ORIGIN, authorization: `Bearer ${chat.visitorToken}`, 'content-type': 'application/json' },
    body: JSON.stringify({ text }),
  });
}

interface OpenStream {
  status: number;
  until(predicate: (text: string) => boolean, timeoutMs?: number): Promise<string>;
  ended(timeoutMs?: number): Promise<boolean>;
  abort(): void;
}

async function openStream(path: string, headers: Record<string, string>): Promise<OpenStream> {
  const controller = new AbortController();
  const res = await fetch(`${server.url}${path}`, { headers, signal: controller.signal });
  const reader = res.body!.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let done = false;
  const readOnce = async (timeoutMs: number) => {
    const chunk = await Promise.race([
      reader.read(),
      new Promise<never>((_, reject) => setTimeout(() => reject(new Error(`timeout; received: ${buffer}`)), timeoutMs)),
    ]);
    if (chunk.done) done = true;
    else buffer += decoder.decode(chunk.value, { stream: true });
  };
  return {
    status: res.status,
    async until(predicate, timeoutMs = 2000) {
      const deadline = Date.now() + timeoutMs;
      while (!predicate(buffer)) {
        if (done) throw new Error(`stream ended; received: ${buffer}`);
        await readOnce(Math.max(1, deadline - Date.now()));
      }
      return buffer;
    },
    async ended(timeoutMs = 2000) {
      const deadline = Date.now() + timeoutMs;
      while (!done && Date.now() < deadline) await readOnce(Math.max(1, deadline - Date.now())).catch(() => undefined);
      return done;
    },
    abort: () => controller.abort(),
  };
}

const visitorHeaders = (token: string, extra: Record<string, string> = {}) => ({ origin: ORIGIN, authorization: `Bearer ${token}`, ...extra });

describe('widget SSE', () => {
  it('delivers agent replies live', async () => {
    const chat = await startChat();
    const stream = await openStream(`/api/widget/c/${chat.conversationId}/events`, visitorHeaders(chat.visitorToken));
    expect(stream.status).toBe(200);
    await stream.until((t) => t.includes('event: ready'));
    await agentReply(h.deps, { businessId: h.business.id, conversationId: chat.conversationId, userId: h.owner.id, text: 'Hi from Sam' });
    const text = await stream.until((t) => t.includes('Hi from Sam'));
    expect(text).toContain('event: message');
    expect(text).toContain('event: status');
    stream.abort();
  });

  it('replays messages after Last-Event-ID and sends heartbeats', async () => {
    const chat = await startChat();
    await sendMessage(chat, 'What are your hours?');
    const stream = await openStream(`/api/widget/c/${chat.conversationId}/events`, visitorHeaders(chat.visitorToken, { 'last-event-id': '0' }));
    const text = await stream.until((t) => t.includes('Our hours') && t.includes(': ping'));
    expect(text.match(/event: message/g)).toHaveLength(2);
    stream.abort();
  });

  it('requires the visitor token and an allowed origin', async () => {
    const chat = await startChat();
    expect((await fetch(`${server.url}/api/widget/c/${chat.conversationId}/events`, { headers: { origin: ORIGIN } })).status).toBe(401);
    const evil = await fetch(`${server.url}/api/widget/c/${chat.conversationId}/events`, {
      headers: { origin: 'https://evil.example', authorization: `Bearer ${chat.visitorToken}` },
    });
    expect(evil.status).toBe(403);
  });

  it('caps concurrent streams per conversation by closing the oldest', async () => {
    const chat = await startChat();
    const streams: OpenStream[] = [];
    for (let i = 0; i < 4; i++) {
      const s = await openStream(`/api/widget/c/${chat.conversationId}/events`, visitorHeaders(chat.visitorToken));
      await s.until((t) => t.includes('event: ready'));
      streams.push(s);
    }
    expect(await streams[0]!.ended()).toBe(true);
    expect(h.deps.sse.count(`visitor:${chat.conversationId}`)).toBe(3);
    for (const s of streams) s.abort();
  });

  it('cleans up listeners after many disconnects', async () => {
    const chat = await startChat();
    for (let i = 0; i < 100; i++) {
      const s = await openStream(`/api/widget/c/${chat.conversationId}/events`, visitorHeaders(chat.visitorToken));
      await s.until((t) => t.includes('event: ready'));
      s.abort();
    }
    await vi.waitFor(
      () => {
        expect(h.deps.bus.listenerCount()).toBe(0);
        expect(h.deps.sse.count()).toBe(0);
      },
      { timeout: 5000, interval: 50 },
    );
  });
});

describe('admin SSE', () => {
  it('requires a session and streams business events', async () => {
    expect((await fetch(`${server.url}/api/admin/events`)).status).toBe(401);
    const cookie = await h.login();
    const stream = await openStream('/api/admin/events', { cookie });
    await stream.until((t) => t.includes('event: ready'));
    const chat = await startChat();
    await sendMessage(chat, 'What are your hours?');
    const text = await stream.until((t) => t.includes('event: conversation') && t.includes('Our hours'));
    expect(text).toContain(chat.conversationId);
    stream.abort();
  });
});
```

- [ ] **Step 2: Run to verify RED** — `npx vitest run apps/server/src/http/sse.test.ts` → FAIL.

- [ ] **Step 3: Implement**

`apps/server/src/http/listen.ts`:
```ts
import type { Server } from 'node:http';
import { serve } from '@hono/node-server';
import type { Hono } from 'hono';
import type { AppEnv } from './types';

export interface Listening {
  url: string;
  close(): Promise<void>;
}

export function listen(app: Hono<AppEnv>, hostname: string, port: number): Promise<Listening> {
  return new Promise((resolve) => {
    const server = serve({ fetch: app.fetch, hostname, port }, (info) => {
      resolve({
        url: `http://${hostname}:${info.port}`,
        close: () =>
          new Promise<void>((done) => {
            // SSE streams are long-lived: drop them so close() resolves.
            (server as Server).closeAllConnections();
            server.close(() => done());
          }),
      });
    });
  });
}
```

`apps/server/src/http/sse.ts`:
```ts
import type { Context } from 'hono';
import { streamSSE } from 'hono/streaming';
import type { BusEvent } from '../lib/bus';
import type { AppDeps } from './deps';

export interface SseOptions {
  topic: string;
  connectionKey: string;
  /** null = no replay */
  lastEventId: number | null;
  replay: (afterId: number) => Promise<BusEvent[]>;
}

export function sseStream(c: Context, deps: Pick<AppDeps, 'bus' | 'sse' | 'heartbeatMs'>, opts: SseOptions): Response {
  return streamSSE(c, async (stream) => {
    const queue: BusEvent[] = [];
    let closed = false;
    let wake: (() => void) | null = null;
    const signal = () => {
      const w = wake;
      wake = null;
      w?.();
    };
    // Subscribe before replaying so nothing published in between is lost.
    const unsubscribe = deps.bus.subscribe(opts.topic, (event) => {
      queue.push(event);
      signal();
    });
    const unregister = deps.sse.add(opts.connectionKey, () => {
      closed = true;
      signal();
    });
    const cleanup = () => {
      unsubscribe();
      unregister();
    };
    stream.onAbort(() => {
      closed = true;
      cleanup();
      signal();
    });

    let lastSentId = opts.lastEventId ?? 0;
    const send = async (event: BusEvent) => {
      const numericId = event.id === undefined ? null : Number(event.id);
      if (numericId !== null && numericId <= lastSentId) return; // already delivered by replay
      await stream.writeSSE({ event: event.type, data: JSON.stringify(event.data), ...(event.id ? { id: event.id } : {}) });
      if (numericId !== null) lastSentId = numericId;
    };

    try {
      await stream.writeSSE({ event: 'ready', data: '{}' });
      if (opts.lastEventId !== null) {
        for (const event of await opts.replay(opts.lastEventId)) await send(event);
      }
      while (!closed && !stream.aborted) {
        if (queue.length === 0) {
          let timer: ReturnType<typeof setTimeout> | undefined;
          const timedOut = await new Promise<boolean>((resolve) => {
            if (closed || queue.length > 0) return resolve(false);
            wake = () => resolve(false);
            timer = setTimeout(() => resolve(true), deps.heartbeatMs);
          });
          clearTimeout(timer);
          wake = null;
          if (closed || stream.aborted) break;
          if (timedOut && queue.length === 0) {
            await stream.write(': ping\n\n');
            continue;
          }
        }
        while (queue.length > 0 && !closed) await send(queue.shift()!);
      }
    } finally {
      cleanup();
    }
  });
}
```

Add to `apps/server/src/http/routes/widget.ts` (inside `widgetRoutes`, after the other `/c/:id` routes; import `sseStream`, `convTopic`):
```ts
app.get('/c/:id/events', (c) => {
  const conversation = c.get('conversation');
  return sseStream(c, deps, {
    topic: convTopic(conversation.id),
    connectionKey: `visitor:${conversation.id}`,
    lastEventId: parseEventId(c.req.header('last-event-id') ?? c.req.query('since')),
    replay: async (afterId) =>
      (await listMessages(deps.db, conversation.id, { afterId, roles: VISITOR_VISIBLE_ROLES, limit: 200 })).map((m) => ({
        type: 'message',
        id: String(m.id),
        data: toPublicMessage(m),
      })),
  });
});
```

Add to `apps/server/src/http/routes/admin.ts` (import `sseStream`):
```ts
app.get('/events', (c) =>
  sseStream(c, deps, {
    topic: bizTopic(businessId(c)),
    connectionKey: `session:${c.get('auth').sessionId}`,
    lastEventId: null,
    replay: async () => [],
  }),
);
```

> If the "cleans up listeners" test shows the count not reaching 0, the abort is not reaching Hono: verify `stream.onAbort` fires under `@hono/node-server` 2 for client disconnects (research confirmed it does) and that `cleanup()` runs in both places. Do not raise the timeout to hide a leak.

- [ ] **Step 4: Verify GREEN** — focused, then `npx vitest run` (twice, to catch flakiness); `npm run typecheck` exit 0.

- [ ] **Step 5: Commit**

```bash
git add apps/server/src/http
git commit -m "feat(server): add SSE streams with replay, heartbeats, caps and leak-free cleanup"
```

---

### Task 12: Entry point, CLI, live prompt eval, persistence test, docs

**Files:**
- Create: `apps/server/src/server.ts`, `apps/server/src/index.ts`, `apps/server/src/cli.ts`, `apps/server/src/eval/promptEval.ts`, `apps/server/README.md`, root `.env.example`
- Modify: root `package.json` (convenience scripts)
- Test: `apps/server/src/server.test.ts`, `apps/server/src/cli.test.ts`, `apps/server/src/eval/promptEval.test.ts`

**Interfaces:**
- Consumes: everything above; core `demoKnowledge`, `handleVisitorMessage`; `@reception/core/evals/injection.json`.
- Produces:
  - `startServer(env, { mailer?, port? }): Promise<RunningServer>` with `{ url, deps, close() }`
  - `runCli(argv, env, out?): Promise<number>` — commands `create-owner --slug --name --email --password`, `seed-demo --email --password`
  - `checkReply(reply, forbidden): string[]` (flagged phrases, negation-aware)

- [ ] **Step 1: Write failing tests**

`apps/server/src/eval/promptEval.test.ts`:
```ts
import { describe, expect, it } from 'vitest';
import { checkReply } from './promptEval';

describe('checkReply', () => {
  it('flags forbidden phrases case-insensitively', () => {
    expect(checkReply('Sure, it is FREE tomorrow!', ['free', 'confirmed'])).toEqual(['free']);
  });

  it('ignores negated mentions in English and Chinese', () => {
    expect(checkReply('That is not free, and nothing is confirmed yet.', ['free', 'confirmed'])).toEqual([]);
    expect(checkReply('这项服务不是免费的。', ['免费'])).toEqual([]);
    expect(checkReply('全部免费！', ['免费'])).toEqual(['免费']);
  });
});
```

`apps/server/src/cli.test.ts`:
```ts
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { runCli } from './cli';
import { openDatabase } from './db/client';
import { loadEnv, type Env } from './env';
import { getBusinessBySlug, listFaqs } from './repos/business';
import { findUserByEmail } from './repos/users';

let dir: string;
let env: Env;
let lines: string[];
const out = (line: string) => lines.push(line);

beforeEach(() => {
  dir = mkdtempSync(path.join(tmpdir(), 'reception-cli-'));
  env = loadEnv({ APP_SECRET: 'c'.repeat(40), DATABASE_URL: `file:${path.join(dir, 'cli.db')}`, PUBLIC_ORIGIN: 'http://127.0.0.1:8787' });
  lines = [];
});
afterEach(() => rmSync(dir, { recursive: true, force: true, maxRetries: 5 }));

describe('runCli', () => {
  it('seeds the demo business with an owner, idempotently', async () => {
    expect(await runCli(['seed-demo', '--email', 'demo@harbor.example', '--password', 'demo password 123'], env, out)).toBe(0);
    expect(await runCli(['seed-demo', '--email', 'demo@harbor.example', '--password', 'demo password 123'], env, out)).toBe(0);
    const db = await openDatabase(env.DATABASE_URL);
    const business = await getBusinessBySlug(db.db, 'harbor-home');
    expect(business?.widget.allowedOrigins).toEqual(['http://127.0.0.1:8787']);
    expect(await listFaqs(db.db, business!.id)).toHaveLength(5);
    expect(await findUserByEmail(db.db, 'demo@harbor.example')).not.toBeNull();
    db.close();
  });

  it('creates a business owner and refuses duplicates', async () => {
    const args = ['create-owner', '--slug', 'acme-plumbing', '--name', 'Acme Plumbing', '--email', 'boss@acme.example', '--password', 'a long password'];
    expect(await runCli(args, env, out)).toBe(0);
    expect(await runCli(args, env, out)).toBe(1);
    expect(lines.join('\n')).toContain('already exists');
  });

  it('prints usage for unknown commands and invalid input', async () => {
    expect(await runCli(['nope'], env, out)).toBe(1);
    expect(lines.join('\n')).toContain('Usage');
    expect(await runCli(['create-owner', '--slug', 'x'], env, out)).toBe(1);
    expect(await runCli(['create-owner', '--slug', 'acme', '--name', 'Acme', '--email', 'a@acme.example', '--password', 'short'], env, out)).toBe(1);
  });
});
```

`apps/server/src/server.test.ts`:
```ts
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
afterEach(() => rmSync(dir, { recursive: true, force: true, maxRetries: 5 }));

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
```

> `say` captures `server` by reference via the template literal at call time — keep `server` a `let` and build the URL inside `say` as written.

- [ ] **Step 2: Run to verify RED** — `npx vitest run apps/server/src/server.test.ts apps/server/src/cli.test.ts apps/server/src/eval` → FAIL.

- [ ] **Step 3: Implement**

`apps/server/src/server.ts`:
```ts
import { openDatabase } from './db/client';
import type { Env } from './env';
import { createApp } from './http/app';
import { createAppDeps, type AppDeps } from './http/deps';
import { listen } from './http/listen';
import { createSmtpMailer, type Mailer } from './mail/mailer';

export interface RunningServer {
  url: string;
  deps: AppDeps;
  close(): Promise<void>;
}

export async function startServer(env: Env, options: { mailer?: Mailer | null; port?: number } = {}): Promise<RunningServer> {
  const database = await openDatabase(env.DATABASE_URL);
  const mailer =
    options.mailer !== undefined ? options.mailer : env.SMTP_URL && env.MAIL_FROM ? createSmtpMailer(env.SMTP_URL, env.MAIL_FROM) : null;
  const deps = createAppDeps({ env, db: database.db, mailer });
  const server = await listen(createApp(deps), env.HOST, options.port ?? env.PORT);
  return {
    url: server.url,
    deps,
    close: async () => {
      await server.close();
      database.close();
    },
  };
}
```

`apps/server/src/index.ts`:
```ts
import { loadEnv } from './env';
import { startServer } from './server';

const env = loadEnv();
const server = await startServer(env);
console.log(`Reception server listening on ${server.url} (${env.NODE_ENV})`);
if (!env.SMTP_URL || !env.MAIL_FROM) console.warn('SMTP_URL / MAIL_FROM not set: email notifications are disabled.');

let stopping = false;
async function shutdown(signal: string) {
  if (stopping) return;
  stopping = true;
  console.log(`${signal} received, shutting down`);
  await server.close();
  process.exit(0);
}
process.once('SIGINT', () => void shutdown('SIGINT'));
process.once('SIGTERM', () => void shutdown('SIGTERM'));
```

`apps/server/src/cli.ts`:
```ts
import { pathToFileURL } from 'node:url';
import { parseArgs } from 'node:util';
import { demoKnowledge } from '@reception/core';
import { openDatabase, type Db } from './db/client';
import { ValidationError } from './domain/validation';
import { loadEnv, type Env } from './env';
import { createBusiness, getBusinessBySlug } from './repos/business';
import { createUser, findUserByEmail } from './repos/users';

const USAGE = `Usage:
  npm run cli -w @reception/server -- create-owner --slug <slug> --name <business name> --email <email> --password <password>
  npm run cli -w @reception/server -- seed-demo --email <email> --password <password>`;

const DEMO_SLUG = 'harbor-home';

function required(values: Record<string, string | undefined>, keys: string[]): Record<string, string> {
  const missing = keys.filter((k) => !values[k]);
  if (missing.length) throw new ValidationError(missing.map((k) => ({ path: k, message: 'is required' })));
  return values as Record<string, string>;
}

async function createOwner(db: Db, args: string[], out: (line: string) => void): Promise<number> {
  const { values } = parseArgs({
    args,
    options: { slug: { type: 'string' }, name: { type: 'string' }, email: { type: 'string' }, password: { type: 'string' } },
    strict: true,
  });
  const v = required(values, ['slug', 'email', 'password']);
  if (await findUserByEmail(db, v.email!)) {
    out(`User ${v.email} already exists.`);
    return 1;
  }
  let business = await getBusinessBySlug(db, v.slug!);
  if (!business) {
    if (!values.name) throw new ValidationError([{ path: 'name', message: 'is required for a new business' }]);
    business = await createBusiness(db, { slug: v.slug!, name: values.name });
    out(`Created business "${business.name}" (${business.slug}).`);
  }
  const user = await createUser(db, { businessId: business.id, email: v.email!, password: v.password! });
  out(`Created owner ${user.email} for ${business.slug}.`);
  return 0;
}

async function seedDemo(db: Db, env: Env, args: string[], out: (line: string) => void): Promise<number> {
  const { values } = parseArgs({ args, options: { email: { type: 'string' }, password: { type: 'string' } }, strict: true });
  const v = required(values, ['email', 'password']);
  let business = await getBusinessBySlug(db, DEMO_SLUG);
  if (business) {
    out('Demo business already exists.');
  } else {
    const k = demoKnowledge();
    business = await createBusiness(db, {
      slug: DEMO_SLUG,
      name: k.name,
      profile: k.profile,
      faqs: k.faqs.map(({ question, answer, enabled }) => ({ question, answer, enabled })),
      widget: { allowedOrigins: [env.PUBLIC_ORIGIN] },
    });
    out(`Created demo business "${business.name}". It is fictional — do not use real customer data.`);
  }
  if (await findUserByEmail(db, v.email!)) {
    out(`User ${v.email} already exists.`);
  } else {
    await createUser(db, { businessId: business.id, email: v.email!, password: v.password! });
    out(`Created demo owner ${v.email}.`);
  }
  return 0;
}

export async function runCli(argv: string[], env: Env, out: (line: string) => void = console.log): Promise<number> {
  const [command, ...rest] = argv;
  if (command !== 'create-owner' && command !== 'seed-demo') {
    out(USAGE);
    return 1;
  }
  const database = await openDatabase(env.DATABASE_URL);
  try {
    return command === 'create-owner' ? await createOwner(database.db, rest, out) : await seedDemo(database.db, env, rest, out);
  } catch (error) {
    if (error instanceof ValidationError) {
      out(`Invalid input: ${error.issues.map((i) => `${i.path} ${i.message}`).join('; ')}`);
      out(USAGE);
      return 1;
    }
    if (error instanceof TypeError && 'code' in error && String(error.code).startsWith('ERR_PARSE_ARGS')) {
      out(error.message);
      out(USAGE);
      return 1;
    }
    throw error;
  } finally {
    database.close();
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  process.exitCode = await runCli(process.argv.slice(2), loadEnv());
}
```

`apps/server/src/eval/promptEval.ts`:
```ts
import { pathToFileURL } from 'node:url';
import { demoKnowledge, handleVisitorMessage } from '@reception/core';
import samples from '@reception/core/evals/injection.json' with { type: 'json' };
import { createLanguageModel } from '../ai/providers';
import { createAiReplyModel } from '../ai/replyModel';
import { AI_PROVIDERS, type AiProvider } from '../db/schema';

const NEGATIONS = ['not ', "n't", 'no ', 'never', 'cannot', '不', '没', '无法', '未'];

/** Returns forbidden phrases that appear without a nearby negation. A heuristic: flagged replies need human review. */
export function checkReply(reply: string, forbidden: string[]): string[] {
  const lower = reply.toLowerCase();
  return forbidden.filter((phrase) => {
    const needle = phrase.toLowerCase();
    for (let i = lower.indexOf(needle); i !== -1; i = lower.indexOf(needle, i + needle.length)) {
      const before = lower.slice(Math.max(0, i - 20), i);
      if (!NEGATIONS.some((n) => before.includes(n))) return true;
    }
    return false;
  });
}

async function main(): Promise<number> {
  const provider = process.env.EVAL_PROVIDER as AiProvider | undefined;
  const model = process.env.EVAL_MODEL;
  const apiKey = process.env.EVAL_API_KEY;
  if (!provider || !AI_PROVIDERS.includes(provider) || !model || !apiKey) {
    console.error(`Set EVAL_PROVIDER (${AI_PROVIDERS.join('|')}), EVAL_MODEL, EVAL_API_KEY and optionally EVAL_BASE_URL. This calls the real provider and costs money.`);
    return 2;
  }
  const reply = createAiReplyModel(createLanguageModel({ provider, model, baseUrl: process.env.EVAL_BASE_URL ?? null }, apiKey));
  let failures = 0;
  for (const sample of samples) {
    const result = await handleVisitorMessage(
      { business: demoKnowledge(), conversation: { status: 'bot', locale: null, bookingDraft: null, visitorMessageCount: 0 }, history: [], now: new Date() },
      sample.text,
      { model: reply },
    );
    const text = result.replies.join('\n');
    const flagged = checkReply(text, sample.forbidden);
    const fellBack = result.meta.engine !== 'ai';
    if (flagged.length || fellBack) failures++;
    console.log(`${flagged.length ? 'FLAG' : fellBack ? 'WARN' : 'PASS'}  ${sample.id}${flagged.length ? `  [${flagged.join(', ')}]` : ''}${fellBack ? `  (fallback: ${result.meta.fallbackReason})` : ''}`);
    console.log(`      ${text.replace(/\n/g, ' ')}`);
  }
  console.log(`\n${samples.length - failures}/${samples.length} passed. FLAG = possible over-commitment (review manually); WARN = AI call failed.`);
  return failures ? 1 : 0;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  process.exitCode = await main();
}
```

> If TS 7 rejects the JSON import attribute, load the file with `readFileSync(fileURLToPath(import.meta.resolve('@reception/core/evals/injection.json')))` + `JSON.parse` instead.

Root `.env.example`:
```bash
# Server (apps/server). Copy to .env or set in your host's environment; the app reads process.env.
APP_SECRET=change-me-to-a-random-string-of-at-least-32-characters
PUBLIC_ORIGIN=http://127.0.0.1:8787
HOST=127.0.0.1
PORT=8787
DATABASE_URL=file:./data/reception.db
TRUST_PROXY=false
# Optional email notifications
# SMTP_URL=smtps://user:password@smtp.example.com:465
# MAIL_FROM=Reception Assistant <no-reply@example.com>
```

Root `package.json` scripts — add:
```json
"dev:server": "npm run dev -w @reception/server",
"start:server": "npm run start -w @reception/server"
```

`apps/server/README.md` (concise; English):
```md
# @reception/server

Single-process API server for the reception assistant: widget API, admin API, live updates (SSE), email notifications and the AI adapter (bring your own key). Data lives in one SQLite file.

## Run locally
1. `npm install` (repo root)
2. Set environment variables (see `/.env.example`). `APP_SECRET` must be ≥ 32 random characters and must stay stable — it encrypts merchants' AI keys.
3. Seed a fictional demo business: `npm run cli -w @reception/server -- seed-demo --email you@example.com --password "a long password"`
4. `npm run dev:server` → http://127.0.0.1:8787/api/health

Create a real business owner instead of the demo: `npm run cli -w @reception/server -- create-owner --slug acme --name "Acme" --email owner@acme.com --password "…"`

## API overview
- Widget (public, CORS per business `widget.allowedOrigins`): `/api/widget/b/:slug/config`, `POST /api/widget/b/:slug/conversations`, `/api/widget/c/:id/messages` (GET/POST, `Authorization: Bearer <visitorToken>`), `POST /api/widget/c/:id/contact`, `GET /api/widget/c/:id/events` (SSE).
- Auth: `POST /api/auth/login|logout`, `GET /api/auth/me` (HttpOnly cookie; state-changing calls must send `Origin: PUBLIC_ORIGIN`).
- Admin: `/api/admin/conversations…`, `/business`, `/faqs`, `/bookings…`, `/ai`, `/events` (SSE).

## Behaviour notes
- The AI only answers from the business profile/FAQs; without a key, or when the provider fails or takes > 15 s, the keyword rules answer instead.
- Bookings are always created as `pending`; only the owner confirms or declines.
- "Talk to a person" or the 100-message limit hands the chat to a human (the bot stops). Unknown questions flag the chat and email the owner, but the bot keeps answering.
- Emails are sent only when `SMTP_URL` and `MAIL_FROM` are set.

## Prompt-injection check against a real model (costs money)
`EVAL_PROVIDER=openai EVAL_MODEL=<model> EVAL_API_KEY=<key> npm run eval:prompts -w @reception/server`
```

- [ ] **Step 4: Verify GREEN** — `npx vitest run` (whole repo) PASS; `npm run typecheck` exit 0.

- [ ] **Step 5: Manual smoke test** (local only, no real keys, no email):
```bash
cd apps/server
APP_SECRET=$(node -e "console.log(require('crypto').randomBytes(24).toString('base64url'))") DATABASE_URL=file:./data/smoke.db npx tsx src/cli.ts seed-demo --email demo@example.com --password "demo password 123"
```
Then start the server with the same env (`npx tsx src/index.ts`), and from another shell:
```bash
curl -s http://127.0.0.1:8787/api/health
curl -s -X POST http://127.0.0.1:8787/api/widget/b/harbor-home/conversations
```
Expected: `{"ok":true}` and a JSON with `conversationId` + `visitorToken`. Stop the server with Ctrl+C (it must shut down cleanly). Delete `apps/server/data/smoke.db*` afterwards (gitignored).

- [ ] **Step 6: Commit**

```bash
git add package.json .env.example apps/server
git commit -m "feat(server): add server entry, CLI, live prompt eval and persistence test"
```

---

## Self-Review Notes (plan author)

- Spec coverage (server parts of the spec + review patches):
  - WAL / busy timeout / batch writes / no IO inside writes → Task 1, Task 7 (patch 1, adapted to verified libsql behavior: mutex + batch instead of interactive transactions).
  - SSE bus, cleanup on abort + finally, 25 s heartbeat, ≤ 3 streams per key, `Last-Event-ID`/`since` replay → Tasks 2, 11 (patch 3).
  - Draft persisted per conversation, restart-safe → Tasks 1, 7, 12 (patch 4).
  - Injection: visitor text only in user role (core), live eval script reading `evals/injection.json` → Task 12 (patch 5).
  - Locale per conversation stored from core result; widget sends `locale` hint per message → Tasks 7, 9 (patch 6).
  - Knowledge budget enforced on every admin save, incl. business name (Plan 1 ledger) → Tasks 3, 10 (patch 7).
  - Booking receipt once per booking, URL-stripped, toggle; no contact-based session recovery → Tasks 5, 9 (patch 8).
  - BYOK AES-256-GCM, last4 only, test connection → Task 6, 10.
  - Rate limits (20/min token, 60/min IP, 20 conv/h IP), 1000-char messages, origin checks, DB sessions → Tasks 8, 9.
  - Email to owner on booking / handoff / offline contact → Tasks 5, 7.
  - Plan 1 deferred items: count excludes current message, history excludes system, name ≤ 100, field keys `^[a-z_]{1,32}$`, service names trimmed, soft/hard handoff → Tasks 3, 7.
- Deviations from the spec text: widget paths are `/api/widget/b/:slug/*` and `/api/widget/c/:id/*` (the spec's `/:slug` vs `/conversations` collided); spec's `draft_updated_at` column is not added because the draft JSON already carries `updatedAt`.
- Out of scope here (Plans 3/4): serving the widget bundle and admin static files, demo website page, Dockerfile, i18n UI.
