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
