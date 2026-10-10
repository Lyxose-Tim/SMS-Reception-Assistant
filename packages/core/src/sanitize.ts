export const RESERVED_TAG_NAMES = ['business_info', 'visitor_message', 'booking_draft'] as const;

const RESERVED_TAG = new RegExp(`<\\s*\\/?\\s*(${RESERVED_TAG_NAMES.join('|')})\\b[^>]*>`, 'gi');
// eslint-disable-next-line no-control-regex
const CONTROL_CHARS = /[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F]/g;

/** Replaces < and > in reserved prompt tags so text cannot open or close a prompt section. */
export function neutralizeTags(text: string): string {
  return text.replace(RESERVED_TAG, (tag) => tag.replace(/</g, '‹').replace(/>/g, '›'));
}

export function cleanText(text: string): string {
  return text.replace(CONTROL_CHARS, '').trim();
}
