function channel(hex: string, start: number): number {
  const v = parseInt(hex.slice(start, start + 2), 16) / 255;
  return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
}

/** Text color (dark or white) that stays readable on a `#rrggbb` brand color, by WCAG relative luminance. */
export function onColor(hex: string): '#111827' | '#ffffff' {
  const luminance = 0.2126 * channel(hex, 1) + 0.7152 * channel(hex, 3) + 0.0722 * channel(hex, 5);
  // White wins when its contrast ratio (1.05 / (L + 0.05)) beats the dark text's ((L + 0.05) / 0.0107).
  return luminance > 0.179 ? '#111827' : '#ffffff';
}
