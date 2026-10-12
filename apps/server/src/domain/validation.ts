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
