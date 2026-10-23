import { describe, expect, it } from 'vitest';
import { pickLocale, STRINGS, tr } from './i18n';

describe('i18n', () => {
  it('prefers the data-locale override, then the browser language, then the business locale', () => {
    expect(pickLocale('zh-CN', 'en-US', 'en')).toBe('zh-CN');
    expect(pickLocale(null, 'zh-TW', 'en')).toBe('zh-CN');
    expect(pickLocale(null, 'en-GB', 'zh-CN')).toBe('en');
    expect(pickLocale(null, 'fr-FR', 'zh-CN')).toBe('zh-CN');
    expect(pickLocale('xx', undefined, 'en')).toBe('en');
  });

  it('has the same keys in every locale and translates', () => {
    expect(Object.keys(STRINGS['zh-CN']).sort()).toEqual(Object.keys(STRINGS.en).sort());
    expect(tr('en', 'send')).toBe('Send');
    expect(tr('zh-CN', 'send')).toBe('发送');
  });

  it('has a generic message for contact details the server rejects', () => {
    expect(tr('en', 'errorContactRejected')).toBe('Please check your details and try again.');
    expect(tr('zh-CN', 'errorContactRejected')).toBe('请检查您填写的信息后重试。');
  });

  it('asks for a human with phrases the server rules recognize', () => {
    expect(tr('en', 'talkToPersonMessage')).toMatch(/real person/);
    expect(tr('zh-CN', 'talkToPersonMessage')).toContain('人工客服');
  });
});
