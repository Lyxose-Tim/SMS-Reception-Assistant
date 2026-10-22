import { pathToFileURL } from 'node:url';
import { parseArgs } from 'node:util';
import { demoKnowledge } from '@reception/core';
import { openDatabase, type Db } from './db/client';
import { ValidationError } from './domain/validation';
import { loadEnv, type Env } from './env';
import { createBusiness, getBusinessBySlug } from './repos/business';
import { createUser, findUserByEmail } from './repos/users';

const USAGE = `Usage:
  npm run cli -w @reception/server -- create-owner --slug <slug> --name <business name> --email <email> --password <password>
  npm run cli -w @reception/server -- seed-demo --email <email> --password <password>`;

const DEMO_SLUG = 'harbor-home';

function required(values: Record<string, string | undefined>, keys: string[]): Record<string, string> {
  const missing = keys.filter((k) => !values[k]);
  if (missing.length) throw new ValidationError(missing.map((k) => ({ path: k, message: 'is required' })));
  return values as Record<string, string>;
}

async function createOwner(db: Db, args: string[], out: (line: string) => void): Promise<number> {
  const { values } = parseArgs({
    args,
    options: { slug: { type: 'string' }, name: { type: 'string' }, email: { type: 'string' }, password: { type: 'string' } },
    strict: true,
  });
  const v = required(values, ['slug', 'email', 'password']);
  if (await findUserByEmail(db, v.email!)) {
    out(`User ${v.email} already exists.`);
    return 1;
  }
  let business = await getBusinessBySlug(db, v.slug!);
  if (!business) {
    if (!values.name) throw new ValidationError([{ path: 'name', message: 'is required for a new business' }]);
    business = await createBusiness(db, { slug: v.slug!, name: values.name });
    out(`Created business "${business.name}" (${business.slug}).`);
  }
  const user = await createUser(db, { businessId: business.id, email: v.email!, password: v.password! });
  out(`Created owner ${user.email} for ${business.slug}.`);
  return 0;
}

async function seedDemo(db: Db, env: Env, args: string[], out: (line: string) => void): Promise<number> {
  const { values } = parseArgs({ args, options: { email: { type: 'string' }, password: { type: 'string' } }, strict: true });
  const v = required(values, ['email', 'password']);
  let business = await getBusinessBySlug(db, DEMO_SLUG);
  if (business) {
    out('Demo business already exists.');
  } else {
    const k = demoKnowledge();
    business = await createBusiness(db, {
      slug: DEMO_SLUG,
      name: k.name,
      profile: k.profile,
      faqs: k.faqs.map(({ question, answer, enabled }) => ({ question, answer, enabled })),
      widget: { allowedOrigins: [env.PUBLIC_ORIGIN] },
    });
    out(`Created demo business "${business.name}". It is fictional — do not use real customer data.`);
  }
  if (await findUserByEmail(db, v.email!)) {
    out(`User ${v.email} already exists.`);
  } else {
    await createUser(db, { businessId: business.id, email: v.email!, password: v.password! });
    out(`Created demo owner ${v.email}.`);
  }
  return 0;
}

export async function runCli(argv: string[], env: Env, out: (line: string) => void = console.log): Promise<number> {
  const [command, ...rest] = argv;
  if (command !== 'create-owner' && command !== 'seed-demo') {
    out(USAGE);
    return 1;
  }
  const database = await openDatabase(env.DATABASE_URL);
  try {
    return command === 'create-owner' ? await createOwner(database.db, rest, out) : await seedDemo(database.db, env, rest, out);
  } catch (error) {
    if (error instanceof ValidationError) {
      out(`Invalid input: ${error.issues.map((i) => `${i.path} ${i.message}`).join('; ')}`);
      out(USAGE);
      return 1;
    }
    if (error instanceof TypeError && 'code' in error && String(error.code).startsWith('ERR_PARSE_ARGS')) {
      out(error.message);
      out(USAGE);
      return 1;
    }
    throw error;
  } finally {
    database.close();
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  process.exitCode = await runCli(process.argv.slice(2), loadEnv());
}
