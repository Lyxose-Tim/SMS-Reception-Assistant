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

export function sseStream(c: Context, deps: Pick<AppDeps, 'bus' | 'sse' | 'heartbeatMs'>, opts: SseOptions): Response {
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
      queue.push(event);
      signal();
    });
    const unregister = deps.sse.add(opts.connectionKey, () => {
      closed = true;
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

    let lastSentId = opts.lastEventId ?? 0;
    const send = async (event: BusEvent) => {
      const numericId = event.id === undefined ? null : Number(event.id);
      if (numericId !== null && numericId <= lastSentId) return; // already delivered by replay
      await stream.writeSSE({ event: event.type, data: JSON.stringify(event.data), ...(event.id ? { id: event.id } : {}) });
      if (numericId !== null) lastSentId = numericId;
    };

    try {
      await stream.writeSSE({ event: 'ready', data: '{}' });
      if (opts.lastEventId !== null) {
        for (const event of await opts.replay(opts.lastEventId)) await send(event);
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
        while (queue.length > 0 && !closed) await send(queue.shift()!);
      }
    } finally {
      cleanup();
    }
  });
}
