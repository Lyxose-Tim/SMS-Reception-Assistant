import { ApiError, type ContactInput, type ConversationStatus, type PublicMessage, type WidgetApi, type WidgetConfig } from './api';
import { pickLocale, tr, type StringKey, type WidgetLocale } from './i18n';
import type { SseEvent, StreamOptions } from './sse';
import type { SessionStore, StoredSession } from './storage';

export const MAX_TEXT = 1000;
export const PAGE_SIZE = 200;
export const BACKOFF_MS = [1000, 2000, 5000, 10000] as const;

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
}

const STATUSES: readonly ConversationStatus[] = ['bot', 'human', 'closed'];

function abortableSleep(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    const timer = setTimeout(resolve, ms);
    signal.addEventListener(
      'abort',
      () => {
        clearTimeout(timer);
        resolve();
      },
      { once: true },
    );
  });
}

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
  private streamAbort: AbortController | null = null;

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
    this.state = { ...this.state, ...patch };
    for (const listener of [...this.listeners]) listener(this.state);
  }

  async init(): Promise<void> {
    try {
      const config = await this.deps.api.getConfig();
      this.set({ phase: 'ready', config, locale: pickLocale(this.deps.localeOverride, this.deps.browserLanguage, config.locale) });
    } catch {
      this.set({ phase: 'unavailable' });
      return;
    }
    this.session = this.deps.sessions.load();
  }

  async setOpen(open: boolean): Promise<void> {
    if (open === this.state.open) return;
    this.set({ open, error: null });
    if (!open) {
      this.stopStream();
      return;
    }
    if (this.session) {
      await this.catchUp();
      this.startStream();
    }
  }

  async send(raw: string): Promise<void> {
    const text = raw.trim();
    if (!text || this.state.sending) return;
    if (text.length > MAX_TEXT) {
      this.set({ error: 'errorTooLong' });
      return;
    }
    this.set({ sending: true, error: null });
    try {
      const result = await this.withSession((s) => this.deps.api.sendMessage(s, text, this.state.locale));
      this.merge(result.messages);
      this.set({ status: result.status });
      this.startStream();
    } catch (error) {
      this.set({ error: errorKey(error) });
    } finally {
      this.set({ sending: false });
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
    try {
      await this.withSession((s) => this.deps.api.sendContact(s, clean));
      this.set({ contact: 'sent' });
      this.startStream();
    } catch (error) {
      this.set({ contact: 'open', error: error instanceof ApiError && error.status === 422 ? 'errorContact' : errorKey(error) });
    }
  }

  destroy(): void {
    this.stopStream();
    this.listeners.clear();
  }

  /** Runs `fn` with a session (creating one if needed); a gone stored session is replaced once. */
  private async withSession<T>(fn: (session: StoredSession) => Promise<T>): Promise<T> {
    const hadSession = this.session !== null;
    const session = this.session ?? (await this.createSession());
    try {
      return await fn(session);
    } catch (error) {
      if (!hadSession || !isGone(error)) throw error;
      this.resetSession();
      return fn(await this.createSession());
    }
  }

  private async createSession(): Promise<StoredSession> {
    const session = await this.deps.api.createConversation();
    this.session = session;
    this.deps.sessions.save(session);
    return session;
  }

  private resetSession(): void {
    this.stopStream();
    this.session = null;
    this.deps.sessions.clear();
    this.set({ messages: [], status: 'bot', contact: 'hidden' });
  }

  private lastId(): number {
    return this.state.messages.at(-1)?.id ?? 0;
  }

  private merge(incoming: PublicMessage[]): void {
    if (incoming.length === 0) return;
    const byId = new Map(this.state.messages.map((m) => [m.id, m]));
    for (const m of incoming) byId.set(m.id, m);
    this.set({ messages: [...byId.values()].sort((a, b) => a.id - b.id) });
  }

  private async catchUp(): Promise<void> {
    const session = this.session;
    if (!session) return;
    try {
      for (;;) {
        const page = await this.deps.api.listMessages(session, this.lastId());
        if (this.session !== session) return;
        this.merge(page.messages);
        this.set({ status: page.status });
        if (page.messages.length < PAGE_SIZE) return;
      }
    } catch (error) {
      if (isGone(error) && this.session === session) this.resetSession();
    }
  }

  private startStream(): void {
    if (!this.state.open || !this.session || this.streamAbort) return;
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
    let attempt = 0;
    while (!controller.signal.aborted) {
      this.set({ connection: attempt === 0 ? 'connecting' : 'retrying' });
      try {
        const lastId = this.lastId();
        await this.deps.stream({
          url: this.deps.api.eventsUrl(session.conversationId),
          token: session.visitorToken,
          lastEventId: lastId > 0 ? lastId : null,
          signal: controller.signal,
          onEvent: (event) => {
            if (this.onStreamEvent(event)) attempt = 0;
          },
        });
      } catch (error) {
        if (controller.signal.aborted) return;
        if (isGone(error)) {
          this.resetSession();
          return;
        }
      }
      if (controller.signal.aborted) return;
      const delay = BACKOFF_MS[Math.min(attempt, BACKOFF_MS.length - 1)]!;
      attempt += 1;
      this.set({ connection: 'retrying' });
      await sleep(delay, controller.signal);
    }
  }

  /** Returns true when the connection is confirmed healthy (resets the backoff). */
  private onStreamEvent(event: SseEvent): boolean {
    if (event.event === 'ready') {
      this.set({ connection: 'live' });
      void this.catchUp();
      return true;
    }
    if (event.event === 'message') {
      const message = parseMessage(event.data);
      if (message) this.merge([message]);
    } else if (event.event === 'status') {
      const status = parseStatus(event.data);
      if (status) this.set({ status });
    }
    return false;
  }
}
