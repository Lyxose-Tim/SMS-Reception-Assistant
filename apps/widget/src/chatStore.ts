import { ApiError, type ContactInput, type ConversationStatus, type PublicMessage, type WidgetApi, type WidgetConfig } from './api';
import { pickLocale, tr, type StringKey, type WidgetLocale } from './i18n';
import type { SseEvent, StreamOptions } from './sse';
import type { SessionStore, StoredSession } from './storage';

export const MAX_TEXT = 1000;
export const PAGE_SIZE = 200;
export const BACKOFF_MS = [1000, 2000, 5000, 10000] as const;
/** A stream must stay up this long after `ready` before the reconnect backoff starts over. */
export const HEALTHY_MS = 10_000;

export interface ChatState {
  phase: 'loading' | 'ready' | 'unavailable';
  config: WidgetConfig | null;
  locale: WidgetLocale;
  open: boolean;
  messages: PublicMessage[];
  status: ConversationStatus;
  sending: boolean;
  error: StringKey | null;
  connection: 'idle' | 'connecting' | 'live' | 'retrying';
  contact: 'hidden' | 'open' | 'sending' | 'sent';
}

export interface ChatStoreDeps {
  api: WidgetApi;
  sessions: SessionStore;
  stream(options: StreamOptions): Promise<void>;
  localeOverride: string | null;
  browserLanguage: string | undefined;
  sleep?(ms: number, signal: AbortSignal): Promise<void>;
  now?(): number;
}

const STATUSES: readonly ConversationStatus[] = ['bot', 'human', 'closed'];

export function abortableSleep(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    if (signal.aborted) {
      resolve();
      return;
    }
    const onAbort = () => {
      clearTimeout(timer);
      resolve();
    };
    const timer = setTimeout(() => {
      signal.removeEventListener('abort', onAbort);
      resolve();
    }, ms);
    signal.addEventListener('abort', onAbort, { once: true });
  });
}

/** Thrown by `withSession` when the store was destroyed mid-request; callers treat it as a silent no-op. */
class StoreDestroyed extends Error {}

const isGone = (error: unknown) => error instanceof ApiError && (error.status === 401 || error.status === 404);
const errorKey = (error: unknown): StringKey => (error instanceof ApiError && error.status === 429 ? 'errorRateLimited' : 'errorGeneric');

function parseMessage(data: string): PublicMessage | null {
  try {
    const m = JSON.parse(data) as Partial<PublicMessage>;
    if (typeof m.id === 'number' && typeof m.content === 'string' && (m.role === 'visitor' || m.role === 'bot' || m.role === 'agent')) {
      return { id: m.id, role: m.role, content: m.content, createdAt: typeof m.createdAt === 'number' ? m.createdAt : 0 };
    }
  } catch {
    // ignore malformed events
  }
  return null;
}

function parseStatus(data: string): ConversationStatus | null {
  try {
    const status = (JSON.parse(data) as { status?: unknown }).status;
    return STATUSES.includes(status as ConversationStatus) ? (status as ConversationStatus) : null;
  } catch {
    return null;
  }
}

export class ChatStore {
  private state: ChatState = {
    phase: 'loading',
    config: null,
    locale: 'en',
    open: false,
    messages: [],
    status: 'bot',
    sending: false,
    error: null,
    connection: 'idle',
    contact: 'hidden',
  };
  private readonly listeners = new Set<(state: ChatState) => void>();
  private session: StoredSession | null = null;
  private creating: Promise<StoredSession> | null = null;
  private streamAbort: AbortController | null = null;
  /**
   * Highest message id up to which the store has seen the full history (via the stream or a
   * catch-up page). Used as the stream's Last-Event-ID and the catch-up cursor. Send responses
   * never advance it: their ids can be ahead of messages the store has not received yet.
   */
  private syncedId = 0;
  /** Bumped on every live status event so slower request-based status updates can tell they are stale. */
  private statusVersion = 0;
  private destroyed = false;

  constructor(private readonly deps: ChatStoreDeps) {}

  getState(): ChatState {
    return this.state;
  }

  subscribe(listener: (state: ChatState) => void): () => void {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  }

  private set(patch: Partial<ChatState>): void {
    if (this.destroyed) return;
    this.state = { ...this.state, ...patch };
    for (const listener of [...this.listeners]) listener(this.state);
  }

  async init(): Promise<void> {
    let config: WidgetConfig;
    try {
      config = await this.deps.api.getConfig();
    } catch {
      this.set({ phase: 'unavailable' });
      return;
    }
    this.session ??= this.deps.sessions.load();
    // Read before `ready` is published: a subscriber that opens the panel on `ready` resumes by itself.
    const openedEarly = this.state.open;
    this.set({ phase: 'ready', config, locale: pickLocale(this.deps.localeOverride, this.deps.browserLanguage, config.locale) });
    if (openedEarly && this.state.open) await this.resume();
  }

  async setOpen(open: boolean): Promise<void> {
    if (open === this.state.open) return;
    this.set({ open, error: null });
    if (open) await this.resume();
    else this.stopStream();
  }

  async send(raw: string): Promise<void> {
    const text = raw.trim();
    if (!text || this.state.sending) return;
    if (text.length > MAX_TEXT) {
      this.set({ error: 'errorTooLong' });
      return;
    }
    this.set({ sending: true, error: null });
    const statusVersion = this.statusVersion;
    let used: StoredSession | null = null;
    try {
      const { session, result } = await this.withSession((s) => {
        used = s;
        return this.deps.api.sendMessage(s, text, this.state.locale);
      });
      if (session !== this.session) return;
      this.merge(result.messages);
      this.applyStatus(result.status, statusVersion);
    } catch (error) {
      // A failure for a destroyed store or a replaced conversation is not the visitor's current problem.
      if (!(error instanceof StoreDestroyed) && !this.destroyed && used === this.session) this.set({ error: errorKey(error) });
    } finally {
      this.set({ sending: false });
      // Also after a failure: the server may have stored the turn, and the stream's catch-up will show it.
      this.startStream();
    }
  }

  talkToPerson(): Promise<void> {
    return this.send(tr(this.state.locale, 'talkToPersonMessage'));
  }

  openContact(): void {
    this.set({ contact: 'open', error: null });
  }

  closeContact(): void {
    if (this.state.contact === 'open') this.set({ contact: 'hidden', error: null });
  }

  async submitContact(input: ContactInput): Promise<void> {
    if (this.state.contact === 'sending') return;
    const clean: ContactInput = {};
    for (const key of ['name', 'email', 'phone', 'note'] as const) {
      const value = input[key]?.trim();
      if (value) clean[key] = value;
    }
    if (!clean.email && !clean.phone) {
      this.set({ error: 'errorContact' });
      return;
    }
    this.set({ contact: 'sending', error: null });
    let used: StoredSession | null = null;
    try {
      const { session } = await this.withSession((s) => {
        used = s;
        return this.deps.api.sendContact(s, clean);
      });
      if (session === this.session) this.set({ contact: 'sent' });
    } catch (error) {
      if (!(error instanceof StoreDestroyed) && !this.destroyed && used === this.session) {
        this.set({ contact: 'open', error: error instanceof ApiError && error.status === 422 ? 'errorContact' : errorKey(error) });
      }
    } finally {
      this.startStream();
    }
  }

  /** Final: the store stops streaming and never changes state or notifies again. */
  destroy(): void {
    if (this.destroyed) return;
    this.destroyed = true;
    this.streamAbort?.abort();
    this.streamAbort = null;
    this.listeners.clear();
  }

  private async resume(): Promise<void> {
    if (!this.session || this.destroyed) return;
    await this.catchUp();
    this.startStream();
  }

  /**
   * Runs `fn` with a session (creating one if needed); a gone stored session is replaced once.
   * Resolves with the session actually used so callers can drop results for a replaced conversation.
   */
  private async withSession<T>(fn: (session: StoredSession) => Promise<T>): Promise<{ session: StoredSession; result: T }> {
    const existing = this.session;
    const session = existing ?? (await this.createSession());
    if (this.destroyed) throw new StoreDestroyed();
    try {
      return { session, result: await fn(session) };
    } catch (error) {
      if (!existing || !isGone(error)) throw error;
      // A concurrent request may already have replaced the gone session: reuse its replacement.
      if (this.session === session) this.resetSession();
      const replacement = this.session ?? (await this.createSession());
      if (this.destroyed) throw new StoreDestroyed();
      return { session: replacement, result: await fn(replacement) };
    }
  }

  /** Single-flight: concurrent callers share one createConversation request. */
  private createSession(): Promise<StoredSession> {
    if (this.creating) return this.creating;
    const p = (async () => {
      const session = await this.deps.api.createConversation();
      this.session = session;
      this.deps.sessions.save(session);
      return session;
    })();
    this.creating = p;
    // Cleared after assignment (not in the IIFE) so a synchronous throw cannot leave a rejected promise stuck.
    void p
      .finally(() => {
        if (this.creating === p) this.creating = null;
      })
      .catch(() => {});
    return p;
  }

  private resetSession(): void {
    this.stopStream();
    this.session = null;
    this.syncedId = 0;
    this.deps.sessions.clear();
    this.set({ messages: [], status: 'bot', contact: 'hidden' });
  }

  private merge(incoming: PublicMessage[]): void {
    if (incoming.length === 0) return;
    const byId = new Map(this.state.messages.map((m) => [m.id, m]));
    for (const m of incoming) byId.set(m.id, m);
    this.set({ messages: [...byId.values()].sort((a, b) => a.id - b.id) });
  }

  private applyStatus(status: ConversationStatus, versionAtRequest: number): void {
    if (versionAtRequest === this.statusVersion) this.set({ status });
  }

  private async catchUp(): Promise<void> {
    const session = this.session;
    if (!session) return;
    let since = this.syncedId;
    try {
      for (;;) {
        const statusVersion = this.statusVersion;
        const page = await this.deps.api.listMessages(session, since);
        if (this.session !== session || this.destroyed) return;
        this.merge(page.messages);
        this.applyStatus(page.status, statusVersion);
        const last = page.messages.at(-1);
        if (last) {
          since = last.id;
          this.syncedId = Math.max(this.syncedId, since);
        }
        if (page.messages.length < PAGE_SIZE) return;
      }
    } catch (error) {
      if (this.session !== session) return;
      if (isGone(error)) this.resetSession();
      // Roll back to the last confirmed point: ids seen from the stream meanwhile must not hide the failed page's gap.
      else this.syncedId = Math.min(this.syncedId, since);
    }
  }

  private startStream(): void {
    if (this.destroyed || !this.state.open || !this.session || this.streamAbort) return;
    const controller = new AbortController();
    this.streamAbort = controller;
    void this.runStream(this.session, controller);
  }

  private stopStream(): void {
    this.streamAbort?.abort();
    this.streamAbort = null;
    if (this.state.connection !== 'idle') this.set({ connection: 'idle' });
  }

  private async runStream(session: StoredSession, controller: AbortController): Promise<void> {
    const sleep = this.deps.sleep ?? abortableSleep;
    const now = this.deps.now ?? Date.now;
    const active = () => !controller.signal.aborted && !this.destroyed;
    let attempt = 0;
    while (active()) {
      this.set({ connection: attempt === 0 ? 'connecting' : 'retrying' });
      // Assigned in a callback; the `as` cast stops TS narrowing it to `null` at the later `readyAt !== null` check.
      let readyAt = null as number | null;
      try {
        await this.deps.stream({
          url: this.deps.api.eventsUrl(session.conversationId),
          token: session.visitorToken,
          lastEventId: this.syncedId > 0 ? this.syncedId : null,
          signal: controller.signal,
          onEvent: (event) => {
            if (!active()) return;
            if (event.event === 'ready') readyAt = now();
            this.onStreamEvent(event);
          },
        });
      } catch (error) {
        if (!active()) return;
        if (isGone(error)) {
          this.resetSession();
          return;
        }
      }
      if (!active()) return;
      // A server that accepts and then drops connections must not keep us retrying at the fastest rate.
      if (readyAt !== null && now() - readyAt >= HEALTHY_MS) attempt = 0;
      const delay = BACKOFF_MS[Math.min(attempt, BACKOFF_MS.length - 1)]!;
      attempt += 1;
      this.set({ connection: 'retrying' });
      await sleep(delay, controller.signal);
    }
  }

  private onStreamEvent(event: SseEvent): void {
    if (event.event === 'ready') {
      this.set({ connection: 'live' });
      void this.catchUp();
    } else if (event.event === 'message') {
      const message = parseMessage(event.data);
      if (!message) return;
      this.merge([message]);
      // A live stream delivers messages in id order, so everything up to this id has been seen.
      if (message.id > this.syncedId) this.syncedId = message.id;
    } else if (event.event === 'status') {
      const status = parseStatus(event.data);
      if (!status) return;
      this.statusVersion += 1;
      this.set({ status });
    }
  }
}
