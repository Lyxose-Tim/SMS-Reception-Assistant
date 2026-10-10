import { describe, expect, it } from 'vitest';
import { demoKnowledge } from '../demo';
import type { Faq } from '../types';
import { faqTokens, matchFaq } from './faqMatch';

const faqs = demoKnowledge().faqs;

describe('faqTokens', () => {
  it('drops stopwords and short words, stems plurals', () => {
    expect([...faqTokens('Do you bring your own cleaning supplies?')]).toEqual(['bring', 'own', 'cleaning', 'supplie']);
  });

  it('produces Chinese bigrams', () => {
    expect([...faqTokens('带宠物')]).toEqual(['带宠', '宠物']);
  });
});

describe('matchFaq', () => {
  it('matches a paraphrased question', () => {
    expect(matchFaq('do you bring supplies?', faqs)?.id).toBe('faq-supplies');
    expect(matchFaq('are the cleaners insured', faqs)?.id).toBe('faq-insured');
    expect(matchFaq('can pets stay at home?', faqs)?.id).toBe('faq-pets');
  });

  it('returns null for unrelated text', () => {
    expect(matchFaq('what is the weather tomorrow', faqs)).toBeNull();
    expect(matchFaq('How much does deep cleaning cost?', faqs)).toBeNull();
  });

  it('ignores disabled FAQs', () => {
    const disabled = faqs.map((f) => ({ ...f, enabled: false }));
    expect(matchFaq('do you bring supplies?', disabled)).toBeNull();
  });

  it('matches Chinese FAQs', () => {
    const zh: Faq[] = [{ id: 'zh-pets', question: '可以带宠物吗', answer: '可以。', enabled: true }];
    expect(matchFaq('家里有宠物可以吗', zh)?.id).toBe('zh-pets');
  });
});
