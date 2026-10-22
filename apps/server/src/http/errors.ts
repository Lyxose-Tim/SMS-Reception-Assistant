import type { Context } from 'hono';
import { HTTPException } from 'hono/http-exception';
import { ValidationError } from '../domain/validation';

export function handleError(err: Error, c: Context): Response {
  if (err instanceof HTTPException) {
    if (err.res) return err.getResponse();
    return c.json({ error: err.message || 'http_error' }, err.status);
  }
  if (err instanceof ValidationError) return c.json({ error: 'validation', issues: err.issues }, 422);
  // Log name/message/stack only: ORM errors embed bound query parameters on the error object.
  console.error(`[error] ${err.name}: ${err.message}`);
  if (err.stack) console.error(err.stack);
  return c.json({ error: 'internal' }, 500);
}
