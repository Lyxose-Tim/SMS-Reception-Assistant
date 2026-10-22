import { describe, expect, it } from 'vitest';
import { checkReply } from './promptEval';

describe('checkReply', () => {
  it('flags forbidden phrases case-insensitively', () => {
    expect(checkReply('Sure, it is FREE tomorrow!', ['free', 'confirmed'])).toEqual(['free']);
  });

  it('ignores negated mentions in English and Chinese', () => {
    expect(checkReply('That is not free, and nothing is confirmed yet.', ['free', 'confirmed'])).toEqual([]);
    expect(checkReply('这项服务不是免费的。', ['免费'])).toEqual([]);
    expect(checkReply('全部免费！', ['免费'])).toEqual(['免费']);
  });
});
