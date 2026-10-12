import type { Locale } from './types';

const CJK = /[\u3400-\u9fff\uf900-\ufaff]/g;
const LATIN_WORD = /[a-zA-Z]{2,}/g;

/** Returns the language of a single message, or null when it is too short to tell. */
export function detectTextLocale(text: string): Locale | null {
  const cjk = text.match(CJK)?.length ?? 0;
  const words = text.match(LATIN_WORD)?.length ?? 0;
  if (cjk >= 2) return 'zh-CN';
  if (cjk === 1 && words === 0) return 'zh-CN';
  if (words >= 2) return 'en';
  return null;
}

export function normalizeLocaleHint(hint?: string | null): Locale {
  return hint?.toLowerCase().startsWith('zh') ? 'zh-CN' : 'en';
}

/** Priority: latest message → established conversation locale → browser hint → en. */
export function resolveLocale(text: string, current: Locale | null, hint?: string | null): Locale {
  return detectTextLocale(text) ?? current ?? normalizeLocaleHint(hint);
}
