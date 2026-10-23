# Plan 3 — Embeddable Chat Widget Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `@reception/widget`: one self-contained `widget.js` that a merchant embeds with a single `<script>` tag; it renders a chat launcher + panel inside a Shadow DOM, talks to the Plan 2 widget API (conversation created on first send, live replies over fetch-streamed SSE), supports en / zh-CN, and lets visitors leave contact details when a human takes over. The server serves the bundle and a minimal demo page.

**Architecture:** `apps/widget` (Preact 11 + Vite 8 library build → single IIFE). DOM-free modules (`embed`, `api`, `sse`, `storage`, `i18n`, `chatStore`) hold all logic and are unit-tested in Node/happy-dom; thin Preact components render `ChatStore` state. `apps/server` gains `GET /widget.js` and `GET /demo`.

**Tech Stack:** preact 11, vite 8 (rolldown/oxc), @preact/preset-vite 2, TypeScript 7, vitest 5 + happy-dom 21 + @testing-library/preact 3 + @testing-library/user-event 14.

Plan 3 of 4 for `docs/superpowers/specs/2026-10-10-web-chat-mvp-design.md` (Plans 1–2 merged).

## Global Constraints

- Repo `D:\SMS Reception Assistant` (Windows, Git Bash). LF line endings. New package `apps/widget` (`@reception/widget`). Commit after every task; message ends with a blank line then EXACTLY `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- **Verified toolchain facts (do not use older APIs):**
  - Preact 11: event types are top-level exports of `'preact'` (`TargetedEvent`, `TargetedKeyboardEvent`, `TargetedInputEvent`, `TargetedSubmitEvent`) — `JSX.TargetedEvent` no longer exists. `defaultProps` is ignored — use destructuring defaults. Renders are batched/async — tests must `await user.*` / `waitFor` / `findBy*`. Use `class`, not `className`.
  - Vite 8 library mode does NOT replace `process.env.NODE_ENV` → keep `define` in the config and never use `process.env` in widget source. `build.rolldownOptions` (not `rollupOptions`) if ever needed.
  - Vitest 5 uses OXC: JSX for `.tsx` tests comes from the root `vitest.config.ts` `oxc.jsx` (`{ runtime: 'automatic', importSource: 'preact' }`); `?inline` CSS is `''` in tests unless `test.css.include` matches it. DOM tests start with `// @vitest-environment happy-dom` on line 1. If JSX config changes seem ignored, delete `node_modules/.vitest-cache`.
  - Testing Library `screen` cannot see inside shadow roots → use `within(shadowRoot as unknown as HTMLElement)`.
  - happy-dom `Storage` caches prototype methods → simulate a throwing localStorage with `vi.stubGlobal('localStorage', …)` or an instance spy, not `Storage.prototype`.
  - Test files are type-checked too (`apps/widget/tsconfig.json` includes them): when passing `vi.fn()` mocks where `typeof fetch` is expected, cast with `as unknown as typeof fetch`.
  - `document.currentScript` must be read synchronously at the top level of the bundle (null later and in tests); fall back to `script[data-business]`.
- **Spec requirements:** `widget.js` gzip < 40 KB; exactly one output file, no separate CSS; all styles inside the Shadow Root via an inline `<style>` (`import css from './widget.css?inline'`), nothing written to `document.head`; hand-written CSS + CSS variables (`--rw-primary`), no Tailwind; `:host { all: initial }`; en + zh-CN; localStorage access wrapped in try/catch with in-memory fallback; mobile friendly.
- **Server contract (Plan 2, under `<apiBase>/api/widget`):** `GET /b/:slug/config` → `{ business: { name, slug }, locale, widget: { primaryColor, position, greeting: { en, 'zh-CN' } }, quickQuestions: string[] }`; `POST /b/:slug/conversations` → 201 `{ conversationId, visitorToken }`; `GET /c/:id/messages?since=<id>` → `{ messages: PublicMessage[], status }` (≤ 200 per page, ascending); `POST /c/:id/messages` `{ text, locale }` → `{ messages, status }`; `POST /c/:id/contact` `{ name?, email?, phone?, note? }` → `{ ok: true }`; `GET /c/:id/events` (SSE: `ready` first — sent BEFORE replay; `message` events `id: <messageId>`, data PublicMessage; `status` events data `{ status }`; `: ping` comments). `PublicMessage = { id: number; role: 'visitor'|'bot'|'agent'; content: string; createdAt: number }`; status `'bot'|'human'|'closed'`. Errors are JSON `{ error }` with 401/404 (conversation gone or bad token), 422 (validation), 429 (rate limited); 404/413 may lack CORS headers (appear as network errors in browsers).
- **Widget behavior rules:** the visitor token is sent ONLY in the `Authorization: Bearer` header (never in a URL) — so SSE uses `fetch` streaming, not `EventSource`; the conversation is created on the visitor's FIRST send (opening the panel creates nothing); messages are de-duplicated by `id` and kept sorted; on every SSE `ready` the store gap-fills with `GET messages?since=<lastId>` until a page has < 200; a 401/404 for a stored session clears it (send retries once with a new conversation); message content is rendered as text only (never `dangerouslySetInnerHTML`); the live stream runs only while the panel is open.

---

## File Structure

```
apps/widget/
  package.json  tsconfig.json  vite.config.ts  README.md
  scripts/check-size.mjs        asserts single dist/widget.js, no process.env, gzip < 40 KB
  src/
    main.tsx                    reads document.currentScript synchronously, boots the widget
    embed.ts                    readEmbedConfig(script, doc, loc)
    mount.tsx                   mountShadow(content): Shadow DOM host + inline style
    widget.css                  all styles (inlined into the shadow root)
    i18n.ts                     pickLocale, tr, strings (en, zh-CN)
    storage.ts                  createSessionStore (localStorage + memory fallback)
    sse.ts                      createSseParser, streamEvents (fetch streaming)
    api.ts                      createApi, ApiError, contract types
    chatStore.ts                ChatStore state machine (no DOM)
    widget.tsx                  startWidget(embed): wires api/store/mount
    components/App.tsx          App + Panel + MessageList + Composer + QuickQuestions + ContactForm
    testing/fakes.ts            fakeApi, memorySessions, idleStream (test helpers)
apps/server/src/http/routes/static.ts   GET /widget.js, GET /demo
```

---

### Task 1: Widget package, build pipeline, Shadow DOM mount

**Files:**
- Create: `apps/widget/package.json`, `apps/widget/tsconfig.json`, `apps/widget/vite.config.ts`, `apps/widget/scripts/check-size.mjs`
- Create: `apps/widget/src/embed.ts`, `apps/widget/src/mount.tsx`, `apps/widget/src/widget.css`, `apps/widget/src/main.tsx`
- Modify: root `vitest.config.ts`, root `package.json`
- Test: `apps/widget/src/embed.test.ts`, `apps/widget/src/mount.test.tsx`

**Interfaces:**
- Produces: `EmbedConfig { slug: string; apiBase: string; locale: string | null }`, `readEmbedConfig(script, doc, loc): EmbedConfig | null`; `HOST_ID`, `Mounted { host: HTMLElement; root: ShadowRoot; container: HTMLElement; unmount(): void }`, `mountShadow(content: ComponentChild, doc?: Document): Mounted | null`.

- [ ] **Step 1: Package and config files**

`apps/widget/package.json`:
```json
{
  "name": "@reception/widget",
  "version": "0.1.0",
  "private": true,
  "type": "module",
  "scripts": {
    "build": "vite build && node scripts/check-size.mjs",
    "dev": "vite build --watch"
  }
}
```

Install (registry is slow; allow ~10 min):
```bash
npm install -w @reception/widget preact@^11
npm install -w @reception/widget -D vite@^8 @preact/preset-vite@^2 happy-dom@^21 @testing-library/preact@^3 @testing-library/user-event@^14 @testing-library/dom
```

`apps/widget/tsconfig.json`:
```json
{
  "extends": "../../tsconfig.base.json",
  "compilerOptions": {
    "lib": ["ES2023", "DOM", "DOM.Iterable"],
    "jsx": "react-jsx",
    "jsxImportSource": "preact",
    "types": ["vite/client"]
  },
  "include": ["src/**/*.ts", "src/**/*.tsx", "vite.config.ts"]
}
```

`apps/widget/vite.config.ts`:
```ts
import preact from '@preact/preset-vite';
import { defineConfig } from 'vite';

export default defineConfig({
  plugins: [preact()],
  // Library mode does not replace process.env.NODE_ENV; dependencies may reference it.
  define: { 'process.env.NODE_ENV': JSON.stringify('production') },
  build: {
    lib: { entry: 'src/main.tsx', formats: ['iife'], name: 'ReceptionWidget', fileName: () => 'widget.js' },
    cssCodeSplit: false,
    minify: true,
    sourcemap: false,
    emptyOutDir: true,
  },
});
```

`apps/widget/scripts/check-size.mjs`:
```js
import { readdirSync, readFileSync } from 'node:fs';
import { gzipSync } from 'node:zlib';

const LIMIT = 40 * 1024;
const dist = new URL('../dist/', import.meta.url);
const files = readdirSync(dist);
if (files.length !== 1 || files[0] !== 'widget.js') {
  console.error(`Expected exactly dist/widget.js, found: ${files.join(', ') || '(nothing)'}`);
  process.exit(1);
}
const code = readFileSync(new URL('widget.js', dist));
const text = code.toString('utf8');
if (/\bprocess\.env\b/.test(text)) {
  console.error('dist/widget.js references process.env (it would throw in browsers)');
  process.exit(1);
}
if (/\bimport\s*\(|\brequire\s*\(|^\s*import\s/m.test(text)) {
  console.error('dist/widget.js contains module imports; everything must be bundled');
  process.exit(1);
}
const gz = gzipSync(code).length;
console.log(`widget.js: ${code.length} B raw, ${gz} B gzip (limit ${LIMIT} B)`);
if (gz > LIMIT) {
  console.error('widget.js exceeds the gzip size budget');
  process.exit(1);
}
```

Root `vitest.config.ts` becomes:
```ts
import { defineConfig } from 'vitest/config';

export default defineConfig({
  // Vite 8 / Vitest 5 transform JSX with OXC; the widget uses Preact.
  oxc: { jsx: { runtime: 'automatic', importSource: 'preact' } },
  test: {
    include: ['packages/*/src/**/*.test.ts', 'apps/*/src/**/*.test.ts', 'apps/*/src/**/*.test.tsx'],
    environment: 'node',
    // Keep `?inline` CSS as a real string in tests (Vitest blanks CSS by default).
    css: { include: [/\.css(\?|$)/] },
  },
});
```

Root `package.json` scripts: set `"typecheck": "tsc -p packages/core/tsconfig.json && tsc -p apps/server/tsconfig.json && tsc -p apps/widget/tsconfig.json"` and add `"build:widget": "npm run build -w @reception/widget"`.

- [ ] **Step 2: Write failing tests**

`apps/widget/src/embed.test.ts`:
```ts
// @vitest-environment happy-dom
import { afterEach, describe, expect, it, vi } from 'vitest';
import { readEmbedConfig } from './embed';

function script(attrs: Record<string, string>, src = 'https://chat.example.com/widget.js'): HTMLScriptElement {
  const el = document.createElement('script');
  el.src = src;
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
  return el;
}

afterEach(() => {
  vi.restoreAllMocks();
  document.body.innerHTML = '';
});

describe('readEmbedConfig', () => {
  it('reads the slug, derives the API base from the script origin and reads an optional locale', () => {
    expect(readEmbedConfig(script({ 'data-business': 'harbor-home', 'data-locale': 'zh-CN' }), document, location)).toEqual({
      slug: 'harbor-home',
      apiBase: 'https://chat.example.com',
      locale: 'zh-CN',
    });
  });

  it('prefers an explicit data-api base without trailing slashes', () => {
    expect(readEmbedConfig(script({ 'data-business': 'harbor-home', 'data-api': 'https://api.example.com/' }), document, location)?.apiBase).toBe(
      'https://api.example.com',
    );
  });

  it('falls back to a script[data-business] element when currentScript is unavailable', () => {
    document.body.append(script({ 'data-business': 'harbor-home' }));
    expect(readEmbedConfig(null, document, location)?.slug).toBe('harbor-home');
  });

  it('rejects a missing or invalid slug with a console warning', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    expect(readEmbedConfig(script({}), document, location)).toBeNull();
    expect(readEmbedConfig(script({ 'data-business': 'Bad Slug!' }), document, location)).toBeNull();
    expect(warn).toHaveBeenCalledTimes(2);
  });
});
```

`apps/widget/src/mount.test.tsx`:
```tsx
// @vitest-environment happy-dom
import { afterEach, describe, expect, it } from 'vitest';
import { HOST_ID, mountShadow, type Mounted } from './mount';

let mounted: Mounted | null = null;
afterEach(() => {
  mounted?.unmount();
  mounted = null;
});

describe('mountShadow', () => {
  it('renders into an open shadow root with inline styles and nothing in document.head', async () => {
    const headBefore = document.head.innerHTML;
    mounted = mountShadow(<p class="probe">hello</p>);
    expect(mounted).not.toBeNull();
    expect(document.getElementById(HOST_ID)).toBe(mounted!.host);
    const style = mounted!.root.querySelector('style');
    expect(style?.textContent).toContain(':host');
    expect(mounted!.root.querySelector('.probe')?.textContent).toBe('hello');
    expect(document.head.innerHTML).toBe(headBefore);
    expect(document.querySelector('.probe')).toBeNull();
  });

  it('refuses to mount twice', () => {
    mounted = mountShadow(<p>one</p>);
    expect(mountShadow(<p>two</p>)).toBeNull();
    expect(document.querySelectorAll(`#${HOST_ID}`)).toHaveLength(1);
  });

  it('unmounts cleanly', () => {
    const m = mountShadow(<p>bye</p>)!;
    m.unmount();
    expect(document.getElementById(HOST_ID)).toBeNull();
  });
});
```

- [ ] **Step 3: Run to verify RED** — `npx vitest run apps/widget` → FAIL (modules not found).

- [ ] **Step 4: Implement**

`apps/widget/src/embed.ts`:
```ts
export interface EmbedConfig {
  slug: string;
  /** Origin (or explicit base URL) of the reception server. */
  apiBase: string;
  /** Optional forced UI language from data-locale. */
  locale: string | null;
}

const SLUG = /^[a-z0-9-]{3,40}$/;

/**
 * `script` must be `document.currentScript` captured synchronously at bundle start;
 * it is null later (and for module scripts), so fall back to `script[data-business]`.
 */
export function readEmbedConfig(script: HTMLScriptElement | null, doc: Document, loc: Location): EmbedConfig | null {
  const el = script ?? doc.querySelector<HTMLScriptElement>('script[data-business]');
  const slug = el?.dataset.business?.trim() ?? '';
  if (!el || !SLUG.test(slug)) {
    console.warn('[reception-widget] Missing or invalid data-business attribute; the chat widget was not loaded.');
    return null;
  }
  const explicit = el.dataset.api?.trim().replace(/\/+$/, '');
  const apiBase = explicit || (el.src ? new URL(el.src, loc.href).origin : loc.origin);
  return { slug, apiBase, locale: el.dataset.locale?.trim() || null };
}
```

`apps/widget/src/mount.tsx`:
```tsx
import { render, type ComponentChild } from 'preact';
import css from './widget.css?inline';

export const HOST_ID = 'reception-widget-host';

export interface Mounted {
  host: HTMLElement;
  root: ShadowRoot;
  container: HTMLElement;
  unmount(): void;
}

/** Renders `content` inside a Shadow DOM so host-page styles cannot leak in (and ours cannot leak out). */
export function mountShadow(content: ComponentChild, doc: Document = document): Mounted | null {
  if (doc.getElementById(HOST_ID)) {
    console.warn('[reception-widget] The chat widget is already loaded on this page.');
    return null;
  }
  const host = doc.createElement('div');
  host.id = HOST_ID;
  const root = host.attachShadow({ mode: 'open' });
  const style = doc.createElement('style');
  style.textContent = css;
  const container = doc.createElement('div');
  root.append(style, container);
  doc.body.appendChild(host);
  render(content, container);
  return {
    host,
    root,
    container,
    unmount() {
      render(null, container);
      host.remove();
    },
  };
}
```

`apps/widget/src/widget.css` (placeholder; Task 4 replaces it with the full stylesheet):
```css
:host {
  all: initial;
}
```

`apps/widget/src/main.tsx` (placeholder boot; Task 4 replaces the body):
```tsx
import { readEmbedConfig } from './embed';
import { mountShadow } from './mount';

// Must be read synchronously during the initial execution of this script.
const embed = readEmbedConfig(document.currentScript as HTMLScriptElement | null, document, location);
if (embed) mountShadow(<p>Reception widget</p>);
```

> If `style.textContent` is empty in the test, check `test.css.include`; if JSX fails with `react/jsx-dev-runtime`, check the root `oxc.jsx` and clear `node_modules/.vitest-cache`.

- [ ] **Step 5: Verify GREEN and build**

Run: `npx vitest run apps/widget` → PASS. Run: `npx vitest run` → all PASS. Run: `npm run typecheck` → exit 0.
Run: `npm run build:widget` → prints `widget.js: … B raw, … B gzip (limit 40960 B)` and exits 0; `apps/widget/dist/` contains only `widget.js`.

- [ ] **Step 6: Commit**

```bash
git add package.json package-lock.json vitest.config.ts apps/widget
git commit -m "feat(widget): scaffold widget package with single-file build and shadow DOM mount"
```

(`apps/widget/dist/` is ignored by the root `.gitignore` `dist/` rule — never commit it.)

---

### Task 2: Client modules — i18n, session storage, SSE streaming, API client

**Files:**
- Create: `apps/widget/src/i18n.ts`, `apps/widget/src/storage.ts`, `apps/widget/src/sse.ts`, `apps/widget/src/api.ts`
- Test: `apps/widget/src/i18n.test.ts`, `apps/widget/src/storage.test.ts`, `apps/widget/src/sse.test.ts`, `apps/widget/src/api.test.ts`

**Interfaces:**
- Produces:
  - `WidgetLocale = 'en' | 'zh-CN'`, `pickLocale(override, browserLanguage, businessLocale): WidgetLocale`, `StringKey`, `tr(locale, key): string`
  - `StoredSession { conversationId: string; visitorToken: string }`, `SessionStore { load(); save(s); clear() }`, `createSessionStore(slug, getStorage?)`
  - `SseEvent { event: string; data: string; id: string | null }`, `createSseParser(onEvent): (chunk: string) => void`, `StreamOptions { url; token; lastEventId: number | null; signal: AbortSignal; onEvent(e: SseEvent): void; fetchImpl?: typeof fetch }`, `streamEvents(options): Promise<void>` (resolves when the server ends the stream; throws `ApiError` on non-2xx)
  - `ApiError { status: number; code: string }` (status 0 = network), `PublicMessage`, `ConversationStatus`, `WidgetConfig`, `ContactInput`, `MessagesResult { messages; status }`, `WidgetApi { getConfig(); createConversation(); listMessages(session, since); sendMessage(session, text, locale); sendContact(session, input); eventsUrl(conversationId) }`, `createApi(apiBase, slug, fetchImpl?)`

- [ ] **Step 1: Write failing tests**

`apps/widget/src/i18n.test.ts`:
```ts
import { describe, expect, it } from 'vitest';
import { pickLocale, STRINGS, tr } from './i18n';

describe('i18n', () => {
  it('prefers the data-locale override, then the browser language, then the business locale', () => {
    expect(pickLocale('zh-CN', 'en-US', 'en')).toBe('zh-CN');
    expect(pickLocale(null, 'zh-TW', 'en')).toBe('zh-CN');
    expect(pickLocale(null, 'en-GB', 'zh-CN')).toBe('en');
    expect(pickLocale(null, 'fr-FR', 'zh-CN')).toBe('zh-CN');
    expect(pickLocale('xx', undefined, 'en')).toBe('en');
  });

  it('has the same keys in every locale and translates', () => {
    expect(Object.keys(STRINGS['zh-CN']).sort()).toEqual(Object.keys(STRINGS.en).sort());
    expect(tr('en', 'send')).toBe('Send');
    expect(tr('zh-CN', 'send')).toBe('发送');
  });

  it('asks for a human with phrases the server rules recognize', () => {
    expect(tr('en', 'talkToPersonMessage')).toMatch(/real person/);
    expect(tr('zh-CN', 'talkToPersonMessage')).toContain('人工客服');
  });
});
```

`apps/widget/src/storage.test.ts`:
```ts
// @vitest-environment happy-dom
import { afterEach, describe, expect, it, vi } from 'vitest';
import { createSessionStore } from './storage';

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  localStorage.clear();
});

const session = { conversationId: 'c1', visitorToken: 't1' };

describe('createSessionStore', () => {
  it('persists per business in localStorage', () => {
    const a = createSessionStore('harbor-home');
    a.save(session);
    expect(createSessionStore('harbor-home').load()).toEqual(session);
    expect(createSessionStore('other-shop').load()).toBeNull();
    a.clear();
    expect(createSessionStore('harbor-home').load()).toBeNull();
  });

  it('ignores corrupt stored values', () => {
    localStorage.setItem('reception-widget:harbor-home', '{"conversationId":1}');
    expect(createSessionStore('harbor-home').load()).toBeNull();
    localStorage.setItem('reception-widget:harbor-home', 'not json');
    expect(createSessionStore('harbor-home').load()).toBeNull();
  });

  it('falls back to memory when localStorage throws (private mode, in-app browsers)', () => {
    const throwing = {
      getItem() {
        throw new DOMException('denied', 'SecurityError');
      },
      setItem() {
        throw new DOMException('denied', 'SecurityError');
      },
      removeItem() {
        throw new DOMException('denied', 'SecurityError');
      },
    };
    vi.stubGlobal('localStorage', throwing);
    const store = createSessionStore('harbor-home');
    expect(() => store.save(session)).not.toThrow();
    expect(store.load()).toEqual(session);
    store.clear();
    expect(store.load()).toBeNull();
  });

  it('works when storage is entirely unavailable', () => {
    const store = createSessionStore('harbor-home', () => null);
    store.save(session);
    expect(store.load()).toEqual(session);
  });
});
```

`apps/widget/src/sse.test.ts`:
```ts
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from './api';
import { createSseParser, streamEvents, type SseEvent } from './sse';

afterEach(() => vi.unstubAllGlobals());

function bodyFrom(chunks: (string | Uint8Array)[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  return new ReadableStream({
    start(controller) {
      for (const c of chunks) controller.enqueue(typeof c === 'string' ? encoder.encode(c) : c);
      controller.close();
    },
  });
}

describe('createSseParser', () => {
  it('parses events across chunk boundaries, keeps ids and skips comments', () => {
    const events: SseEvent[] = [];
    const parse = createSseParser((e) => events.push(e));
    parse('event: ready\ndata: {}\n\n: ping\n\nevent: mess');
    parse('age\nid: 7\ndata: {"id":7}\n\n');
    parse('data: line1\r\ndata: line2\r\n\r\n');
    expect(events).toEqual([
      { event: 'ready', data: '{}', id: null },
      { event: 'message', data: '{"id":7}', id: '7' },
      { event: 'message', data: 'line1\nline2', id: null },
    ]);
  });
});

describe('streamEvents', () => {
  it('sends the token in the Authorization header and decodes multi-byte text split across chunks', async () => {
    const bytes = new TextEncoder().encode('event: message\nid: 3\ndata: {"content":"你好"}\n\n');
    const split = bytes.indexOf(0xe4) + 1; // inside the first multi-byte character
    const fetchImpl = vi.fn().mockResolvedValue(new Response(bodyFrom([bytes.slice(0, split), bytes.slice(split)]), { status: 200 }));
    const events: SseEvent[] = [];
    const controller = new AbortController();
    await streamEvents({
      url: 'https://chat.example.com/api/widget/c/c1/events',
      token: 'tok',
      lastEventId: 2,
      signal: controller.signal,
      onEvent: (e) => events.push(e),
      fetchImpl,
    });
    expect(events).toEqual([{ event: 'message', data: '{"content":"你好"}', id: '3' }]);
    const [url, init] = fetchImpl.mock.calls[0]!;
    expect(url).toBe('https://chat.example.com/api/widget/c/c1/events');
    expect(String(url)).not.toContain('tok');
    expect(init.headers).toMatchObject({ Authorization: 'Bearer tok', 'Last-Event-ID': '2', Accept: 'text/event-stream' });
    expect(init.signal).toBe(controller.signal);
  });

  it('omits Last-Event-ID when there is nothing to resume from', async () => {
    const fetchImpl = vi.fn().mockResolvedValue(new Response(bodyFrom([]), { status: 200 }));
    await streamEvents({ url: 'u', token: 't', lastEventId: null, signal: new AbortController().signal, onEvent: () => {}, fetchImpl });
    expect(fetchImpl.mock.calls[0]![1].headers).not.toHaveProperty('Last-Event-ID');
  });

  it('throws ApiError with the server error code on non-2xx responses', async () => {
    const fetchImpl = vi.fn().mockResolvedValue(new Response(JSON.stringify({ error: 'unauthorized' }), { status: 401 }));
    await expect(
      streamEvents({ url: 'u', token: 't', lastEventId: null, signal: new AbortController().signal, onEvent: () => {}, fetchImpl }),
    ).rejects.toMatchObject({ name: 'ApiError', status: 401, code: 'unauthorized' });
    expect(ApiError).toBeDefined();
  });
});
```

`apps/widget/src/api.test.ts`:
```ts
import { describe, expect, it, vi } from 'vitest';
import { ApiError, createApi } from './api';

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });
const session = { conversationId: 'c1', visitorToken: 'tok' };

describe('createApi', () => {
  it('calls the widget endpoints with the right methods, paths, bodies and headers', async () => {
    const fetchImpl = vi
      .fn()
      .mockResolvedValueOnce(json({ business: { name: 'Harbor', slug: 'harbor-home' }, locale: 'en', widget: {}, quickQuestions: [] }))
      .mockResolvedValueOnce(json({ conversationId: 'c1', visitorToken: 'tok' }, 201))
      .mockResolvedValueOnce(json({ messages: [], status: 'bot' }))
      .mockResolvedValueOnce(json({ messages: [], status: 'bot' }))
      .mockResolvedValueOnce(json({ ok: true }));
    const api = createApi('https://chat.example.com', 'harbor-home', fetchImpl);
    await api.getConfig();
    await api.createConversation();
    await api.listMessages(session, 5);
    await api.sendMessage(session, 'hi', 'zh-CN');
    await api.sendContact(session, { email: 'a@example.com' });

    const calls = fetchImpl.mock.calls.map(([url, init]) => [url, init?.method ?? 'GET']);
    expect(calls).toEqual([
      ['https://chat.example.com/api/widget/b/harbor-home/config', 'GET'],
      ['https://chat.example.com/api/widget/b/harbor-home/conversations', 'POST'],
      ['https://chat.example.com/api/widget/c/c1/messages?since=5', 'GET'],
      ['https://chat.example.com/api/widget/c/c1/messages', 'POST'],
      ['https://chat.example.com/api/widget/c/c1/contact', 'POST'],
    ]);
    const send = fetchImpl.mock.calls[3]![1];
    expect(JSON.parse(send.body)).toEqual({ text: 'hi', locale: 'zh-CN' });
    expect(send.headers).toMatchObject({ Authorization: 'Bearer tok', 'Content-Type': 'application/json' });
    for (const [url] of fetchImpl.mock.calls) expect(String(url)).not.toContain('tok');
    expect(api.eventsUrl('c1')).toBe('https://chat.example.com/api/widget/c/c1/events');
  });

  it('maps HTTP errors and network failures to ApiError', async () => {
    const api = createApi('https://chat.example.com', 'harbor-home', vi.fn().mockResolvedValue(json({ error: 'rate_limited' }, 429)));
    await expect(api.sendMessage(session, 'hi', 'en')).rejects.toMatchObject({ status: 429, code: 'rate_limited' });

    const broken = createApi('https://chat.example.com', 'harbor-home', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')));
    const error = await broken.getConfig().catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 0, code: 'network' });

    const html = createApi('https://chat.example.com', 'harbor-home', vi.fn().mockResolvedValue(new Response('<html>', { status: 502 })));
    await expect(html.getConfig()).rejects.toMatchObject({ status: 502, code: 'http_error' });
  });
});
```

- [ ] **Step 2: Run to verify RED** — `npx vitest run apps/widget` → FAIL.

- [ ] **Step 3: Implement**

`apps/widget/src/i18n.ts`:
```ts
export type WidgetLocale = 'en' | 'zh-CN';

const en = {
  openChat: 'Chat with us',
  closeChat: 'Close chat',
  inputPlaceholder: 'Type your message…',
  inputLabel: 'Message',
  send: 'Send',
  typing: 'Typing…',
  talkToPerson: 'Talk to a person',
  talkToPersonMessage: 'I want to talk to a real person',
  humanNotice: 'A team member will reply here as soon as possible.',
  leaveContact: 'Leave your contact details',
  contactIntro: 'Leave your email or phone number and we will get back to you.',
  name: 'Name',
  email: 'Email',
  phone: 'Phone',
  note: 'Message (optional)',
  submitContact: 'Send details',
  cancel: 'Cancel',
  contactThanks: 'Thanks! We will get back to you soon.',
  errorGeneric: 'Something went wrong. Please try again.',
  errorRateLimited: 'You are sending messages too quickly. Please wait a moment.',
  errorTooLong: 'Your message is too long (1000 characters max).',
  errorContact: 'Please enter a valid email address or phone number.',
  reconnecting: 'Reconnecting…',
} as const;

export type StringKey = keyof typeof en;

const zh: Record<StringKey, string> = {
  openChat: '在线咨询',
  closeChat: '关闭聊天',
  inputPlaceholder: '请输入您的问题…',
  inputLabel: '消息',
  send: '发送',
  typing: '正在输入…',
  talkToPerson: '联系人工客服',
  talkToPersonMessage: '我想找人工客服',
  humanNotice: '工作人员会尽快在这里回复您。',
  leaveContact: '留下联系方式',
  contactIntro: '留下您的邮箱或电话，我们会尽快联系您。',
  name: '姓名',
  email: '邮箱',
  phone: '电话',
  note: '留言（可选）',
  submitContact: '提交',
  cancel: '取消',
  contactThanks: '谢谢！我们会尽快与您联系。',
  errorGeneric: '出了点问题，请稍后再试。',
  errorRateLimited: '消息发送太频繁，请稍候再试。',
  errorTooLong: '消息太长了（最多 1000 个字符）。',
  errorContact: '请填写有效的邮箱或电话号码。',
  reconnecting: '正在重新连接…',
};

export const STRINGS: Record<WidgetLocale, Record<StringKey, string>> = { en, 'zh-CN': zh };

function toLocale(value: string | null | undefined): WidgetLocale | null {
  const v = value?.toLowerCase() ?? '';
  if (v.startsWith('zh')) return 'zh-CN';
  if (v.startsWith('en')) return 'en';
  return null;
}

/** data-locale override → visitor's browser language → business default. */
export function pickLocale(override: string | null, browserLanguage: string | undefined, businessLocale: WidgetLocale): WidgetLocale {
  return toLocale(override) ?? toLocale(browserLanguage) ?? businessLocale;
}

export function tr(locale: WidgetLocale, key: StringKey): string {
  return STRINGS[locale][key];
}
```

`apps/widget/src/storage.ts`:
```ts
export interface StoredSession {
  conversationId: string;
  visitorToken: string;
}

export interface SessionStore {
  load(): StoredSession | null;
  save(session: StoredSession): void;
  clear(): void;
}

function defaultStorage(): Storage | null {
  return typeof localStorage === 'undefined' ? null : localStorage;
}

function isSession(value: unknown): value is StoredSession {
  const v = value as Partial<StoredSession> | null;
  return typeof v?.conversationId === 'string' && typeof v.visitorToken === 'string' && v.conversationId !== '' && v.visitorToken !== '';
}

/** localStorage can be missing or throw (private mode, in-app WebViews): fall back to memory and keep working. */
export function createSessionStore(slug: string, getStorage: () => Storage | null = defaultStorage): SessionStore {
  const key = `reception-widget:${slug}`;
  let memory: StoredSession | null = null;
  const storage = (): Storage | null => {
    try {
      return getStorage();
    } catch {
      return null;
    }
  };
  return {
    load() {
      try {
        const raw = storage()?.getItem(key);
        if (raw) {
          const parsed: unknown = JSON.parse(raw);
          if (isSession(parsed)) return parsed;
        }
      } catch {
        // fall through to memory
      }
      return memory;
    },
    save(session) {
      memory = session;
      try {
        storage()?.setItem(key, JSON.stringify(session));
      } catch {
        // memory copy is enough for this page view
      }
    },
    clear() {
      memory = null;
      try {
        storage()?.removeItem(key);
      } catch {
        // nothing persisted
      }
    },
  };
}
```

`apps/widget/src/api.ts`:
```ts
import type { WidgetLocale } from './i18n';
import type { StoredSession } from './storage';

export type ConversationStatus = 'bot' | 'human' | 'closed';

export interface PublicMessage {
  id: number;
  role: 'visitor' | 'bot' | 'agent';
  content: string;
  createdAt: number;
}

export interface WidgetConfig {
  business: { name: string; slug: string };
  locale: WidgetLocale;
  widget: { primaryColor: string; position: 'left' | 'right'; greeting: Record<WidgetLocale, string> };
  quickQuestions: string[];
}

export interface ContactInput {
  name?: string;
  email?: string;
  phone?: string;
  note?: string;
}

export interface MessagesResult {
  messages: PublicMessage[];
  status: ConversationStatus;
}

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
  ) {
    super(`Request failed: ${status} ${code}`);
    this.name = 'ApiError';
  }
}

export interface WidgetApi {
  getConfig(): Promise<WidgetConfig>;
  createConversation(): Promise<StoredSession>;
  listMessages(session: StoredSession, since: number): Promise<MessagesResult>;
  sendMessage(session: StoredSession, text: string, locale: WidgetLocale): Promise<MessagesResult>;
  sendContact(session: StoredSession, input: ContactInput): Promise<void>;
  eventsUrl(conversationId: string): string;
}

const REQUEST_TIMEOUT_MS = 30_000; // a turn may wait up to 15 s for the AI model

export async function errorFromResponse(res: Response): Promise<ApiError> {
  let code = 'http_error';
  try {
    const body = (await res.json()) as { error?: unknown };
    if (typeof body.error === 'string') code = body.error;
  } catch {
    // non-JSON error body
  }
  return new ApiError(res.status, code);
}

export function createApi(apiBase: string, slug: string, fetchImpl: typeof fetch = globalThis.fetch.bind(globalThis)): WidgetApi {
  const base = `${apiBase}/api/widget`;
  const auth = (s: StoredSession) => ({ Authorization: `Bearer ${s.visitorToken}` });
  const conv = (s: StoredSession) => `${base}/c/${encodeURIComponent(s.conversationId)}`;

  async function request<T>(url: string, init: RequestInit = {}): Promise<T> {
    let res: Response;
    try {
      res = await fetchImpl(url, { ...init, signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS) });
    } catch {
      throw new ApiError(0, 'network');
    }
    if (!res.ok) throw await errorFromResponse(res);
    return (await res.json()) as T;
  }

  const postJson = (body: unknown, headers: Record<string, string> = {}): RequestInit => ({
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...headers },
    body: JSON.stringify(body),
  });

  return {
    getConfig: () => request<WidgetConfig>(`${base}/b/${encodeURIComponent(slug)}/config`),
    createConversation: () => request<StoredSession>(`${base}/b/${encodeURIComponent(slug)}/conversations`, { method: 'POST' }),
    listMessages: (s, since) => request<MessagesResult>(`${conv(s)}/messages?since=${since}`, { headers: auth(s) }),
    sendMessage: (s, text, locale) => request<MessagesResult>(`${conv(s)}/messages`, postJson({ text, locale }, auth(s))),
    sendContact: async (s, input) => {
      await request<{ ok: boolean }>(`${conv(s)}/contact`, postJson(input, auth(s)));
    },
    eventsUrl: (conversationId) => `${base}/c/${encodeURIComponent(conversationId)}/events`,
  };
}
```

> `createConversation` returns the server's `{ conversationId, visitorToken }`, which is exactly `StoredSession`.

`apps/widget/src/sse.ts`:
```ts
import { errorFromResponse } from './api';

export interface SseEvent {
  event: string;
  data: string;
  id: string | null;
}

/** Incremental text/event-stream parser (handles \n and \r\n, comments and multi-line data). */
export function createSseParser(onEvent: (event: SseEvent) => void): (chunk: string) => void {
  let buffer = '';
  return (chunk) => {
    buffer += chunk;
    for (let match = /\r?\n\r?\n/.exec(buffer); match; match = /\r?\n\r?\n/.exec(buffer)) {
      const raw = buffer.slice(0, match.index);
      buffer = buffer.slice(match.index + match[0].length);
      let event = 'message';
      let id: string | null = null;
      const data: string[] = [];
      for (const line of raw.split(/\r?\n/)) {
        if (line === '' || line.startsWith(':')) continue;
        const colon = line.indexOf(':');
        const field = colon < 0 ? line : line.slice(0, colon);
        const value = colon < 0 ? '' : line.slice(colon + 1).replace(/^ /, '');
        if (field === 'event') event = value;
        else if (field === 'data') data.push(value);
        else if (field === 'id') id = value;
      }
      if (data.length > 0) onEvent({ event, data: data.join('\n'), id });
    }
  };
}

export interface StreamOptions {
  url: string;
  token: string;
  lastEventId: number | null;
  signal: AbortSignal;
  onEvent(event: SseEvent): void;
  fetchImpl?: typeof fetch;
}

/**
 * Reads a server-sent event stream with fetch so the visitor token can travel in the
 * Authorization header (EventSource cannot set headers, and tokens must never be in URLs).
 * Resolves when the server closes the stream; rejects with ApiError on non-2xx.
 */
export async function streamEvents(options: StreamOptions): Promise<void> {
  const fetchImpl = options.fetchImpl ?? globalThis.fetch.bind(globalThis);
  const res = await fetchImpl(options.url, {
    headers: {
      Authorization: `Bearer ${options.token}`,
      Accept: 'text/event-stream',
      ...(options.lastEventId !== null ? { 'Last-Event-ID': String(options.lastEventId) } : {}),
    },
    cache: 'no-store',
    signal: options.signal,
  });
  if (!res.ok || !res.body) throw await errorFromResponse(res);
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  const parse = createSseParser(options.onEvent);
  for (;;) {
    const { done, value } = await reader.read();
    if (done) return;
    parse(decoder.decode(value, { stream: true }));
  }
}
```

- [ ] **Step 4: Verify GREEN** — `npx vitest run apps/widget` PASS; `npx vitest run` PASS; `npm run typecheck` exit 0.

- [ ] **Step 5: Commit**

```bash
git add apps/widget/src
git commit -m "feat(widget): add i18n, session storage, SSE streaming and API client"
```

---

### Task 3: Chat store (state machine) and test fakes

**Files:**
- Create: `apps/widget/src/chatStore.ts`, `apps/widget/src/testing/fakes.ts`
- Test: `apps/widget/src/chatStore.test.ts`

**Interfaces:**
- Consumes: Task 2 modules.
- Produces:
  - `ChatState { phase: 'loading'|'ready'|'unavailable'; config: WidgetConfig | null; locale: WidgetLocale; open: boolean; messages: PublicMessage[]; status: ConversationStatus; sending: boolean; error: StringKey | null; connection: 'idle'|'connecting'|'live'|'retrying'; contact: 'hidden'|'open'|'sending'|'sent' }`
  - `ChatStoreDeps { api: WidgetApi; sessions: SessionStore; stream(options: StreamOptions): Promise<void>; localeOverride: string | null; browserLanguage: string | undefined; sleep?(ms: number, signal: AbortSignal): Promise<void> }`
  - `class ChatStore { getState(); subscribe(fn): () => void; init(); setOpen(open); send(text); talkToPerson(); openContact(); closeContact(); submitContact(input); destroy() }`, constants `MAX_TEXT = 1000`, `PAGE_SIZE = 200`, `BACKOFF_MS = [1000, 2000, 5000, 10000]`
  - test helpers: `DEMO_CONFIG`, `msg(role, content, id)`, `fakeApi(overrides?)`, `memorySessions()`, `controllableStream()` → `{ stream, calls: { options, end(), fail(error) }[] }`

Behavior (see Global Constraints → widget behavior rules):
- `init()` loads config (`phase: 'ready'`, locale via `pickLocale`) or sets `phase: 'unavailable'` on any error; restores a stored session.
- `setOpen(true)`: if a session exists, gap-fill (`catchUp`) then start the stream. `setOpen(false)` aborts the stream (`connection: 'idle'`).
- `send(text)`: trims; ignores empty or while `sending`; > 1000 chars → `error: 'errorTooLong'` without a request; creates the conversation if none; on 401/404 with a stored session → clear it and retry ONCE with a new conversation; merges the returned messages and status; starts the stream; maps 429 → `errorRateLimited`, everything else → `errorGeneric`.
- Stream loop (only while open with a session): `connecting` → events: `ready` → `live` + `catchUp()` and reset the backoff; `message` → merge; `status` → set status. Stream end or failure → if aborted stop; 401/404 → reset session and stop; else `retrying`, sleep `BACKOFF_MS[min(attempt, 3)]`, reconnect with `Last-Event-ID = last message id` (null if none).
- `catchUp()`: `listMessages(session, lastId)` repeatedly until a page has < 200 messages; sets status; 401/404 → reset session.
- `submitContact(input)`: trims fields, drops empty ones; needs email or phone else `errorContact`; creates a conversation if none; 422 → back to `open` with `errorContact`; success → `sent` and start the stream.
- `resetSession()`: aborts the stream, clears stored session, clears messages, status `bot`, contact `hidden`.

- [ ] **Step 1: Create the test fakes** — `apps/widget/src/testing/fakes.ts`:
```ts
import { vi } from 'vitest';
import type { MessagesResult, PublicMessage, WidgetApi, WidgetConfig } from '../api';
import type { StreamOptions } from '../sse';
import { createSessionStore, type SessionStore, type StoredSession } from '../storage';

export const DEMO_CONFIG: WidgetConfig = {
  business: { name: 'Harbor & Home Cleaning', slug: 'harbor-home' },
  locale: 'en',
  widget: {
    primaryColor: '#2563eb',
    position: 'right',
    greeting: { en: 'Hi! How can we help you today?', 'zh-CN': '您好！有什么可以帮您？' },
  },
  quickQuestions: ['What are your hours?', 'Do you bring supplies?'],
};

let autoId = 10_000;
export function msg(role: PublicMessage['role'], content: string, id: number = autoId++): PublicMessage {
  return { id, role, content, createdAt: 1_760_000_000_000 + id };
}

export function fakeApi(overrides: Partial<WidgetApi> = {}) {
  let counter = 0;
  const defaults = {
    getConfig: vi.fn(async (): Promise<WidgetConfig> => DEMO_CONFIG),
    createConversation: vi.fn(async (): Promise<StoredSession> => {
      counter += 1;
      return { conversationId: `c${counter}`, visitorToken: `t${counter}` };
    }),
    listMessages: vi.fn(async (_s: StoredSession, _since: number): Promise<MessagesResult> => ({ messages: [], status: 'bot' })),
    sendMessage: vi.fn(
      async (_s: StoredSession, text: string): Promise<MessagesResult> => ({
        messages: [msg('visitor', text), msg('bot', `Echo: ${text}`)],
        status: 'bot',
      }),
    ),
    sendContact: vi.fn(async (): Promise<void> => undefined),
    eventsUrl: vi.fn((id: string) => `https://chat.example/api/widget/c/${id}/events`),
  };
  // Tests only override with vi.fn() mocks, so keep the mock-typed shape for `.mock` access.
  return { ...defaults, ...overrides } as typeof defaults;
}

export function memorySessions(): SessionStore {
  return createSessionStore('test', () => null);
}

/** Stream stand-in that stays open until aborted; tests drive it via `calls`. */
export function controllableStream() {
  const calls: { options: StreamOptions; end(): void; fail(error: unknown): void }[] = [];
  const stream = (options: StreamOptions) =>
    new Promise<void>((resolve, reject) => {
      calls.push({ options, end: resolve, fail: reject });
      options.signal.addEventListener('abort', () => resolve(), { once: true });
    });
  return { stream, calls };
}
```

- [ ] **Step 2: Write failing tests** — `apps/widget/src/chatStore.test.ts`:
```ts
import { describe, expect, it, vi } from 'vitest';
import { ApiError } from './api';
import { ChatStore, PAGE_SIZE } from './chatStore';
import type { SessionStore } from './storage';
import { controllableStream, fakeApi, memorySessions, msg } from './testing/fakes';

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

function setup(opts: { api?: ReturnType<typeof fakeApi>; sessions?: SessionStore; language?: string } = {}) {
  const api = opts.api ?? fakeApi();
  const sessions = opts.sessions ?? memorySessions();
  const { stream, calls } = controllableStream();
  const sleep = vi.fn(async (_ms: number, _signal: AbortSignal) => {});
  const store = new ChatStore({ api, sessions, stream, localeOverride: null, browserLanguage: opts.language ?? 'en-US', sleep });
  return { api, sessions, store, streams: calls, sleep };
}

async function ready(opts?: Parameters<typeof setup>[0]) {
  const ctx = setup(opts);
  await ctx.store.init();
  await ctx.store.setOpen(true);
  return ctx;
}

describe('ChatStore: config', () => {
  it('loads the config and picks the visitor locale', async () => {
    const { store } = setup({ language: 'zh-TW' });
    await store.init();
    expect(store.getState()).toMatchObject({ phase: 'ready', locale: 'zh-CN' });
    expect(store.getState().config?.business.name).toBe('Harbor & Home Cleaning');
  });

  it('becomes unavailable when the config cannot be loaded', async () => {
    const { store } = setup({ api: fakeApi({ getConfig: vi.fn().mockRejectedValue(new ApiError(403, 'origin_not_allowed')) }) });
    await store.init();
    expect(store.getState().phase).toBe('unavailable');
  });

  it('notifies subscribers', async () => {
    const { store } = setup();
    const seen: string[] = [];
    const off = store.subscribe((s) => seen.push(s.phase));
    await store.init();
    off();
    expect(seen).toContain('ready');
  });
});

describe('ChatStore: conversation lifecycle', () => {
  it('creates the conversation only on the first send', async () => {
    const { api, sessions, store, streams } = await ready();
    expect(api.createConversation).not.toHaveBeenCalled();
    expect(streams).toHaveLength(0);
    await store.send('  hi  ');
    expect(api.createConversation).toHaveBeenCalledTimes(1);
    expect(api.sendMessage).toHaveBeenCalledWith({ conversationId: 'c1', visitorToken: 't1' }, 'hi', 'en');
    expect(sessions.load()).toEqual({ conversationId: 'c1', visitorToken: 't1' });
    expect(store.getState().messages.map((m) => m.role)).toEqual(['visitor', 'bot']);
    expect(streams).toHaveLength(1);
    expect(streams[0]!.options).toMatchObject({ token: 't1', url: 'https://chat.example/api/widget/c/c1/events' });
  });

  it('de-duplicates and sorts messages from responses and the stream', async () => {
    const api = fakeApi({ sendMessage: vi.fn().mockResolvedValue({ messages: [msg('bot', 'b', 3), msg('visitor', 'v', 2)], status: 'bot' }) });
    const { store, streams } = await ready({ api });
    await store.send('v');
    streams[0]!.options.onEvent({ event: 'message', data: JSON.stringify(msg('bot', 'b', 3)), id: '3' });
    streams[0]!.options.onEvent({ event: 'message', data: JSON.stringify(msg('agent', 'Sam here', 4)), id: '4' });
    streams[0]!.options.onEvent({ event: 'message', data: 'not json', id: null });
    expect(store.getState().messages.map((m) => m.id)).toEqual([2, 3, 4]);
  });

  it('applies status events and asks for a person with the localized phrase', async () => {
    const { api, store, streams } = await ready();
    await store.talkToPerson();
    expect(api.sendMessage.mock.calls[0]![1]).toBe('I want to talk to a real person');
    streams[0]!.options.onEvent({ event: 'status', data: '{"status":"human"}', id: null });
    expect(store.getState().status).toBe('human');
    streams[0]!.options.onEvent({ event: 'status', data: '{"status":"weird"}', id: null });
    expect(store.getState().status).toBe('human');
  });

  it('restores a stored session and catches up across pages when opened', async () => {
    const sessions = memorySessions();
    sessions.save({ conversationId: 'old', visitorToken: 'tok' });
    const page1 = Array.from({ length: PAGE_SIZE }, (_, i) => msg('bot', `m${i + 1}`, i + 1));
    const api = fakeApi({
      listMessages: vi
        .fn()
        .mockResolvedValueOnce({ messages: page1, status: 'bot' })
        .mockResolvedValueOnce({ messages: [msg('agent', 'latest', 201)], status: 'human' }),
    });
    const { store, streams } = await ready({ api, sessions });
    expect(api.listMessages.mock.calls.map((c) => c[1])).toEqual([0, 200]);
    expect(store.getState().messages).toHaveLength(201);
    expect(store.getState().status).toBe('human');
    expect(streams[0]!.options).toMatchObject({ token: 'tok', lastEventId: 201 });
  });

  it('gap-fills after every ready event', async () => {
    const { api, store, streams } = await ready();
    await store.send('hi');
    const lastId = store.getState().messages.at(-1)!.id;
    streams[0]!.options.onEvent({ event: 'ready', data: '{}', id: null });
    await flush();
    expect(store.getState().connection).toBe('live');
    expect(api.listMessages).toHaveBeenLastCalledWith({ conversationId: 'c1', visitorToken: 't1' }, lastId);
  });

  it('starts a new conversation once when the stored one is gone', async () => {
    const sessions = memorySessions();
    sessions.save({ conversationId: 'gone', visitorToken: 'x' });
    const api = fakeApi({
      listMessages: vi.fn().mockRejectedValue(new ApiError(404, 'not_found')),
      sendMessage: vi
        .fn()
        .mockRejectedValueOnce(new ApiError(404, 'not_found'))
        .mockResolvedValueOnce({ messages: [msg('visitor', 'hi', 1), msg('bot', 'hello', 2)], status: 'bot' }),
    });
    const { store } = setup({ api, sessions });
    await store.init();
    await store.send('hi');
    expect(api.createConversation).toHaveBeenCalledTimes(1);
    expect(sessions.load()).toEqual({ conversationId: 'c1', visitorToken: 't1' });
    expect(store.getState().messages).toHaveLength(2);
  });

  it('resets the session when the stream says the conversation is gone', async () => {
    const { sessions, store, streams } = await ready();
    await store.send('hi');
    streams[0]!.fail(new ApiError(401, 'unauthorized'));
    await flush();
    expect(sessions.load()).toBeNull();
    expect(store.getState().messages).toEqual([]);
    expect(store.getState().connection).toBe('idle');
  });

  it('reconnects with backoff and resets the backoff after ready', async () => {
    const { store, streams, sleep } = await ready();
    await store.send('hi');
    streams[0]!.fail(new ApiError(0, 'network'));
    await flush();
    expect(sleep.mock.calls.map((c) => c[0])).toEqual([1000]);
    expect(streams).toHaveLength(2);
    expect(streams[1]!.options.lastEventId).toBe(store.getState().messages.at(-1)!.id);
    streams[1]!.end();
    await flush();
    expect(sleep.mock.calls.map((c) => c[0])).toEqual([1000, 2000]);
    streams[2]!.options.onEvent({ event: 'ready', data: '{}', id: null });
    streams[2]!.end();
    await flush();
    expect(sleep.mock.calls.map((c) => c[0])).toEqual([1000, 2000, 1000]);
  });

  it('aborts the stream when the panel closes', async () => {
    const { store, streams } = await ready();
    await store.send('hi');
    await store.setOpen(false);
    expect(streams[0]!.options.signal.aborted).toBe(true);
    expect(store.getState().connection).toBe('idle');
  });
});

describe('ChatStore: validation and errors', () => {
  it('ignores empty text and rejects over-long text without a request', async () => {
    const { api, store } = await ready();
    await store.send('   ');
    await store.send('x'.repeat(1001));
    expect(api.sendMessage).not.toHaveBeenCalled();
    expect(store.getState().error).toBe('errorTooLong');
  });

  it('maps rate limits and other failures to messages and clears the sending flag', async () => {
    const api = fakeApi({
      sendMessage: vi.fn().mockRejectedValueOnce(new ApiError(429, 'rate_limited')).mockRejectedValueOnce(new ApiError(0, 'network')),
    });
    const { store } = await ready({ api });
    await store.send('a');
    expect(store.getState()).toMatchObject({ error: 'errorRateLimited', sending: false });
    await store.send('b');
    expect(store.getState()).toMatchObject({ error: 'errorGeneric', sending: false });
  });
});

describe('ChatStore: contact form', () => {
  it('requires an email or phone number', async () => {
    const { api, store } = await ready();
    store.openContact();
    await store.submitContact({ name: 'Alex', email: '  ' });
    expect(api.sendContact).not.toHaveBeenCalled();
    expect(store.getState()).toMatchObject({ contact: 'open', error: 'errorContact' });
  });

  it('creates a conversation if needed, sends trimmed details and shows thanks', async () => {
    const { api, store } = await ready();
    store.openContact();
    await store.submitContact({ name: ' Alex ', email: ' a@example.com ', phone: '', note: '' });
    expect(api.createConversation).toHaveBeenCalledTimes(1);
    expect(api.sendContact).toHaveBeenCalledWith({ conversationId: 'c1', visitorToken: 't1' }, { name: 'Alex', email: 'a@example.com' });
    expect(store.getState().contact).toBe('sent');
  });

  it('returns to the form when the server rejects the details', async () => {
    const api = fakeApi({ sendContact: vi.fn().mockRejectedValue(new ApiError(422, 'validation')) });
    const { store } = await ready({ api });
    store.openContact();
    await store.submitContact({ phone: '123' });
    expect(store.getState()).toMatchObject({ contact: 'open', error: 'errorContact' });
  });
});
```

- [ ] **Step 3: Run to verify RED** — `npx vitest run apps/widget/src/chatStore.test.ts` → FAIL.

- [ ] **Step 4: Implement `apps/widget/src/chatStore.ts`**

```ts
import { ApiError, type ContactInput, type ConversationStatus, type PublicMessage, type WidgetApi, type WidgetConfig } from './api';
import { pickLocale, tr, type StringKey, type WidgetLocale } from './i18n';
import type { SseEvent, StreamOptions } from './sse';
import type { SessionStore, StoredSession } from './storage';

export const MAX_TEXT = 1000;
export const PAGE_SIZE = 200;
export const BACKOFF_MS = [1000, 2000, 5000, 10000] as const;

export interface ChatState {
  phase: 'loading' | 'ready' | 'unavailable';
  config: WidgetConfig | null;
  locale: WidgetLocale;
  open: boolean;
  messages: PublicMessage[];
  status: ConversationStatus;
  sending: boolean;
  error: StringKey | null;
  connection: 'idle' | 'connecting' | 'live' | 'retrying';
  contact: 'hidden' | 'open' | 'sending' | 'sent';
}

export interface ChatStoreDeps {
  api: WidgetApi;
  sessions: SessionStore;
  stream(options: StreamOptions): Promise<void>;
  localeOverride: string | null;
  browserLanguage: string | undefined;
  sleep?(ms: number, signal: AbortSignal): Promise<void>;
}

const STATUSES: readonly ConversationStatus[] = ['bot', 'human', 'closed'];

function abortableSleep(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    const timer = setTimeout(resolve, ms);
    signal.addEventListener(
      'abort',
      () => {
        clearTimeout(timer);
        resolve();
      },
      { once: true },
    );
  });
}

const isGone = (error: unknown) => error instanceof ApiError && (error.status === 401 || error.status === 404);
const errorKey = (error: unknown): StringKey => (error instanceof ApiError && error.status === 429 ? 'errorRateLimited' : 'errorGeneric');

function parseMessage(data: string): PublicMessage | null {
  try {
    const m = JSON.parse(data) as Partial<PublicMessage>;
    if (typeof m.id === 'number' && typeof m.content === 'string' && (m.role === 'visitor' || m.role === 'bot' || m.role === 'agent')) {
      return { id: m.id, role: m.role, content: m.content, createdAt: typeof m.createdAt === 'number' ? m.createdAt : 0 };
    }
  } catch {
    // ignore malformed events
  }
  return null;
}

function parseStatus(data: string): ConversationStatus | null {
  try {
    const status = (JSON.parse(data) as { status?: unknown }).status;
    return STATUSES.includes(status as ConversationStatus) ? (status as ConversationStatus) : null;
  } catch {
    return null;
  }
}

export class ChatStore {
  private state: ChatState = {
    phase: 'loading',
    config: null,
    locale: 'en',
    open: false,
    messages: [],
    status: 'bot',
    sending: false,
    error: null,
    connection: 'idle',
    contact: 'hidden',
  };
  private readonly listeners = new Set<(state: ChatState) => void>();
  private session: StoredSession | null = null;
  private streamAbort: AbortController | null = null;

  constructor(private readonly deps: ChatStoreDeps) {}

  getState(): ChatState {
    return this.state;
  }

  subscribe(listener: (state: ChatState) => void): () => void {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  }

  private set(patch: Partial<ChatState>): void {
    this.state = { ...this.state, ...patch };
    for (const listener of [...this.listeners]) listener(this.state);
  }

  async init(): Promise<void> {
    try {
      const config = await this.deps.api.getConfig();
      this.set({ phase: 'ready', config, locale: pickLocale(this.deps.localeOverride, this.deps.browserLanguage, config.locale) });
    } catch {
      this.set({ phase: 'unavailable' });
      return;
    }
    this.session = this.deps.sessions.load();
  }

  async setOpen(open: boolean): Promise<void> {
    if (open === this.state.open) return;
    this.set({ open, error: null });
    if (!open) {
      this.stopStream();
      return;
    }
    if (this.session) {
      await this.catchUp();
      this.startStream();
    }
  }

  async send(raw: string): Promise<void> {
    const text = raw.trim();
    if (!text || this.state.sending) return;
    if (text.length > MAX_TEXT) {
      this.set({ error: 'errorTooLong' });
      return;
    }
    this.set({ sending: true, error: null });
    try {
      const result = await this.withSession((s) => this.deps.api.sendMessage(s, text, this.state.locale));
      this.merge(result.messages);
      this.set({ status: result.status });
      this.startStream();
    } catch (error) {
      this.set({ error: errorKey(error) });
    } finally {
      this.set({ sending: false });
    }
  }

  talkToPerson(): Promise<void> {
    return this.send(tr(this.state.locale, 'talkToPersonMessage'));
  }

  openContact(): void {
    this.set({ contact: 'open', error: null });
  }

  closeContact(): void {
    if (this.state.contact === 'open') this.set({ contact: 'hidden', error: null });
  }

  async submitContact(input: ContactInput): Promise<void> {
    const clean: ContactInput = {};
    for (const key of ['name', 'email', 'phone', 'note'] as const) {
      const value = input[key]?.trim();
      if (value) clean[key] = value;
    }
    if (!clean.email && !clean.phone) {
      this.set({ error: 'errorContact' });
      return;
    }
    this.set({ contact: 'sending', error: null });
    try {
      await this.withSession((s) => this.deps.api.sendContact(s, clean));
      this.set({ contact: 'sent' });
      this.startStream();
    } catch (error) {
      this.set({ contact: 'open', error: error instanceof ApiError && error.status === 422 ? 'errorContact' : errorKey(error) });
    }
  }

  destroy(): void {
    this.stopStream();
    this.listeners.clear();
  }

  /** Runs `fn` with a session (creating one if needed); a gone stored session is replaced once. */
  private async withSession<T>(fn: (session: StoredSession) => Promise<T>): Promise<T> {
    const hadSession = this.session !== null;
    const session = this.session ?? (await this.createSession());
    try {
      return await fn(session);
    } catch (error) {
      if (!hadSession || !isGone(error)) throw error;
      this.resetSession();
      return fn(await this.createSession());
    }
  }

  private async createSession(): Promise<StoredSession> {
    const session = await this.deps.api.createConversation();
    this.session = session;
    this.deps.sessions.save(session);
    return session;
  }

  private resetSession(): void {
    this.stopStream();
    this.session = null;
    this.deps.sessions.clear();
    this.set({ messages: [], status: 'bot', contact: 'hidden' });
  }

  private lastId(): number {
    return this.state.messages.at(-1)?.id ?? 0;
  }

  private merge(incoming: PublicMessage[]): void {
    if (incoming.length === 0) return;
    const byId = new Map(this.state.messages.map((m) => [m.id, m]));
    for (const m of incoming) byId.set(m.id, m);
    this.set({ messages: [...byId.values()].sort((a, b) => a.id - b.id) });
  }

  private async catchUp(): Promise<void> {
    const session = this.session;
    if (!session) return;
    try {
      for (;;) {
        const page = await this.deps.api.listMessages(session, this.lastId());
        if (this.session !== session) return;
        this.merge(page.messages);
        this.set({ status: page.status });
        if (page.messages.length < PAGE_SIZE) return;
      }
    } catch (error) {
      if (isGone(error) && this.session === session) this.resetSession();
    }
  }

  private startStream(): void {
    if (!this.state.open || !this.session || this.streamAbort) return;
    const controller = new AbortController();
    this.streamAbort = controller;
    void this.runStream(this.session, controller);
  }

  private stopStream(): void {
    this.streamAbort?.abort();
    this.streamAbort = null;
    if (this.state.connection !== 'idle') this.set({ connection: 'idle' });
  }

  private async runStream(session: StoredSession, controller: AbortController): Promise<void> {
    const sleep = this.deps.sleep ?? abortableSleep;
    let attempt = 0;
    while (!controller.signal.aborted) {
      this.set({ connection: attempt === 0 ? 'connecting' : 'retrying' });
      try {
        const lastId = this.lastId();
        await this.deps.stream({
          url: this.deps.api.eventsUrl(session.conversationId),
          token: session.visitorToken,
          lastEventId: lastId > 0 ? lastId : null,
          signal: controller.signal,
          onEvent: (event) => {
            if (this.onStreamEvent(event)) attempt = 0;
          },
        });
      } catch (error) {
        if (controller.signal.aborted) return;
        if (isGone(error)) {
          this.resetSession();
          return;
        }
      }
      if (controller.signal.aborted) return;
      const delay = BACKOFF_MS[Math.min(attempt, BACKOFF_MS.length - 1)]!;
      attempt += 1;
      this.set({ connection: 'retrying' });
      await sleep(delay, controller.signal);
    }
  }

  /** Returns true when the connection is confirmed healthy (resets the backoff). */
  private onStreamEvent(event: SseEvent): boolean {
    if (event.event === 'ready') {
      this.set({ connection: 'live' });
      void this.catchUp();
      return true;
    }
    if (event.event === 'message') {
      const message = parseMessage(event.data);
      if (message) this.merge([message]);
    } else if (event.event === 'status') {
      const status = parseStatus(event.data);
      if (status) this.set({ status });
    }
    return false;
  }
}
```

> The reconnect test expects the backoff sequence 1000 → 2000 → (ready) → 1000: `attempt` resets to 0 on `ready`, so the next delay after that stream ends is `BACKOFF_MS[0]`. If the test's ordering assumptions about `flush()` don't hold (multiple awaits per loop iteration), add another `await flush()` in the test — do not change the algorithm.

- [ ] **Step 5: Verify GREEN** — `npx vitest run apps/widget` PASS; `npx vitest run` PASS; `npm run typecheck` exit 0.

- [ ] **Step 6: Commit**

```bash
git add apps/widget/src
git commit -m "feat(widget): add chat store with lazy conversations, dedupe, gap-fill and reconnect"
```

---

### Task 4: UI components, styles and boot

**Files:**
- Create: `apps/widget/src/components/App.tsx`, `apps/widget/src/widget.tsx`
- Modify: `apps/widget/src/widget.css` (full stylesheet), `apps/widget/src/main.tsx` (real boot)
- Test: `apps/widget/src/components/App.test.tsx`

**Interfaces:**
- Consumes: `ChatStore`, `ChatState`, `tr`, `StringKey`, `mountShadow`, `readEmbedConfig`, `createApi`, `createSessionStore`, `streamEvents`, fakes.
- Produces: `useChatState(store)`, `App({ store })`; `startWidget(embed, env?): { store; mounted; destroy() } | null`.

UI requirements: launcher button (`aria-label` "Chat with us"/"在线咨询", `aria-expanded`); panel `role="dialog"` labelled by the business name; header with close button; greeting bubble; quick-question chips only while there are no messages; message log `role="log"` `aria-live="polite"`; message text rendered as plain text; typing indicator while sending; "Talk to a person" link (bot mode) or "Leave your contact details" (human mode) + human notice; contact form with labelled inputs; error `role="alert"`; reconnect notice; composer textarea focused on open — Enter sends, Shift+Enter newline, never send while an IME composition is active (`isComposing`); Escape closes the panel and returns focus to the launcher; `--rw-primary` from config; left/right position; full-screen panel under 480 px.

- [ ] **Step 1: Write failing tests** — `apps/widget/src/components/App.test.tsx`:
```tsx
// @vitest-environment happy-dom
import { waitFor, within } from '@testing-library/preact';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from '../api';
import { ChatStore } from '../chatStore';
import { mountShadow, type Mounted } from '../mount';
import { controllableStream, fakeApi, memorySessions, msg } from '../testing/fakes';
import { App } from './App';

let mounted: Mounted | null = null;
afterEach(() => {
  mounted?.unmount();
  mounted = null;
});

async function setup(api = fakeApi(), locale: string | null = null) {
  const { stream, calls } = controllableStream();
  const store = new ChatStore({ api, sessions: memorySessions(), stream, localeOverride: locale, browserLanguage: 'en-US', sleep: async () => {} });
  await store.init();
  mounted = mountShadow(<App store={store} />)!;
  const root = mounted.root;
  const q = within(root as unknown as HTMLElement);
  return { api, store, root, q, user: userEvent.setup(), streams: calls };
}

async function openPanel(ctx: Awaited<ReturnType<typeof setup>>) {
  await ctx.user.click(ctx.q.getByRole('button', { name: 'Chat with us' }));
  return ctx.q.findByRole('dialog', { name: 'Harbor & Home Cleaning' });
}

describe('App', () => {
  it('shows only the launcher until opened, then the greeting, quick questions and a focused input', async () => {
    const ctx = await setup();
    expect(ctx.q.queryByRole('dialog')).toBeNull();
    const launcher = ctx.q.getByRole('button', { name: 'Chat with us' });
    expect(launcher.getAttribute('aria-expanded')).toBe('false');
    await openPanel(ctx);
    expect(ctx.q.getByText('Hi! How can we help you today?')).toBeTruthy();
    expect(ctx.q.getByRole('button', { name: 'What are your hours?' })).toBeTruthy();
    await waitFor(() => expect(ctx.root.activeElement).toBe(ctx.q.getByLabelText('Message')));
  });

  it('applies the brand color and position', async () => {
    const ctx = await setup();
    const rootDiv = ctx.root.querySelector('.rw-root') as HTMLElement;
    expect(rootDiv.style.getPropertyValue('--rw-primary').trim()).toBe('#2563eb');
    expect(rootDiv.classList.contains('rw-right')).toBe(true);
  });

  it('sends a quick question and hides the chips afterwards', async () => {
    const ctx = await setup();
    await openPanel(ctx);
    await ctx.user.click(ctx.q.getByRole('button', { name: 'What are your hours?' }));
    expect(await ctx.q.findByText('Echo: What are your hours?')).toBeTruthy();
    expect(ctx.q.queryByRole('button', { name: 'Do you bring supplies?' })).toBeNull();
  });

  it('sends with Enter, keeps Shift+Enter as a newline and clears the input', async () => {
    const ctx = await setup();
    await openPanel(ctx);
    const input = ctx.q.getByLabelText('Message') as HTMLTextAreaElement;
    await ctx.user.type(input, 'line one{Shift>}{Enter}{/Shift}line two');
    expect(ctx.api.sendMessage).not.toHaveBeenCalled();
    await ctx.user.type(input, '{Enter}');
    await waitFor(() => expect(ctx.api.sendMessage).toHaveBeenCalledTimes(1));
    expect(ctx.api.sendMessage.mock.calls[0]![1]).toBe('line one\nline two');
    await waitFor(() => expect(input.value).toBe(''));
  });

  it('renders message content as plain text', async () => {
    const api = fakeApi({
      sendMessage: vi.fn().mockResolvedValue({ messages: [msg('visitor', 'hi', 1), msg('bot', '<img src=x onerror="alert(1)">', 2)], status: 'bot' }),
    });
    const ctx = await setup(api);
    await openPanel(ctx);
    await ctx.user.type(ctx.q.getByLabelText('Message'), 'hi{Enter}');
    expect(await ctx.q.findByText('<img src=x onerror="alert(1)">')).toBeTruthy();
    expect(ctx.root.querySelector('img')).toBeNull();
  });

  it('offers the contact form when a person takes over and thanks the visitor', async () => {
    const ctx = await setup();
    await openPanel(ctx);
    await ctx.user.click(ctx.q.getByRole('button', { name: 'Talk to a person' }));
    await waitFor(() => expect(ctx.streams).toHaveLength(1));
    ctx.streams[0]!.options.onEvent({ event: 'status', data: '{"status":"human"}', id: null });
    expect(await ctx.q.findByText('A team member will reply here as soon as possible.')).toBeTruthy();
    await ctx.user.click(ctx.q.getByRole('button', { name: 'Leave your contact details' }));
    await ctx.user.type(ctx.q.getByLabelText('Email'), 'a@example.com');
    await ctx.user.click(ctx.q.getByRole('button', { name: 'Send details' }));
    expect(await ctx.q.findByText('Thanks! We will get back to you soon.')).toBeTruthy();
    expect(ctx.api.sendContact).toHaveBeenCalledWith(expect.anything(), { email: 'a@example.com' });
  });

  it('shows errors as alerts', async () => {
    const ctx = await setup(fakeApi({ sendMessage: vi.fn().mockRejectedValue(new ApiError(429, 'rate_limited')) }));
    await openPanel(ctx);
    await ctx.user.type(ctx.q.getByLabelText('Message'), 'hi{Enter}');
    expect((await ctx.q.findByRole('alert')).textContent).toBe('You are sending messages too quickly. Please wait a moment.');
  });

  it('closes with Escape and returns focus to the launcher', async () => {
    const ctx = await setup();
    await openPanel(ctx);
    await ctx.user.type(ctx.q.getByLabelText('Message'), '{Escape}');
    await waitFor(() => expect(ctx.q.queryByRole('dialog')).toBeNull());
    expect(ctx.root.activeElement).toBe(ctx.q.getByRole('button', { name: 'Chat with us' }));
  });

  it('renders in Chinese when requested', async () => {
    const ctx = await setup(fakeApi(), 'zh-CN');
    await ctx.user.click(ctx.q.getByRole('button', { name: '在线咨询' }));
    expect(await ctx.q.findByText('您好！有什么可以帮您？')).toBeTruthy();
  });

  it('renders nothing when the widget is unavailable', async () => {
    const ctx = await setup(fakeApi({ getConfig: vi.fn().mockRejectedValue(new ApiError(403, 'origin_not_allowed')) }));
    expect(ctx.q.queryByRole('button')).toBeNull();
  });
});
```

- [ ] **Step 2: Run to verify RED** — `npx vitest run apps/widget/src/components` → FAIL.

- [ ] **Step 3: Implement `apps/widget/src/components/App.tsx`**

```tsx
import type { TargetedEvent, TargetedKeyboardEvent, TargetedSubmitEvent } from 'preact';
import { useEffect, useRef, useState } from 'preact/hooks';
import type { ContactInput, PublicMessage } from '../api';
import type { ChatState, ChatStore } from '../chatStore';
import { tr, type StringKey } from '../i18n';

type Translate = (key: StringKey) => string;

export function useChatState(store: ChatStore): ChatState {
  const [state, setState] = useState(() => store.getState());
  useEffect(() => {
    setState(store.getState());
    return store.subscribe(setState);
  }, [store]);
  return state;
}

function ChatIcon() {
  return (
    <svg viewBox="0 0 24 24" width="26" height="26" aria-hidden="true">
      <path fill="currentColor" d="M4 4h16a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H8l-4 4V6a2 2 0 0 1 2-2z" />
    </svg>
  );
}

export function App({ store }: { store: ChatStore }) {
  const state = useChatState(store);
  const launcherRef = useRef<HTMLButtonElement>(null);
  if (state.phase !== 'ready' || !state.config) return null;
  const { config, locale } = state;
  const t: Translate = (key) => tr(locale, key);
  const close = () => {
    void store.setOpen(false);
    launcherRef.current?.focus();
  };
  return (
    <div class={`rw-root rw-${config.widget.position}`} style={`--rw-primary:${config.widget.primaryColor}`} lang={locale}>
      {state.open ? <Panel store={store} state={state} t={t} onClose={close} /> : null}
      <button
        ref={launcherRef}
        type="button"
        class="rw-launcher"
        aria-expanded={state.open}
        aria-label={t(state.open ? 'closeChat' : 'openChat')}
        onClick={() => (state.open ? close() : void store.setOpen(true))}
      >
        {state.open ? <span aria-hidden="true">×</span> : <ChatIcon />}
      </button>
    </div>
  );
}

interface PanelProps {
  store: ChatStore;
  state: ChatState;
  t: Translate;
  onClose(): void;
}

function Panel({ store, state, t, onClose }: PanelProps) {
  const config = state.config!;
  const greeting = config.widget.greeting[state.locale] || config.widget.greeting.en;
  const onKeyDown = (e: TargetedKeyboardEvent<HTMLDivElement>) => {
    if (e.key === 'Escape') {
      e.stopPropagation();
      onClose();
    }
  };
  return (
    <div class="rw-panel" role="dialog" aria-label={config.business.name} onKeyDown={onKeyDown}>
      <div class="rw-header">
        <span class="rw-title">{config.business.name}</span>
        <button type="button" class="rw-close" aria-label={t('closeChat')} onClick={onClose}>
          ×
        </button>
      </div>
      <MessageList greeting={greeting} messages={state.messages} sending={state.sending} t={t} />
      {state.messages.length === 0 && config.quickQuestions.length > 0 ? (
        <div class="rw-quick">
          {config.quickQuestions.map((question) => (
            <button type="button" class="rw-chip" key={question} disabled={state.sending} onClick={() => void store.send(question)}>
              {question}
            </button>
          ))}
        </div>
      ) : null}
      {state.status === 'human' ? <p class="rw-notice">{t('humanNotice')}</p> : null}
      {state.contact === 'hidden' ? (
        <div class="rw-actions">
          {state.status === 'human' ? (
            <button type="button" class="rw-link" onClick={() => store.openContact()}>
              {t('leaveContact')}
            </button>
          ) : (
            <button type="button" class="rw-link" disabled={state.sending} onClick={() => void store.talkToPerson()}>
              {t('talkToPerson')}
            </button>
          )}
        </div>
      ) : (
        <ContactForm store={store} state={state} t={t} />
      )}
      {state.connection === 'retrying' ? <p class="rw-status">{t('reconnecting')}</p> : null}
      {state.error ? (
        <p class="rw-error" role="alert">
          {t(state.error)}
        </p>
      ) : null}
      <Composer store={store} sending={state.sending} t={t} />
    </div>
  );
}

function MessageList({ greeting, messages, sending, t }: { greeting: string; messages: PublicMessage[]; sending: boolean; t: Translate }) {
  const endRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    endRef.current?.scrollIntoView?.({ block: 'end' });
  }, [messages.length, sending]);
  return (
    <div class="rw-messages" role="log" aria-live="polite">
      {greeting ? <div class="rw-msg rw-bot">{greeting}</div> : null}
      {messages.map((m) => (
        <div key={m.id} class={`rw-msg rw-${m.role}`}>
          {m.content}
        </div>
      ))}
      {sending ? (
        <div class="rw-msg rw-bot rw-typing" aria-label={t('typing')}>
          …
        </div>
      ) : null}
      <div ref={endRef} />
    </div>
  );
}

function Composer({ store, sending, t }: { store: ChatStore; sending: boolean; t: Translate }) {
  const [text, setText] = useState('');
  const inputRef = useRef<HTMLTextAreaElement>(null);
  useEffect(() => {
    inputRef.current?.focus();
  }, []);
  const submit = async () => {
    if (!text.trim() || sending) return;
    await store.send(text);
    if (!store.getState().error) setText('');
  };
  return (
    <form
      class="rw-composer"
      onSubmit={(e: TargetedSubmitEvent<HTMLFormElement>) => {
        e.preventDefault();
        void submit();
      }}
    >
      <textarea
        ref={inputRef}
        class="rw-input"
        rows={1}
        aria-label={t('inputLabel')}
        placeholder={t('inputPlaceholder')}
        value={text}
        onInput={(e: TargetedEvent<HTMLTextAreaElement, Event>) => setText(e.currentTarget.value)}
        onKeyDown={(e: TargetedKeyboardEvent<HTMLTextAreaElement>) => {
          // Never send while an IME (e.g. Chinese pinyin) composition is in progress.
          if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
            e.preventDefault();
            void submit();
          }
        }}
      />
      <button type="submit" class="rw-send" disabled={sending || !text.trim()}>
        {t('send')}
      </button>
    </form>
  );
}

function ContactForm({ store, state, t }: { store: ChatStore; state: ChatState; t: Translate }) {
  const [values, setValues] = useState<Required<ContactInput>>({ name: '', email: '', phone: '', note: '' });
  if (state.contact === 'sent') {
    return (
      <p class="rw-notice" role="status">
        {t('contactThanks')}
      </p>
    );
  }
  const update = (key: keyof ContactInput) => (e: TargetedEvent<HTMLInputElement | HTMLTextAreaElement, Event>) => {
    const value = e.currentTarget.value;
    setValues((v) => ({ ...v, [key]: value }));
  };
  return (
    <form
      class="rw-contact"
      noValidate
      onSubmit={(e: TargetedSubmitEvent<HTMLFormElement>) => {
        e.preventDefault();
        void store.submitContact(values);
      }}
    >
      <p class="rw-intro">{t('contactIntro')}</p>
      <label class="rw-field">
        <span>{t('name')}</span>
        <input type="text" autocomplete="name" value={values.name} onInput={update('name')} />
      </label>
      <label class="rw-field">
        <span>{t('email')}</span>
        <input type="email" autocomplete="email" value={values.email} onInput={update('email')} />
      </label>
      <label class="rw-field">
        <span>{t('phone')}</span>
        <input type="tel" autocomplete="tel" value={values.phone} onInput={update('phone')} />
      </label>
      <label class="rw-field">
        <span>{t('note')}</span>
        <textarea rows={2} value={values.note} onInput={update('note')} />
      </label>
      <div class="rw-row">
        <button type="button" class="rw-link" onClick={() => store.closeContact()}>
          {t('cancel')}
        </button>
        <button type="submit" class="rw-send" disabled={state.contact === 'sending'}>
          {t('submitContact')}
        </button>
      </div>
    </form>
  );
}
```

> If Preact 11's `aria-expanded={boolean}` renders as `"true"`/`"false"` strings (expected) the test passes; if it omits the attribute for `false`, pass `String(state.open)`.

- [ ] **Step 4: Full stylesheet** — replace `apps/widget/src/widget.css`:
```css
:host {
  all: initial;
}

.rw-root {
  --rw-primary: #2563eb;
  --rw-text: #1f2937;
  --rw-muted: #6b7280;
  --rw-bg: #ffffff;
  --rw-bubble: #f3f4f6;
  --rw-radius: 14px;
  position: fixed;
  bottom: 20px;
  z-index: 2147483000;
  font-family: system-ui, -apple-system, 'Segoe UI', Roboto, 'PingFang SC', 'Microsoft YaHei', sans-serif;
  font-size: 15px;
  line-height: 1.45;
  color: var(--rw-text);
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 12px;
}
.rw-right { right: 20px; }
.rw-left { left: 20px; align-items: flex-start; }
.rw-root *, .rw-root *::before, .rw-root *::after { box-sizing: border-box; }
.rw-root button { font: inherit; cursor: pointer; }
.rw-root button:disabled { cursor: default; opacity: 0.6; }
.rw-root :focus-visible { outline: 2px solid var(--rw-primary); outline-offset: 2px; }

.rw-launcher {
  width: 56px;
  height: 56px;
  border: none;
  border-radius: 50%;
  background: var(--rw-primary);
  color: #fff;
  font-size: 28px;
  display: grid;
  place-items: center;
  box-shadow: 0 6px 20px rgba(0, 0, 0, 0.2);
}

.rw-panel {
  width: 360px;
  height: min(560px, calc(100vh - 110px));
  background: var(--rw-bg);
  border-radius: var(--rw-radius);
  box-shadow: 0 12px 40px rgba(0, 0, 0, 0.22);
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.rw-header {
  background: var(--rw-primary);
  color: #fff;
  padding: 12px 14px;
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.rw-title { font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.rw-close { background: none; border: none; color: #fff; font-size: 22px; line-height: 1; padding: 0 4px; }

.rw-messages {
  flex: 1;
  overflow-y: auto;
  padding: 12px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.rw-msg {
  max-width: 85%;
  padding: 8px 12px;
  border-radius: var(--rw-radius);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.rw-bot, .rw-agent { align-self: flex-start; background: var(--rw-bubble); }
.rw-agent { border-left: 3px solid var(--rw-primary); }
.rw-visitor { align-self: flex-end; background: var(--rw-primary); color: #fff; }
.rw-typing { color: var(--rw-muted); letter-spacing: 2px; }

.rw-quick { display: flex; flex-wrap: wrap; gap: 6px; padding: 0 12px 8px; }
.rw-chip {
  border: 1px solid var(--rw-primary);
  color: var(--rw-primary);
  background: #fff;
  border-radius: 999px;
  padding: 4px 10px;
  font-size: 13px;
}
.rw-actions { padding: 0 12px 6px; }
.rw-link { background: none; border: none; color: var(--rw-primary); padding: 2px 0; font-size: 13px; text-decoration: underline; }
.rw-notice, .rw-status, .rw-error, .rw-intro { margin: 0; padding: 0 12px 6px; font-size: 13px; }
.rw-notice, .rw-status, .rw-intro { color: var(--rw-muted); }
.rw-error { color: #b91c1c; }

.rw-contact { padding: 0 12px 8px; display: flex; flex-direction: column; gap: 6px; }
.rw-contact .rw-intro { padding: 0; }
.rw-field { display: flex; flex-direction: column; gap: 2px; font-size: 13px; }
.rw-field input, .rw-field textarea {
  font: inherit;
  padding: 6px 8px;
  border: 1px solid #d1d5db;
  border-radius: 8px;
  color: var(--rw-text);
  background: #fff;
}
.rw-row { display: flex; justify-content: flex-end; align-items: center; gap: 12px; }

.rw-composer { display: flex; gap: 8px; padding: 10px 12px; border-top: 1px solid #e5e7eb; }
.rw-input {
  flex: 1;
  resize: none;
  font: inherit;
  padding: 8px 10px;
  border: 1px solid #d1d5db;
  border-radius: 10px;
  max-height: 96px;
  color: var(--rw-text);
  background: #fff;
}
.rw-send { border: none; border-radius: 10px; background: var(--rw-primary); color: #fff; padding: 0 14px; }

@media (max-width: 480px) {
  .rw-root { bottom: 12px; }
  .rw-right { right: 12px; }
  .rw-left { left: 12px; }
  .rw-panel { position: fixed; inset: 0; width: 100vw; height: 100dvh; border-radius: 0; }
}
```

- [ ] **Step 5: Boot wiring**

`apps/widget/src/widget.tsx`:
```tsx
import { createApi } from './api';
import { ChatStore } from './chatStore';
import { App } from './components/App';
import type { EmbedConfig } from './embed';
import { mountShadow, type Mounted } from './mount';
import { streamEvents } from './sse';
import { createSessionStore } from './storage';

export interface StartedWidget {
  store: ChatStore;
  mounted: Mounted;
  destroy(): void;
}

export function startWidget(
  embed: EmbedConfig,
  env: { fetch: typeof fetch; language: string | undefined } = { fetch: globalThis.fetch.bind(globalThis), language: navigator.language },
): StartedWidget | null {
  const api = createApi(embed.apiBase, embed.slug, env.fetch);
  const store = new ChatStore({
    api,
    sessions: createSessionStore(embed.slug),
    stream: (options) => streamEvents({ ...options, fetchImpl: env.fetch }),
    localeOverride: embed.locale,
    browserLanguage: env.language,
  });
  const mounted = mountShadow(<App store={store} />);
  if (!mounted) return null;
  void store.init();
  return {
    store,
    mounted,
    destroy() {
      store.destroy();
      mounted.unmount();
    },
  };
}
```

`apps/widget/src/main.tsx`:
```tsx
import { readEmbedConfig } from './embed';
import { startWidget } from './widget';

// document.currentScript is only set during this synchronous first execution.
const embed = readEmbedConfig(document.currentScript as HTMLScriptElement | null, document, location);
if (embed) startWidget(embed);
```

- [ ] **Step 6: Verify GREEN and size** — `npx vitest run apps/widget` PASS (run the App test file twice to check for flakiness); `npx vitest run` PASS; `npm run typecheck` exit 0; `npm run build:widget` passes the size check (expect roughly 15–25 KB gzip; report the number).

- [ ] **Step 7: Commit**

```bash
git add apps/widget/src
git commit -m "feat(widget): add accessible chat UI, styles and widget boot"
```

---

### Task 5: Serve the widget and a demo page; embedding docs

**Files:**
- Create: `apps/server/src/http/routes/static.ts`, `apps/server/src/http/routes/static.test.ts`, `apps/widget/README.md`
- Modify: `apps/server/src/http/deps.ts` (`widgetFile`), `apps/server/src/http/app.ts` (register routes), `apps/server/src/testing/harness.ts` (`widgetFile` option), `apps/server/README.md` (link to the widget docs)

**Interfaces:**
- Produces: `AppDeps.widgetFile: string` (default: `apps/widget/dist/widget.js` resolved from `deps.ts` via `fileURLToPath(new URL('../../../widget/dist/widget.js', import.meta.url))`), `CreateAppDepsInput.widgetFile?: string`, `HarnessOptions.widgetFile?: string`; routes `GET /widget.js`, `GET /demo?business=<slug>`; `staticRoutes(deps)`.

- [ ] **Step 1: Write failing tests** — `apps/server/src/http/routes/static.test.ts`:
```ts
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
  if (dir) rmSync(dir, { recursive: true, force: true });
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
```

- [ ] **Step 2: Run to verify RED** — `npx vitest run apps/server/src/http/routes/static.test.ts` → FAIL.

- [ ] **Step 3: Implement**

`apps/server/src/http/routes/static.ts`:
```ts
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
```

`apps/server/src/http/deps.ts`: add `widgetFile: string` to `AppDeps`, `widgetFile?: string` to `CreateAppDepsInput`, and in `createAppDeps` set `widgetFile: input.widgetFile ?? DEFAULT_WIDGET_FILE` with
```ts
import { fileURLToPath } from 'node:url';
/** apps/widget/dist/widget.js, resolved from this file so it works from any cwd. */
export const DEFAULT_WIDGET_FILE = fileURLToPath(new URL('../../../widget/dist/widget.js', import.meta.url));
```

`apps/server/src/http/app.ts`: register `app.route('/', staticRoutes(deps));` after the API routes.

`apps/server/src/testing/harness.ts`: add `widgetFile?: string` to `HarnessOptions` and pass it through to `createAppDeps` when set.

- [ ] **Step 4: Embedding docs** — `apps/widget/README.md`:
```md
# @reception/widget

The chat bubble merchants add to their website. It is one file, `widget.js` (≈ 20 KB gzip), with all styles isolated in a Shadow DOM so it cannot clash with the site's CSS.

## Embed on a website
1. In the dashboard (or via the API), add the website's origin, e.g. `https://www.example.com`, to the business's **allowed origins**. `www.example.com` and `example.com` are different origins.
2. Paste before `</body>`:
   ```html
   <script src="https://chat.your-domain.com/widget.js" data-business="your-slug" async></script>
   ```
Optional attributes: `data-locale="zh-CN"` (force the language; default: visitor's browser language), `data-api="https://…"` (API server, default: where `widget.js` is served from).

## Behaviour
- The conversation is created when the visitor sends their first message; it survives page reloads (stored in `localStorage`, or memory in private mode).
- Replies from a team member arrive live while the panel is open.
- When a team member takes over, the visitor can leave an email or phone number.

## Develop
- `npm run build:widget` (repo root) builds `apps/widget/dist/widget.js` and fails if it exceeds 40 KB gzip.
- The server serves it at `/widget.js` and a fictional demo page at `/demo?business=<slug>`.
```

In `apps/server/README.md` add one line under the API overview: "`GET /widget.js` (the built chat widget, see `apps/widget/README.md`) and `GET /demo?business=<slug>` (fictional demo page)".

- [ ] **Step 5: Verify GREEN** — `npx vitest run` (all packages) PASS; `npm run typecheck` exit 0; `npm run build:widget` passes.

- [ ] **Step 6: Commit**

```bash
git add apps/server apps/widget/README.md
git commit -m "feat(server): serve the widget bundle and a demo page"
```

---

## Final verification (controller, after all tasks — not a subagent task)

Real-browser end-to-end check with the built-in browser:
1. `npm run build:widget`.
2. Create a root `.env` with a generated `APP_SECRET`, `DATABASE_URL=file:./data/e2e.db`, `PUBLIC_ORIGIN=http://127.0.0.1:8787`; `npm run cli -w @reception/server -- seed-demo --email demo@example.com --password "demo password 123"`; start `npm run start:server`.
3. Open `http://127.0.0.1:8787/demo`: launcher visible, opens panel, quick question answered, booking flow completes with the "pending confirmation" template, reload keeps the conversation, "Talk to a person" switches to human mode and shows the contact form.
4. Log in via the admin API (curl with `Origin`), post an agent reply → it appears live in the widget.
5. Mobile viewport (375 px): panel full-screen.
6. Clean up `.env` and `apps/server/data/e2e.db*`.

## Self-Review Notes (plan author)
- Spec coverage: one-tag embed ✔ (T1, T5); Shadow DOM + inline CSS, nothing in head ✔ (T1); hand-written CSS + variables, no Tailwind ✔ (T4); gzip < 40 KB with a failing check ✔ (T1/T4); en/zh-CN ✔ (T2/T4); localStorage fallback ✔ (T2); refresh-persistent sessions ✔ (T2/T3); live agent replies via SSE with Bearer header ✔ (T2/T3); contact form for offline/human mode ✔ (T3/T4); "you can close this window" booking template comes from the server ✔.
- Plan 2 contract notes honored: fetch-streaming SSE, dedupe by id, gap-fill on ready (ready precedes replay; replay capped at 200), create on first send, 404/401 → new conversation, text-only rendering, status values incl. `closed` (next message reopens server-side).
- Deviations: none from the spec; `/demo` is a minimal fictional page (Plan 4 builds the proper demo site and dashboard).
