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
