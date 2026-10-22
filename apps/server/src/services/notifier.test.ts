import { and, eq } from 'drizzle-orm';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Database } from '../db/client';
import { auditLog, bookingRequests } from '../db/schema';
import { sha256 } from '../lib/crypto';
import { createMemoryMailer, type Mailer } from '../mail/mailer';
import type { BookingRow } from '../repos/bookings';
import { updateBusiness, type BusinessRow } from '../repos/business';
import { insertConversation, type ConversationRow } from '../repos/conversations';
import { seedDemoBusiness, testDatabase } from '../testing/fixtures';
import { createNotifier } from './notifier';

let database: Database;
let business: BusinessRow;
let conversation: ConversationRow;
const now = new Date('2026-10-11T10:00:00Z');

beforeEach(async () => {
  database = await testDatabase();
  business = await seedDemoBusiness(database.db);
  conversation = await insertConversation(database.db, { businessId: business.id, visitorTokenHash: sha256('t'), locale: 'en', now });
});
afterEach(() => database.close());

async function makeBooking(fields: Record<string, string>): Promise<BookingRow> {
  const [row] = await database.db.insert(bookingRequests).values({ businessId: business.id, conversationId: conversation.id, fields }).returning();
  return row!;
}

const fields = { service: 'Deep cleaning', preferred_time: 'Sat 10am', name: 'Alex', contact: 'alex@example.com' };

describe('notifier', () => {
  it('emails the owner and sends one visitor receipt per booking', async () => {
    const mailer = createMemoryMailer();
    const notifier = createNotifier({ db: database.db, mailer, publicOrigin: 'https://chat.example', now: () => now });
    const booking = await makeBooking(fields);
    await notifier.bookingCreated({ business, conversation, booking });
    await notifier.bookingCreated({ business, conversation, booking });
    const owner = mailer.sent.filter((m) => m.to === 'owner@harbor.example');
    const visitor = mailer.sent.filter((m) => m.to === 'alex@example.com');
    expect(owner).toHaveLength(2);
    expect(owner[0]!.text).toContain(`https://chat.example/admin/#/conversations/${conversation.id}`);
    expect(visitor).toHaveLength(1);
    const [stored] = await database.db.select().from(bookingRequests).where(eq(bookingRequests.id, booking.id));
    expect(stored!.receiptSentAt?.getTime()).toBe(now.getTime());
  });

  it('skips receipts when disabled or when the contact is a phone number', async () => {
    const mailer = createMemoryMailer();
    const notifier = createNotifier({ db: database.db, mailer, publicOrigin: 'https://chat.example', now: () => now });
    await notifier.bookingCreated({ business, conversation, booking: await makeBooking({ ...fields, contact: '13800138000' }) });
    const updated = (await updateBusiness(database.db, business.id, { notify: { ownerEmail: null, visitorReceipts: false } }, now))!;
    await notifier.bookingCreated({ business: updated, conversation, booking: await makeBooking(fields) });
    expect(mailer.sent.map((m) => m.to)).toEqual(['owner@harbor.example']);
  });

  it.each(['a@x.com,b@y.com', 'a@x.com\r\nBcc: v@y.com', 'Name <a@x.com>', 'a@x.com;b@y.com', 'a b@x.com'])(
    'does not send a receipt to a non-plain address (%j)',
    async (contact) => {
      const mailer = createMemoryMailer();
      const notifier = createNotifier({ db: database.db, mailer, publicOrigin: 'https://chat.example', now: () => now });
      const booking = await makeBooking({ ...fields, contact });
      await notifier.bookingCreated({ business, conversation, booking });
      expect(mailer.sent.map((m) => m.to)).toEqual(['owner@harbor.example']);
      const [stored] = await database.db.select().from(bookingRequests).where(eq(bookingRequests.id, booking.id));
      expect(stored!.receiptSentAt).toBeNull();
    },
  );

  it('does not retry a failed visitor receipt but audits the failure', async () => {
    const failing: Mailer = {
      async send(message) {
        if (message.to === 'alex@example.com') throw new Error('SMTP down');
      },
    };
    const notifier = createNotifier({ db: database.db, mailer: failing, publicOrigin: 'https://chat.example', now: () => now });
    const booking = await makeBooking(fields);
    await notifier.bookingCreated({ business, conversation, booking });
    const [stored] = await database.db.select().from(bookingRequests).where(eq(bookingRequests.id, booking.id));
    expect(stored!.receiptSentAt?.getTime()).toBe(now.getTime());
    const rows = await database.db.select().from(auditLog).where(and(eq(auditLog.action, 'email_failed'), eq(auditLog.target, 'visitor_receipt')));
    expect(rows).toHaveLength(1);
  });

  it('never throws and records failures in the audit log', async () => {
    const failing: Mailer = {
      async send() {
        throw new Error('SMTP down');
      },
    };
    const notifier = createNotifier({ db: database.db, mailer: failing, publicOrigin: 'https://chat.example', now: () => now });
    await expect(notifier.handoff({ business, conversation, reason: 'unknown_question', excerpt: 'boats?' })).resolves.toBeUndefined();
    const rows = await database.db.select().from(auditLog).where(and(eq(auditLog.action, 'email_failed'), eq(auditLog.target, 'owner_handoff')));
    expect(rows).toHaveLength(1);
  });

  it('does nothing without a mailer', async () => {
    const notifier = createNotifier({ db: database.db, mailer: null, publicOrigin: 'https://chat.example', now: () => now });
    await expect(
      notifier.offlineContact({ business, conversation, contact: { name: 'A', email: 'a@example.com', phone: null }, note: '' }),
    ).resolves.toBeUndefined();
  });
});

describe('notifier receipt guard', () => {
  it('skips the receipt without claiming it when the guard denies, and audits receipt_skipped', async () => {
    const mailer = createMemoryMailer();
    const allow = vi.fn(() => false);
    const notifier = createNotifier({ db: database.db, mailer, publicOrigin: 'https://chat.example', now: () => now, receiptGuard: { allow } });
    const booking = await makeBooking(fields);
    await notifier.bookingCreated({ business, conversation, booking, ip: '198.51.100.4' });
    expect(allow).toHaveBeenCalledWith({ ip: '198.51.100.4', conversationId: conversation.id });
    expect(mailer.sent.map((m) => m.to)).toEqual(['owner@harbor.example']);
    const [stored] = await database.db.select().from(bookingRequests).where(eq(bookingRequests.id, booking.id));
    expect(stored!.receiptSentAt).toBeNull();
    const rows = await database.db.select().from(auditLog).where(eq(auditLog.action, 'receipt_skipped'));
    expect(rows).toHaveLength(1);
    expect(rows[0]).toMatchObject({ target: booking.id, data: { reason: 'rate_limited' } });
  });

  it('passes a null ip to the guard when none is known and sends when it allows', async () => {
    const mailer = createMemoryMailer();
    const allow = vi.fn(() => true);
    const notifier = createNotifier({ db: database.db, mailer, publicOrigin: 'https://chat.example', now: () => now, receiptGuard: { allow } });
    await notifier.bookingCreated({ business, conversation, booking: await makeBooking(fields) });
    expect(allow).toHaveBeenCalledWith({ ip: null, conversationId: conversation.id });
    expect(mailer.sent.map((m) => m.to).sort()).toEqual(['alex@example.com', 'owner@harbor.example']);
  });
});
