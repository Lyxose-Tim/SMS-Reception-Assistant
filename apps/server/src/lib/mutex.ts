export interface KeyedMutex {
  run<T>(key: string, fn: () => Promise<T>): Promise<T>;
  size(): number;
}

export function createKeyedMutex(): KeyedMutex {
  const tails = new Map<string, Promise<void>>();
  return {
    async run(key, fn) {
      const previous = tails.get(key) ?? Promise.resolve();
      let release!: () => void;
      const current = new Promise<void>((resolve) => {
        release = resolve;
      });
      const tail = previous.then(() => current);
      tails.set(key, tail);
      await previous;
      try {
        return await fn();
      } finally {
        release();
        if (tails.get(key) === tail) tails.delete(key);
      }
    },
    size: () => tails.size,
  };
}
