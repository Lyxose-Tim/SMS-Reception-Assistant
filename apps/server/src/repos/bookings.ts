import { and, desc, eq, lt, or, type SQL } from 'drizzle-orm';
import type { Db } from '../db/client';
import { bookingRequests } from '../db/schema';
import { clampLimit, type PageCursor } from './conversations';

export type BookingRow = typeof bookingRequests.$inferSelect;
export type BookingStatus = BookingRow['status'];

export async function listBookings(
  db: Db,
  businessId: string,
  opts: { status?: BookingStatus; limit?: number; before?: PageCursor } = {},
): Promise<BookingRow[]> {
  const conditions: SQL[] = [eq(bookingRequests.businessId, businessId)];
  if (opts.status) conditions.push(eq(bookingRequests.status, opts.status));
  if (opts.before) {
    const at = new Date(opts.before.at);
    conditions.push(or(lt(bookingRequests.createdAt, at), and(eq(bookingRequests.createdAt, at), lt(bookingRequests.id, opts.before.id)))!);
  }
  return db
    .select()
    .from(bookingRequests)
    .where(and(...conditions))
    .orderBy(desc(bookingRequests.createdAt), desc(bookingRequests.id))
    .limit(clampLimit(opts.limit, 100, 500));
}

export async function getBookingForBusiness(db: Db, businessId: string, id: string): Promise<BookingRow | null> {
  const [row] = await db
    .select()
    .from(bookingRequests)
    .where(and(eq(bookingRequests.id, id), eq(bookingRequests.businessId, businessId)))
    .limit(1);
  return row ?? null;
}

/** UNSCOPED: visitor/service use only, or after a business-scoped parent lookup. */
export async function listBookingsForConversation(db: Db, conversationId: string): Promise<BookingRow[]> {
  return db
    .select()
    .from(bookingRequests)
    .where(eq(bookingRequests.conversationId, conversationId))
    .orderBy(desc(bookingRequests.createdAt), desc(bookingRequests.id));
}

/** Only a pending request can be decided; returns null when not found, foreign, or already decided. */
export async function decideBooking(
  db: Db,
  input: { businessId: string; bookingId: string; decision: 'confirmed' | 'declined'; note: string | null; userId: string; now: Date },
): Promise<BookingRow | null> {
  const [row] = await db
    .update(bookingRequests)
    .set({ status: input.decision, ownerNote: input.note, decidedAt: input.now, decidedBy: input.userId })
    .where(
      and(
        eq(bookingRequests.id, input.bookingId),
        eq(bookingRequests.businessId, input.businessId),
        eq(bookingRequests.status, 'pending'),
      ),
    )
    .returning();
  return row ?? null;
}
