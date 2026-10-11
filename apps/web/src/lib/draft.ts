/** Unsent composer text, kept per conversation so a reload or a server restart does not lose it. */
const PREFIX = "rocket:draft:";

export function draftKey(sessionId: string | undefined): string {
  return `${PREFIX}${sessionId || "draft"}`;
}

export function loadDraft(sessionId: string | undefined): string {
  try {
    return window.localStorage.getItem(draftKey(sessionId)) ?? "";
  } catch {
    return "";
  }
}

export function saveDraft(sessionId: string | undefined, text: string): void {
  try {
    if (text.trim()) window.localStorage.setItem(draftKey(sessionId), text);
    else window.localStorage.removeItem(draftKey(sessionId));
  } catch {
    // Private mode or blocked storage: the draft just stays in memory.
  }
}
