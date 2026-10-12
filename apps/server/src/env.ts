import { z } from 'zod';

const EnvSchema = z.object({
  NODE_ENV: z.enum(['development', 'test', 'production']).default('development'),
  HOST: z.string().min(1).default('127.0.0.1'),
  PORT: z.coerce.number().int().min(0).max(65535).default(8787),
  DATABASE_URL: z.string().min(1).default('file:./data/reception.db'),
  APP_SECRET: z.string({ error: 'APP_SECRET is required' }).min(32, { error: 'APP_SECRET must be at least 32 characters' }),
  PUBLIC_ORIGIN: z
    .string()
    .default('http://127.0.0.1:8787')
    .transform((v) => v.replace(/\/+$/, ''))
    .refine((v) => /^https?:\/\/[^/\s]+$/.test(v), { error: 'PUBLIC_ORIGIN must be an origin like https://chat.example.com' }),
  TRUST_PROXY: z
    .enum(['true', 'false'])
    .default('false')
    .transform((v) => v === 'true'),
  SMTP_URL: z.string().min(1).optional(),
  MAIL_FROM: z.string().min(1).optional(),
});

export type Env = z.infer<typeof EnvSchema>;

export function loadEnv(source: Record<string, string | undefined> = process.env): Env {
  const result = EnvSchema.safeParse(source);
  if (!result.success) {
    const details = result.error.issues.map((i) => `${i.path.join('.') || 'env'}: ${i.message}`).join('; ');
    throw new Error(`Invalid environment: ${details}`);
  }
  return result.data;
}
