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

describe('matchFaq precision fixes', () => {
  const domain = ['Standard cleaning', 'Deep cleaning', 'Move-out cleaning'];
  const zhPets: Faq = { id: 'zh-pets', question: '可以带宠物吗', answer: '可以。', enabled: true };

  it('strips Chinese function words before building bigrams', () => {
    expect([...faqTokens('可以带宠物吗')]).toEqual(['带宠', '宠物']);
  });

  it('does not match on function words or a single shared character run', () => {
    expect(matchFaq('可以带孩子吗', [zhPets])).toBeNull();
    const card: Faq = { id: 'card', question: '你们可以刷卡吗', answer: '可以。', enabled: true };
    expect(matchFaq('你们可以带宠物吗', [card])).toBeNull();
    expect(matchFaq('家里有宠物可以吗', [zhPets])?.id).toBe('zh-pets');
  });

  it('ignores domain terms as match signals', () => {
    expect(matchFaq('Do you do deep cleaning at home?', faqs, domain)).toBeNull();
    expect(matchFaq('do you bring supplies?', faqs, domain)?.id).toBe('faq-supplies');
    expect(matchFaq('can pets stay at home?', faqs, domain)?.id).toBe('faq-pets');
  });

  it('does not match on single shared token in multi-token FAQ', () => {
    expect(matchFaq('Will you allow me to reschedule?', faqs, domain)).toBeNull();
  });
});
