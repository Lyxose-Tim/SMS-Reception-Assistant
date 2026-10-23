import type { TargetedEvent, TargetedKeyboardEvent, TargetedSubmitEvent } from 'preact';
import { useEffect, useRef, useState } from 'preact/hooks';
import type { ContactInput, PublicMessage } from '../api';
import type { ChatState, ChatStore } from '../chatStore';
import { tr, type StringKey } from '../i18n';

type Translate = (key: StringKey) => string;

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
  return (
    <div class={`rw-root rw-${config.widget.position}`} style={`--rw-primary:${config.widget.primaryColor}`} lang={locale}>
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
  const onKeyDown = (e: TargetedKeyboardEvent<HTMLDivElement>) => {
    if (e.key === 'Escape') {
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
      <MessageList greeting={greeting} messages={state.messages} sending={state.sending} t={t} />
      {state.messages.length === 0 && config.quickQuestions.length > 0 ? (
        <div class="rw-quick">
          {config.quickQuestions.map((question) => (
            <button type="button" class="rw-chip" key={question} disabled={state.sending} onClick={() => void store.send(question)}>
              {question}
            </button>
          ))}
        </div>
      ) : null}
      {state.status === 'human' ? <p class="rw-notice">{t('humanNotice')}</p> : null}
      {state.contact === 'hidden' ? (
        <div class="rw-actions">
          {state.status === 'human' ? (
            <button type="button" class="rw-link" onClick={() => store.openContact()}>
              {t('leaveContact')}
            </button>
          ) : (
            <button type="button" class="rw-link" disabled={state.sending} onClick={() => void store.talkToPerson()}>
              {t('talkToPerson')}
            </button>
          )}
        </div>
      ) : (
        <ContactForm store={store} state={state} t={t} />
      )}
      {state.connection === 'retrying' ? <p class="rw-status">{t('reconnecting')}</p> : null}
      {state.error ? (
        <p class="rw-error" role="alert">
          {t(state.error)}
        </p>
      ) : null}
      <Composer store={store} sending={state.sending} t={t} />
    </div>
  );
}

function MessageList({ greeting, messages, sending, t }: { greeting: string; messages: PublicMessage[]; sending: boolean; t: Translate }) {
  const endRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    endRef.current?.scrollIntoView?.({ block: 'end' });
  }, [messages.length, sending]);
  return (
    <div class="rw-messages" role="log" aria-live="polite">
      {greeting ? <div class="rw-msg rw-bot">{greeting}</div> : null}
      {messages.map((m) => (
        <div key={m.id} class={`rw-msg rw-${m.role}`}>
          {m.content}
        </div>
      ))}
      {sending ? (
        <div class="rw-msg rw-bot rw-typing" aria-label={t('typing')}>
          …
        </div>
      ) : null}
      <div ref={endRef} />
    </div>
  );
}

function Composer({ store, sending, t }: { store: ChatStore; sending: boolean; t: Translate }) {
  const [text, setText] = useState('');
  const inputRef = useRef<HTMLTextAreaElement>(null);
  useEffect(() => {
    inputRef.current?.focus();
  }, []);
  const submit = async () => {
    if (!text.trim() || sending) return;
    await store.send(text);
    if (!store.getState().error) setText('');
  };
  return (
    <form
      class="rw-composer"
      onSubmit={(e: TargetedSubmitEvent<HTMLFormElement>) => {
        e.preventDefault();
        void submit();
      }}
    >
      <textarea
        ref={inputRef}
        class="rw-input"
        rows={1}
        aria-label={t('inputLabel')}
        placeholder={t('inputPlaceholder')}
        value={text}
        onInput={(e: TargetedEvent<HTMLTextAreaElement, Event>) => setText(e.currentTarget.value)}
        onKeyDown={(e: TargetedKeyboardEvent<HTMLTextAreaElement>) => {
          // Never send while an IME (e.g. Chinese pinyin) composition is in progress.
          if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
            e.preventDefault();
            void submit();
          }
        }}
      />
      <button type="submit" class="rw-send" disabled={sending || !text.trim()}>
        {t('send')}
      </button>
    </form>
  );
}

function ContactForm({ store, state, t }: { store: ChatStore; state: ChatState; t: Translate }) {
  const [values, setValues] = useState<Required<ContactInput>>({ name: '', email: '', phone: '', note: '' });
  if (state.contact === 'sent') {
    return (
      <p class="rw-notice" role="status">
        {t('contactThanks')}
      </p>
    );
  }
  const update = (key: keyof ContactInput) => (e: TargetedEvent<HTMLInputElement | HTMLTextAreaElement, Event>) => {
    const value = e.currentTarget.value;
    setValues((v) => ({ ...v, [key]: value }));
  };
  return (
    <form
      class="rw-contact"
      noValidate
      onSubmit={(e: TargetedSubmitEvent<HTMLFormElement>) => {
        e.preventDefault();
        void store.submitContact(values);
      }}
    >
      <p class="rw-intro">{t('contactIntro')}</p>
      <label class="rw-field">
        <span>{t('name')}</span>
        <input type="text" autocomplete="name" value={values.name} onInput={update('name')} />
      </label>
      <label class="rw-field">
        <span>{t('email')}</span>
        <input type="email" autocomplete="email" value={values.email} onInput={update('email')} />
      </label>
      <label class="rw-field">
        <span>{t('phone')}</span>
        <input type="tel" autocomplete="tel" value={values.phone} onInput={update('phone')} />
      </label>
      <label class="rw-field">
        <span>{t('note')}</span>
        <textarea rows={2} value={values.note} onInput={update('note')} />
      </label>
      <div class="rw-row">
        <button type="button" class="rw-link" onClick={() => store.closeContact()}>
          {t('cancel')}
        </button>
        <button type="submit" class="rw-send" disabled={state.contact === 'sending'}>
          {t('submitContact')}
        </button>
      </div>
    </form>
  );
}
