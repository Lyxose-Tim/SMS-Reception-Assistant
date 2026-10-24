import type { RefObject } from 'preact';
import { useEffect, useRef } from 'preact/hooks';

/** How close (px) to the bottom the visitor must be for new messages to keep the list pinned. */
export const NEAR_BOTTOM_PX = 80;

export interface ScrollMetrics {
  scrollHeight: number;
  scrollTop: number;
  clientHeight: number;
}

export function isNearBottom({ scrollHeight, scrollTop, clientHeight }: ScrollMetrics): boolean {
  return scrollHeight - scrollTop - clientHeight <= NEAR_BOTTOM_PX;
}

/**
 * Keeps a scroll container pinned to the bottom while the visitor is reading the latest content.
 * Pinning is tracked on scroll and applied after `changeKey` changes; `force` (the visitor's own turn) always scrolls down.
 */
export function useStickToBottom<T extends HTMLElement>(changeKey: unknown[], force: boolean): { ref: RefObject<T | null>; onScroll(e: { currentTarget: T }): void } {
  const ref = useRef<T>(null);
  const pinnedRef = useRef(true);
  useEffect(() => {
    const el = ref.current;
    if (el && (pinnedRef.current || force)) {
      el.scrollTop = el.scrollHeight;
      pinnedRef.current = true;
    }
  }, changeKey);
  return { ref, onScroll: (e) => (pinnedRef.current = isNearBottom(e.currentTarget)) };
}
