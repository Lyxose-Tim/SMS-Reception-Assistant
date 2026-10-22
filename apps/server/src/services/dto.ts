import type { BookingFieldDef } from '@reception/core';
import { contactFromFields } from '../domain/contact';
import type { BookingRow } from '../repos/bookings';
import type { FaqRow } from '../repos/business';
import type { ContactRow } from '../repos/conversations';

/** Admin-facing shapes: timestamps are epoch milliseconds and internal columns never appear. */
export interface ContactDto {
  id: string;
  name: string | null;
  email: string | null;
  phone: string | null;
}

export interface FaqDto {
  id: string;
  question: string;
  answer: string;
  enabled: boolean;
  sort: number;
  createdAt: number;
  updatedAt: number;
}

export interface BookingDto {
  id: string;
  conversationId: string;
  status: BookingRow['status'];
  fields: Record<string, string>;
  contact: { name: string | null; email: string | null; phone: string | null };
  ownerNote: string | null;
  createdAt: number;
  decidedAt: number | null;
}

export function toContactDto(row: ContactRow): ContactDto {
  return { id: row.id, name: row.name, email: row.email, phone: row.phone };
}

export function toFaqDto(row: FaqRow): FaqDto {
  return {
    id: row.id,
    question: row.question,
    answer: row.answer,
    enabled: row.enabled,
    sort: row.sort,
    createdAt: row.createdAt.getTime(),
    updatedAt: row.updatedAt.getTime(),
  };
}

export function toBookingDto(row: BookingRow, defs: BookingFieldDef[]): BookingDto {
  return {
    id: row.id,
    conversationId: row.conversationId,
    status: row.status,
    fields: row.fields,
    contact: contactFromFields(defs, row.fields),
    ownerNote: row.ownerNote,
    createdAt: row.createdAt.getTime(),
    decidedAt: row.decidedAt ? row.decidedAt.getTime() : null,
  };
}
