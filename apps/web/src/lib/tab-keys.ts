import type { KeyboardEvent } from "react";

/**
 * A tab's key handler: selects the tab the key moves to and focuses it. `domId` names the
 * tab element, so focus follows the selection (one tab stop for the whole list).
 */
export function tabKeyHandler<T extends string>(
  tabs: readonly T[],
  current: T,
  select: (tab: T) => void,
  domId: (tab: T) => string,
  orientation: "horizontal" | "vertical" = "horizontal"
) {
  return (event: KeyboardEvent<HTMLElement>) => {
    const key =
      orientation === "vertical"
        ? { ArrowDown: "ArrowRight", ArrowUp: "ArrowLeft" }[event.key] ?? event.key
        : event.key;
    const next = tabForKey(key, tabs, current);
    if (!next) return;
    event.preventDefault();
    select(next);
    document.getElementById(domId(next))?.focus();
  };
}

/**
 * The tab a key moves to in a horizontal tab list (WAI-ARIA tabs pattern): the arrows wrap
 * around, Home and End jump to the ends. Null for any other key.
 */
export function tabForKey<T extends string>(key: string, tabs: readonly T[], current: T): T | null {
  const index = tabs.indexOf(current);
  if (tabs.length === 0) return null;
  switch (key) {
    case "ArrowRight":
      return tabs[(index + 1) % tabs.length];
    case "ArrowLeft":
      return tabs[(index - 1 + tabs.length) % tabs.length];
    case "Home":
      return tabs[0];
    case "End":
      return tabs[tabs.length - 1];
    default:
      return null;
  }
}
