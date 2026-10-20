import type { TurnBinding,
  AgentPersonaConfig,
  FlightLogEntry,
  SessionData,
} from "../../types/mission";
import type { IncomingUserMessage, OwnUserMessage } from "./types";

export const DEFAULT_AGENTS: AgentPersonaConfig[] = [
  {
    id: "persona-general",
    name: "General Software Engineer",
    role_title: "Senior Autonomous Engineer",
    description:
      "Autonomous full-stack engineering agent with full access to terminal execution, file inspection and editing, web lookup, git operations, and interactive decision gates.",
    system_prompt:
      "You are a Senior Autonomous Software Engineer. You write clean, modular, production-grade code adhering to strict types and software craftsmanship. Plan methodically, inspect files, apply surgical diffs, and verify your work with automated tests.",
    model: "openrouter/deepseek/deepseek-v4.1-flash",
    built_in: true,
    whitelisted_tools: [
      "bash_exec",
      "file_read",
      "file_write",
      "file_edit",
      "apply_patch",
      "web_fetch",
      "web_search",
      "ask_question",
      "session_rename",
      "update_task_checklist",
    ],
    enabled: true,
    icon: "bot",
  },
  {
    id: "persona-security",
    name: "Security & Penetration Auditor",
    role_title: "Application Security Specialist",
    description:
      "Audits source code for injection attacks, RLS boundary leaks, secret exposure, and permissions.",
    system_prompt:
      "You are a dedicated Security Auditor persona. Inspect source files and configuration for vulnerabilities (OWASP Top 10, SQL injection, RLS bypasses, secret leakage).",
    model: "openrouter/anthropic/claude-3.5-sonnet",
    whitelisted_tools: ["file_read", "web_search", "web_fetch"],
    built_in: true,
    enabled: true,
    icon: "shield",
  },
];

/**
 * Whether a broadcast `user_message` is the echo of a prompt this tab submitted.
 *
 * The primary contract is the client-generated `message_id` echoed back by the
 * server. The content check covers a server that assigns its own id: without it the
 * originating tab would render its own prompt twice, which is the duplicate-prompt
 * failure this guards against.
 */
export function matchesPendingEcho(
  pending: OwnUserMessage,
  incoming: IncomingUserMessage,
  content: string
): boolean {
  if (incoming.messageId && pending.messageId === incoming.messageId) return true;
  if (pending.content !== content) return false;
  if (pending.authorUserId && incoming.authorUserId) {
    return pending.authorUserId === incoming.authorUserId;
  }
  return true;
}

export function formatTimestamp(ts?: number | string | null): string {
  if (!ts) return "";
  const num = typeof ts === "string" ? parseInt(ts, 10) : ts;
  if (!num || isNaN(num)) return "";
  const ms = num < 1e11 ? num * 1000 : num;
  return new Date(ms).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

export function formatEventTime(ts?: number | string | null): string {
  if (ts !== undefined && ts !== null) {
    const formatted = formatTimestamp(ts);
    if (formatted) return formatted;
  }
  return new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

export function convertHistoryToFlightLogs(
  history: NonNullable<SessionData["conversation_history"]>,
  sessionId: string
): FlightLogEntry[] {
  const logs: FlightLogEntry[] = [];
  // Each answer belongs to the turn of the user message before it, which records who answered.
  let answeredBy: TurnBinding | null = null;
  history.forEach((m, idx) => {
    const roleUpper = m.role.toUpperCase();
    const timestamp = formatTimestamp(m.created_at);
    if (roleUpper === "USER") {
      answeredBy = m.model ? { agentName: m.agent_name || "", model: m.model } : null;
      logs.push({
        id: `msg-${sessionId}-${idx}-user`,
        role: "USER",
        content: m.content,
        timestamp,
        isPersisted: true,
        authorUserId: m.author_user_id ?? null,
      });
    } else if (roleUpper === "SYSTEM") {
      logs.push({
        id: `msg-${sessionId}-${idx}-sys`,
        role: "SYSTEM",
        content: m.content,
        timestamp,
      });
    } else {
      // Newer turns keep each step's reasoning on the call it led to, and on the message only
      // what followed the last call; older ones keep it all on the message, shown first.
      const stepReasoning = (m.tool_calls ?? []).some((tc) => !!tc.reasoning);
      const thought = (text: string, key: string, at: string) =>
        logs.push({
          id: `msg-${sessionId}-${idx}-${key}`,
          role: "AGENT",
          content: "",
          reasoning: text,
          timestamp: at,
          isPersisted: true,
        });
      if (m.reasoning && !stepReasoning) thought(m.reasoning, "thought", timestamp);
      if (m.tool_calls && m.tool_calls.length > 0) {
        m.tool_calls.forEach((tc, tIdx) => {
          const toolTimestamp = formatTimestamp(tc.created_at) || timestamp;
          if (tc.reasoning) thought(tc.reasoning, `thought-${tIdx}`, toolTimestamp);
          logs.push({
            id: `msg-${sessionId}-${idx}-tool-${tIdx}`,
            role: "AGENT",
            content: `Executing tool: ${tc.name}`,
            toolCall: tc,
            timestamp: toolTimestamp,
          });
        });
      }
      if (m.reasoning && stepReasoning) thought(m.reasoning, "thought", timestamp);
      if (m.content) {
        logs.push({
          id: `msg-${sessionId}-${idx}-ans`,
          role: "AGENT",
          content: m.content,
          timestamp,
          isPersisted: true,
          answeredBy,
        });
      }
    }
  });
  return logs;
}
