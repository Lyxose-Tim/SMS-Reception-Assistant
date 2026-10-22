import type { ReplyModel } from '@reception/core';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createTestHarness, type TestHarness } from '../testing/harness';
import { createAppDeps } from './deps';

let h: TestHarness;
beforeEach(async () => {
  h = await createTestHarness();
});
afterEach(() => {
  vi.restoreAllMocks();
  h.close();
});

const fakeModel = { reply: async () => ({ text: 'hi', toolCalls: [] }) } as unknown as ReplyModel;

describe('createAppDeps AI daily call budget', () => {
  it('stops handing out the model after the daily limit and resets on the next day', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    const deps = createAppDeps({ env: h.env, db: h.database.db, mailer: null, now: () => h.clock.now, resolveModel: () => fakeModel, aiDailyCallLimit: 2 });
    expect(deps.resolveModel(h.business)).toBe(fakeModel);
    expect(deps.resolveModel(h.business)).toBe(fakeModel);
    expect(deps.resolveModel(h.business)).toBeNull();
    expect(deps.resolveModel(h.business)).toBeNull();
    expect(warn).toHaveBeenCalledTimes(1);
    expect(String(warn.mock.calls[0]![0])).toBe(`AI daily call limit reached for business ${h.business.id}; using rules until tomorrow.`);
    h.clock.now = new Date(h.clock.now.getTime() + 24 * 60 * 60_000);
    expect(deps.resolveModel(h.business)).toBe(fakeModel);
  });

  it('does not count businesses without a model and tracks businesses separately', () => {
    vi.spyOn(console, 'warn').mockImplementation(() => {});
    let model: ReplyModel | null = null;
    const deps = createAppDeps({ env: h.env, db: h.database.db, mailer: null, now: () => h.clock.now, resolveModel: () => model, aiDailyCallLimit: 1 });
    expect(deps.resolveModel(h.business)).toBeNull();
    model = fakeModel;
    expect(deps.resolveModel(h.business)).toBe(fakeModel);
    expect(deps.resolveModel(h.business)).toBeNull();
    expect(deps.resolveModel({ ...h.business, id: 'other-business' })).toBe(fakeModel);
  });

  it('takes the limit from AI_DAILY_CALL_LIMIT (default 500)', async () => {
    const { loadEnv } = await import('../env');
    const base = { APP_SECRET: 'test-secret-'.padEnd(40, 'x') };
    expect(loadEnv(base).AI_DAILY_CALL_LIMIT).toBe(500);
    expect(loadEnv({ ...base, AI_DAILY_CALL_LIMIT: '' }).AI_DAILY_CALL_LIMIT).toBe(500);
    expect(loadEnv({ ...base, AI_DAILY_CALL_LIMIT: '25' }).AI_DAILY_CALL_LIMIT).toBe(25);
    expect(() => loadEnv({ ...base, AI_DAILY_CALL_LIMIT: '0' })).toThrow(/AI_DAILY_CALL_LIMIT/);
    expect(() => loadEnv({ ...base, AI_DAILY_CALL_LIMIT: '-3' })).toThrow(/AI_DAILY_CALL_LIMIT/);
  });
});
