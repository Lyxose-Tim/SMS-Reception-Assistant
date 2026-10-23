// @vitest-environment happy-dom
import { describe, expect, it, vi } from 'vitest';
import { whenBodyReady } from './widget';

function fakeDoc(body: unknown) {
  const listeners: Array<() => void> = [];
  const doc = {
    body,
    addEventListener: vi.fn((_type: string, fn: () => void) => listeners.push(fn)),
  };
  return { doc: doc as unknown as Document, doc_: doc, fire: () => listeners.forEach((fn) => fn()) };
}

describe('whenBodyReady', () => {
  it('runs immediately when document.body exists', () => {
    const { doc, doc_ } = fakeDoc({});
    const fn = vi.fn();
    whenBodyReady(doc, fn);
    expect(fn).toHaveBeenCalledTimes(1);
    expect(doc_.addEventListener).not.toHaveBeenCalled();
  });

  it('defers until DOMContentLoaded when the script runs in <head>', () => {
    const f = fakeDoc(null);
    const fn = vi.fn();
    whenBodyReady(f.doc, fn);
    expect(fn).not.toHaveBeenCalled();
    expect(f.doc_.addEventListener).toHaveBeenCalledWith('DOMContentLoaded', expect.any(Function), { once: true });
    f.doc_.body = {};
    f.fire();
    expect(fn).toHaveBeenCalledTimes(1);
  });
});
