import { describe, expect, it } from 'vitest';
import { isNearBottom } from './scroll';

describe('isNearBottom', () => {
  it('is true within 80 px of the bottom and false further up', () => {
    expect(isNearBottom({ scrollHeight: 1000, scrollTop: 500, clientHeight: 500 })).toBe(true);
    expect(isNearBottom({ scrollHeight: 1000, scrollTop: 420, clientHeight: 500 })).toBe(true);
    expect(isNearBottom({ scrollHeight: 1000, scrollTop: 419, clientHeight: 500 })).toBe(false);
    expect(isNearBottom({ scrollHeight: 300, scrollTop: 0, clientHeight: 500 })).toBe(true);
  });
});
