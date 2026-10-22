import type { Server } from 'node:http';
import { serve } from '@hono/node-server';
import type { Hono } from 'hono';
import type { AppEnv } from './types';

export interface Listening {
  url: string;
  close(): Promise<void>;
}

export function listen(app: Hono<AppEnv>, hostname: string, port: number): Promise<Listening> {
  return new Promise((resolve, reject) => {
    const urlHost = hostname === '0.0.0.0' ? '127.0.0.1' : hostname === '::' ? '[::1]' : hostname;
    const server = serve({ fetch: app.fetch, hostname, port }, (info) => {
      server.off('error', reject);
      resolve({
        url: `http://${urlHost}:${info.port}`,
        close: () =>
          new Promise<void>((done) => {
            // SSE streams are long-lived: drop them so close() resolves.
            (server as Server).closeAllConnections();
            server.close(() => done());
          }),
      });
    });
    // EADDRINUSE etc. surface as 'error' events; reject instead of crashing with an uncaught exception.
    server.once('error', reject);
  });
}
