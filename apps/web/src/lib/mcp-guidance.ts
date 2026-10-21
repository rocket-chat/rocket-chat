/** Limits of an MCP server's usage guidance. Mirrors `agent_core.guidance`, which is the rule. */
export const MAX_GUIDANCE_CHARS = 8_000;
/** Up to this length the guidance applies as soon as one of the server's tools is loaded. */
export const INLINE_GUIDANCE_CHARS = 1_500;
export const TRIGGER_CHARS = 200;

const LINE_MARKER = /^\s*(?:#{1,6}\s+|>+\s*|[-*+]\s+|\d+[.)]\s+)+/;
const INLINE_MARKUP = /(\*\*|__|`|\*|_(?=\S))/g;

/**
 * The first meaningful line as plain words: the one line the agent weighs the server by. The same
 * extraction as `agent_core.guidance.trigger_of`, so the preview shows what the agent will read.
 */
export function triggerOf(text: string): string {
  for (const line of text.split(/\r?\n/)) {
    const plain = line.replace(LINE_MARKER, "").replace(INLINE_MARKUP, "").split(/\s+/).filter(Boolean).join(" ");
    if (plain) return plain.length <= TRIGGER_CHARS ? plain : `${plain.slice(0, TRIGGER_CHARS - 1).trimEnd()}…`;
  }
  return "";
}

export const GUIDANCE_TEMPLATE = `Use for <what this server is good at, in one line>.

## How to use
- <what to check or pass first>
- <what the results mean>

## Avoid
- <what the agent should not do with this server>
`;
