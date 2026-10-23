import { useEffect, useRef } from 'preact/hooks';
import type { PublicMessage } from '../api';
import { isNearBottom } from './scroll';
import type { Translate } from './types';

interface Props {
  greeting: string;
  messages: PublicMessage[];
  sending: boolean;
  t: Translate;
}

export function MessageList({ greeting, messages, sending, t }: Props) {
  const listRef = useRef<HTMLDivElement>(null);
  // Whether the visitor is reading the latest messages; updated on scroll, read after new content is rendered.
  // A turn the visitor just sent (sending, or their message newest) always scrolls down.
  const pinnedRef = useRef(true);
  const last = messages[messages.length - 1];
  useEffect(() => {
    const el = listRef.current;
    if (el && (pinnedRef.current || sending || last?.role === 'visitor')) {
      el.scrollTop = el.scrollHeight;
      pinnedRef.current = true;
    }
  }, [messages.length, sending]);
  return (
    <div class="rw-messages" role="log" ref={listRef} onScroll={(e) => (pinnedRef.current = isNearBottom(e.currentTarget))}>
      {greeting ? <div class="rw-msg rw-bot">{greeting}</div> : null}
      {messages.map((m) => (
        <div key={m.id} class={`rw-msg rw-${m.role}`}>
          {m.content}
        </div>
      ))}
      {sending ? (
        <div class="rw-msg rw-bot rw-typing">
          <span aria-hidden="true">…</span>
          <span class="rw-sr">{t('typing')}</span>
        </div>
      ) : null}
    </div>
  );
}
