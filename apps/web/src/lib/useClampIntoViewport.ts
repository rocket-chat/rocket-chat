"use client";

import { useLayoutEffect, type RefObject } from "react";

/**
 * Shift a popover back inside the window when its anchor leaves it running off an edge (a control
 * mid-header on a phone, or at the left of a composer): translate it by the overflow, with a margin.
 */
export function useClampIntoViewport(ref: RefObject<HTMLElement | null>, open: boolean, margin = 8): void {
  useLayoutEffect(() => {
    const element = ref.current;
    if (!open || !element) return;
    element.style.transform = "";
    const { left, right } = element.getBoundingClientRect();
    const shift =
      right > window.innerWidth - margin ? window.innerWidth - margin - right : left < margin ? margin - left : 0;
    if (shift) element.style.transform = `translateX(${shift}px)`;
  }, [ref, open, margin]);
}
