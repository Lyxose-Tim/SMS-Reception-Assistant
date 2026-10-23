import { contactValue, type BookingFieldDef } from '@reception/core';

export interface ContactFields {
  name: string | null;
  email: string | null;
  phone: string | null;
}

export function contactFromFields(defs: BookingFieldDef[], fields: Record<string, string>): ContactFields {
  const contact = contactValue(defs, fields).trim();
  const name = fields.name?.trim() || null;
  if (!contact) return { name, email: null, phone: null };
  return contact.includes('@') ? { name, email: contact, phone: null } : { name, email: null, phone: contact };
}
