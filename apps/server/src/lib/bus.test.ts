import { describe, expect, it, vi } from 'vitest';
import { createEventBus } from './bus';

describe('createEventBus', () => {
  it('delivers to subscribers of a topic only', () => {
    const bus = createEventBus();
    const a = vi.fn();
    const b = vi.fn();
    bus.subscribe('t1', a);
    bus.subscribe('t2', b);
    bus.publish('t1', { type: 'x', data: 1 });
    expect(a).toHaveBeenCalledWith({ type: 'x', data: 1 });
    expect(b).not.toHaveBeenCalled();
  });

  it('unsubscribe is idempotent and empties the topic map', () => {
    const bus = createEventBus();
    const off = bus.subscribe('t', () => {});
    bus.subscribe('t', () => {});
    expect(bus.listenerCount('t')).toBe(2);
    off();
    off();
    expect(bus.listenerCount('t')).toBe(1);
    expect(bus.listenerCount()).toBe(1);
  });

  it('isolates a throwing subscriber', () => {
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
    try {
      const bus = createEventBus();
      const good = vi.fn();
      bus.subscribe('t', () => {
        throw new Error('boom');
      });
      bus.subscribe('t', good);
      expect(() => bus.publish('t', { type: 'x', data: null })).not.toThrow();
      expect(good).toHaveBeenCalledOnce();
      expect(errorSpy).toHaveBeenCalledOnce();
    } finally {
      errorSpy.mockRestore();
    }
  });
});
