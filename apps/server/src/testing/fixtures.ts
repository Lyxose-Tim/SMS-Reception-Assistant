import { demoKnowledge } from '@reception/core';
import { openDatabase, type Database, type Db } from '../db/client';
import { createBusiness, type BusinessRow, type CreateBusinessInput } from '../repos/business';

export function testDatabase(): Promise<Database> {
  return openDatabase(':memory:');
}

export function seedDemoBusiness(db: Db, overrides: Partial<CreateBusinessInput> = {}): Promise<BusinessRow> {
  const k = demoKnowledge();
  return createBusiness(db, {
    slug: 'harbor-home',
    name: k.name,
    profile: k.profile,
    faqs: k.faqs.map(({ question, answer, enabled }) => ({ question, answer, enabled })),
    widget: { allowedOrigins: ['https://harbor.example'] },
    notify: { ownerEmail: 'owner@harbor.example', visitorReceipts: true },
    ...overrides,
  });
}
