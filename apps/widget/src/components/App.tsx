import type { CSSProperties, TargetedKeyboardEvent } from 'preact';
import { useEffect, useRef, useState } from 'preact/hooks';
import type { ChatState, ChatStore } from '../chatStore';
import { tr } from '../i18n';
import { onColor } from './color';
import { Composer } from './Composer';
import { ContactForm } from './ContactForm';
import { MessageList } from './MessageList';
import { useStickToBottom } from './scroll';
import type { Translate } from './types';

export function useChatState(store: ChatStore): ChatState {
  const [state, setState] = useState(() => store.getState());
  useEffect(() => {
    setState(store.getState());
    return store.subscribe(setState);
  }, [store]);
  return state;
}

function ChatIcon() {
  return (
    <svg viewBox="0 0 24 24" width="26" height="26" aria-hidden="true">
      <path fill="currentColor" d="M4 4h16a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H8l-4 4V6a2 2 0 0 1 2-2z" />
    </svg>
  );
}

export function App({ store }: { store: ChatStore }) {
  const state = useChatState(store);
  const launcherRef = useRef<HTMLButtonElement>(null);
  if (state.phase !== 'ready' || !state.config) return null;
  const { config, locale } = state;
  const t: Translate = (key) => tr(locale, key);
  const close = () => {
    void store.setOpen(false);
    launcherRef.current?.focus();
  };
  const color = config.widget.primaryColor;
  const style = { '--rw-primary': color, '--rw-on-primary': onColor(color) } as CSSProperties;
  return (
    <div class={`rw-root rw-${config.widget.position}`} style={style} lang={locale}>
      {state.open ? <Panel store={store} state={state} t={t} onClose={close} /> : null}
      <button
        ref={launcherRef}
        type="button"
        class="rw-launcher"
        aria-expanded={state.open}
        aria-label={t(state.open ? 'closeChat' : 'openChat')}
        onClick={() => (state.open ? close() : void store.setOpen(true))}
      >
        {state.open ? <span aria-hidden="true">×</span> : <ChatIcon />}
      </button>
    </div>
  );
}

interface PanelProps {
  store: ChatStore;
  state: ChatState;
  t: Translate;
  onClose(): void;
}

function Panel({ store, state, t, onClose }: PanelProps) {
  const config = state.config!;
  const greeting = config.widget.greeting[state.locale] || config.widget.greeting.en;
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const focusInput = () => inputRef.current?.focus();
  // A turn the visitor just sent (sending, or their message newest) always scrolls down.
  const lastRole = state.messages[state.messages.length - 1]?.role;
  const body = useStickToBottom<HTMLDivElement>([state.messages.length, state.sending], state.sending || lastRole === 'visitor');
  // The contact form is replaced by a thank-you note once sent; keep focus inside the panel.
  useEffect(() => {
    if (state.contact === 'sent') focusInput();
  }, [state.contact]);
  const onKeyDown = (e: TargetedKeyboardEvent<HTMLDivElement>) => {
    // Escape also cancels an IME composition; that must not close the panel.
    if (e.key === 'Escape' && !e.isComposing && e.keyCode !== 229) {
      e.stopPropagation();
      onClose();
    }
  };
  return (
    <div class="rw-panel" role="dialog" aria-label={config.business.name} onKeyDown={onKeyDown}>
      <div class="rw-header">
        <span class="rw-title">{config.business.name}</span>
        <button type="button" class="rw-close" aria-label={t('closeChat')} onClick={onClose}>
          ×
        </button>
      </div>
      <div class="rw-body" ref={body.ref} onScroll={body.onScroll}>
        <MessageList greeting={greeting} messages={state.messages} sending={state.sending} t={t} />
        {state.messages.length === 0 && config.quickQuestions.length > 0 ? (
          <div class="rw-quick">
            {config.quickQuestions.map((question) => (
              <button
                type="button"
                class="rw-chip"
                key={question}
                disabled={state.sending}
                onClick={() => {
                  focusInput();
                  void store.send(question);
                }}
              >
                {question}
              </button>
            ))}
          </div>
        ) : null}
        {state.status === 'human' ? (
          <p class="rw-notice" role="status">
            {t('humanNotice')}
          </p>
        ) : null}
        {state.contact === 'hidden' ? (
          <div class="rw-actions">
            {state.status === 'human' ? (
              <button type="button" class="rw-link" onClick={() => store.openContact()}>
                {t('leaveContact')}
              </button>
            ) : (
              <button
                type="button"
                class="rw-link"
                disabled={state.sending}
                onClick={() => {
                  focusInput();
                  void store.talkToPerson();
                }}
              >
                {t('talkToPerson')}
              </button>
            )}
          </div>
        ) : (
          <ContactForm
            store={store}
            state={state}
            t={t}
            onCancel={() => {
              store.closeContact();
              focusInput();
            }}
          />
        )}
      </div>
      {state.connection === 'retrying' ? (
        <p class="rw-status" role="status">
          {t('reconnecting')}
        </p>
      ) : null}
      {state.error ? (
        <p class="rw-error" role="alert">
          {t(state.error)}
        </p>
      ) : null}
      <Composer store={store} sending={state.sending} t={t} inputRef={inputRef} />
    </div>
  );
}
