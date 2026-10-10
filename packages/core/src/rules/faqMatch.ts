import type { Faq } from '../types';

const STOPWORDS = new Set([
  'the', 'and', 'for', 'you', 'your', 'are', 'was', 'were', 'what', 'which', 'with', 'have', 'has', 'can', 'our',
  'any', 'how', 'who', 'when', 'where', 'this', 'that', 'there', 'about', 'will', 'would', 'could', 'should',
  'please', 'does', 'did', 'not', 'but', 'all', 'okay', 'during', 'from', 'into', 'its', 'they', 'them',
]);

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
  for (const run of lower.match(/[㐀-鿿]+/g) ?? []) {
    if (run.length === 1) out.add(run);
    for (let i = 0; i + 1 < run.length; i++) out.add(run.slice(i, i + 2));
  }
  return out;
}

export function matchFaq(text: string, faqs: Faq[]): Faq | null {
  const query = faqTokens(text);
  if (query.size === 0) return null;
  let best: { faq: Faq; overlap: number; ratio: number } | null = null;
  for (const faq of faqs) {
    if (!faq.enabled) continue;
    const tokens = faqTokens(faq.question);
    if (tokens.size === 0) continue;
    let overlap = 0;
    for (const token of tokens) if (query.has(token)) overlap++;
    const ratio = overlap / tokens.size;
    const accepted = (overlap >= 2 && ratio >= 0.34) || (overlap === 1 && tokens.size === 1);
    if (!accepted) continue;
    if (!best || overlap > best.overlap || (overlap === best.overlap && ratio > best.ratio)) {
      best = { faq, overlap, ratio };
    }
  }
  return best?.faq ?? null;
}
