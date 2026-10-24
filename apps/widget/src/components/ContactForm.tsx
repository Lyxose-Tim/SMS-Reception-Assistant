import type { TargetedEvent, TargetedSubmitEvent } from 'preact';
import { useEffect, useRef, useState } from 'preact/hooks';
import type { ContactInput } from '../api';
import type { ChatState, ChatStore } from '../chatStore';
import type { Translate } from './types';

interface Props {
  store: ChatStore;
  state: ChatState;
  t: Translate;
  onCancel(): void;
}

export function ContactForm({ store, state, t, onCancel }: Props) {
  const [values, setValues] = useState<Required<ContactInput>>({ name: '', email: '', phone: '', note: '' });
  const nameRef = useRef<HTMLInputElement>(null);
  useEffect(() => {
    nameRef.current?.focus();
  }, []);
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
        <input ref={nameRef} type="text" autocomplete="name" maxLength={100} value={values.name} onInput={update('name')} />
      </label>
      <label class="rw-field">
        <span>{t('email')}</span>
        <input type="email" autocomplete="email" maxLength={200} value={values.email} onInput={update('email')} />
      </label>
      <label class="rw-field">
        <span>{t('phone')}</span>
        <input type="tel" autocomplete="tel" maxLength={40} value={values.phone} onInput={update('phone')} />
      </label>
      <label class="rw-field">
        <span>{t('note')}</span>
        <textarea rows={2} maxLength={500} value={values.note} onInput={update('note')} />
      </label>
      <div class="rw-row">
        <button type="button" class="rw-link" onClick={onCancel}>
          {t('cancel')}
        </button>
        <button type="submit" class="rw-send" disabled={state.contact === 'sending'}>
          {t('submitContact')}
        </button>
      </div>
    </form>
  );
}
