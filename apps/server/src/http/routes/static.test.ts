import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';
import { createTestHarness, type TestHarness } from '../../testing/harness';

let h: TestHarness | null = null;
let dir: string | null = null;
afterEach(() => {
  h?.close();
  h = null;
  if (dir) {
    try {
      rmSync(dir, { recursive: true, force: true });
    } catch {
      // Windows can keep a just-read file locked for a moment; a leftover temp dir is harmless.
    }
  }
  dir = null;
});

describe('static routes', () => {
  it('serves the built widget with caching and nosniff headers', async () => {
    dir = mkdtempSync(path.join(tmpdir(), 'widget-'));
    const file = path.join(dir, 'widget.js');
    writeFileSync(file, '(function(){console.log("widget")})();');
    h = await createTestHarness({ widgetFile: file });
    const res = await h.request('/widget.js');
    expect(res.status).toBe(200);
    expect(res.headers.get('content-type')).toBe('application/javascript; charset=utf-8');
    expect(res.headers.get('cache-control')).toBe('public, max-age=300');
    expect(res.headers.get('x-content-type-options')).toBe('nosniff');
    expect(await res.text()).toContain('console.log("widget")');
  });

  it('returns 404 when the widget has not been built', async () => {
    h = await createTestHarness({ widgetFile: path.join(tmpdir(), 'does-not-exist', 'widget.js') });
    const res = await h.request('/widget.js');
    expect(res.status).toBe(404);
    expect(await res.json()).toEqual({ error: 'widget_not_built' });
  });

  it('serves a demo page that embeds the widget under a strict CSP', async () => {
    h = await createTestHarness();
    const res = await h.request('/demo');
    expect(res.status).toBe(200);
    expect(res.headers.get('content-type')).toContain('text/html');
    const csp = res.headers.get('content-security-policy') ?? '';
    expect(csp).toContain("script-src 'self'");
    expect(csp).toContain("connect-src 'self'");
    const html = await res.text();
    expect(html).toContain('<script src="/widget.js" data-business="harbor-home" async></script>');
    expect(html.toLowerCase()).toContain('fictional');
  });

  it('accepts a valid business slug and ignores unsafe input', async () => {
    h = await createTestHarness();
    expect(await (await h.request('/demo?business=acme-plumbing')).text()).toContain('data-business="acme-plumbing"');
    const evil = await (await h.request('/demo?business=%22%3E%3Cscript%3Ealert(1)%3C%2Fscript%3E')).text();
    expect(evil).toContain('data-business="harbor-home"');
    expect(evil).not.toContain('alert(1)');
  });
});
