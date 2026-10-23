import { errorFromResponse } from './api';

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
}

/**
 * Reads a server-sent event stream with fetch so the visitor token can travel in the
 * Authorization header (EventSource cannot set headers, and tokens must never be in URLs).
 * Resolves when the server closes the stream; rejects with ApiError on non-2xx.
 */
export async function streamEvents(options: StreamOptions): Promise<void> {
  const fetchImpl = options.fetchImpl ?? globalThis.fetch.bind(globalThis);
  const res = await fetchImpl(options.url, {
    headers: {
      Authorization: `Bearer ${options.token}`,
      Accept: 'text/event-stream',
      ...(options.lastEventId !== null ? { 'Last-Event-ID': String(options.lastEventId) } : {}),
    },
    cache: 'no-store',
    signal: options.signal,
  });
  if (!res.ok || !res.body) throw await errorFromResponse(res);
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  const parse = createSseParser(options.onEvent);
  for (;;) {
    const { done, value } = await reader.read();
    if (done) return;
    parse(decoder.decode(value, { stream: true }));
  }
}
