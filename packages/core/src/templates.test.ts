import { describe, expect, it } from 'vitest';
import { t, templateKeys } from './templates';

describe('t', () => {
  it('interpolates variables in English', () => {
    expect(t('en', 'booking_ask_field', { label: 'name' })).toBe('Could you share your name?');
  });

  it('interpolates variables in Chinese', () => {
    expect(t('zh-CN', 'booking_ask_field', { label: '姓名' })).toBe('请提供您的姓名。');
  });

  it('replaces missing variables with empty strings', () => {
    expect(t('en', 'answer_hours')).toBe('Our hours:');
  });

  it('defines the same keys for every locale', () => {
    expect(templateKeys('zh-CN').sort()).toEqual(templateKeys('en').sort());
  });
});
