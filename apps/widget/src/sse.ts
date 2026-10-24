import { ApiError, errorFromResponse } from './api';

export interface SseEvent {
  event: string;
  data: string;
  id: string | null;
}

/** Incremental text/event-stream parser (handles \n and \r\n, comments and multi-line data). */
export function createSseParser(onEvent: (event: SseEvent) => void): (chunk: string) => void {
  let buffer = '';
  return (chunk) => {
    buffer += chunk;
    for (let match = /\r?\n\r?\n/.exec(buffer); match; match = /\r?\n\r?\n/.exec(buffer)) {
      const raw = buffer.slice(0, match.index);
      buffer = buffer.slice(match.index + match[0].length);
      let event = 'message';
      let id: string | null = null;
      const data: string[] = [];
      for (const line of raw.split(/\r?\n/)) {
        if (line === '' || line.startsWith(':')) continue;
        const colon = line.indexOf(':');
        const field = colon < 0 ? line : line.slice(0, colon);
        const value = colon < 0 ? '' : line.slice(colon + 1).replace(/^ /, '');
        if (field === 'event') event = value;
        else if (field === 'data') data.push(value);
        else if (field === 'id') id = value;
      }
      if (data.length > 0) onEvent({ event, data: data.join('\n'), id });
    }
  };
}

export interface StreamOptions {
  url: string;
  token: string;
  lastEventId: number | null;
  signal: AbortSignal;
  onEvent(event: SseEvent): void;
  fetchImpl?: typeof fetch;
  /** Reject with ApiError(0, 'stalled') when no chunk (pings included) arrives for this long. */
  idleTimeoutMs?: number;
}

/** The server sends a `: ping` comment every 25 s, so 60 s of silence means a dead connection. */
export const IDLE_TIMEOUT_MS = 60_000;

/**
 * Reads a server-sent event stream with fetch so the visitor token can travel in the
 * Authorization header (EventSource cannot set headers, and tokens must never be in URLs).
 * Resolves when the server closes the stream; rejects with ApiError on non-2xx.
 */
export async function streamEvents(options: StreamOptions): Promise<void> {
  const fetchImpl = options.fetchImpl ?? globalThis.fetch.bind(globalThis);
  const idleMs = options.idleTimeoutMs ?? IDLE_TIMEOUT_MS;
  let res: Response;
  try {
    res = await fetchImpl(options.url, {
      headers: {
        Authorization: `Bearer ${options.token}`,
        Accept: 'text/event-stream',
        ...(options.lastEventId !== null ? { 'Last-Event-ID': String(options.lastEventId) } : {}),
      },
      cache: 'no-store',
      signal: options.signal,
    });
  } catch (error) {
    if (options.signal.aborted) throw error;
    throw new ApiError(0, 'network');
  }
  if (!res.ok) throw await errorFromResponse(res);
  if (!res.body) throw new ApiError(res.status, 'no_body');

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  const parse = createSseParser(options.onEvent);
  let timer: ReturnType<typeof setTimeout> | undefined;
  let stall: (error: ApiError) => void = () => {};
  const stalled = new Promise<never>((_, reject) => {
    stall = reject;
  });
  stalled.catch(() => {}); // only observed through the race below
  const arm = () => {
    clearTimeout(timer);
    timer = setTimeout(() => stall(new ApiError(0, 'stalled')), idleMs);
  };
  try {
    arm();
    for (;;) {
      let chunk: ReadableStreamReadResult<Uint8Array>;
      try {
        chunk = await Promise.race([reader.read(), stalled]);
      } catch (error) {
        if (error instanceof ApiError || options.signal.aborted) throw error;
        throw new ApiError(0, 'network');
      }
      if (chunk.done) return;
      arm();
      parse(decoder.decode(chunk.value, { stream: true }));
    }
  } finally {
    clearTimeout(timer);
    reader.cancel().catch(() => {}); // no-op after normal completion; releases the connection otherwise
  }
}
