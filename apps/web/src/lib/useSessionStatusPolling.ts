import { useEffect } from "react";
import { useMissionStore } from "./store";

/** How often the list is re-read while the page is in view. */
export const SESSION_STATUS_POLL_MS = 6000;

/**
 * Keeps the sidebar's session status live for every session, not only the open one: the live
 * connection carries the open session's events, and the others change on the server (a turn
 * starts, ends or asks for an answer) with nothing to tell this page. Quiet while the tab is
 * hidden, and caught up the moment it is shown again.
 */
export function useSessionStatusPolling(): void {
  const refresh = useMissionStore((state) => state.refreshSessionStatuses);

  useEffect(() => {
    const refreshIfVisible = () => {
      if (document.visibilityState === "visible") void refresh();
    };
    const timer = setInterval(refreshIfVisible, SESSION_STATUS_POLL_MS);
    document.addEventListener("visibilitychange", refreshIfVisible);
    window.addEventListener("focus", refreshIfVisible);
    return () => {
      clearInterval(timer);
      document.removeEventListener("visibilitychange", refreshIfVisible);
      window.removeEventListener("focus", refreshIfVisible);
    };
  }, [refresh]);
}
