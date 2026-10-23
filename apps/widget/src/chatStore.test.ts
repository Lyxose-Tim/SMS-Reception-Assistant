import { describe, expect, it, vi } from 'vitest';
import { ApiError } from './api';
import { ChatStore, PAGE_SIZE } from './chatStore';
import type { SessionStore } from './storage';
import { controllableStream, fakeApi, memorySessions, msg } from './testing/fakes';

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

function setup(opts: { api?: ReturnType<typeof fakeApi>; sessions?: SessionStore; language?: string } = {}) {
  const api = opts.api ?? fakeApi();
  const sessions = opts.sessions ?? memorySessions();
  const { stream, calls } = controllableStream();
  const sleep = vi.fn(async (_ms: number, _signal: AbortSignal) => {});
  const store = new ChatStore({ api, sessions, stream, localeOverride: null, browserLanguage: opts.language ?? 'en-US', sleep });
  return { api, sessions, store, streams: calls, sleep };
}

async function ready(opts?: Parameters<typeof setup>[0]) {
  const ctx = setup(opts);
  await ctx.store.init();
  await ctx.store.setOpen(true);
  return ctx;
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
});

describe('ChatStore: conversation lifecycle', () => {
  it('creates the conversation only on the first send', async () => {
    const { api, sessions, store, streams } = await ready();
    expect(api.createConversation).not.toHaveBeenCalled();
    expect(streams).toHaveLength(0);
    await store.send('  hi  ');
    expect(api.createConversation).toHaveBeenCalledTimes(1);
    expect(api.sendMessage).toHaveBeenCalledWith({ conversationId: 'c1', visitorToken: 't1' }, 'hi', 'en');
    expect(sessions.load()).toEqual({ conversationId: 'c1', visitorToken: 't1' });
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
    const sessions = memorySessions();
    sessions.save({ conversationId: 'old', visitorToken: 'tok' });
    const page1 = Array.from({ length: PAGE_SIZE }, (_, i) => msg('bot', `m${i + 1}`, i + 1));
    const api = fakeApi({
      listMessages: vi
        .fn()
        .mockResolvedValueOnce({ messages: page1, status: 'bot' })
        .mockResolvedValueOnce({ messages: [msg('agent', 'latest', 201)], status: 'human' }),
    });
    const { store, streams } = await ready({ api, sessions });
    expect(api.listMessages.mock.calls.map((c) => c[1])).toEqual([0, 200]);
    expect(store.getState().messages).toHaveLength(201);
    expect(store.getState().status).toBe('human');
    expect(streams[0]!.options).toMatchObject({ token: 'tok', lastEventId: 201 });
  });

  it('gap-fills after every ready event', async () => {
    const { api, store, streams } = await ready();
    await store.send('hi');
    const lastId = store.getState().messages.at(-1)!.id;
    streams[0]!.options.onEvent({ event: 'ready', data: '{}', id: null });
    await flush();
    expect(store.getState().connection).toBe('live');
    expect(api.listMessages).toHaveBeenLastCalledWith({ conversationId: 'c1', visitorToken: 't1' }, lastId);
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
    expect(sessions.load()).toEqual({ conversationId: 'c1', visitorToken: 't1' });
    expect(store.getState().messages).toHaveLength(2);
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

  it('reconnects with backoff and resets the backoff after ready', async () => {
    const { store, streams, sleep } = await ready();
    await store.send('hi');
    streams[0]!.fail(new ApiError(0, 'network'));
    await flush();
    expect(sleep.mock.calls.map((c) => c[0])).toEqual([1000]);
    expect(streams).toHaveLength(2);
    expect(streams[1]!.options.lastEventId).toBe(store.getState().messages.at(-1)!.id);
    streams[1]!.end();
    await flush();
    expect(sleep.mock.calls.map((c) => c[0])).toEqual([1000, 2000]);
    streams[2]!.options.onEvent({ event: 'ready', data: '{}', id: null });
    streams[2]!.end();
    await flush();
    expect(sleep.mock.calls.map((c) => c[0])).toEqual([1000, 2000, 1000]);
  });

  it('aborts the stream when the panel closes', async () => {
    const { store, streams } = await ready();
    await store.send('hi');
    await store.setOpen(false);
    expect(streams[0]!.options.signal.aborted).toBe(true);
    expect(store.getState().connection).toBe('idle');
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
    expect(api.sendContact).toHaveBeenCalledWith({ conversationId: 'c1', visitorToken: 't1' }, { name: 'Alex', email: 'a@example.com' });
    expect(store.getState().contact).toBe('sent');
  });

  it('returns to the form when the server rejects the details', async () => {
    const api = fakeApi({ sendContact: vi.fn().mockRejectedValue(new ApiError(422, 'validation')) });
    const { store } = await ready({ api });
    store.openContact();
    await store.submitContact({ phone: '123' });
    expect(store.getState()).toMatchObject({ contact: 'open', error: 'errorContact' });
  });
});
