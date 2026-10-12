import { describe, expect, it, vi } from 'vitest';
import { createConnectionRegistry } from './connections';

describe('createConnectionRegistry', () => {
  it('closes the oldest connection beyond the cap', () => {
    const reg = createConnectionRegistry(2);
    const c1 = vi.fn();
    const c2 = vi.fn();
    const c3 = vi.fn();
    reg.add('k', c1);
    reg.add('k', c2);
    reg.add('k', c3);
    expect(c1).toHaveBeenCalledOnce();
    expect(c2).not.toHaveBeenCalled();
    expect(reg.count('k')).toBe(2);
  });

  it('removes entries on release, idempotently, and per key', () => {
    const reg = createConnectionRegistry(3);
    const off = reg.add('a', () => {});
    reg.add('b', () => {});
    off();
    off();
    expect(reg.count('a')).toBe(0);
    expect(reg.count()).toBe(1);
  });
});
