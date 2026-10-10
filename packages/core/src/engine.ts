import {
  askFor,
  contactValue,
  isComplete,
  isDraftExpired,
  newDraft,
  nextMissingField,
  setDraftFields,
} from './booking';
import { resolveLocale } from './locale';
import { buildModelMessages, buildSystemPrompt, MAX_OUTPUT_TOKENS } from './prompt';
import { respondWithRules } from './rules/respond';
import { cleanText } from './sanitize';
import { t } from './templates';
import type {
  BookingDraft,
  EngineContext,
  EngineMeta,
  EngineResult,
  Locale,
  ModelOutput,
  ReplyModel,
} from './types';

export const MODEL_TIMEOUT_MS = 15_000;
export const MAX_VISITOR_MESSAGES = 100;
export const MAX_REPLY_CHARS = 1200;

export interface EngineDeps {
  model?: ReplyModel | null;
  timeoutMs?: number;
}

class ModelTimeoutError extends Error {
  constructor() {
    super('model timed out');
    this.name = 'ModelTimeoutError';
  }
}

export async function handleVisitorMessage(
  ctx: EngineContext,
  rawText: string,
  deps: EngineDeps = {},
): Promise<EngineResult> {
  const text = cleanText(rawText);
  const { business: k, conversation: conv, now } = ctx;
  const locale = resolveLocale(text, conv.locale, ctx.localeHint);

  if (!text) {
    return { replies: [], locale, bookingDraft: conv.bookingDraft, meta: { engine: 'none' } };
  }
  if (conv.status !== 'bot') {
    return { replies: [], locale, bookingDraft: conv.bookingDraft, meta: { engine: 'none' } };
  }
  if (conv.visitorMessageCount >= MAX_VISITOR_MESSAGES) {
    const contact = k.profile.contactEmail || k.profile.contactPhone;
    const stale = conv.bookingDraft !== null && isDraftExpired(conv.bookingDraft, now);
    return {
      replies: [t(locale, 'message_limit', { business: k.name, contact })],
      locale,
      bookingDraft: stale ? null : conv.bookingDraft,
      handoff: { reason: 'message_limit' },
      meta: { engine: 'none' },
    };
  }

  let draft = conv.bookingDraft;
  const prefix: string[] = [];
  if (draft && isDraftExpired(draft, now)) {
    draft = null;
    prefix.push(t(locale, 'booking_expired'));
  }

  let fallbackReason: NonNullable<EngineMeta['fallbackReason']> = 'no_model';
  if (deps.model) {
    try {
      const result = await runModel(deps.model, ctx, text, locale, draft, deps.timeoutMs ?? MODEL_TIMEOUT_MS);
      if (result) return { ...result, replies: [...prefix, ...result.replies] };
      fallbackReason = 'empty_reply';
    } catch (error) {
      fallbackReason = error instanceof ModelTimeoutError ? 'timeout' : 'model_error';
    }
  }

  const out = respondWithRules(k, text, locale, draft, now);
  return {
    replies: [...prefix, ...out.replies],
    locale,
    bookingDraft: out.bookingDraft,
    ...(out.bookingRequest ? { bookingRequest: out.bookingRequest } : {}),
    ...(out.handoff ? { handoff: out.handoff } : {}),
    meta: { engine: 'rules', fallbackReason },
  };
}

async function runModel(
  model: ReplyModel,
  ctx: EngineContext,
  text: string,
  locale: Locale,
  draft: BookingDraft | null,
  timeoutMs: number,
): Promise<EngineResult | null> {
  const k = ctx.business;
  const { system, truncated } = buildSystemPrompt(k, locale);
  const messages = buildModelMessages(ctx.history, text, draft);

  const controller = new AbortController();
  let timer: ReturnType<typeof setTimeout> | undefined;
  const timeout = new Promise<never>((_resolve, reject) => {
    timer = setTimeout(() => {
      controller.abort();
      reject(new ModelTimeoutError());
    }, timeoutMs);
  });

  let output: ModelOutput;
  try {
    output = await Promise.race([
      model.generate({ system, messages, signal: controller.signal, maxOutputTokens: MAX_OUTPUT_TOKENS }),
      timeout,
    ]);
  } finally {
    clearTimeout(timer);
  }

  const services = k.profile.services;
  let next = draft;
  let submitted: Record<string, string> | null = null;
  let submitAttempted = false;
  let handoff = false;

  for (const action of output.actions) {
    if (action.type === 'update_booking_draft') {
      const updated = setDraftFields(next ?? newDraft(ctx.now), k.bookingFields, action.fields, services, ctx.now).draft;
      // A fully rejected update must not leave an empty draft behind.
      if (next || Object.keys(updated.fields).length > 0) next = updated;
    } else if (action.type === 'handoff_to_human') {
      handoff = true;
    } else if (action.type === 'submit_booking_request') {
      submitAttempted = true;
      if (next && isComplete(k.bookingFields, next)) {
        submitted = { ...next.fields };
        next = null;
      }
    }
  }

  // A tool-only turn that completes the draft submits it, exactly as an explicit submit_booking_request would.
  if (!submitted && !output.text.trim() && next && !sameFields(draft, next) && isComplete(k.bookingFields, next)) {
    submitted = { ...next.fields };
    next = null;
  }

  // Keep `awaiting` aligned with the draft so a later rules turn stores the answer in the right field.
  if (next) next = { ...next, awaiting: nextMissingField(k.bookingFields, next)?.key ?? null };

  const meta: EngineMeta = { engine: 'ai', knowledgeTruncated: truncated, actions: output.actions };

  if (submitted) {
    const replies = [t(locale, 'booking_submitted', { business: k.name, contact: contactValue(k.bookingFields, submitted) })];
    if (handoff) replies.push(t(locale, 'handoff'));
    return {
      replies,
      locale,
      bookingDraft: null,
      bookingRequest: { fields: submitted },
      ...(handoff ? { handoff: { reason: 'model_request' as const } } : {}),
      meta,
    };
  }
  if (handoff) {
    return { replies: [t(locale, 'handoff')], locale, bookingDraft: next, handoff: { reason: 'model_request' }, meta };
  }
  if (submitAttempted) {
    const base = next ?? newDraft(ctx.now);
    const missing = nextMissingField(k.bookingFields, base);
    if (missing) {
      return {
        replies: [askFor(missing, locale, services)],
        locale,
        bookingDraft: { ...base, awaiting: missing.key },
        meta,
      };
    }
  }

  const reply = output.text.trim();
  if (!reply) {
    // Tool-call-only turn: if it changed the draft, ask for the next field ourselves instead of discarding it.
    if (next && !sameFields(draft, next)) {
      const missing = nextMissingField(k.bookingFields, next);
      if (missing) return { replies: [askFor(missing, locale, services)], locale, bookingDraft: next, meta };
    }
    return null;
  }
  return { replies: [reply.slice(0, MAX_REPLY_CHARS)], locale, bookingDraft: next, meta };
}

function sameFields(a: BookingDraft | null, b: BookingDraft | null): boolean {
  const left = Object.entries(a?.fields ?? {}).sort(([x], [y]) => x.localeCompare(y));
  const right = Object.entries(b?.fields ?? {}).sort(([x], [y]) => x.localeCompare(y));
  return left.length === right.length && left.every(([k, v], i) => k === right[i]![0] && v === right[i]![1]);
}
