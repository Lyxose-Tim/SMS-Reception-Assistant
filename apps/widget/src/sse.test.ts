import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from './api';
import { IDLE_TIMEOUT_MS, createSseParser, streamEvents, type SseEvent } from './sse';

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
  });

  const baseOptions = (fetchImpl: unknown, extra: Partial<Parameters<typeof streamEvents>[0]> = {}) => ({
    url: 'u',
    token: 't',
    lastEventId: null,
    signal: new AbortController().signal,
    onEvent: () => {},
    fetchImpl: fetchImpl as typeof fetch,
    ...extra,
  });

  it('exports a 60 s default idle timeout (server pings every 25 s)', () => {
    expect(IDLE_TIMEOUT_MS).toBe(60_000);
  });

  it('rejects with code stalled and cancels the reader when no chunk arrives within idleTimeoutMs', async () => {
    const cancel = vi.fn();
    const body = new ReadableStream<Uint8Array>({ cancel });
    const fetchImpl = vi.fn().mockResolvedValue(new Response(body, { status: 200 }));
    await expect(streamEvents(baseOptions(fetchImpl, { idleTimeoutMs: 30 }))).rejects.toMatchObject({ name: 'ApiError', status: 0, code: 'stalled' });
    expect(cancel).toHaveBeenCalled();
  });

  it('does not stall while comment-only pings keep arriving', async () => {
    const encoder = new TextEncoder();
    let interval: ReturnType<typeof setInterval> | undefined;
    let sent = 0;
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        interval = setInterval(() => {
          controller.enqueue(encoder.encode(': ping\n\n'));
          if (++sent === 6) {
            clearInterval(interval);
            controller.close();
          }
        }, 10);
      },
      cancel() {
        clearInterval(interval);
      },
    });
    const fetchImpl = vi.fn().mockResolvedValue(new Response(body, { status: 200 }));
    await expect(streamEvents(baseOptions(fetchImpl, { idleTimeoutMs: 40 }))).resolves.toBeUndefined();
    expect(sent).toBe(6);
  });

  it('cancels the stream and rejects when onEvent throws', async () => {
    const cancel = vi.fn();
    const encoder = new TextEncoder();
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(encoder.encode('data: x\n\n'));
      },
      cancel,
    });
    const fetchImpl = vi.fn().mockResolvedValue(new Response(body, { status: 200 }));
    const boom = new Error('boom');
    await expect(
      streamEvents(
        baseOptions(fetchImpl, {
          onEvent: () => {
            throw boom;
          },
        }),
      ),
    ).rejects.toBe(boom);
    expect(cancel).toHaveBeenCalled();
  });

  it('maps a fetch rejection to ApiError network, but rethrows as-is when the signal was aborted', async () => {
    const failing = vi.fn().mockRejectedValue(new TypeError('Failed to fetch'));
    const error = await streamEvents(baseOptions(failing)).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 0, code: 'network' });

    const controller = new AbortController();
    controller.abort();
    const abortError = new DOMException('aborted', 'AbortError');
    const aborted = vi.fn().mockRejectedValue(abortError);
    await expect(streamEvents(baseOptions(aborted, { signal: controller.signal }))).rejects.toBe(abortError);
  });

  it('maps a mid-stream read error to ApiError network', async () => {
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.error(new TypeError('connection reset'));
      },
    });
    const fetchImpl = vi.fn().mockResolvedValue(new Response(body, { status: 200 }));
    await expect(streamEvents(baseOptions(fetchImpl))).rejects.toMatchObject({ status: 0, code: 'network' });
  });

  it('rejects with no_body when a 2xx response has no body', async () => {
    const fetchImpl = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
    await expect(streamEvents(baseOptions(fetchImpl))).rejects.toMatchObject({ status: 204, code: 'no_body' });
  });
});
