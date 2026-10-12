import type { Db } from '../db/client';
import { auditLog } from '../db/schema';

export interface AuditEntry {
  businessId: string | null;
  /** 'visitor', 'system' or `user:<id>` */
  actor: string;
  action: string;
  target?: string | null;
  data?: Record<string, unknown> | null;
  now?: Date;
}

export function auditInsert(db: Db, entry: AuditEntry) {
  return db.insert(auditLog).values({
    businessId: entry.businessId,
    actor: entry.actor,
    action: entry.action,
    target: entry.target ?? null,
    data: entry.data ?? null,
    ...(entry.now ? { createdAt: entry.now } : {}),
  });
}

export async function writeAudit(db: Db, entry: AuditEntry): Promise<void> {
  await auditInsert(db, entry);
}
