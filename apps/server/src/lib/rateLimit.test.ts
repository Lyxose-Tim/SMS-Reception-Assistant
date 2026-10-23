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

  it('prunes at most once per window', () => {
    const limiter = createRateLimiter(1, 1000);
    for (let i = 0; i < 10_001; i++) limiter.hit(`k${i}`, 0);
    limiter.hit('a', 1500); // prune runs: all idle keys dropped
    expect(limiter.size()).toBe(1);
    for (let i = 0; i < 10_001; i++) limiter.hit(`m${i}`, 1500);
    limiter.hit('b', 3000); // 1500ms since last prune: prunes again
    expect(limiter.size()).toBe(1);
    for (let i = 0; i < 10_001; i++) limiter.hit(`n${i}`, 2500); // idle by 3600
    limiter.hit('c', 3600); // idle keys exist but only 600ms since the last prune: skipped
    expect(limiter.size()).toBeGreaterThan(10_000);
  });
});
