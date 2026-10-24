import { render, type ComponentChild } from 'preact';
import css from './widget.css?inline';

export const HOST_ID = 'reception-widget-host';

export interface Mounted {
  host: HTMLElement;
  root: ShadowRoot;
  container: HTMLElement;
  unmount(): void;
}

/** Renders `content` inside a Shadow DOM so host-page styles cannot leak in (and ours cannot leak out). */
export function mountShadow(content: ComponentChild, doc: Document = document): Mounted | null {
  if (doc.getElementById(HOST_ID)) {
    console.warn('[reception-widget] The chat widget is already loaded on this page.');
    return null;
  }
  const host = doc.createElement('div');
  host.id = HOST_ID;
  // Inline !important beats host-page author rules (even !important ones) that match the host div, e.g. `div { transform: ... }`.
  host.style.setProperty('all', 'initial', 'important');
  const root = host.attachShadow({ mode: 'open' });
  const style = doc.createElement('style');
  style.textContent = css;
  const container = doc.createElement('div');
  root.append(style, container);
  doc.body.appendChild(host);
  render(content, container);
  return {
    host,
    root,
    container,
    unmount() {
      render(null, container);
      host.remove();
    },
  };
}
