import { and, eq, lte } from 'drizzle-orm';
import type { Db } from '../db/client';
import { sessions, users } from '../db/schema';
import { ValidationError } from '../domain/validation';
import { hashPassword, randomToken, sha256 } from '../lib/crypto';

export type UserRow = typeof users.$inferSelect;
export const SESSION_TTL_MS = 7 * 24 * 60 * 60 * 1000;
export const MIN_PASSWORD_LENGTH = 10;

export interface AuthContext {
  user: { id: string; email: string; role: UserRow['role'] };
  businessId: string;
  /** sha256 of the session token */
  sessionId: string;
}

export async function createUser(
  db: Db,
  input: { businessId: string; email: string; password: string; role?: UserRow['role'] },
): Promise<UserRow> {
  if (input.password.length < MIN_PASSWORD_LENGTH) {
    throw new ValidationError([{ path: 'password', message: `Use at least ${MIN_PASSWORD_LENGTH} characters` }]);
  }
  const [row] = await db
    .insert(users)
    .values({
      businessId: input.businessId,
      email: input.email.trim().toLowerCase(),
      passwordHash: await hashPassword(input.password),
      role: input.role ?? 'owner',
    })
    .returning();
  return row!;
}

export async function findUserByEmail(db: Db, email: string): Promise<UserRow | null> {
  const [row] = await db.select().from(users).where(eq(users.email, email.trim().toLowerCase())).limit(1);
  return row ?? null;
}

export async function createSession(db: Db, userId: string, now: Date): Promise<{ token: string; expiresAt: Date }> {
  const token = randomToken();
  const expiresAt = new Date(now.getTime() + SESSION_TTL_MS);
  await db.insert(sessions).values({ id: sha256(token), userId, expiresAt, createdAt: now });
  return { token, expiresAt };
}

export async function getSessionAuth(db: Db, token: string, now: Date): Promise<AuthContext | null> {
  if (!token) return null;
  const id = sha256(token);
  const [row] = await db
    .select({ session: sessions, user: users })
    .from(sessions)
    .innerJoin(users, eq(users.id, sessions.userId))
    .where(eq(sessions.id, id))
    .limit(1);
  if (!row) return null;
  if (row.session.expiresAt.getTime() <= now.getTime()) {
    await db.delete(sessions).where(eq(sessions.id, id));
    return null;
  }
  return { user: { id: row.user.id, email: row.user.email, role: row.user.role }, businessId: row.user.businessId, sessionId: id };
}

export async function deleteSession(db: Db, token: string): Promise<void> {
  await db.delete(sessions).where(eq(sessions.id, sha256(token)));
}

export async function deleteExpiredSessions(db: Db, userId: string, now: Date): Promise<void> {
  await db.delete(sessions).where(and(eq(sessions.userId, userId), lte(sessions.expiresAt, now)));
}
