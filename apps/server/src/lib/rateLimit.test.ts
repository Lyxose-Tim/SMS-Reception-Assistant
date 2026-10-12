import { describe, expect, it } from 'vitest';
import { createRateLimiter } from './rateLimit';

describe('createRateLimiter', () => {
  it('allows up to the limit within the window, per key', () => {
    const limiter = createRateLimiter(3, 1000);
    expect([1, 2, 3, 4].map(() => limiter.hit('a', 0))).toEqual([true, true, true, false]);
    expect(limiter.hit('b', 0)).toBe(true);
  });

  it('frees capacity after the window slides', () => {
    const limiter = createRateLimiter(2, 1000);
    limiter.hit('a', 0);
    limiter.hit('a', 500);
    expect(limiter.hit('a', 900)).toBe(false);
    expect(limiter.hit('a', 1001)).toBe(true);
  });

  it('prunes idle keys when the map grows large', () => {
    const limiter = createRateLimiter(1, 10);
    for (let i = 0; i < 10_001; i++) limiter.hit(`k${i}`, 0);
    limiter.hit('fresh', 1000);
    expect(limiter.size()).toBe(1);
  });
});
