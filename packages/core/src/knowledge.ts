import { neutralizeTags } from './sanitize';
import type { BusinessKnowledge, BusinessProfile, Faq } from './types';

export const KNOWLEDGE_LIMITS = {
  profileChars: 2000,
  faqQuestionChars: 200,
  faqAnswerChars: 500,
  maxEnabledFaqs: 30,
  totalChars: 12000,
} as const;

export const MAX_NAME_CHARS = 100;

const FAQ_QUESTION_PREFIX = 'Q: ';
const FAQ_ANSWER_PREFIX = '\nA: ';
const NAME_PREFIX = 'Business name: ';
const FAQ_HEADER = '\nFAQ:';

/** Characters one FAQ entry costs beyond its question and answer: both prefixes plus one join newline. */
export const FAQ_ENTRY_OVERHEAD = FAQ_QUESTION_PREFIX.length + FAQ_ANSWER_PREFIX.length + 1;

/** Worst-case characters outside profile and FAQ entries: name line at max length, 2 join newlines, FAQ header. */
export const HEADER_RESERVE = NAME_PREFIX.length + MAX_NAME_CHARS + 2 + FAQ_HEADER.length;

export interface KnowledgeIssue {
  path: string;
  code: 'too_long' | 'too_many';
  limit: number;
  actual: number;
}

export function profileText(p: BusinessProfile): string {
  const lines: string[] = [];
  if (p.about.trim()) lines.push(`About: ${p.about.trim()}`);
  if (p.hours.trim()) lines.push(`Hours: ${p.hours.trim()}`);
  if (p.serviceAreas.length) lines.push(`Service areas: ${p.serviceAreas.join(', ')}`);
  if (p.services.length) {
    lines.push('Services:');
    for (const s of p.services) {
      lines.push(`- ${s.name}: ${s.description}${s.pricing ? ` Pricing: ${s.pricing}` : ''}`);
    }
  }
  if (p.priceNote.trim()) lines.push(`Price note: ${p.priceNote.trim()}`);
  if (p.address.trim()) lines.push(`Address: ${p.address.trim()}`);
  if (p.contactEmail.trim()) lines.push(`Email: ${p.contactEmail.trim()}`);
  if (p.contactPhone.trim()) lines.push(`Phone: ${p.contactPhone.trim()}`);
  return lines.join('\n');
}

export function validateKnowledge(profile: BusinessProfile, faqs: Faq[]): KnowledgeIssue[] {
  const L = KNOWLEDGE_LIMITS;
  const issues: KnowledgeIssue[] = [];
  const profileLength = profileText(profile).length;
  if (profileLength > L.profileChars) {
    issues.push({ path: 'profile', code: 'too_long', limit: L.profileChars, actual: profileLength });
  }
  faqs.forEach((f, i) => {
    if (f.question.length > L.faqQuestionChars) {
      issues.push({ path: `faqs.${i}.question`, code: 'too_long', limit: L.faqQuestionChars, actual: f.question.length });
    }
    if (f.answer.length > L.faqAnswerChars) {
      issues.push({ path: `faqs.${i}.answer`, code: 'too_long', limit: L.faqAnswerChars, actual: f.answer.length });
    }
  });
  const enabled = faqs.filter((f) => f.enabled);
  if (enabled.length > L.maxEnabledFaqs) {
    issues.push({ path: 'faqs', code: 'too_many', limit: L.maxEnabledFaqs, actual: enabled.length });
  }
  // Same accounting the block builder spends: reserved header/name plus profile plus every FAQ entry.
  const total =
    HEADER_RESERVE +
    profileLength +
    enabled.reduce((n, f) => n + f.question.length + f.answer.length + FAQ_ENTRY_OVERHEAD, 0);
  if (total > L.totalChars) {
    issues.push({ path: 'knowledge', code: 'too_long', limit: L.totalChars, actual: total });
  }
  return issues;
}

function clip(text: string, max: number): string {
  return text.length <= max ? text : `${text.slice(0, max - 1)}…`;
}

/**
 * Defensive second line of the budget: clips even if stored data bypassed validation.
 * The returned text never exceeds KNOWLEDGE_LIMITS.totalChars, and data that passes
 * validateKnowledge is never truncated here (validation reserves at least what is spent here).
 */
export function buildKnowledgeBlock(k: BusinessKnowledge): { text: string; truncated: boolean } {
  const L = KNOWLEDGE_LIMITS;
  let truncated = false;
  const name = clip(k.name, MAX_NAME_CHARS);
  if (name !== k.name) truncated = true;
  const profile = profileText(k.profile);
  if (profile.length > L.profileChars) truncated = true;
  const parts = [`${NAME_PREFIX}${name}`, clip(profile, L.profileChars)];
  let total = parts.join('\n').length;

  const enabled = k.faqs.filter((f) => f.enabled);
  if (enabled.length > L.maxEnabledFaqs) truncated = true;
  const entries: string[] = [];
  for (const f of enabled.slice(0, L.maxEnabledFaqs)) {
    const q = clip(f.question, L.faqQuestionChars);
    const a = clip(f.answer, L.faqAnswerChars);
    if (q !== f.question || a !== f.answer) truncated = true;
    const entry = `${FAQ_QUESTION_PREFIX}${q}${FAQ_ANSWER_PREFIX}${a}`;
    // Cost of emitting this entry: join newline + entry, plus the FAQ header before the first one.
    const cost = entry.length + 1 + (entries.length === 0 ? FAQ_HEADER.length : 0);
    if (total + cost > L.totalChars) {
      truncated = true;
      break;
    }
    entries.push(entry);
    total += cost;
  }
  if (entries.length) parts.push(FAQ_HEADER.slice(1), ...entries);
  return { text: neutralizeTags(parts.join('\n')), truncated };
}
