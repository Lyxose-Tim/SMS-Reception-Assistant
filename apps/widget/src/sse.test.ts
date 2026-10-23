import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from './api';
import { createSseParser, streamEvents, type SseEvent } from './sse';

afterEach(() => vi.unstubAllGlobals());

function bodyFrom(chunks: (string | Uint8Array)[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  return new ReadableStream({
    start(controller) {
      for (const c of chunks) controller.enqueue(typeof c === 'string' ? encoder.encode(c) : c);
      controller.close();
    },
  });
}

describe('createSseParser', () => {
  it('parses events across chunk boundaries, keeps ids and skips comments', () => {
    const events: SseEvent[] = [];
    const parse = createSseParser((e) => events.push(e));
    parse('event: ready\ndata: {}\n\n: ping\n\nevent: mess');
    parse('age\nid: 7\ndata: {"id":7}\n\n');
    parse('data: line1\r\ndata: line2\r\n\r\n');
    expect(events).toEqual([
      { event: 'ready', data: '{}', id: null },
      { event: 'message', data: '{"id":7}', id: '7' },
      { event: 'message', data: 'line1\nline2', id: null },
    ]);
  });
});

describe('streamEvents', () => {
  it('sends the token in the Authorization header and decodes multi-byte text split across chunks', async () => {
    const bytes = new TextEncoder().encode('event: message\nid: 3\ndata: {"content":"你好"}\n\n');
    const split = bytes.indexOf(0xe4) + 1; // inside the first multi-byte character
    const fetchImpl = vi.fn().mockResolvedValue(new Response(bodyFrom([bytes.slice(0, split), bytes.slice(split)]), { status: 200 }));
    const events: SseEvent[] = [];
    const controller = new AbortController();
    await streamEvents({
      url: 'https://chat.example.com/api/widget/c/c1/events',
      token: 'tok',
      lastEventId: 2,
      signal: controller.signal,
      onEvent: (e) => events.push(e),
      fetchImpl: fetchImpl as unknown as typeof fetch,
    });
    expect(events).toEqual([{ event: 'message', data: '{"content":"你好"}', id: '3' }]);
    const [url, init] = fetchImpl.mock.calls[0]!;
    expect(url).toBe('https://chat.example.com/api/widget/c/c1/events');
    expect(String(url)).not.toContain('tok');
    expect(init.headers).toMatchObject({ Authorization: 'Bearer tok', 'Last-Event-ID': '2', Accept: 'text/event-stream' });
    expect(init.signal).toBe(controller.signal);
  });

  it('omits Last-Event-ID when there is nothing to resume from', async () => {
    const fetchImpl = vi.fn().mockResolvedValue(new Response(bodyFrom([]), { status: 200 }));
    await streamEvents({
      url: 'u',
      token: 't',
      lastEventId: null,
      signal: new AbortController().signal,
      onEvent: () => {},
      fetchImpl: fetchImpl as unknown as typeof fetch,
    });
    expect(fetchImpl.mock.calls[0]![1].headers).not.toHaveProperty('Last-Event-ID');
  });

  it('throws ApiError with the server error code on non-2xx responses', async () => {
    const fetchImpl = vi.fn().mockResolvedValue(new Response(JSON.stringify({ error: 'unauthorized' }), { status: 401 }));
    await expect(
      streamEvents({
        url: 'u',
        token: 't',
        lastEventId: null,
        signal: new AbortController().signal,
        onEvent: () => {},
        fetchImpl: fetchImpl as unknown as typeof fetch,
      }),
    ).rejects.toMatchObject({ name: 'ApiError', status: 401, code: 'unauthorized' });
    expect(ApiError).toBeDefined();
  });
});
