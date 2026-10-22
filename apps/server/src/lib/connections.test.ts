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

  it('closeKey closes every connection of that key only, and entries leave via their release functions', () => {
    const reg = createConnectionRegistry(5);
    const a1 = vi.fn();
    const a2 = vi.fn();
    const b1 = vi.fn();
    const off1 = reg.add('a', a1);
    const off2 = reg.add('a', a2);
    reg.add('b', b1);
    reg.closeKey('a');
    expect(a1).toHaveBeenCalledOnce();
    expect(a2).toHaveBeenCalledOnce();
    expect(b1).not.toHaveBeenCalled();
    off1();
    off2();
    expect(reg.count('a')).toBe(0);
    reg.closeKey('missing');
  });

  it('closeKey keeps closing the rest when one close throws', () => {
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
    try {
      const reg = createConnectionRegistry(5);
      const second = vi.fn();
      reg.add('k', () => {
        throw new Error('boom');
      });
      reg.add('k', second);
      reg.closeKey('k');
      expect(second).toHaveBeenCalledOnce();
      expect(errorSpy).toHaveBeenCalledOnce();
    } finally {
      errorSpy.mockRestore();
    }
  });

  it('keeps the cap and returns a working release when an evicted close throws', () => {
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
    try {
      const reg = createConnectionRegistry(1);
      reg.add('k', () => {
        throw new Error('boom');
      });
      const release = reg.add('k', () => {});
      expect(reg.count('k')).toBe(1);
      expect(errorSpy).toHaveBeenCalledOnce();
      release();
      expect(reg.count('k')).toBe(0);
    } finally {
      errorSpy.mockRestore();
    }
  });
});
