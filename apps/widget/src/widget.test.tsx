// @vitest-environment happy-dom
import { afterEach, describe, expect, it, vi } from 'vitest';
import { startWidget, whenBodyReady } from './widget';

afterEach(() => localStorage.clear());

function fakeDoc(body: unknown) {
  const listeners: Array<() => void> = [];
  const doc = {
    body,
    addEventListener: vi.fn((_type: string, fn: () => void) => listeners.push(fn)),
  };
  return { doc: doc as unknown as Document, doc_: doc, fire: () => listeners.forEach((fn) => fn()) };
}

describe('whenBodyReady', () => {
  it('runs immediately when document.body exists', () => {
    const { doc, doc_ } = fakeDoc({});
    const fn = vi.fn();
    whenBodyReady(doc, fn);
    expect(fn).toHaveBeenCalledTimes(1);
    expect(doc_.addEventListener).not.toHaveBeenCalled();
  });

  it('defers until DOMContentLoaded when the script runs in <head>', () => {
    const f = fakeDoc(null);
    const fn = vi.fn();
    whenBodyReady(f.doc, fn);
    expect(fn).not.toHaveBeenCalled();
    expect(f.doc_.addEventListener).toHaveBeenCalledWith('DOMContentLoaded', expect.any(Function), { once: true });
    f.doc_.body = {};
    f.fire();
    expect(fn).toHaveBeenCalledTimes(1);
  });
});

describe('startWidget', () => {
  it('stores the session under a key scoped to the business and the API server', async () => {
    const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });
    const fetchImpl = vi.fn(async (url: string | URL | Request) => {
      const u = String(url);
      if (u.endsWith('/config')) return json({ business: { name: 'Harbor', slug: 'harbor-home' }, locale: 'en', widget: {}, quickQuestions: [] });
      if (u.endsWith('/conversations')) return json({ conversationId: 'c1', visitorToken: 't1' }, 201);
      return json({ messages: [], status: 'bot' });
    });
    const started = startWidget({ slug: 'harbor-home', apiBase: 'https://chat.example.com', locale: null }, { fetch: fetchImpl as typeof fetch, language: 'en-US' })!;
    try {
      await vi.waitFor(() => expect(started.store.getState().phase).toBe('ready'));
      await started.store.send('hi');
      expect(localStorage.getItem('reception-widget:harbor-home@https://chat.example.com')).toBe(JSON.stringify({ conversationId: 'c1', visitorToken: 't1' }));
    } finally {
      started.destroy();
    }
  });
});
