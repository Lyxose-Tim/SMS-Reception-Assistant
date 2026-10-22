import { loadEnv } from './env';
import { startServer } from './server';

const env = loadEnv();
const server = await startServer(env);
console.log(`Reception server listening on ${server.url} (${env.NODE_ENV})`);
if (!env.SMTP_URL || !env.MAIL_FROM) console.warn('SMTP_URL / MAIL_FROM not set: email notifications are disabled.');

let stopping = false;
async function shutdown(signal: string) {
  if (stopping) return;
  stopping = true;
  console.log(`${signal} received, shutting down`);
  await server.close();
  process.exit(0);
}
process.once('SIGINT', () => void shutdown('SIGINT'));
process.once('SIGTERM', () => void shutdown('SIGTERM'));
