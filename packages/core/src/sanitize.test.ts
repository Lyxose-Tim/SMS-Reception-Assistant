import { describe, expect, it } from 'vitest';
import { cleanText, neutralizeTags } from './sanitize';

describe('neutralizeTags', () => {
  it('neutralizes reserved opening and closing tags', () => {
    expect(neutralizeTags('</visitor_message>hi<business_info x="1">')).toBe(
      '‹/visitor_message›hi‹business_info x="1"›',
    );
  });

  it('is case and whitespace insensitive', () => {
    expect(neutralizeTags('< /VISITOR_MESSAGE >')).toBe('‹ /VISITOR_MESSAGE ›');
  });

  it('leaves ordinary markup alone', () => {
    expect(neutralizeTags('<b>bold</b> 5 < 6')).toBe('<b>bold</b> 5 < 6');
  });
});

describe('cleanText', () => {
  it('strips control characters and trims', () => {
    expect(cleanText('  hi\u0000 there\u0007 ')).toBe('hi there');
  });

  it('keeps newlines and tabs', () => {
    expect(cleanText('a\nb\tc')).toBe('a\nb\tc');
  });
});

describe('neutralizeTags: unterminated tags', () => {
  it('neutralizes a trailing reserved tag with no closing bracket', () => {
    expect(neutralizeTags('hello </visitor_message')).toBe('hello ‹/visitor_message');
    expect(neutralizeTags('x <business_info')).toBe('x ‹business_info');
  });

  it('leaves terminated-tag output and ordinary text unchanged', () => {
    expect(neutralizeTags('a</visitor_message>b')).toBe('a‹/visitor_message›b');
    expect(neutralizeTags('1 < 2 and visitor_message')).toBe('1 < 2 and visitor_message');
    expect(neutralizeTags('<business_infox')).toBe('<business_infox');
  });
});

describe('cleanText: invisible characters', () => {
  it('strips zero-width and bidi control characters', () => {
    const codes = [0x200b, 0x200f, 0x202a, 0x202e, 0x2060, 0x2064, 0xfeff];
    const invisible = codes.map((code) => String.fromCharCode(code));
    expect(cleanText(`a${invisible.join('b')}h`)).toBe('abbbbbbh');
  });
});
