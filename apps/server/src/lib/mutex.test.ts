import { describe, expect, it } from 'vitest';
import { createKeyedMutex } from './mutex';

const delay = (ms: number) => new Promise((r) => setTimeout(r, ms));

describe('createKeyedMutex', () => {
  it('serializes work for the same key', async () => {
    const mutex = createKeyedMutex();
    const log: string[] = [];
    await Promise.all([
      mutex.run('a', async () => {
        log.push('1-start');
        await delay(20);
        log.push('1-end');
      }),
      mutex.run('a', async () => {
        log.push('2-start');
        log.push('2-end');
      }),
    ]);
    expect(log).toEqual(['1-start', '1-end', '2-start', '2-end']);
  });

  it('runs different keys concurrently', async () => {
    const mutex = createKeyedMutex();
    const log: string[] = [];
    await Promise.all([
      mutex.run('a', async () => {
        log.push('a-start');
        await delay(20);
        log.push('a-end');
      }),
      mutex.run('b', async () => {
        log.push('b');
      }),
    ]);
    expect(log.indexOf('b')).toBeLessThan(log.indexOf('a-end'));
  });

  it('releases after a failure and cleans up keys', async () => {
    const mutex = createKeyedMutex();
    await expect(mutex.run('a', async () => Promise.reject(new Error('x')))).rejects.toThrow('x');
    await expect(mutex.run('a', async () => 42)).resolves.toBe(42);
    expect(mutex.size()).toBe(0);
  });
});
