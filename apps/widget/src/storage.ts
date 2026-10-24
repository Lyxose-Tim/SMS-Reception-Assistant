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

export function isSession(value: unknown): value is StoredSession {
  const v = value as Partial<StoredSession> | null;
  return typeof v?.conversationId === 'string' && typeof v.visitorToken === 'string' && v.conversationId !== '' && v.visitorToken !== '';
}

/**
 * localStorage can be missing or throw (private mode, in-app WebViews): fall back to memory and keep working.
 * The key includes the API base so switching servers (staging → production) never reuses a foreign session.
 */
export function createSessionStore(slug: string, apiBase: string, getStorage: () => Storage | null = defaultStorage): SessionStore {
  const key = `reception-widget:${slug}@${apiBase}`;
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
      if (memory) return memory; // within one page view the latest save() wins
      try {
        const raw = storage()?.getItem(key);
        if (raw) {
          const parsed: unknown = JSON.parse(raw);
          if (isSession(parsed)) return parsed;
        }
      } catch {
        // unreadable or corrupt: treat as no session
      }
      return null;
    },
    save(session) {
      memory = session;
      try {
        storage()?.setItem(key, JSON.stringify(session));
      } catch {
        // memory copy is enough for this page view; drop any stale stored session so it cannot reappear later
        try {
          storage()?.removeItem(key);
        } catch {
          // nothing more to do
        }
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
