import type {
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

export function convertHistoryToFlightLogs(
  history: NonNullable<SessionData["conversation_history"]>,
  sessionId: string
): FlightLogEntry[] {
  const logs: FlightLogEntry[] = [];
  history.forEach((m, idx) => {
    const roleUpper = m.role.toUpperCase();
    if (roleUpper === "USER") {
      logs.push({
        id: `msg-${sessionId}-${idx}-user`,
        role: "USER",
        content: m.content,
        timestamp: "",
        isPersisted: true,
        authorUserId: m.author_user_id ?? null,
      });
    } else if (roleUpper === "SYSTEM") {
      logs.push({
        id: `msg-${sessionId}-${idx}-sys`,
        role: "SYSTEM",
        content: m.content,
        timestamp: "",
      });
    } else {
      if (m.reasoning) {
        logs.push({
          id: `msg-${sessionId}-${idx}-thought`,
          role: "AGENT",
          content: "",
          reasoning: m.reasoning,
          timestamp: "",
          isPersisted: true,
        });
      }
      if (m.tool_calls && m.tool_calls.length > 0) {
        m.tool_calls.forEach((tc, tIdx) => {
          logs.push({
            id: `msg-${sessionId}-${idx}-tool-${tIdx}`,
            role: "AGENT",
            content: `Executing tool: ${tc.name}`,
            toolCall: tc,
            timestamp: "",
          });
        });
      }
      if (m.content) {
        logs.push({
          id: `msg-${sessionId}-${idx}-ans`,
          role: "AGENT",
          content: m.content,
          timestamp: "",
          isPersisted: true,
        });
      }
    }
  });
  return logs;
}
