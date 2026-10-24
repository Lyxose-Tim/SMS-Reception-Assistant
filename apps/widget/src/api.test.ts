import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiError, createApi, timeoutSignal } from './api';

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });
const session = { conversationId: 'c1', visitorToken: 'tok' };
const asFetch = (fn: unknown) => fn as typeof fetch;

describe('createApi', () => {
  it('calls the widget endpoints with the right methods, paths, bodies and headers', async () => {
    const fetchImpl = vi
      .fn()
      .mockResolvedValueOnce(json({ business: { name: 'Harbor', slug: 'harbor-home' }, locale: 'en', widget: {}, quickQuestions: [] }))
      .mockResolvedValueOnce(json({ conversationId: 'c1', visitorToken: 'tok' }, 201))
      .mockResolvedValueOnce(json({ messages: [], status: 'bot' }))
      .mockResolvedValueOnce(json({ messages: [], status: 'bot' }))
      .mockResolvedValueOnce(json({ ok: true }));
    const api = createApi('https://chat.example.com', 'harbor-home', asFetch(fetchImpl));
    await api.getConfig();
    await api.createConversation();
    await api.listMessages(session, 5);
    await api.sendMessage(session, 'hi', 'zh-CN');
    await api.sendContact(session, { email: 'a@example.com' });

    const calls = fetchImpl.mock.calls.map(([url, init]) => [url, init?.method ?? 'GET']);
    expect(calls).toEqual([
      ['https://chat.example.com/api/widget/b/harbor-home/config', 'GET'],
      ['https://chat.example.com/api/widget/b/harbor-home/conversations', 'POST'],
      ['https://chat.example.com/api/widget/c/c1/messages?since=5', 'GET'],
      ['https://chat.example.com/api/widget/c/c1/messages', 'POST'],
      ['https://chat.example.com/api/widget/c/c1/contact', 'POST'],
    ]);
    const send = fetchImpl.mock.calls[3]![1];
    expect(JSON.parse(send.body)).toEqual({ text: 'hi', locale: 'zh-CN' });
    expect(send.headers).toMatchObject({ Authorization: 'Bearer tok', 'Content-Type': 'application/json' });
    const headersOf = (i: number) => (fetchImpl.mock.calls[i]![1]?.headers ?? {}) as Record<string, string>;
    expect(headersOf(0)).not.toHaveProperty('Authorization');
    expect(headersOf(1)).not.toHaveProperty('Authorization');
    expect(headersOf(2)).toMatchObject({ Authorization: 'Bearer tok' });
    expect(headersOf(4)).toMatchObject({ Authorization: 'Bearer tok' });
    for (const [url] of fetchImpl.mock.calls) expect(String(url)).not.toContain('tok');
    expect(api.eventsUrl('c1')).toBe('https://chat.example.com/api/widget/c/c1/events');
  });

  it('maps HTTP errors and network failures to ApiError', async () => {
    const api = createApi('https://chat.example.com', 'harbor-home', asFetch(vi.fn().mockResolvedValue(json({ error: 'rate_limited' }, 429))));
    await expect(api.sendMessage(session, 'hi', 'en')).rejects.toMatchObject({ status: 429, code: 'rate_limited' });

    const broken = createApi('https://chat.example.com', 'harbor-home', asFetch(vi.fn().mockRejectedValue(new TypeError('Failed to fetch'))));
    const error = await broken.getConfig().catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 0, code: 'network' });

    const html = createApi('https://chat.example.com', 'harbor-home', asFetch(vi.fn().mockResolvedValue(new Response('<html>', { status: 502 }))));
    await expect(html.getConfig()).rejects.toMatchObject({ status: 502, code: 'http_error' });
  });

  it('maps an unparseable 2xx body to bad_response', async () => {
    const api = createApi('https://chat.example.com', 'harbor-home', asFetch(vi.fn().mockResolvedValue(new Response('<html>ok</html>', { status: 200 }))));
    await expect(api.getConfig()).rejects.toMatchObject({ name: 'ApiError', status: 200, code: 'bad_response' });
  });

  it('rejects a createConversation response that is not a valid session', async () => {
    const api = createApi('https://chat.example.com', 'harbor-home', asFetch(vi.fn().mockResolvedValue(json({}, 201))));
    await expect(api.createConversation()).rejects.toMatchObject({ status: 201, code: 'bad_response' });
  });
});

describe('timeoutSignal', () => {
  afterEach(() => vi.useRealTimers());

  it('uses AbortSignal.timeout when the implementation has it', () => {
    const signal = new AbortController().signal;
    const timeout = vi.fn().mockReturnValue(signal);
    const handle = timeoutSignal(500, { timeout });
    expect(timeout).toHaveBeenCalledWith(500);
    expect(handle.signal).toBe(signal);
    handle.dispose();
  });

  it('falls back to AbortController + setTimeout and clears the timer on dispose', () => {
    vi.useFakeTimers();
    const first = timeoutSignal(1000, {});
    expect(first.signal.aborted).toBe(false);
    vi.advanceTimersByTime(1000);
    expect(first.signal.aborted).toBe(true);

    const second = timeoutSignal(1000, {});
    second.dispose();
    expect(vi.getTimerCount()).toBe(0);
    vi.advanceTimersByTime(2000);
    expect(second.signal.aborted).toBe(false);
  });
});

describe('getConfig normalization', () => {
  const configFrom = async (body: unknown) => createApi('https://chat.example.com', 'harbor-home', asFetch(vi.fn().mockResolvedValue(json(body)))).getConfig();

  it('replaces malformed fields with safe defaults', async () => {
    const config = await configFrom({
      business: { name: 42 },
      locale: 'fr',
      widget: { primaryColor: 'red;} body{display:none', position: 'top', greeting: { en: 7 } },
      quickQuestions: 'nope',
    });
    expect(config).toEqual({
      business: { name: '', slug: 'harbor-home' },
      locale: 'en',
      widget: { primaryColor: '#2563eb', position: 'right', greeting: { en: '', 'zh-CN': '' } },
      quickQuestions: [],
    });
  });

  it('keeps valid values, de-duplicates quick questions and caps them at four', async () => {
    const config = await configFrom({
      business: { name: 'Harbor', slug: 'harbor-home' },
      locale: 'zh-CN',
      widget: { primaryColor: '#FDE047', position: 'left', greeting: { en: 'Hi', 'zh-CN': '你好' } },
      quickQuestions: ['a', 'a', 'b', 3, 'c', 'd', 'e'],
    });
    expect(config.locale).toBe('zh-CN');
    expect(config.widget).toEqual({ primaryColor: '#FDE047', position: 'left', greeting: { en: 'Hi', 'zh-CN': '你好' } });
    expect(config.quickQuestions).toEqual(['a', 'b', 'c', 'd']);
  });

  it('rejects a config that is not an object', async () => {
    await expect(configFrom([])).rejects.toMatchObject({ code: 'bad_response' });
  });
});
