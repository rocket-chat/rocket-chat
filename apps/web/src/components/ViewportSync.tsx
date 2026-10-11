"use client";

import { useEffect } from "react";

const TEXT_FIELD =
  "input:not([type=checkbox]):not([type=radio]):not([type=range]):not([type=file]), textarea, [contenteditable=true]";

/**
 * Keeps the application shell usable on a phone.
 *
 * The shell never scrolls, but mobile browsers pan the page to reveal a focused field and may
 * leave it panned when the keyboard closes (hiding the top bar with no way back), or, if the page
 * is simply held still, leave the field hidden under the keyboard. So:
 *  - the shell gets bottom room equal to the keyboard (`--keyboard-inset`), but only while a text
 *    field is focused. Tying it to focus, not to resize events, means it cannot go stale: iOS does
 *    not always report the keyboard closing, and a height held by script stayed short;
 *  - any page scroll is undone.
 */
export function ViewportSync(): null {
  useEffect(() => {
    const root = document.documentElement;
    const viewport = window.visualViewport;

    const resetScroll = () => {
      if (window.scrollX !== 0 || window.scrollY !== 0) window.scrollTo(0, 0);
    };
    const update = () => {
      const editing = document.activeElement?.matches(TEXT_FIELD) ?? false;
      const covered = viewport ? window.innerHeight - viewport.height - viewport.offsetTop : 0;
      root.style.setProperty("--keyboard-inset", editing ? `${Math.max(0, Math.round(covered))}px` : "0px");
      resetScroll();
    };
    // The keyboard finishes opening and closing a moment after focus changes.
    const updateSoon = () => {
      update();
      window.setTimeout(update, 150);
      window.setTimeout(update, 400);
    };

    window.addEventListener("scroll", resetScroll, { passive: true });
    window.addEventListener("focusin", updateSoon);
    window.addEventListener("focusout", updateSoon);
    viewport?.addEventListener("resize", update);
    viewport?.addEventListener("scroll", update);
    return () => {
      window.removeEventListener("scroll", resetScroll);
      window.removeEventListener("focusin", updateSoon);
      window.removeEventListener("focusout", updateSoon);
      viewport?.removeEventListener("resize", update);
      viewport?.removeEventListener("scroll", update);
      root.style.removeProperty("--keyboard-inset");
    };
  }, []);

  return null;
}
