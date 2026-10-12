import { mkdirSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createClient, type Client } from '@libsql/client';
import { drizzle, type LibSQLDatabase } from 'drizzle-orm/libsql';
import { migrate } from 'drizzle-orm/libsql/migrator';
import * as schema from './schema';

export type Db = LibSQLDatabase<typeof schema>;

export interface Database {
  db: Db;
  client: Client;
  close(): void;
}

/** Resolved from this file so it works regardless of the process cwd. */
export const MIGRATIONS_DIR = fileURLToPath(new URL('../../drizzle', import.meta.url));

export async function openDatabase(url: string): Promise<Database> {
  if (url.startsWith('file:')) {
    mkdirSync(path.dirname(path.resolve(url.slice('file:'.length))), { recursive: true });
  }
  const client = createClient({ url, timeout: 5000 });
  if (url !== ':memory:') await client.execute('PRAGMA journal_mode=WAL');
  await client.execute('PRAGMA foreign_keys=ON');
  const db = drizzle(client, { schema });
  await migrate(db, { migrationsFolder: MIGRATIONS_DIR });
  return { db, client, close: () => client.close() };
}
