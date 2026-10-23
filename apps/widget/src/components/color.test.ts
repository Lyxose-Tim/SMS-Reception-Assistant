import { describe, expect, it } from 'vitest';
import { onColor } from './color';

describe('onColor', () => {
  it('picks white on dark brand colors and dark text on light ones', () => {
    expect(onColor('#2563eb')).toBe('#ffffff');
    expect(onColor('#000000')).toBe('#ffffff');
    expect(onColor('#fde047')).toBe('#111827');
    expect(onColor('#FFFFFF')).toBe('#111827');
  });
});
