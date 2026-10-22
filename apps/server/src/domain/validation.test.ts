import { demoKnowledge, FAQ_ENTRY_OVERHEAD, HEADER_RESERVE, KNOWLEDGE_LIMITS, profileText } from '@reception/core';
import { describe, expect, it } from 'vitest';
import { knowledgeUsage } from './validation';

describe('knowledgeUsage', () => {
  it('equals the core budget formula over enabled FAQs only', () => {
    const k = demoKnowledge();
    const faqs = [...k.faqs, { id: 'off', question: 'Disabled?', answer: 'x'.repeat(400), enabled: false }];
    const enabled = faqs.filter((f) => f.enabled);
    const expected = HEADER_RESERVE + profileText(k.profile).length + enabled.reduce((n, f) => n + f.question.length + f.answer.length + FAQ_ENTRY_OVERHEAD, 0);
    expect(knowledgeUsage(k.name, k.profile, faqs)).toEqual({ knowledgeChars: expected, enabledFaqs: enabled.length });
    expect(expected).toBeLessThanOrEqual(KNOWLEDGE_LIMITS.totalChars);
  });
});
