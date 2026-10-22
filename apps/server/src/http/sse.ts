import type { Context } from 'hono';
import { streamSSE } from 'hono/streaming';
import type { BusEvent } from '../lib/bus';
import type { AppDeps } from './deps';

export interface SseOptions {
  topic: string;
  connectionKey: string;
  /** null = no replay */
  lastEventId: number | null;
  replay: (afterId: number) => Promise<BusEvent[]>;
}

/** Max events buffered for a slow client before we cut it off; it reconnects with Last-Event-ID and replays. */
const MAX_QUEUED_EVENTS = 500;

function numericEventId(id: string | undefined): number | null {
  if (id === undefined || !/^\d+$/.test(id)) return null;
  const n = Number(id);
  return Number.isSafeInteger(n) ? n : null;
}

export function sseStream(c: Context, deps: Pick<AppDeps, 'bus' | 'sse' | 'heartbeatMs'>, opts: SseOptions): Response {
  // Tell reverse proxies (nginx) not to buffer the stream.
  c.header('X-Accel-Buffering', 'no');
  return streamSSE(c, async (stream) => {
    const queue: BusEvent[] = [];
    let closed = false;
    let wake: (() => void) | null = null;
    const signal = () => {
      const w = wake;
      wake = null;
      w?.();
    };
    // Subscribe before replaying so nothing published in between is lost.
    const unsubscribe = deps.bus.subscribe(opts.topic, (event) => {
      if (closed) return;
      if (queue.length >= MAX_QUEUED_EVENTS) {
        // Slow client: stop buffering and end the stream; the client reconnects with Last-Event-ID and replays.
        closed = true;
        queue.length = 0;
        signal();
        return;
      }
      queue.push(event);
      signal();
    });
    const unregister = deps.sse.add(opts.connectionKey, () => {
      closed = true;
      // Also abort so a pending write to a stalled client is cancelled instead of blocking cleanup forever.
      stream.abort();
      signal();
    });
    const cleanup = () => {
      unsubscribe();
      unregister();
    };
    stream.onAbort(() => {
      closed = true;
      cleanup();
      signal();
    });

    // Largest id actually delivered by the replay; live events at or below it are duplicates.
    let replayMax = 0;
    const write = (event: BusEvent) =>
      stream.writeSSE({ event: event.type, data: JSON.stringify(event.data), ...(event.id ? { id: event.id } : {}) });
    const sendLive = async (event: BusEvent) => {
      const numericId = numericEventId(event.id);
      if (numericId !== null && numericId <= replayMax) return; // already delivered by replay
      await write(event);
    };

    try {
      await stream.writeSSE({ event: 'ready', data: '{}' });
      if (opts.lastEventId !== null) {
        for (const event of await opts.replay(opts.lastEventId)) {
          await write(event);
          const numericId = numericEventId(event.id);
          if (numericId !== null && numericId > replayMax) replayMax = numericId;
        }
      }
      while (!closed && !stream.aborted) {
        if (queue.length === 0) {
          let timer: ReturnType<typeof setTimeout> | undefined;
          const timedOut = await new Promise<boolean>((resolve) => {
            if (closed || queue.length > 0) return resolve(false);
            wake = () => resolve(false);
            timer = setTimeout(() => resolve(true), deps.heartbeatMs);
          });
          clearTimeout(timer);
          wake = null;
          if (closed || stream.aborted) break;
          if (timedOut && queue.length === 0) {
            await stream.write(': ping\n\n');
            continue;
          }
        }
        while (queue.length > 0 && !closed) await sendLive(queue.shift()!);
      }
    } finally {
      cleanup();
    }
  });
}
