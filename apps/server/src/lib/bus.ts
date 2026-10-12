export interface BusEvent {
  type: string;
  /** Numeric message id as a string, used for SSE replay; omitted for non-message events. */
  id?: string;
  data: unknown;
}

export type Listener = (event: BusEvent) => void;

export interface EventBus {
  subscribe(topic: string, listener: Listener): () => void;
  publish(topic: string, event: BusEvent): void;
  listenerCount(topic?: string): number;
}

export function createEventBus(): EventBus {
  const topics = new Map<string, Set<Listener>>();
  return {
    subscribe(topic, listener) {
      let set = topics.get(topic);
      if (!set) {
        set = new Set();
        topics.set(topic, set);
      }
      set.add(listener);
      let active = true;
      return () => {
        if (!active) return;
        active = false;
        const current = topics.get(topic);
        if (!current) return;
        current.delete(listener);
        if (current.size === 0) topics.delete(topic);
      };
    },
    publish(topic, event) {
      const set = topics.get(topic);
      if (!set) return;
      for (const listener of [...set]) {
        try {
          listener(event);
        } catch (error) {
          // One broken subscriber (e.g. a closed stream) must not break the publisher or other subscribers.
          console.error('event listener failed', error);
        }
      }
    },
    listenerCount(topic) {
      if (topic !== undefined) return topics.get(topic)?.size ?? 0;
      let total = 0;
      for (const set of topics.values()) total += set.size;
      return total;
    },
  };
}
