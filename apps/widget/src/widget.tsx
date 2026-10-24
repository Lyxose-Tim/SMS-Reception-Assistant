import { createApi } from './api';
import { ChatStore } from './chatStore';
import { App } from './components/App';
import type { EmbedConfig } from './embed';
import { mountShadow, type Mounted } from './mount';
import { streamEvents } from './sse';
import { createSessionStore } from './storage';

export interface StartedWidget {
  store: ChatStore;
  mounted: Mounted;
  destroy(): void;
}

/** The snippet may sit in <head>, where `document.body` does not exist yet: wait for it. */
export function whenBodyReady(doc: Document, fn: () => void): void {
  if (doc.body) fn();
  else doc.addEventListener('DOMContentLoaded', fn, { once: true });
}

export function startWidget(
  embed: EmbedConfig,
  env: { fetch: typeof fetch; language: string | undefined } = { fetch: globalThis.fetch.bind(globalThis), language: navigator.language },
): StartedWidget | null {
  const api = createApi(embed.apiBase, embed.slug, env.fetch);
  const store = new ChatStore({
    api,
    sessions: createSessionStore(embed.slug, embed.apiBase),
    stream: (options) => streamEvents({ ...options, fetchImpl: env.fetch }),
    localeOverride: embed.locale,
    browserLanguage: env.language,
  });
  const mounted = mountShadow(<App store={store} />);
  if (!mounted) return null;
  void store.init();
  return {
    store,
    mounted,
    destroy() {
      store.destroy();
      mounted.unmount();
    },
  };
}
