export interface ConnectionRegistry {
  /** Registers a connection; closes the oldest ones beyond the cap. Returns an idempotent release function. */
  add(key: string, close: () => void): () => void;
  count(key?: string): number;
}

export function createConnectionRegistry(maxPerKey: number): ConnectionRegistry {
  const byKey = new Map<string, Array<{ close: () => void }>>();
  return {
    add(key, close) {
      const list = byKey.get(key) ?? [];
      const entry = { close };
      list.push(entry);
      byKey.set(key, list);
      while (list.length > maxPerKey) list.shift()?.close();
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
    count(key) {
      if (key !== undefined) return byKey.get(key)?.length ?? 0;
      let total = 0;
      for (const list of byKey.values()) total += list.length;
      return total;
    },
  };
}
