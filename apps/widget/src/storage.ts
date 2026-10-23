export interface StoredSession {
  conversationId: string;
  visitorToken: string;
}

export interface SessionStore {
  load(): StoredSession | null;
  save(session: StoredSession): void;
  clear(): void;
}

function defaultStorage(): Storage | null {
  return typeof localStorage === 'undefined' ? null : localStorage;
}

function isSession(value: unknown): value is StoredSession {
  const v = value as Partial<StoredSession> | null;
  return typeof v?.conversationId === 'string' && typeof v.visitorToken === 'string' && v.conversationId !== '' && v.visitorToken !== '';
}

/** localStorage can be missing or throw (private mode, in-app WebViews): fall back to memory and keep working. */
export function createSessionStore(slug: string, getStorage: () => Storage | null = defaultStorage): SessionStore {
  const key = `reception-widget:${slug}`;
  let memory: StoredSession | null = null;
  const storage = (): Storage | null => {
    try {
      return getStorage();
    } catch {
      return null;
    }
  };
  return {
    load() {
      try {
        const raw = storage()?.getItem(key);
        if (raw) {
          const parsed: unknown = JSON.parse(raw);
          if (isSession(parsed)) return parsed;
        }
      } catch {
        // fall through to memory
      }
      return memory;
    },
    save(session) {
      memory = session;
      try {
        storage()?.setItem(key, JSON.stringify(session));
      } catch {
        // memory copy is enough for this page view
      }
    },
    clear() {
      memory = null;
      try {
        storage()?.removeItem(key);
      } catch {
        // nothing persisted
      }
    },
  };
}
