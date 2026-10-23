import type { WidgetLocale } from './i18n';
import type { StoredSession } from './storage';

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

export function createApi(apiBase: string, slug: string, fetchImpl: typeof fetch = globalThis.fetch.bind(globalThis)): WidgetApi {
  const base = `${apiBase}/api/widget`;
  const auth = (s: StoredSession) => ({ Authorization: `Bearer ${s.visitorToken}` });
  const conv = (s: StoredSession) => `${base}/c/${encodeURIComponent(s.conversationId)}`;

  async function request<T>(url: string, init: RequestInit = {}): Promise<T> {
    let res: Response;
    try {
      res = await fetchImpl(url, { ...init, signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS) });
    } catch {
      throw new ApiError(0, 'network');
    }
    if (!res.ok) throw await errorFromResponse(res);
    return (await res.json()) as T;
  }

  const postJson = (body: unknown, headers: Record<string, string> = {}): RequestInit => ({
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...headers },
    body: JSON.stringify(body),
  });

  return {
    getConfig: () => request<WidgetConfig>(`${base}/b/${encodeURIComponent(slug)}/config`),
    createConversation: () => request<StoredSession>(`${base}/b/${encodeURIComponent(slug)}/conversations`, { method: 'POST' }),
    listMessages: (s, since) => request<MessagesResult>(`${conv(s)}/messages?since=${since}`, { headers: auth(s) }),
    sendMessage: (s, text, locale) => request<MessagesResult>(`${conv(s)}/messages`, postJson({ text, locale }, auth(s))),
    sendContact: async (s, input) => {
      await request<{ ok: boolean }>(`${conv(s)}/contact`, postJson(input, auth(s)));
    },
    eventsUrl: (conversationId) => `${base}/c/${encodeURIComponent(conversationId)}/events`,
  };
}
