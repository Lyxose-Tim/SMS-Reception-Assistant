// @vitest-environment happy-dom
import { waitFor, within } from '@testing-library/preact';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from '../api';
import { ChatStore } from '../chatStore';
import { mountShadow, type Mounted } from '../mount';
import { controllableStream, fakeApi, memorySessions, msg } from '../testing/fakes';
import { App } from './App';

let mounted: Mounted | null = null;
afterEach(() => {
  mounted?.unmount();
  mounted = null;
});

async function setup(api = fakeApi(), locale: string | null = null) {
  const { stream, calls } = controllableStream();
  const store = new ChatStore({ api, sessions: memorySessions(), stream, localeOverride: locale, browserLanguage: 'en-US', sleep: async () => {} });
  await store.init();
  mounted = mountShadow(<App store={store} />)!;
  const root = mounted.root;
  const q = within(root as unknown as HTMLElement);
  return { api, store, root, q, user: userEvent.setup(), streams: calls };
}

async function openPanel(ctx: Awaited<ReturnType<typeof setup>>) {
  await ctx.user.click(ctx.q.getByRole('button', { name: 'Chat with us' }));
  return ctx.q.findByRole('dialog', { name: 'Harbor & Home Cleaning' });
}

describe('App', () => {
  it('shows only the launcher until opened, then the greeting, quick questions and a focused input', async () => {
    const ctx = await setup();
    expect(ctx.q.queryByRole('dialog')).toBeNull();
    const launcher = ctx.q.getByRole('button', { name: 'Chat with us' });
    expect(launcher.getAttribute('aria-expanded')).toBe('false');
    await openPanel(ctx);
    expect(ctx.q.getByText('Hi! How can we help you today?')).toBeTruthy();
    expect(ctx.q.getByRole('button', { name: 'What are your hours?' })).toBeTruthy();
    await waitFor(() => expect(ctx.root.activeElement).toBe(ctx.q.getByLabelText('Message')));
  });

  it('applies the brand color and position', async () => {
    const ctx = await setup();
    const rootDiv = ctx.root.querySelector('.rw-root') as HTMLElement;
    expect(rootDiv.style.getPropertyValue('--rw-primary').trim()).toBe('#2563eb');
    expect(rootDiv.classList.contains('rw-right')).toBe(true);
  });

  it('sends a quick question and hides the chips afterwards', async () => {
    const ctx = await setup();
    await openPanel(ctx);
    await ctx.user.click(ctx.q.getByRole('button', { name: 'What are your hours?' }));
    expect(await ctx.q.findByText('Echo: What are your hours?')).toBeTruthy();
    expect(ctx.q.queryByRole('button', { name: 'Do you bring supplies?' })).toBeNull();
  });

  it('sends with Enter, keeps Shift+Enter as a newline and clears the input', async () => {
    const ctx = await setup();
    await openPanel(ctx);
    const input = ctx.q.getByLabelText('Message') as HTMLTextAreaElement;
    await ctx.user.type(input, 'line one{Shift>}{Enter}{/Shift}line two');
    expect(ctx.api.sendMessage).not.toHaveBeenCalled();
    await ctx.user.type(input, '{Enter}');
    await waitFor(() => expect(ctx.api.sendMessage).toHaveBeenCalledTimes(1));
    expect(ctx.api.sendMessage.mock.calls[0]![1]).toBe('line one\nline two');
    await waitFor(() => expect(input.value).toBe(''));
  });

  it('renders message content as plain text', async () => {
    const api = fakeApi({
      sendMessage: vi.fn().mockResolvedValue({ messages: [msg('visitor', 'hi', 1), msg('bot', '<img src=x onerror="alert(1)">', 2)], status: 'bot' }),
    });
    const ctx = await setup(api);
    await openPanel(ctx);
    await ctx.user.type(ctx.q.getByLabelText('Message'), 'hi{Enter}');
    expect(await ctx.q.findByText('<img src=x onerror="alert(1)">')).toBeTruthy();
    expect(ctx.root.querySelector('img')).toBeNull();
  });

  it('offers the contact form when a person takes over and thanks the visitor', async () => {
    const ctx = await setup();
    await openPanel(ctx);
    await ctx.user.click(ctx.q.getByRole('button', { name: 'Talk to a person' }));
    await waitFor(() => expect(ctx.streams).toHaveLength(1));
    ctx.streams[0]!.options.onEvent({ event: 'status', data: '{"status":"human"}', id: null });
    expect(await ctx.q.findByText('A team member will reply here as soon as possible.')).toBeTruthy();
    await ctx.user.click(ctx.q.getByRole('button', { name: 'Leave your contact details' }));
    await ctx.user.type(ctx.q.getByLabelText('Email'), 'a@example.com');
    await ctx.user.click(ctx.q.getByRole('button', { name: 'Send details' }));
    expect(await ctx.q.findByText('Thanks! We will get back to you soon.')).toBeTruthy();
    expect(ctx.api.sendContact).toHaveBeenCalledWith(expect.anything(), { email: 'a@example.com' });
  });

  it('shows errors as alerts', async () => {
    const ctx = await setup(fakeApi({ sendMessage: vi.fn().mockRejectedValue(new ApiError(429, 'rate_limited')) }));
    await openPanel(ctx);
    await ctx.user.type(ctx.q.getByLabelText('Message'), 'hi{Enter}');
    expect((await ctx.q.findByRole('alert')).textContent).toBe('You are sending messages too quickly. Please wait a moment.');
  });

  it('closes with Escape and returns focus to the launcher', async () => {
    const ctx = await setup();
    await openPanel(ctx);
    await ctx.user.type(ctx.q.getByLabelText('Message'), '{Escape}');
    await waitFor(() => expect(ctx.q.queryByRole('dialog')).toBeNull());
    expect(ctx.root.activeElement).toBe(ctx.q.getByRole('button', { name: 'Chat with us' }));
  });

  it('renders in Chinese when requested', async () => {
    const ctx = await setup(fakeApi(), 'zh-CN');
    await ctx.user.click(ctx.q.getByRole('button', { name: '在线咨询' }));
    expect(await ctx.q.findByText('您好！有什么可以帮您？')).toBeTruthy();
  });

  it('renders nothing when the widget is unavailable', async () => {
    const ctx = await setup(fakeApi({ getConfig: vi.fn().mockRejectedValue(new ApiError(403, 'origin_not_allowed')) }));
    expect(ctx.q.queryByRole('button')).toBeNull();
  });
});
