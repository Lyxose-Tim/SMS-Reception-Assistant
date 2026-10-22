import type { Server } from 'node:http';
import { serve } from '@hono/node-server';
import type { Hono } from 'hono';
import type { AppEnv } from './types';

export interface Listening {
  url: string;
  close(): Promise<void>;
}

export function listen(app: Hono<AppEnv>, hostname: string, port: number): Promise<Listening> {
  return new Promise((resolve) => {
    const server = serve({ fetch: app.fetch, hostname, port }, (info) => {
      resolve({
        url: `http://${hostname}:${info.port}`,
        close: () =>
          new Promise<void>((done) => {
            // SSE streams are long-lived: drop them so close() resolves.
            (server as Server).closeAllConnections();
            server.close(() => done());
          }),
      });
    });
  });
}
