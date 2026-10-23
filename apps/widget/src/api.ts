import type { WidgetLocale } from './i18n';
import { isSession, type StoredSession } from './storage';

export type ConversationStatus = 'bot' | 'human' | 'closed';

export interface PublicMessage {
  id: number;
  role: 'visitor' | 'bot' | 'agent';
  content: string;
  createdAt: number;
}

export interface WidgetConfig {
  business: { name: string; slug: string };
  locale: WidgetLocale;
  widget: { primaryColor: string; position: 'left' | 'right'; greeting: Record<WidgetLocale, string> };
  quickQuestions: string[];
}

export interface ContactInput {
  name?: string;
  email?: string;
  phone?: string;
  note?: string;
}

export interface MessagesResult {
  messages: PublicMessage[];
  status: ConversationStatus;
}

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
  ) {
    super(`Request failed: ${status} ${code}`);
    this.name = 'ApiError';
  }
}

export interface WidgetApi {
  getConfig(): Promise<WidgetConfig>;
  createConversation(): Promise<StoredSession>;
  listMessages(session: StoredSession, since: number): Promise<MessagesResult>;
  sendMessage(session: StoredSession, text: string, locale: WidgetLocale): Promise<MessagesResult>;
  sendContact(session: StoredSession, input: ContactInput): Promise<void>;
  eventsUrl(conversationId: string): string;
}

const REQUEST_TIMEOUT_MS = 30_000; // a turn may wait up to 15 s for the AI model

export interface TimeoutImpl {
  timeout?: (ms: number) => AbortSignal;
}

/** AbortSignal.timeout where available, else AbortController + setTimeout. Call dispose() once the request settles. */
export function timeoutSignal(ms: number, impl: TimeoutImpl = AbortSignal): { signal: AbortSignal; dispose(): void } {
  if (typeof impl.timeout === 'function') return { signal: impl.timeout(ms), dispose() {} };
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), ms);
  return { signal: controller.signal, dispose: () => clearTimeout(timer) };
}

export async function errorFromResponse(res: Response): Promise<ApiError> {
  let code = 'http_error';
  try {
    const body = (await res.json()) as { error?: unknown };
    if (typeof body.error === 'string') code = body.error;
  } catch {
    // non-JSON error body
  }
  return new ApiError(res.status, code);
}

const DEFAULT_COLOR = '#2563eb';
const MAX_QUICK_QUESTIONS = 4;

type Dict = Record<string, unknown>;

function isDict(value: unknown): value is Dict {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

const asDict = (value: unknown): Dict => (isDict(value) ? value : {});
const asString = (value: unknown, fallback = ''): string => (typeof value === 'string' ? value : fallback);

/** The server is trusted for shape, not for content: clamp every field the UI depends on. */
export function normalizeConfig(raw: Dict, slug: string): WidgetConfig {
  const business = asDict(raw.business);
  const widget = asDict(raw.widget);
  const greeting = asDict(widget.greeting);
  const questions = Array.isArray(raw.quickQuestions) ? raw.quickQuestions.filter((q): q is string => typeof q === 'string' && q.trim() !== '') : [];
  const color = widget.primaryColor;
  return {
    business: { name: asString(business.name), slug: asString(business.slug, slug) },
    locale: raw.locale === 'zh-CN' ? 'zh-CN' : 'en',
    widget: {
      primaryColor: typeof color === 'string' && /^#[0-9a-fA-F]{6}$/.test(color) ? color : DEFAULT_COLOR,
      position: widget.position === 'left' ? 'left' : 'right',
      greeting: { en: asString(greeting.en), 'zh-CN': asString(greeting['zh-CN']) },
    },
    quickQuestions: [...new Set(questions)].slice(0, MAX_QUICK_QUESTIONS),
  };
}

export function createApi(apiBase: string, slug: string, fetchImpl: typeof fetch = globalThis.fetch.bind(globalThis)): WidgetApi {
  const base = `${apiBase}/api/widget`;
  const auth = (s: StoredSession) => ({ Authorization: `Bearer ${s.visitorToken}` });
  const conv = (s: StoredSession) => `${base}/c/${encodeURIComponent(s.conversationId)}`;

  async function request<T>(url: string, init: RequestInit = {}, validate?: (value: unknown) => value is T): Promise<T> {
    const timeout = timeoutSignal(REQUEST_TIMEOUT_MS);
    try {
      let res: Response;
      try {
        res = await fetchImpl(url, { ...init, signal: timeout.signal });
      } catch {
        throw new ApiError(0, 'network');
      }
      if (!res.ok) throw await errorFromResponse(res);
      let body: unknown;
      try {
        body = await res.json();
      } catch {
        throw new ApiError(res.status, 'bad_response');
      }
      if (validate && !validate(body)) throw new ApiError(res.status, 'bad_response');
      return body as T;
    } finally {
      timeout.dispose();
    }
  }

  const postJson = (body: unknown, headers: Record<string, string> = {}): RequestInit => ({
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...headers },
    body: JSON.stringify(body),
  });

  return {
    getConfig: async () => normalizeConfig(await request<Dict>(`${base}/b/${encodeURIComponent(slug)}/config`, {}, isDict), slug),
    createConversation: () => request<StoredSession>(`${base}/b/${encodeURIComponent(slug)}/conversations`, { method: 'POST' }, isSession),
    listMessages: (s, since) => request<MessagesResult>(`${conv(s)}/messages?since=${since}`, { headers: auth(s) }),
    sendMessage: (s, text, locale) => request<MessagesResult>(`${conv(s)}/messages`, postJson({ text, locale }, auth(s))),
    sendContact: async (s, input) => {
      await request<{ ok: boolean }>(`${conv(s)}/contact`, postJson(input, auth(s)));
    },
    eventsUrl: (conversationId) => `${base}/c/${encodeURIComponent(conversationId)}/events`,
  };
}
