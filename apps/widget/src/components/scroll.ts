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
