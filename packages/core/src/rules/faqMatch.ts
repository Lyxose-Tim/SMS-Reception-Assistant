import type { Faq } from '../types';

const STOPWORDS = new Set([
  'the', 'and', 'for', 'you', 'your', 'are', 'was', 'were', 'what', 'which', 'with', 'have', 'has', 'can', 'our',
  'any', 'how', 'who', 'when', 'where', 'this', 'that', 'there', 'about', 'will', 'would', 'could', 'should',
  'please', 'does', 'did', 'not', 'but', 'all', 'okay', 'during', 'from', 'into', 'its', 'they', 'them',
]);

// Chinese function words that must never form tokens. Longest first so that
// multi-character words are removed before their single-character parts.
const ZH_STOPWORDS = [
  '有没有', '能不能', '你们', '我们', '他们', '可以', '请问', '是否', '一下', '什么', '怎么',
  '吗', '呢', '吧', '啊', '的', '了', '么',
];
const ZH_STOP_PATTERN = new RegExp(ZH_STOPWORDS.join('|'), 'g');

function stem(word: string): string {
  return word.length > 4 && word.endsWith('s') && !word.endsWith('ss') ? word.slice(0, -1) : word;
}

export function faqTokens(text: string): Set<string> {
  const out = new Set<string>();
  const lower = text.toLowerCase();
  for (const word of lower.match(/[a-z0-9]+/g) ?? []) {
    if (word.length < 3 || STOPWORDS.has(word)) continue;
    out.add(stem(word));
  }
  for (const run of lower.replace(ZH_STOP_PATTERN, ' ').match(/[\u3400-\u9fff]+/g) ?? []) {
    if (run.length === 1) out.add(run);
    for (let i = 0; i + 1 < run.length; i++) out.add(run.slice(i, i + 2));
  }
  return out;
}

/**
 * `ignoreTerms` are domain words (business name, service names) that appear in
 * nearly every message and must not count as a match signal.
 */
export function matchFaq(text: string, faqs: Faq[], ignoreTerms: readonly string[] = []): Faq | null {
  const ignored = new Set<string>();
  for (const term of ignoreTerms) for (const token of faqTokens(term)) ignored.add(token);
  const query = new Set([...faqTokens(text)].filter((token) => !ignored.has(token)));
  if (query.size === 0) return null;
  let best: { faq: Faq; overlap: number; ratio: number } | null = null;
  for (const faq of faqs) {
    if (!faq.enabled) continue;
    const tokens = new Set([...faqTokens(faq.question)].filter((token) => !ignored.has(token)));
    if (tokens.size === 0) continue;
    let overlap = 0;
    for (const token of tokens) if (query.has(token)) overlap++;
    const ratio = overlap / tokens.size;
    const accepted = (overlap >= 2 && ratio >= 0.34) || (overlap >= 1 && ratio >= 0.5 && tokens.size <= 2);
    if (!accepted) continue;
    if (!best || overlap > best.overlap || (overlap === best.overlap && ratio > best.ratio)) {
      best = { faq, overlap, ratio };
    }
  }
  return best?.faq ?? null;
}
