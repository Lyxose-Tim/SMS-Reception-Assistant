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
