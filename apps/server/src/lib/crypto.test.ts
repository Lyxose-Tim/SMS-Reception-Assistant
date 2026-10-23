import { randomBytes } from 'node:crypto';
import { describe, expect, it } from 'vitest';
import { decryptSecret, deriveKey, encryptSecret, hashPassword, randomToken, safeEqualHex, sha256, verifyPassword } from './crypto';

describe('passwords', () => {
  it('hashes with a random salt and verifies', async () => {
    const a = await hashPassword('correct horse');
    const b = await hashPassword('correct horse');
    expect(a).toMatch(/^scrypt\$/);
    expect(a).not.toBe(b);
    expect(await verifyPassword('correct horse', a)).toBe(true);
    expect(await verifyPassword('wrong', a)).toBe(false);
  });

  it('rejects malformed stored hashes', async () => {
    expect(await verifyPassword('x', 'plain')).toBe(false);
    expect(await verifyPassword('x', 'scrypt$only')).toBe(false);
  });

  it('returns false instead of throwing on undecodable salt or key', async () => {
    await expect(verifyPassword('x', 'scrypt$!!$abc')).resolves.toBe(false);
    await expect(verifyPassword('x', 'scrypt$$abc')).resolves.toBe(false);
  });
});

describe('tokens and hashes', () => {
  it('creates url-safe random tokens', () => {
    const t = randomToken();
    expect(t).toMatch(/^[A-Za-z0-9_-]{40,}$/);
    expect(randomToken()).not.toBe(t);
  });

  it('hashes with sha256 hex and compares safely', () => {
    expect(sha256('abc')).toBe('ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad');
    expect(safeEqualHex(sha256('a'), sha256('a'))).toBe(true);
    expect(safeEqualHex(sha256('a'), sha256('b'))).toBe(false);
    expect(safeEqualHex('ab', 'abcd')).toBe(false);
  });

  it('rejects non-hex, odd-length and empty input', () => {
    expect(safeEqualHex('ab', 'abzz')).toBe(false);
    expect(safeEqualHex('abc', 'ab')).toBe(false);
    expect(safeEqualHex('', '')).toBe(false);
  });
});

describe('secret encryption', () => {
  const key = deriveKey('x'.repeat(32), 'ai-api-keys');

  it('derives a stable 32-byte key per purpose', () => {
    expect(key).toHaveLength(32);
    expect(deriveKey('x'.repeat(32), 'ai-api-keys').equals(key)).toBe(true);
    expect(deriveKey('x'.repeat(32), 'other').equals(key)).toBe(false);
  });

  it('round-trips and never contains the plaintext', () => {
    const payload = encryptSecret('sk-test-1234567890', key);
    expect(payload.startsWith('v1.')).toBe(true);
    expect(payload).not.toContain('sk-test');
    expect(decryptSecret(payload, key)).toBe('sk-test-1234567890');
  });

  it('fails with the wrong key or a tampered payload', () => {
    const payload = encryptSecret('secret', key);
    expect(() => decryptSecret(payload, deriveKey('y'.repeat(32), 'ai-api-keys'))).toThrow();
    const parts = payload.split('.');
    parts[3] = Buffer.from('tampered').toString('base64url');
    expect(() => decryptSecret(parts.join('.'), key)).toThrow();
    expect(() => decryptSecret('garbage', key)).toThrow();
  });

  it('round-trips the empty string', () => {
    expect(decryptSecret(encryptSecret('', key), key)).toBe('');
  });

  it('rejects payloads with a truncated tag or a non-12-byte IV', () => {
    const parts = encryptSecret('secret', key).split('.');
    const shortTag = [...parts];
    shortTag[2] = Buffer.from(parts[2]!, 'base64url').subarray(0, 4).toString('base64url');
    expect(() => decryptSecret(shortTag.join('.'), key)).toThrow('Invalid secret payload');
    const shortIv = [...parts];
    shortIv[1] = randomBytes(8).toString('base64url');
    expect(() => decryptSecret(shortIv.join('.'), key)).toThrow('Invalid secret payload');
  });
});
