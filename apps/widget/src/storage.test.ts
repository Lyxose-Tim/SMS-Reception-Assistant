// @vitest-environment happy-dom
import { afterEach, describe, expect, it, vi } from 'vitest';
import { createSessionStore } from './storage';

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  localStorage.clear();
});

const session = { conversationId: 'c1', visitorToken: 't1' };
const API = 'https://chat.example.com';
const KEY = `reception-widget:harbor-home@${API}`;

describe('createSessionStore', () => {
  it('persists per business in localStorage', () => {
    const a = createSessionStore('harbor-home', API);
    a.save(session);
    expect(localStorage.getItem(KEY)).toBe(JSON.stringify(session));
    expect(createSessionStore('harbor-home', API).load()).toEqual(session);
    expect(createSessionStore('other-shop', API).load()).toBeNull();
    a.clear();
    expect(createSessionStore('harbor-home', API).load()).toBeNull();
  });

  it('never reuses a session saved for another API server', () => {
    createSessionStore('harbor-home', 'https://staging.chat.example.com').save(session);
    expect(createSessionStore('harbor-home', API).load()).toBeNull();
    expect(createSessionStore('harbor-home', 'https://staging.chat.example.com').load()).toEqual(session);
  });

  it('ignores corrupt stored values', () => {
    localStorage.setItem(KEY, '{"conversationId":1}');
    expect(createSessionStore('harbor-home', API).load()).toBeNull();
    localStorage.setItem(KEY, 'not json');
    expect(createSessionStore('harbor-home', API).load()).toBeNull();
  });

  it('falls back to memory when localStorage throws (private mode, in-app browsers)', () => {
    const throwing = {
      getItem() {
        throw new DOMException('denied', 'SecurityError');
      },
      setItem() {
        throw new DOMException('denied', 'SecurityError');
      },
      removeItem() {
        throw new DOMException('denied', 'SecurityError');
      },
    };
    vi.stubGlobal('localStorage', throwing);
    const store = createSessionStore('harbor-home', API);
    expect(() => store.save(session)).not.toThrow();
    expect(store.load()).toEqual(session);
    store.clear();
    expect(store.load()).toBeNull();
  });

  it('works when storage is entirely unavailable', () => {
    const store = createSessionStore('harbor-home', API, () => null);
    store.save(session);
    expect(store.load()).toEqual(session);
  });

  it('keeps the latest save when setItem fails but an older session is still stored', () => {
    const old = { conversationId: 'old', visitorToken: 'old-token' };
    const removeItem = vi.fn();
    const stuck = {
      getItem: () => JSON.stringify(old),
      setItem() {
        throw new DOMException('quota', 'QuotaExceededError');
      },
      removeItem,
    } as unknown as Storage;
    const store = createSessionStore('harbor-home', API, () => stuck);
    expect(store.load()).toEqual(old);
    const latest = { conversationId: 'c2', visitorToken: 't2' };
    store.save(latest);
    expect(store.load()).toEqual(latest);
    expect(removeItem).toHaveBeenCalledWith(KEY);
  });
});
