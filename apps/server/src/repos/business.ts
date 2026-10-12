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
