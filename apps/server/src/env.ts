import { z } from 'zod';

/** Returns the canonical origin (lowercase host, default port dropped) for a bare http(s) origin, else null. */
function normalizeOrigin(value: string): string | null {
  let url: URL;
  try {
    url = new URL(value.trim());
  } catch {
    return null;
  }
  if (url.protocol !== 'http:' && url.protocol !== 'https:') return null;
  if (url.username || url.password || url.search || url.hash || url.pathname !== '/') return null;
  return url.origin;
}

const EnvSchema = z.object({
  NODE_ENV: z.enum(['development', 'test', 'production']).default('development'),
  HOST: z.string().min(1).default('127.0.0.1'),
  PORT: z.coerce.number().int().min(0).max(65535).default(8787),
  DATABASE_URL: z.string().min(1).default('file:./data/reception.db'),
  APP_SECRET: z.string({ error: 'APP_SECRET is required' }).min(32, { error: 'APP_SECRET must be at least 32 characters' }),
  PUBLIC_ORIGIN: z
    .string()
    .default('http://127.0.0.1:8787')
    .transform((v, ctx) => {
      const origin = normalizeOrigin(v);
      if (!origin) ctx.issues.push({ code: 'custom', message: 'PUBLIC_ORIGIN must be an origin like https://chat.example.com', input: v });
      return origin ?? v;
    }),
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
