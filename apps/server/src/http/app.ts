import { Hono } from 'hono';
import { bodyLimit } from 'hono/body-limit';
import type { AppDeps } from './deps';
import { handleError } from './errors';
import { authRoutes } from './routes/auth';
import type { AppEnv } from './types';

export const MAX_BODY_BYTES = 32 * 1024;

export function createApp(deps: AppDeps): Hono<AppEnv> {
  const app = new Hono<AppEnv>();
  app.onError(handleError);
  app.use('/api/*', bodyLimit({ maxSize: MAX_BODY_BYTES, onError: (c) => c.json({ error: 'payload_too_large' }, 413) }));
  app.get('/api/health', (c) => c.json({ ok: true }));
  app.route('/api/auth', authRoutes(deps));
  return app;
}
