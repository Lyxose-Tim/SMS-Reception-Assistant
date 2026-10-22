import { openDatabase } from './db/client';
import type { Env } from './env';
import { createApp } from './http/app';
import { createAppDeps, type AppDeps } from './http/deps';
import { listen } from './http/listen';
import { createSmtpMailer, type Mailer } from './mail/mailer';

export interface RunningServer {
  url: string;
  deps: AppDeps;
  close(): Promise<void>;
}

export async function startServer(env: Env, options: { mailer?: Mailer | null; port?: number } = {}): Promise<RunningServer> {
  const database = await openDatabase(env.DATABASE_URL);
  const mailer =
    options.mailer !== undefined ? options.mailer : env.SMTP_URL && env.MAIL_FROM ? createSmtpMailer(env.SMTP_URL, env.MAIL_FROM) : null;
  let deps: AppDeps;
  let server: Awaited<ReturnType<typeof listen>>;
  try {
    deps = createAppDeps({ env, db: database.db, mailer });
    server = await listen(createApp(deps), env.HOST, options.port ?? env.PORT);
  } catch (error) {
    database.close();
    throw error;
  }
  return {
    url: server.url,
    deps,
    close: async () => {
      await server.close();
      database.close();
    },
  };
}
