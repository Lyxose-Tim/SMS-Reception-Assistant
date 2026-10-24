// @vitest-environment happy-dom
import { fireEvent, waitFor, within } from '@testing-library/preact';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiError, type MessagesResult } from '../api';
import { ChatStore } from '../chatStore';
import { mountShadow, type Mounted } from '../mount';
import { controllableStream, DEMO_CONFIG, fakeApi, memorySessions, msg } from '../testing/fakes';
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

describe('App composer', () => {
  const pendingSend = () => {
    let resolve!: (r: MessagesResult) => void;
    const sendMessage = vi.fn(() => new Promise<MessagesResult>((r) => (resolve = r)));
    return { sendMessage, resolve: (r: MessagesResult) => resolve(r) };
  };

  it('keeps text typed while a send is pending', async () => {
    const pending = pendingSend();
    const ctx = await setup(fakeApi({ sendMessage: pending.sendMessage }));
    await openPanel(ctx);
    const input = ctx.q.getByLabelText('Message') as HTMLTextAreaElement;
    await ctx.user.type(input, 'first{Enter}');
    await waitFor(() => expect(pending.sendMessage).toHaveBeenCalledTimes(1));
    await ctx.user.type(input, 'second draft');
    pending.resolve({ messages: [msg('visitor', 'first', 1), msg('bot', 'ok', 2)], status: 'bot' });
    expect(await ctx.q.findByText('ok')).toBeTruthy();
    expect(input.value).toBe('firstsecond draft'); // the pending text was not cleared over the new typing
  });

  it('keeps the original text when the send fails', async () => {
    const ctx = await setup(fakeApi({ sendMessage: vi.fn().mockRejectedValue(new ApiError(429, 'rate_limited')) }));
    await openPanel(ctx);
    const input = ctx.q.getByLabelText('Message') as HTMLTextAreaElement;
    await ctx.user.type(input, 'please help{Enter}');
    await ctx.q.findByRole('alert');
    expect(input.value).toBe('please help');
  });

  it('sends once when Enter is pressed twice quickly', async () => {
    const pending = pendingSend();
    const ctx = await setup(fakeApi({ sendMessage: pending.sendMessage }));
    await openPanel(ctx);
    const input = ctx.q.getByLabelText('Message') as HTMLTextAreaElement;
    await ctx.user.type(input, 'hello');
    await ctx.user.keyboard('{Enter}{Enter}');
    await waitFor(() => expect(pending.sendMessage).toHaveBeenCalledTimes(1));
    pending.resolve({ messages: [msg('visitor', 'hello', 1)], status: 'bot' });
    await waitFor(() => expect(input.value).toBe(''));
    expect(pending.sendMessage).toHaveBeenCalledTimes(1);
  });

  it('limits the draft to 1000 characters', async () => {
    const ctx = await setup();
    await openPanel(ctx);
    expect(ctx.q.getByLabelText('Message').getAttribute('maxlength')).toBe('1000');
  });

  it('does not send on Enter while an IME composition is in progress', async () => {
    const ctx = await setup();
    await openPanel(ctx);
    const input = ctx.q.getByLabelText('Message') as HTMLTextAreaElement;
    await ctx.user.type(input, 'ni hao');
    fireEvent.keyDown(input, { key: 'Enter', keyCode: 229 });
    fireEvent.compositionStart(input);
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(ctx.api.sendMessage).not.toHaveBeenCalled();
    fireEvent.compositionEnd(input);
    fireEvent.keyDown(input, { key: 'Enter' });
    await waitFor(() => expect(ctx.api.sendMessage).toHaveBeenCalledTimes(1));
  });

  it('does not close the panel on Escape during composition', async () => {
    const ctx = await setup();
    await openPanel(ctx);
    const input = ctx.q.getByLabelText('Message');
    fireEvent.keyDown(input, { key: 'Escape', keyCode: 229 });
    fireEvent.keyDown(input, { key: 'Escape', isComposing: true });
    expect(ctx.q.queryByRole('dialog')).toBeTruthy();
    fireEvent.keyDown(input, { key: 'Escape' });
    await waitFor(() => expect(ctx.q.queryByRole('dialog')).toBeNull());
  });
});

describe('App focus and accessibility', () => {
  it('moves focus to the composer after a quick question', async () => {
    const ctx = await setup();
    await openPanel(ctx);
    await ctx.user.click(ctx.q.getByRole('button', { name: 'What are your hours?' }));
    await ctx.q.findByText('Echo: What are your hours?');
    expect(ctx.root.activeElement).toBe(ctx.q.getByLabelText('Message'));
  });

  it('moves focus into the contact form, and back to the composer on cancel and after sending', async () => {
    const ctx = await setup();
    await openPanel(ctx);
    await ctx.user.click(ctx.q.getByRole('button', { name: 'Talk to a person' }));
    await waitFor(() => expect(ctx.streams).toHaveLength(1));
    ctx.streams[0]!.options.onEvent({ event: 'status', data: '{"status":"human"}', id: null });
    await ctx.user.click(await ctx.q.findByRole('button', { name: 'Leave your contact details' }));
    await waitFor(() => expect(ctx.root.activeElement).toBe(ctx.q.getByLabelText('Name')));
    await ctx.user.click(ctx.q.getByRole('button', { name: 'Cancel' }));
    await waitFor(() => expect(ctx.root.activeElement).toBe(ctx.q.getByLabelText('Message')));
    await ctx.user.click(ctx.q.getByRole('button', { name: 'Leave your contact details' }));
    await ctx.user.type(ctx.q.getByLabelText('Email'), 'a@example.com');
    await ctx.user.click(ctx.q.getByRole('button', { name: 'Send details' }));
    await ctx.q.findByText('Thanks! We will get back to you soon.');
    await waitFor(() => expect(ctx.root.activeElement).toBe(ctx.q.getByLabelText('Message')));
  });

  it('announces the typing indicator and the human notice for screen readers', async () => {
    let resolve!: (r: MessagesResult) => void;
    const sendMessage = vi.fn(() => new Promise<MessagesResult>((r) => (resolve = r)));
    const ctx = await setup(fakeApi({ sendMessage }));
    await openPanel(ctx);
    await ctx.user.type(ctx.q.getByLabelText('Message'), 'hi{Enter}');
    const typing = await waitFor(() => {
      const el = ctx.root.querySelector('.rw-typing');
      expect(el).toBeTruthy();
      return el!;
    });
    expect(typing.querySelector('[aria-hidden="true"]')?.textContent).toBe('…');
    expect(typing.querySelector('.rw-sr')?.textContent).toBe('Typing…');
    expect(ctx.root.querySelector('.rw-messages')?.hasAttribute('aria-live')).toBe(false);
    resolve({ messages: [msg('visitor', 'hi', 1)], status: 'human' });
    expect((await ctx.q.findByText('A team member will reply here as soon as possible.')).getAttribute('role')).toBe('status');
  });

  it('auto-scrolls only when near the bottom or for the visitor own message', async () => {
    const ctx = await setup();
    await openPanel(ctx);
    // .rw-body is the panel's single scroll container (messages, chips, notices and the contact form scroll together).
    const list = ctx.root.querySelector('.rw-body') as HTMLElement;
    Object.defineProperty(list, 'scrollHeight', { configurable: true, value: 1000 });
    Object.defineProperty(list, 'clientHeight', { configurable: true, value: 400 });
    await ctx.user.type(ctx.q.getByLabelText('Message'), 'hello{Enter}');
    await ctx.q.findByText('Echo: hello');
    await waitFor(() => expect(list.scrollTop).toBe(1000));
    await waitFor(() => expect(ctx.streams).toHaveLength(1));
    const agent = (id: number) => ({ event: 'message', data: JSON.stringify(msg('agent', `agent ${id}`, id)), id: String(id) });

    list.scrollTop = 100; // reading history
    fireEvent.scroll(list);
    ctx.streams[0]!.options.onEvent(agent(90));
    await ctx.q.findByText('agent 90');
    expect(list.scrollTop).toBe(100);

    list.scrollTop = 560; // within 80 px of the bottom again
    fireEvent.scroll(list);
    ctx.streams[0]!.options.onEvent(agent(91));
    await ctx.q.findByText('agent 91');
    await waitFor(() => expect(list.scrollTop).toBe(1000));

    list.scrollTop = 100;
    fireEvent.scroll(list);
    await ctx.user.type(ctx.q.getByLabelText('Message'), 'mine{Enter}');
    await ctx.q.findByText('Echo: mine');
    await waitFor(() => expect(list.scrollTop).toBe(1000));
  });
});

describe('App contact form', () => {
  async function openContactForm(ctx: Awaited<ReturnType<typeof setup>>) {
    await openPanel(ctx);
    await ctx.user.click(ctx.q.getByRole('button', { name: 'Talk to a person' }));
    await waitFor(() => expect(ctx.streams).toHaveLength(1));
    ctx.streams[0]!.options.onEvent({ event: 'status', data: '{"status":"human"}', id: null });
    await ctx.user.click(await ctx.q.findByRole('button', { name: 'Leave your contact details' }));
  }

  it('limits each field to the server maximum', async () => {
    const ctx = await setup();
    await openContactForm(ctx);
    expect(ctx.q.getByLabelText('Name').getAttribute('maxlength')).toBe('100');
    expect(ctx.q.getByLabelText('Email').getAttribute('maxlength')).toBe('200');
    expect(ctx.q.getByLabelText('Phone').getAttribute('maxlength')).toBe('40');
    expect(ctx.q.getByLabelText('Message (optional)').getAttribute('maxlength')).toBe('500');
  });

  it('asks for an email or phone when both are missing', async () => {
    const ctx = await setup();
    await openContactForm(ctx);
    await ctx.user.click(ctx.q.getByRole('button', { name: 'Send details' }));
    expect((await ctx.q.findByRole('alert')).textContent).toBe('Please enter a valid email address or phone number.');
    expect(ctx.api.sendContact).not.toHaveBeenCalled();
  });

  it('shows a generic check-your-details message when the server rejects the details', async () => {
    const ctx = await setup(fakeApi({ sendContact: vi.fn().mockRejectedValue(new ApiError(422, 'validation')) }));
    await openContactForm(ctx);
    await ctx.user.type(ctx.q.getByLabelText('Phone'), '123');
    await ctx.user.click(ctx.q.getByRole('button', { name: 'Send details' }));
    expect((await ctx.q.findByRole('alert')).textContent).toBe('Please check your details and try again.');
  });
});

describe('App connection status', () => {
  it('announces reconnecting as a status', async () => {
    const ctx = await setup();
    await openPanel(ctx);
    await ctx.user.type(ctx.q.getByLabelText('Message'), 'hi{Enter}');
    await waitFor(() => expect(ctx.streams).toHaveLength(1));
    ctx.streams[0]!.fail(new ApiError(0, 'network'));
    const status = await ctx.q.findByText('Reconnecting…');
    expect(status.getAttribute('role')).toBe('status');
  });
});

describe('App theming', () => {
  it('derives readable text color from the brand color', async () => {
    const light = { ...DEMO_CONFIG, widget: { ...DEMO_CONFIG.widget, primaryColor: '#fde047' } };
    const ctx = await setup(fakeApi({ getConfig: vi.fn().mockResolvedValue(light) }));
    const rootDiv = ctx.root.querySelector('.rw-root') as HTMLElement;
    expect(rootDiv.style.getPropertyValue('--rw-primary').trim()).toBe('#fde047');
    expect(rootDiv.style.getPropertyValue('--rw-on-primary').trim()).toBe('#111827');
  });
});
