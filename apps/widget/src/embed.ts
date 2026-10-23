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
