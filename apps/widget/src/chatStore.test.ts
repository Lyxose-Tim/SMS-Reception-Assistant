import { describe, expect, it, vi } from 'vitest';
import { ApiError, type MessagesResult } from './api';
import { abortableSleep, ChatStore, HEALTHY_MS, PAGE_SIZE } from './chatStore';
import type { SessionStore } from './storage';
import { controllableStream, fakeApi, memorySessions, msg } from './testing/fakes';

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

interface SetupOptions {
  api?: ReturnType<typeof fakeApi>;
  sessions?: SessionStore;
  language?: string;
  sleep?: (ms: number, signal: AbortSignal) => Promise<void>;
  now?: () => number;
}

function setup(opts: SetupOptions = {}) {
  const api = opts.api ?? fakeApi();
  const sessions = opts.sessions ?? memorySessions();
  const { stream, calls } = controllableStream();
  const sleep = vi.fn<(ms: number, signal: AbortSignal) => Promise<void>>(opts.sleep ?? (async () => {}));
  const store = new ChatStore({ api, sessions, stream, localeOverride: null, browserLanguage: opts.language ?? 'en-US', sleep, now: opts.now });
  return { api, sessions, store, streams: calls, sleep };
}

async function ready(opts?: SetupOptions) {
  const ctx = setup(opts);
  await ctx.store.init();
  await ctx.store.setOpen(true);
  return ctx;
}

const OLD = { conversationId: 'old', visitorToken: 'tok' };
const C1 = { conversationId: 'c1', visitorToken: 't1' };

function storedSession(): SessionStore {
  const sessions = memorySessions();
  sessions.save(OLD);
  return sessions;
}

describe('ChatStore: config', () => {
  it('loads the config and picks the visitor locale', async () => {
    const { store } = setup({ language: 'zh-TW' });
    await store.init();
    expect(store.getState()).toMatchObject({ phase: 'ready', locale: 'zh-CN' });
    expect(store.getState().config?.business.name).toBe('Harbor & Home Cleaning');
  });

  it('becomes unavailable when the config cannot be loaded', async () => {
    const { store } = setup({ api: fakeApi({ getConfig: vi.fn().mockRejectedValue(new ApiError(403, 'origin_not_allowed')) }) });
    await store.init();
    expect(store.getState().phase).toBe('unavailable');
  });

  it('notifies subscribers', async () => {
    const { store } = setup();
    const seen: string[] = [];
    const off = store.subscribe((s) => seen.push(s.phase));
    await store.init();
    off();
    expect(seen).toContain('ready');
  });

  it('resumes a stored session when the panel was opened before init finished', async () => {
    const { api, store, streams } = setup({ sessions: storedSession() });
    const opening = store.setOpen(true);
    await store.init();
    await opening;
    expect(api.listMessages).toHaveBeenCalledWith(OLD, 0);
    expect(streams).toHaveLength(1);
    expect(streams[0]!.options.token).toBe('tok');
  });
});

describe('ChatStore: conversation lifecycle', () => {
  it('creates the conversation only on the first send', async () => {
    const { api, sessions, store, streams } = await ready();
    expect(api.createConversation).not.toHaveBeenCalled();
    expect(streams).toHaveLength(0);
    await store.send('  hi  ');
    expect(api.createConversation).toHaveBeenCalledTimes(1);
    expect(api.sendMessage).toHaveBeenCalledWith(C1, 'hi', 'en');
    expect(sessions.load()).toEqual(C1);
    expect(store.getState().messages.map((m) => m.role)).toEqual(['visitor', 'bot']);
    expect(streams).toHaveLength(1);
    expect(streams[0]!.options).toMatchObject({ token: 't1', url: 'https://chat.example/api/widget/c/c1/events' });
  });

  it('de-duplicates and sorts messages from responses and the stream', async () => {
    const api = fakeApi({ sendMessage: vi.fn().mockResolvedValue({ messages: [msg('bot', 'b', 3), msg('visitor', 'v', 2)], status: 'bot' }) });
    const { store, streams } = await ready({ api });
    await store.send('v');
    streams[0]!.options.onEvent({ event: 'message', data: JSON.stringify(msg('bot', 'b', 3)), id: '3' });
    streams[0]!.options.onEvent({ event: 'message', data: JSON.stringify(msg('agent', 'Sam here', 4)), id: '4' });
    streams[0]!.options.onEvent({ event: 'message', data: 'not json', id: null });
    expect(store.getState().messages.map((m) => m.id)).toEqual([2, 3, 4]);
  });

  it('applies status events and asks for a person with the localized phrase', async () => {
    const { api, store, streams } = await ready();
    await store.talkToPerson();
    expect(api.sendMessage.mock.calls[0]![1]).toBe('I want to talk to a real person');
    streams[0]!.options.onEvent({ event: 'status', data: '{"status":"human"}', id: null });
    expect(store.getState().status).toBe('human');
    streams[0]!.options.onEvent({ event: 'status', data: '{"status":"weird"}', id: null });
    expect(store.getState().status).toBe('human');
  });

  it('restores a stored session and catches up across pages when opened', async () => {
    const page1 = Array.from({ length: PAGE_SIZE }, (_, i) => msg('bot', `m${i + 1}`, i + 1));
    const api = fakeApi({
      listMessages: vi
        .fn()
        .mockResolvedValueOnce({ messages: page1, status: 'bot' })
        .mockResolvedValueOnce({ messages: [msg('agent', 'latest', 201)], status: 'human' }),
    });
    const { store, streams } = await ready({ api, sessions: storedSession() });
    expect(api.listMessages.mock.calls.map((c) => c[1])).toEqual([0, 200]);
    expect(store.getState().messages).toHaveLength(201);
    expect(store.getState().status).toBe('human');
    expect(streams[0]!.options).toMatchObject({ token: 'tok', lastEventId: 201 });
  });

  it('gap-fills after every ready event from the last synced id', async () => {
    const { api, store, streams } = await ready();
    await store.send('hi');
    streams[0]!.options.onEvent({ event: 'ready', data: '{}', id: null });
    await flush();
    expect(store.getState().connection).toBe('live');
    // The send response does not advance the sync cursor, so the first gap-fill starts from 0.
    expect(api.listMessages).toHaveBeenLastCalledWith(C1, 0);
    const lastId = store.getState().messages.at(-1)!.id;
    streams[0]!.options.onEvent({ event: 'message', data: JSON.stringify(msg('bot', 'Echo: hi', lastId)), id: String(lastId) });
    streams[0]!.options.onEvent({ event: 'ready', data: '{}', id: null });
    await flush();
    expect(api.listMessages).toHaveBeenLastCalledWith(C1, lastId);
  });

  it('never moves the sync cursor past messages it has not seen', async () => {
    const history = [1, 2, 3, 4].map((id) => msg(id % 2 ? 'visitor' : 'bot', `m${id}`, id));
    const api = fakeApi({
      listMessages: vi
        .fn()
        .mockResolvedValueOnce({ messages: history, status: 'bot' })
        .mockResolvedValueOnce({ messages: [msg('agent', 'missed while offline', 5)], status: 'bot' }),
      sendMessage: vi.fn().mockResolvedValue({ messages: [msg('visitor', 'hi', 6), msg('bot', 'hello', 7)], status: 'bot' }),
    });
    const backoff = deferred<void>();
    const { store, streams } = await ready({ api, sessions: storedSession(), sleep: () => backoff.promise });
    expect(streams[0]!.options.lastEventId).toBe(4);
    streams[0]!.fail(new ApiError(0, 'network'));
    await flush();
    expect(store.getState().connection).toBe('retrying');
    await store.send('hi');
    backoff.resolve();
    await flush();
    expect(streams).toHaveLength(2);
    expect(streams[1]!.options.lastEventId).toBe(4);
    streams[1]!.options.onEvent({ event: 'ready', data: '{}', id: null });
    await flush();
    expect(api.listMessages).toHaveBeenLastCalledWith(OLD, 4);
    expect(store.getState().messages.map((m) => m.id)).toEqual([1, 2, 3, 4, 5, 6, 7]);
  });

  it('rolls the sync cursor back when a catch-up page fails so the next ready refetches the gap', async () => {
    const page1 = Array.from({ length: PAGE_SIZE }, (_, i) => msg('bot', `m${i + 1}`, i + 1));
    const page2 = deferred<MessagesResult>();
    const api = fakeApi({
      listMessages: vi
        .fn()
        .mockResolvedValueOnce({ messages: [], status: 'bot' })
        .mockResolvedValueOnce({ messages: page1, status: 'bot' })
        .mockReturnValueOnce(page2.promise),
    });
    const { store, streams } = await ready({ api, sessions: storedSession() });
    streams[0]!.options.onEvent({ event: 'ready', data: '{}', id: null });
    await flush();
    expect(api.listMessages.mock.calls.map((c) => c[1])).toEqual([0, 0, 200]);
    streams[0]!.options.onEvent({ event: 'message', data: JSON.stringify(msg('agent', 'live', 400)), id: '400' });
    page2.reject(new ApiError(0, 'network'));
    await flush();
    streams[0]!.options.onEvent({ event: 'ready', data: '{}', id: null });
    await flush();
    expect(api.listMessages).toHaveBeenLastCalledWith(OLD, 200);
  });

  it('starts a new conversation once when the stored one is gone', async () => {
    const sessions = memorySessions();
    sessions.save({ conversationId: 'gone', visitorToken: 'x' });
    const api = fakeApi({
      listMessages: vi.fn().mockRejectedValue(new ApiError(404, 'not_found')),
      sendMessage: vi
        .fn()
        .mockRejectedValueOnce(new ApiError(404, 'not_found'))
        .mockResolvedValueOnce({ messages: [msg('visitor', 'hi', 1), msg('bot', 'hello', 2)], status: 'bot' }),
    });
    const { store } = setup({ api, sessions });
    await store.init();
    await store.send('hi');
    expect(api.createConversation).toHaveBeenCalledTimes(1);
    expect(sessions.load()).toEqual(C1);
    expect(store.getState().messages).toHaveLength(2);
  });

  it('creates one conversation when a send and a contact request race', async () => {
    const { api, store } = await ready();
    await Promise.all([store.send('a'), store.submitContact({ email: 'a@example.com' })]);
    expect(api.createConversation).toHaveBeenCalledTimes(1);
    expect(api.sendMessage.mock.calls[0]![0]).toEqual(C1);
    expect(api.sendContact).toHaveBeenCalledWith(C1, { email: 'a@example.com' });
  });

  it('replaces a gone session once when concurrent requests both find it gone', async () => {
    const api = fakeApi({
      sendMessage: vi
        .fn()
        .mockRejectedValueOnce(new ApiError(404, 'not_found'))
        .mockResolvedValueOnce({ messages: [msg('visitor', 'a', 1)], status: 'bot' }),
      sendContact: vi.fn().mockRejectedValueOnce(new ApiError(404, 'not_found')).mockResolvedValueOnce(undefined),
    });
    const { store } = await ready({ api, sessions: storedSession() });
    await Promise.all([store.send('a'), store.submitContact({ email: 'a@example.com' })]);
    expect(api.createConversation).toHaveBeenCalledTimes(1);
    expect(api.sendMessage.mock.calls[1]![0]).toEqual(C1);
    expect(api.sendContact).toHaveBeenNthCalledWith(2, C1, { email: 'a@example.com' });
    expect(store.getState().messages.map((m) => m.id)).toEqual([1]);
  });

  it('drops a late send response when the conversation was reset meanwhile', async () => {
    const reply = deferred<MessagesResult>();
    const { api, store, streams } = await ready();
    await store.send('hi');
    api.sendMessage.mockReturnValueOnce(reply.promise);
    const sending = store.send('again');
    await flush();
    streams[0]!.fail(new ApiError(401, 'unauthorized'));
    await flush();
    reply.resolve({ messages: [msg('visitor', 'again', 900), msg('bot', 'late', 901)], status: 'human' });
    await sending;
    expect(store.getState()).toMatchObject({ messages: [], status: 'bot', sending: false });
  });

  it('resets the session when the stream says the conversation is gone', async () => {
    const { sessions, store, streams } = await ready();
    await store.send('hi');
    streams[0]!.fail(new ApiError(401, 'unauthorized'));
    await flush();
    expect(sessions.load()).toBeNull();
    expect(store.getState().messages).toEqual([]);
    expect(store.getState().connection).toBe('idle');
  });

  it('resets on a 404 catch-up when opened with a stored session, and the next send creates a new conversation', async () => {
    const api = fakeApi({ listMessages: vi.fn().mockRejectedValueOnce(new ApiError(404, 'not_found')) });
    const { sessions, store, streams } = await ready({ api, sessions: storedSession() });
    expect(api.listMessages).toHaveBeenCalledWith(OLD, 0);
    expect(sessions.load()).toBeNull();
    expect(streams).toHaveLength(0);
    await store.send('hi');
    expect(api.createConversation).toHaveBeenCalledTimes(1);
    expect(api.sendMessage.mock.calls[0]![0]).toEqual(C1);
    expect(sessions.load()).toEqual(C1);
  });

  it('resets on a 404 from the stream, and the next send creates a new conversation', async () => {
    const { api, sessions, store, streams } = await ready({ sessions: storedSession() });
    expect(streams).toHaveLength(1);
    streams[0]!.fail(new ApiError(404, 'not_found'));
    await flush();
    expect(sessions.load()).toBeNull();
    expect(store.getState().connection).toBe('idle');
    await store.send('hi');
    expect(api.createConversation).toHaveBeenCalledTimes(1);
    expect(api.sendMessage.mock.calls[0]![0]).toEqual(C1);
    expect(sessions.load()).toEqual(C1);
  });

  it('reconnects with backoff and resets the backoff only after a healthy connection', async () => {
    let clock = 0;
    const { store, streams, sleep } = await ready({ now: () => clock });
    await store.send('hi');
    const lastId = store.getState().messages.at(-1)!.id;
    streams[0]!.options.onEvent({ event: 'message', data: JSON.stringify(msg('bot', 'Echo: hi', lastId)), id: String(lastId) });
    streams[0]!.fail(new ApiError(0, 'network'));
    await flush();
    expect(sleep.mock.calls.map((c) => c[0])).toEqual([1000]);
    expect(streams).toHaveLength(2);
    expect(streams[1]!.options.lastEventId).toBe(lastId);
    streams[1]!.end();
    await flush();
    expect(sleep.mock.calls.map((c) => c[0])).toEqual([1000, 2000]);
    streams[2]!.options.onEvent({ event: 'ready', data: '{}', id: null });
    clock += HEALTHY_MS;
    streams[2]!.end();
    await flush();
    expect(sleep.mock.calls.map((c) => c[0])).toEqual([1000, 2000, 1000]);
  });

  it('keeps backing off when connections drop right after ready', async () => {
    const { store, streams, sleep } = await ready({ now: () => 0 });
    await store.send('hi');
    for (let i = 0; i < 4; i++) {
      streams[i]!.options.onEvent({ event: 'ready', data: '{}', id: null });
      streams[i]!.end();
      await flush();
    }
    expect(sleep.mock.calls.map((c) => c[0])).toEqual([1000, 2000, 5000, 10000]);
  });

  it('ignores a stale catch-up status that arrives after a live status event', async () => {
    const page = deferred<MessagesResult>();
    const { api, store, streams } = await ready();
    await store.send('hi');
    api.listMessages.mockReturnValueOnce(page.promise);
    streams[0]!.options.onEvent({ event: 'ready', data: '{}', id: null });
    streams[0]!.options.onEvent({ event: 'status', data: '{"status":"human"}', id: null });
    page.resolve({ messages: [], status: 'bot' });
    await flush();
    expect(store.getState().status).toBe('human');
  });

  it('ignores a stale send status that arrives after a live status event', async () => {
    const reply = deferred<MessagesResult>();
    const { api, store, streams } = await ready();
    await store.send('hi');
    api.sendMessage.mockReturnValueOnce(reply.promise);
    const sending = store.send('again');
    streams[0]!.options.onEvent({ event: 'status', data: '{"status":"human"}', id: null });
    reply.resolve({ messages: [msg('visitor', 'again')], status: 'bot' });
    await sending;
    expect(store.getState().status).toBe('human');
  });

  it('ignores events from an aborted stream', async () => {
    const { store, streams } = await ready();
    await store.send('hi');
    const before = store.getState().messages;
    await store.setOpen(false);
    streams[0]!.options.onEvent({ event: 'message', data: JSON.stringify(msg('agent', 'late', 99_999)), id: '99999' });
    streams[0]!.options.onEvent({ event: 'status', data: '{"status":"human"}', id: null });
    expect(store.getState().messages).toBe(before);
    expect(store.getState().status).toBe('bot');
  });

  it('aborts the stream when the panel closes', async () => {
    const { store, streams } = await ready();
    await store.send('hi');
    await store.setOpen(false);
    expect(streams[0]!.options.signal.aborted).toBe(true);
    expect(store.getState().connection).toBe('idle');
  });

  it('sends no request when destroyed while the conversation is being created', async () => {
    const created = deferred<typeof C1>();
    const api = fakeApi({ createConversation: vi.fn().mockReturnValue(created.promise) });
    const { store, streams } = await ready({ api });
    const sending = store.send('hi');
    await flush();
    store.destroy();
    created.resolve(C1);
    await sending;
    expect(api.sendMessage).not.toHaveBeenCalled();
    expect(streams).toHaveLength(0);
  });

  it('creates a conversation on a later send after createConversation threw synchronously', async () => {
    const api = fakeApi();
    api.createConversation.mockImplementationOnce(() => {
      throw new Error('boom');
    });
    const { store } = await ready({ api });
    await store.send('a');
    expect(store.getState().error).toBe('errorGeneric');
    await store.send('b');
    expect(api.createConversation).toHaveBeenCalledTimes(2);
    expect(api.sendMessage).toHaveBeenCalledWith(C1, 'b', 'en');
    expect(store.getState()).toMatchObject({ error: null, sending: false });
  });

  it('stays inert after destroy, even when a request finishes later', async () => {
    const reply = deferred<MessagesResult>();
    const api = fakeApi({ sendMessage: vi.fn().mockReturnValue(reply.promise) });
    const { store, streams } = await ready({ api });
    const early = vi.fn();
    store.subscribe(early);
    const sending = store.send('hi');
    await flush();
    early.mockClear();
    store.destroy();
    store.destroy();
    const late = vi.fn();
    store.subscribe(late);
    reply.resolve({ messages: [msg('visitor', 'hi', 1), msg('bot', 'hello', 2)], status: 'human' });
    await sending;
    expect(streams).toHaveLength(0);
    expect(early).not.toHaveBeenCalled();
    expect(late).not.toHaveBeenCalled();
  });
});

describe('ChatStore: validation and errors', () => {
  it('ignores empty text and rejects over-long text without a request', async () => {
    const { api, store } = await ready();
    await store.send('   ');
    await store.send('x'.repeat(1001));
    expect(api.sendMessage).not.toHaveBeenCalled();
    expect(store.getState().error).toBe('errorTooLong');
  });

  it('maps rate limits and other failures to messages and clears the sending flag', async () => {
    const api = fakeApi({
      sendMessage: vi.fn().mockRejectedValueOnce(new ApiError(429, 'rate_limited')).mockRejectedValueOnce(new ApiError(0, 'network')),
    });
    const { store } = await ready({ api });
    await store.send('a');
    expect(store.getState()).toMatchObject({ error: 'errorRateLimited', sending: false });
    await store.send('b');
    expect(store.getState()).toMatchObject({ error: 'errorGeneric', sending: false });
  });

  it('ignores a send failure for a conversation that was reset meanwhile', async () => {
    const reply = deferred<MessagesResult>();
    const { api, store, streams } = await ready();
    await store.send('hi');
    api.sendMessage.mockReturnValueOnce(reply.promise);
    const sending = store.send('again');
    await flush();
    streams[0]!.fail(new ApiError(401, 'unauthorized'));
    await flush();
    reply.reject(new ApiError(0, 'network'));
    await sending;
    expect(store.getState()).toMatchObject({ error: null, sending: false, messages: [] });
  });

  it('starts the stream after a failed send so a stored turn still shows up', async () => {
    const api = fakeApi({ sendMessage: vi.fn().mockRejectedValue(new ApiError(0, 'network')) });
    const { store, streams } = await ready({ api });
    await store.send('a');
    expect(store.getState().error).toBe('errorGeneric');
    expect(streams).toHaveLength(1);
    expect(streams[0]!.options.token).toBe('t1');
  });
});

describe('ChatStore: contact form', () => {
  it('requires an email or phone number', async () => {
    const { api, store } = await ready();
    store.openContact();
    await store.submitContact({ name: 'Alex', email: '  ' });
    expect(api.sendContact).not.toHaveBeenCalled();
    expect(store.getState()).toMatchObject({ contact: 'open', error: 'errorContact' });
  });

  it('creates a conversation if needed, sends trimmed details and shows thanks', async () => {
    const { api, store } = await ready();
    store.openContact();
    await store.submitContact({ name: ' Alex ', email: ' a@example.com ', phone: '', note: '' });
    expect(api.createConversation).toHaveBeenCalledTimes(1);
    expect(api.sendContact).toHaveBeenCalledWith(C1, { name: 'Alex', email: 'a@example.com' });
    expect(store.getState().contact).toBe('sent');
  });

  it('ignores a second submit while the first is in flight', async () => {
    const { api, store } = await ready();
    store.openContact();
    await Promise.all([store.submitContact({ email: 'a@example.com' }), store.submitContact({ email: 'a@example.com' })]);
    expect(api.sendContact).toHaveBeenCalledTimes(1);
    expect(store.getState().contact).toBe('sent');
  });

  it('does not show thanks for a contact request to a conversation that was reset meanwhile', async () => {
    const saved = deferred<void>();
    const { api, store, streams } = await ready();
    await store.send('hi');
    api.sendContact.mockReturnValueOnce(saved.promise);
    store.openContact();
    const submitting = store.submitContact({ email: 'a@example.com' });
    await flush();
    streams[0]!.fail(new ApiError(404, 'not_found'));
    await flush();
    saved.resolve();
    await submitting;
    expect(store.getState()).toMatchObject({ contact: 'hidden', messages: [] });
  });

  it('ignores a contact failure for a conversation that was reset meanwhile', async () => {
    const saved = deferred<void>();
    const { api, store, streams } = await ready();
    await store.send('hi');
    api.sendContact.mockReturnValueOnce(saved.promise);
    store.openContact();
    const submitting = store.submitContact({ email: 'a@example.com' });
    await flush();
    streams[0]!.fail(new ApiError(401, 'unauthorized'));
    await flush();
    saved.reject(new ApiError(0, 'network'));
    await submitting;
    expect(store.getState()).toMatchObject({ contact: 'hidden', error: null });
  });

  it('returns to the form when the server rejects the details', async () => {
    const api = fakeApi({ sendContact: vi.fn().mockRejectedValue(new ApiError(422, 'validation')) });
    const { store, streams } = await ready({ api });
    store.openContact();
    await store.submitContact({ phone: '123' });
    expect(store.getState()).toMatchObject({ contact: 'open', error: 'errorContactRejected' });
    expect(streams).toHaveLength(1);
  });
});

describe('abortableSleep', () => {
  it('removes its abort listener when the timer fires', async () => {
    vi.useFakeTimers();
    try {
      const controller = new AbortController();
      const remove = vi.spyOn(controller.signal, 'removeEventListener');
      const done = abortableSleep(1000, controller.signal);
      vi.advanceTimersByTime(1000);
      await done;
      expect(remove).toHaveBeenCalledWith('abort', expect.any(Function));
    } finally {
      vi.useRealTimers();
    }
  });

  it('resolves early when aborted', async () => {
    vi.useFakeTimers();
    try {
      const controller = new AbortController();
      const done = abortableSleep(60_000, controller.signal);
      controller.abort();
      await done;
      expect(vi.getTimerCount()).toBe(0);
    } finally {
      vi.useRealTimers();
    }
  });
});
