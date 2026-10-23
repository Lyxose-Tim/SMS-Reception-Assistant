import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
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
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    expect(resolveReplyModel(row, deriveKey('t'.repeat(32), 'ai-api-keys'), factory)).toBeNull();
    warn.mockRestore();
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

  it('requires re-entering the key when the provider or base URL changes', async () => {
    await saveAiSettings(database.db, business, { provider: 'openai', model: 'gpt-x', apiKey, enabled: false }, key, now);
    const row = await reload();
    const apiKeyIssue = { path: 'apiKey', message: 'Re-enter the API key when changing the provider or base URL' };
    for (const input of [
      { provider: 'openai-compatible', baseUrl: 'https://other.example/v1', model: 'm', enabled: false },
      { provider: 'openai', baseUrl: 'https://other.example/v1', model: 'm', enabled: false },
      { provider: 'anthropic', model: 'm', enabled: false },
    ]) {
      const error = await saveAiSettings(database.db, row, input, key, now).catch((e: unknown) => e);
      expect(error).toBeInstanceOf(ValidationError);
      expect((error as ValidationError).issues).toContainEqual(apiKeyIssue);
    }
    expect((await reload()).ai.keyLast4).toBe('5678');
    const newKey = 'sk-new-0000111122223333';
    await saveAiSettings(database.db, row, { provider: 'openai-compatible', baseUrl: 'https://other.example/v1', model: 'm', apiKey: newKey, enabled: false }, key, now);
    expect((await reload()).ai).toMatchObject({ provider: 'openai-compatible', baseUrl: 'https://other.example/v1', keyLast4: '3333' });
    await saveAiSettings(database.db, await reload(), { provider: 'openai-compatible', baseUrl: 'https://other.example/v1', model: 'm2', enabled: false }, key, now);
    expect((await reload()).ai.keyLast4).toBe('3333');
  });

  it('restricts base URLs', async () => {
    const base = { provider: 'openai-compatible', model: 'm', apiKey, enabled: false };
    for (const baseUrl of [
      'file:///etc/passwd',
      'ftp://x.example/v1',
      'http://169.254.169.254/',
      'http://10.0.0.5/v1',
      'https://u:p@x.example/v1',
      'https://:p@x.example/v1',
      'https://x.example/v1?x=1',
      'https://x.example/v1#frag',
      'not a url',
    ]) {
      const error = await saveAiSettings(database.db, business, { ...base, baseUrl }, key, now).catch((e: unknown) => e);
      expect(error, baseUrl).toBeInstanceOf(ValidationError);
      expect((error as ValidationError).issues.map((i) => i.path), baseUrl).toContain('baseUrl');
    }
    for (const baseUrl of ['https://api.deepseek.com/v1', 'http://localhost:11434/v1', 'http://127.0.0.1:8080/v1', 'http://[::1]:8080/v1']) {
      await expect(saveAiSettings(database.db, business, { ...base, baseUrl }, key, now), baseUrl).resolves.toMatchObject({ baseUrl });
    }
    await expect(saveAiSettings(database.db, business, { provider: 'openai', model: 'm', apiKey, baseUrl: 'https://proxy.example/v1', enabled: false }, key, now)).resolves.toBeDefined();
    for (const provider of ['anthropic', 'google', null]) {
      const error = await saveAiSettings(database.db, business, { provider, model: 'm', apiKey, baseUrl: 'https://x.example/v1', enabled: false }, key, now).catch((e: unknown) => e);
      expect(error).toBeInstanceOf(ValidationError);
      expect((error as ValidationError).issues.map((i) => i.path)).toContain('baseUrl');
    }
  });

  it('rejects API keys shorter than 16 characters', async () => {
    await expect(saveAiSettings(database.db, business, { provider: 'openai', model: 'm', apiKey: 'a'.repeat(15), enabled: false }, key, now)).rejects.toBeInstanceOf(ValidationError);
    await expect(saveAiSettings(database.db, business, { provider: 'openai', model: 'm', apiKey: 'a'.repeat(16), enabled: false }, key, now)).resolves.toBeDefined();
  });

  it('also redacts the URL-encoded key from connection errors', async () => {
    const specialKey = 'sk/live+abcdefgh=12345678';
    await saveAiSettings(database.db, business, { provider: 'openai', model: 'gpt-x', apiKey: specialKey, enabled: false }, key, now);
    const row = await reload();
    const failing = await testAiConnection(row, key, () =>
      mockLanguageModel({ error: new Error(`GET /v1?key=${encodeURIComponent(specialKey)} failed; raw ${specialKey}`) }),
    );
    expect(failing.ok).toBe(false);
    expect(JSON.stringify(failing)).not.toContain(encodeURIComponent(specialKey));
    expect(JSON.stringify(failing)).not.toContain(specialKey);
  });

  it('warns once, without key material, when the stored key cannot be decrypted', async () => {
    await saveAiSettings(database.db, business, { provider: 'openai', model: 'gpt-x', apiKey, enabled: true }, key, now);
    const row = await reload();
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    try {
      expect(resolveReplyModel(row, deriveKey('t'.repeat(32), 'ai-api-keys'), () => mockLanguageModel({ text: 'hi' }))).toBeNull();
      expect(warn).toHaveBeenCalledTimes(1);
      expect(warn).toHaveBeenCalledWith('AI is enabled but the stored API key could not be decrypted (was APP_SECRET changed?); using rules fallback.');
    } finally {
      warn.mockRestore();
    }
  });
});
