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
    const rows = await listFaqs(database.db, business.id);
    const [first, second] = rows;
    await updateFaq(database.db, business.id, first!.id, { enabled: false }, now);
    await updateFaq(database.db, business.id, second!.id, { sort: 50 }, now);
    const k = await loadKnowledge(database.db, business);
    const expected = rows.slice(2).map((r) => r.question);
    expected.push(second!.question);
    expect(k.faqs.map((f) => f.question)).toEqual(expected);
    expect(k.faqs.some((f) => f.id === first!.id)).toBe(false);
  });

  it('appends new FAQs after existing ones by default', async () => {
    const created = await createFaq(database.db, business.id, { question: 'Newest?', answer: 'Yes.' });
    const rows = await listFaqs(database.db, business.id);
    expect(rows.at(-1)?.id).toBe(created.id);
    expect(created.sort).toBe(Math.max(...rows.filter((r) => r.id !== created.id).map((r) => r.sort)) + 1);
    const explicit = await createFaq(database.db, business.id, { question: 'Pinned?', answer: 'Yes.', sort: 0 });
    expect(explicit.sort).toBe(0);
  });

  it('rejects invalid slugs and duplicate slugs', async () => {
    await expectValidation(createBusiness(database.db, { slug: 'Bad Slug', name: 'X' }), 'slug');
    await expectValidation(createBusiness(database.db, { slug: 'harbor-home', name: 'X' }), 'slug');
  });

  it('validates everything createBusiness stores', async () => {
    await expectValidation(createBusiness(database.db, { slug: 'blank-name', name: '   ' }), 'name');
    await expectValidation(createBusiness(database.db, { slug: 'long-name', name: 'n'.repeat(101) }), 'name');
    await expectValidation(createBusiness(database.db, { slug: 'bad-locale', name: 'X', locale: 'fr' as never }), 'locale');
    await expectValidation(createBusiness(database.db, { slug: 'bad-color', name: 'X', widget: { primaryColor: 'red' } }), 'primaryColor');
    await expectValidation(createBusiness(database.db, { slug: 'bad-notify', name: 'X', notify: { ownerEmail: 'nope' } }), 'ownerEmail');
    const trimmed = await createBusiness(database.db, { slug: 'trimmed', name: '  Spaced  ' });
    expect(trimmed.name).toBe('Spaced');
  });

  it('rejects more than 30 enabled FAQs when creating a business', async () => {
    const faqs = Array.from({ length: 31 }, (_, i) => ({ question: `Q${i}?`, answer: 'Short.', enabled: true }));
    await expectValidation(createBusiness(database.db, { slug: 'many-faqs', name: 'X', faqs }), 'faqs');
  });

  it('normalizes and dedupes allowed origins', async () => {
    const created = await createBusiness(database.db, {
      slug: 'origins',
      name: 'X',
      widget: { allowedOrigins: ['HTTPS://Shop.Example:443', 'https://shop.example', 'http://localhost:3000', 'https://shop.example/'] },
    });
    expect(created.widget.allowedOrigins).toEqual(['https://shop.example', 'http://localhost:3000']);
    const updated = await updateBusiness(
      database.db,
      business.id,
      { widget: { ...business.widget, allowedOrigins: ['http://A.example:80', 'http://a.example'] } },
      now,
    );
    expect(updated?.widget.allowedOrigins).toEqual(['http://a.example']);
  });

  it('rejects unsafe or malformed origins', async () => {
    for (const bad of ['https://user@x.example', 'https://user:pw@x.example', 'https://x.example/path', 'https://x.example?q=1', 'https://x.example#h', 'https://*.x.example', 'ftp://x.example', 'not a url']) {
      await expectValidation(
        updateBusiness(database.db, business.id, { widget: { ...business.widget, allowedOrigins: [bad] } }, now),
        'widget.allowedOrigins.0',
      );
    }
  });

  it('validates timezones', async () => {
    await expectValidation(updateBusiness(database.db, business.id, { timezone: 'Mars/Olympus' }, now), 'timezone');
    const ok = await updateBusiness(database.db, business.id, { timezone: 'Asia/Shanghai' }, now);
    expect(ok?.timezone).toBe('Asia/Shanghai');
  });

  it('requires a required contact booking field', async () => {
    const label = { en: 'a', 'zh-CN': 'a' };
    await expectValidation(
      updateBusiness(database.db, business.id, { bookingFields: [{ key: 'name', label, kind: 'text', required: true }, { key: 'contact', label, kind: 'contact', required: false }] }, now),
      'bookingFields',
    );
    const ok = await updateBusiness(database.db, business.id, { bookingFields: [{ key: 'contact', label, kind: 'contact', required: true }] }, now);
    expect(ok?.bookingFields).toHaveLength(1);
  });

  it('rejects profiles whose rendered text exceeds the profile budget', async () => {
    const k = demoKnowledge();
    const profile = { ...k.profile, about: 'a'.repeat(1000), hours: 'h'.repeat(300), priceNote: 'p'.repeat(300), address: 'x'.repeat(200) };
    await expectValidation(updateBusiness(database.db, business.id, { profile }, now), 'profile');
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

  it('rejects patching an FAQ answer beyond 500 characters', async () => {
    const [faq] = await listFaqs(database.db, business.id);
    await expectValidation(updateFaq(database.db, business.id, faq!.id, { answer: 'a'.repeat(501) }, now), 'answer');
  });

  it('re-checks the knowledge budget when re-enabling a disabled FAQ', async () => {
    const long = await createFaq(database.db, business.id, { question: 'Long one?', answer: 'a'.repeat(500), enabled: false });
    for (let i = 0; i < 21; i++) {
      await createFaq(database.db, business.id, { question: `Question ${i}?`, answer: 'a'.repeat(470) });
    }
    await expectValidation(updateFaq(database.db, business.id, long.id, { enabled: true }, now), 'knowledge');
    const rows = await listFaqs(database.db, business.id);
    expect(rows.find((r) => r.id === long.id)?.enabled).toBe(false);
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
    const reread = (await listFaqs(database.db, business.id)).find((r) => r.id === faq!.id);
    expect(reread?.answer).toBe(faq!.answer);
    expect(reread?.answer).not.toBe('hacked');
    expect(await deleteFaq(database.db, other.id, faq!.id)).toBe(false);
    expect(await deleteFaq(database.db, business.id, faq!.id)).toBe(true);
    expect(await listFaqs(database.db, business.id)).toHaveLength(4);
  });
});
