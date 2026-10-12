import { DEFAULT_BOOKING_FIELDS } from '@reception/core';
import { describe, expect, it } from 'vitest';
import { contactFromFields } from './contact';

describe('contactFromFields', () => {
  it('splits email and phone contacts', () => {
    expect(contactFromFields(DEFAULT_BOOKING_FIELDS, { name: 'Alex', contact: 'alex@example.com' })).toEqual({
      name: 'Alex',
      email: 'alex@example.com',
      phone: null,
    });
    expect(contactFromFields(DEFAULT_BOOKING_FIELDS, { name: '小李', contact: '13800138000' })).toEqual({
      name: '小李',
      email: null,
      phone: '13800138000',
    });
  });

  it('returns nulls when fields are missing', () => {
    expect(contactFromFields(DEFAULT_BOOKING_FIELDS, {})).toEqual({ name: null, email: null, phone: null });
  });
});
