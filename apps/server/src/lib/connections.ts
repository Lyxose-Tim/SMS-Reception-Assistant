export interface ConnectionRegistry {
  /** Registers a connection; closes the oldest ones beyond the cap. Returns an idempotent release function. */
  add(key: string, close: () => void): () => void;
  count(key?: string): number;
  /** Closes every connection registered under the key. Entries leave via their release functions as streams end. */
  closeKey(key: string): void;
}

export function createConnectionRegistry(maxPerKey: number): ConnectionRegistry {
  const byKey = new Map<string, Array<{ close: () => void }>>();
  return {
    add(key, close) {
      const list = byKey.get(key) ?? [];
      const entry = { close };
      list.push(entry);
      byKey.set(key, list);
      while (list.length > maxPerKey) {
        try {
          list.shift()?.close();
        } catch (error) {
          // A failing close must not abort add() and leave the new entry registered without a release function.
          console.error('connection close failed', error);
        }
      }
      let active = true;
      return () => {
        if (!active) return;
        active = false;
        const current = byKey.get(key);
        if (!current) return;
        const index = current.indexOf(entry);
        if (index >= 0) current.splice(index, 1);
        if (current.length === 0) byKey.delete(key);
      };
    },
    closeKey(key) {
      for (const entry of [...(byKey.get(key) ?? [])]) {
        try {
          entry.close();
        } catch (error) {
          console.error('connection close failed', error);
        }
      }
    },
    count(key) {
      if (key !== undefined) return byKey.get(key)?.length ?? 0;
      let total = 0;
      for (const list of byKey.values()) total += list.length;
      return total;
    },
  };
}
