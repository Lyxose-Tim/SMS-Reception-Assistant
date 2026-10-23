import { fileURLToPath } from 'node:url';
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

/** apps/widget/dist/widget.js, resolved from this file so it works from any cwd. */
export const DEFAULT_WIDGET_FILE = fileURLToPath(new URL('../../../widget/dist/widget.js', import.meta.url));

export interface Limiters {
  /** Per source IP only, whatever the email: stops one host rotating emails to dodge the per-email limits. */
  loginIp: RateLimiter;
  login: RateLimiter;
  /** Per normalized email, independent of the source IP (blunts distributed guessing). */
  loginEmail: RateLimiter;
  visitorMessage: RateLimiter;
  ipMessage: RateLimiter;
  newConversation: RateLimiter;
  contact: RateLimiter;
  /** Visitor receipt emails per source IP (spec: throttle per IP and per session). */
  receiptIp: RateLimiter;
  /** Visitor receipt emails per conversation. */
  receiptConversation: RateLimiter;
}

export function createLimiters(): Limiters {
  return {
    loginIp: createRateLimiter(30, 5 * 60_000),
    login: createRateLimiter(5, 5 * 60_000),
    loginEmail: createRateLimiter(20, 15 * 60_000),
    visitorMessage: createRateLimiter(20, 60_000),
    ipMessage: createRateLimiter(60, 60_000),
    newConversation: createRateLimiter(20, 60 * 60_000),
    contact: createRateLimiter(5, 60 * 60_000),
    receiptIp: createRateLimiter(3, 60 * 60_000),
    receiptConversation: createRateLimiter(1, 24 * 60 * 60_000),
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
  /** True when a mailer is configured (owner notifications and visitor receipts can actually be sent). */
  emailEnabled: boolean;
  /** Built widget bundle served at /widget.js. */
  widgetFile: string;
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
  /** Overrides env.AI_DAILY_CALL_LIMIT. */
  aiDailyCallLimit?: number;
  /** Overrides the built widget bundle path (tests). */
  widgetFile?: string;
}

/** Caps model-backed replies per business per UTC day; beyond the cap the engine falls back to rules. */
function limitDailyModelCalls(
  resolve: (business: BusinessRow) => ReplyModel | null,
  limit: number,
  now: () => Date,
): (business: BusinessRow) => ReplyModel | null {
  const usage = new Map<string, { day: string; count: number; warned: boolean }>();
  return (business) => {
    const model = resolve(business);
    if (!model) return null;
    const day = now().toISOString().slice(0, 10);
    let entry = usage.get(business.id);
    if (!entry || entry.day !== day) {
      entry = { day, count: 0, warned: false };
      usage.set(business.id, entry);
    }
    if (entry.count >= limit) {
      if (!entry.warned) {
        entry.warned = true;
        console.warn(`AI daily call limit reached for business ${business.id}; using rules until tomorrow.`);
      }
      return null;
    }
    entry.count += 1;
    return model;
  };
}

export function createAppDeps(input: CreateAppDepsInput): AppDeps {
  const now = input.now ?? (() => new Date());
  const secretKey = deriveKey(input.env.APP_SECRET, 'ai-api-keys');
  const languageModelFactory = input.languageModelFactory ?? createLanguageModel;
  const limiters = createLimiters();
  const resolveModel = limitDailyModelCalls(
    input.resolveModel ?? ((business) => resolveReplyModel(business, secretKey, languageModelFactory)),
    input.aiDailyCallLimit ?? input.env.AI_DAILY_CALL_LIMIT,
    now,
  );
  return {
    env: input.env,
    db: input.db,
    bus: createEventBus(),
    notifier: createNotifier({
      db: input.db,
      mailer: input.mailer,
      publicOrigin: input.env.PUBLIC_ORIGIN,
      now,
      receiptGuard: {
        // Conversation first so a denied repeat does not burn the IP's budget; both must pass.
        allow: ({ ip, conversationId }) => limiters.receiptConversation.hit(conversationId) && limiters.receiptIp.hit(ip ?? 'unknown'),
      },
    }),
    mutex: createKeyedMutex(),
    now,
    secretKey,
    emailEnabled: input.mailer !== null,
    widgetFile: input.widgetFile ?? DEFAULT_WIDGET_FILE,
    languageModelFactory,
    resolveModel,
    limiters,
    sse: createConnectionRegistry(MAX_STREAMS_PER_KEY),
    heartbeatMs: input.heartbeatMs ?? HEARTBEAT_MS,
    ...(input.modelTimeoutMs ? { modelTimeoutMs: input.modelTimeoutMs } : {}),
  };
}
