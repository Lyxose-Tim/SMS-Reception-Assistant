import type { Context } from 'hono';
import { HTTPException } from 'hono/http-exception';
import { ValidationError } from '../domain/validation';

export function handleError(err: Error, c: Context): Response {
  if (err instanceof HTTPException) return c.json({ error: err.message || 'http_error' }, err.status);
  if (err instanceof ValidationError) return c.json({ error: 'validation', issues: err.issues }, 422);
  console.error(err);
  return c.json({ error: 'internal' }, 500);
}
