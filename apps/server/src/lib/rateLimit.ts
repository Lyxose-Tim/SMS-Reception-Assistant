export interface RateLimiter {
  /** Records a hit and returns true when it is within the limit. */
  hit(key: string, now?: number): boolean;
  size(): number;
}

const PRUNE_THRESHOLD = 10_000;

export function createRateLimiter(limit: number, windowMs: number): RateLimiter {
  const hits = new Map<string, number[]>();
  let lastPrune = -Infinity;
  return {
    hit(key, now = Date.now()) {
      const cutoff = now - windowMs;
      if (hits.size > PRUNE_THRESHOLD && now - lastPrune >= windowMs) {
        lastPrune = now;
        for (const [k, times] of hits) if (!times.some((t) => t > cutoff)) hits.delete(k);
      }
      const recent = (hits.get(key) ?? []).filter((t) => t > cutoff);
      if (recent.length >= limit) {
        hits.set(key, recent);
        return false;
      }
      recent.push(now);
      hits.set(key, recent);
      return true;
    },
    size: () => hits.size,
  };
}
