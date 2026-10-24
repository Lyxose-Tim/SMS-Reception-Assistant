import type { PublicMessage } from '../api';
import type { Translate } from './types';

interface Props {
  greeting: string;
  messages: PublicMessage[];
  sending: boolean;
  t: Translate;
}

/** Not a scroll container itself: the panel's `.rw-body` scrolls (and auto-scrolls) the list together with what follows it. */
export function MessageList({ greeting, messages, sending, t }: Props) {
  return (
    <div class="rw-messages" role="log">
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
