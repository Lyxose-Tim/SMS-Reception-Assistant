import { describe, expect, it } from 'vitest';
import { detectTextLocale, normalizeLocaleHint, resolveLocale } from './locale';

describe('detectTextLocale', () => {
  it('detects Chinese', () => expect(detectTextLocale('你们几点开门？')).toBe('zh-CN'));
  it('detects an English sentence', () => expect(detectTextLocale('What are your hours?')).toBe('en'));
  it('treats Chinese mixed with an English word as Chinese', () =>
    expect(detectTextLocale('deep cleaning 多少钱')).toBe('zh-CN'));
  it('returns null for a single English word', () => expect(detectTextLocale('pricing?')).toBeNull());
  it('returns null for emoji and numbers', () => expect(detectTextLocale('👍 123')).toBeNull());
  it('accepts a single Chinese character', () => expect(detectTextLocale('在？')).toBe('zh-CN'));
});

describe('normalizeLocaleHint', () => {
  it('maps any zh variant to zh-CN', () => expect(normalizeLocaleHint('zh-TW')).toBe('zh-CN'));
  it('defaults to en', () => expect(normalizeLocaleHint(undefined)).toBe('en'));
});

describe('resolveLocale', () => {
  it('keeps the established locale for ambiguous text', () =>
    expect(resolveLocale('pricing?', 'zh-CN', 'en-US')).toBe('zh-CN'));
  it('switches when the latest message is clearly another language', () =>
    expect(resolveLocale('What are your hours?', 'zh-CN', null)).toBe('en'));
  it('falls back to the browser hint', () => expect(resolveLocale('hi', null, 'zh-CN')).toBe('zh-CN'));
  it('defaults to English', () => expect(resolveLocale('hi', null, undefined)).toBe('en'));
});
