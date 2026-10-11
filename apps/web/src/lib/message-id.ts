/**
 * Identity of an optimistically rendered user message.
 *
 * The cockpit generates it and sends it with the turn so the server can echo it back
 * in the `user_message` broadcast; the originating tab then recognises its own entry
 * instead of rendering it twice.
 */
export function newMessageId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return `msg-${crypto.randomUUID()}`;
  }
  return `msg-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}
