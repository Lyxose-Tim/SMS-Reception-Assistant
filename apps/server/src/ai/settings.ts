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
