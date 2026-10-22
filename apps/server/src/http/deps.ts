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
  /** Per normalized email, independent of the source IP (blunts distributed guessing). */
  loginEmail: RateLimiter;
  visitorMessage: RateLimiter;
  ipMessage: RateLimiter;
  newConversation: RateLimiter;
  contact: RateLimiter;
}

export function createLimiters(): Limiters {
  return {
    login: createRateLimiter(5, 5 * 60_000),
    loginEmail: createRateLimiter(20, 15 * 60_000),
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
