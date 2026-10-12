import type { HandoffReason, Locale } from '@reception/core';
import { and, eq, isNull } from 'drizzle-orm';
import type { Db } from '../db/client';
import { bookingRequests } from '../db/schema';
import { contactFromFields, type ContactFields } from '../domain/contact';
import type { MailMessage, Mailer } from '../mail/mailer';
import { ownerBookingEmail, ownerHandoffEmail, ownerOfflineContactEmail, visitorReceiptEmail, type FieldLine } from '../mail/templates';
import { writeAudit } from '../repos/audit';
import type { BookingRow } from '../repos/bookings';
import type { BusinessRow } from '../repos/business';
import type { ConversationRow } from '../repos/conversations';

export interface Notifier {
  bookingCreated(input: { business: BusinessRow; conversation: ConversationRow; booking: BookingRow }): Promise<void>;
  handoff(input: { business: BusinessRow; conversation: ConversationRow; reason: HandoffReason; excerpt: string }): Promise<void>;
  offlineContact(input: { business: BusinessRow; conversation: ConversationRow; contact: ContactFields; note: string }): Promise<void>;
}

export interface NotifierDeps {
  db: Db;
  mailer: Mailer | null;
  publicOrigin: string;
  now: () => Date;
}

export function adminConversationUrl(publicOrigin: string, conversationId: string): string {
  return `${publicOrigin}/admin/#/conversations/${conversationId}`;
}

export function createNotifier(deps: NotifierDeps): Notifier {
  async function deliver(business: BusinessRow, kind: string, message: MailMessage): Promise<void> {
    if (!deps.mailer) return;
    try {
      await deps.mailer.send(message);
      await writeAudit(deps.db, { businessId: business.id, actor: 'system', action: 'email_sent', target: kind, now: deps.now() });
    } catch (error) {
      await writeAudit(deps.db, {
        businessId: business.id,
        actor: 'system',
        action: 'email_failed',
        target: kind,
        data: { error: error instanceof Error ? error.message.slice(0, 200) : 'unknown error' },
        now: deps.now(),
      }).catch(() => undefined);
    }
  }

  /** Notifications are best-effort: they must never fail the caller's request. */
  async function safely(work: () => Promise<void>): Promise<void> {
    try {
      await work();
    } catch (error) {
      console.error('notification failed', error);
    }
  }

  function fieldLines(business: BusinessRow, fields: Record<string, string>, locale: Locale): FieldLine[] {
    return business.bookingFields.filter((d) => fields[d.key]).map((d) => ({ label: d.label[locale], value: fields[d.key]! }));
  }

  return {
    bookingCreated: ({ business, conversation, booking }) =>
      safely(async () => {
        const tasks: Promise<void>[] = [];
        const adminUrl = adminConversationUrl(deps.publicOrigin, conversation.id);
        if (business.notify.ownerEmail) {
          const content = ownerBookingEmail(
            { businessName: business.name, fields: fieldLines(business, booking.fields, business.locale), adminUrl },
            business.locale,
          );
          tasks.push(deliver(business, 'owner_booking', { to: business.notify.ownerEmail, ...content }));
        }
        const { email } = contactFromFields(business.bookingFields, booking.fields);
        if (email && business.notify.visitorReceipts && deps.mailer) {
          // Atomic claim: at most one receipt per booking even if this runs twice.
          const claimed = await deps.db
            .update(bookingRequests)
            .set({ receiptSentAt: deps.now() })
            .where(and(eq(bookingRequests.id, booking.id), isNull(bookingRequests.receiptSentAt)))
            .returning({ id: bookingRequests.id });
          if (claimed.length > 0) {
            const locale = conversation.locale ?? business.locale;
            const content = visitorReceiptEmail({ businessName: business.name, fields: fieldLines(business, booking.fields, locale) }, locale);
            tasks.push(deliver(business, 'visitor_receipt', { to: email, ...content }));
          }
        }
        await Promise.all(tasks);
      }),

    handoff: ({ business, conversation, reason, excerpt }) =>
      safely(async () => {
        if (!business.notify.ownerEmail) return;
        const content = ownerHandoffEmail(
          { businessName: business.name, reason, excerpt, adminUrl: adminConversationUrl(deps.publicOrigin, conversation.id) },
          business.locale,
        );
        await deliver(business, 'owner_handoff', { to: business.notify.ownerEmail, ...content });
      }),

    offlineContact: ({ business, conversation, contact, note }) =>
      safely(async () => {
        if (!business.notify.ownerEmail) return;
        const labels =
          business.locale === 'zh-CN' ? { name: '姓名', email: '邮箱', phone: '电话' } : { name: 'name', email: 'email', phone: 'phone' };
        const lines: FieldLine[] = (['name', 'email', 'phone'] as const)
          .filter((k) => contact[k])
          .map((k) => ({ label: labels[k], value: contact[k]! }));
        const content = ownerOfflineContactEmail(
          { businessName: business.name, contact: lines, note, adminUrl: adminConversationUrl(deps.publicOrigin, conversation.id) },
          business.locale,
        );
        await deliver(business, 'owner_offline_contact', { to: business.notify.ownerEmail, ...content });
      }),
  };
}
