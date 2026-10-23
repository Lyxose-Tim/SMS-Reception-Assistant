import type { RefObject, TargetedEvent, TargetedKeyboardEvent, TargetedSubmitEvent } from 'preact';
import { useEffect, useRef, useState } from 'preact/hooks';
import type { ChatStore } from '../chatStore';
import type { Translate } from './types';

interface Props {
  store: ChatStore;
  sending: boolean;
  t: Translate;
  inputRef: RefObject<HTMLTextAreaElement | null>;
}

export function Composer({ store, sending, t, inputRef }: Props) {
  const [text, setText] = useState('');
  const composing = useRef(false);
  useEffect(() => {
    inputRef.current?.focus();
  }, []);
  const submit = async () => {
    // Live store state, not the render-time prop: two quick Enters must not both pass.
    if (!text.trim() || store.getState().sending) return;
    const sent = text;
    await store.send(sent);
    // Keep the draft on failure, and keep anything the visitor typed while the send was pending.
    if (!store.getState().error) setText((current) => (current === sent ? '' : current));
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
        maxLength={1000}
        aria-label={t('inputLabel')}
        placeholder={t('inputPlaceholder')}
        value={text}
        onInput={(e: TargetedEvent<HTMLTextAreaElement, Event>) => setText(e.currentTarget.value)}
        onCompositionStart={() => (composing.current = true)}
        onCompositionEnd={() => (composing.current = false)}
        onBlur={() => (composing.current = false)}
        onKeyDown={(e: TargetedKeyboardEvent<HTMLTextAreaElement>) => {
          // Never send while an IME (pinyin, kana, ...) composition is in progress; Safari reports keyCode 229 for it.
          const isComposing = composing.current || e.isComposing || e.keyCode === 229;
          if (e.key === 'Enter' && !e.shiftKey && !isComposing) {
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
