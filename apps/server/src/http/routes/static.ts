import { readFile } from 'node:fs/promises';
import { Hono } from 'hono';
import type { AppDeps } from '../deps';
import type { AppEnv } from '../types';

const SLUG = /^[a-z0-9-]{3,40}$/;
const DEFAULT_DEMO_SLUG = 'harbor-home';
// The widget injects a <style> element into its shadow root, which counts as an inline style.
const DEMO_CSP = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data:";

function demoPage(slug: string): string {
  return `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Reception widget demo</title>
<style>
  body { font-family: system-ui, sans-serif; margin: 0; padding: 48px 24px; background: #f8fafc; color: #0f172a; }
  main { max-width: 720px; margin: 0 auto; }
</style>
</head>
<body>
<main>
  <h1>Harbor &amp; Home Cleaning</h1>
  <p>This is a fictional demo website. Use the chat button in the corner to ask about hours, services or to request a booking.</p>
  <p>Do not enter real personal information.</p>
</main>
<script src="/widget.js" data-business="${slug}" async></script>
</body>
</html>`;
}

export function staticRoutes(deps: AppDeps) {
  const app = new Hono<AppEnv>();

  app.get('/widget.js', async (c) => {
    let code: Buffer;
    try {
      code = await readFile(deps.widgetFile);
    } catch {
      return c.json({ error: 'widget_not_built' }, 404);
    }
    return c.body(new Uint8Array(code), 200, {
      'Content-Type': 'application/javascript; charset=utf-8',
      'Cache-Control': 'public, max-age=300',
      'X-Content-Type-Options': 'nosniff',
    });
  });

  app.get('/demo', (c) => {
    const requested = c.req.query('business') ?? DEFAULT_DEMO_SLUG;
    const slug = SLUG.test(requested) ? requested : DEFAULT_DEMO_SLUG;
    c.header('Content-Security-Policy', DEMO_CSP);
    return c.html(demoPage(slug));
  });

  return app;
}
