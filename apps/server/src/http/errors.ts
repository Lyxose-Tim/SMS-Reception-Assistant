import type { Context } from 'hono';
import { HTTPException } from 'hono/http-exception';
import { ValidationError } from '../domain/validation';

export function handleError(err: Error, c: Context): Response {
  if (err instanceof HTTPException) {
    if (err.res) return err.getResponse();
    return c.json({ error: err.message || 'http_error' }, err.status);
  }
  if (err instanceof ValidationError) return c.json({ error: 'validation', issues: err.issues }, 422);
  const query = (err as { query?: unknown }).query;
  if (err.name === 'DrizzleQueryError' || typeof query === 'string') {
    // ORM errors embed the bound parameters (user data) in message, stack and a params property: log only the
    // error name, the SQL text (placeholders, no values) and the driver's own message from the cause.
    const cause = err.cause instanceof Error ? err.cause.message : '';
    console.error(`[error] ${err.name}: query failed: ${typeof query === 'string' ? query : 'unknown query'}${cause ? ` (${cause})` : ''}`);
  } else {
    // Never log the error object itself; name, message and stack only.
    console.error(`[error] ${err.name}: ${err.message}`);
    if (err.stack) console.error(err.stack);
  }
  return c.json({ error: 'internal' }, 500);
}
