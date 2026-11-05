import { useEffect, useState } from "react";
import type { AgentPersonaConfig } from "../types/mission";

/** One level's default agent, as `GET /v1/me/default-agent` reports it. */
export interface DefaultAgentLevel {
  agent_id: string | null;
  agent_name: string;
  /** "member", "team:<id>", "org", or "platform" (none applies). */
  source: string;
  /** Levels whose agent the member may no longer use, skipped. */
  skipped: string[];
}

export interface DefaultAgent extends DefaultAgentLevel {
  /** The member's only team, whose default counts; null with several teams or none. */
  team_id: string | null;
  locked: boolean;
  own_agent_id: string | null;
  /** What applies without the member's own choice. */
  inherited: DefaultAgentLevel;
}

/** The agent the member's new sessions start with, or null when the API is unreachable. */
export async function fetchDefaultAgent(signal?: AbortSignal): Promise<DefaultAgent | null> {
  try {
    const res = await fetch("/v1/me/default-agent", { signal });
    return res.ok ? ((await res.json()) as DefaultAgent) : null;
  } catch {
    return null;
  }
}

/** The agents a default may name: enabled ones, and no personal agent for an organization's
 * or a team's default (it would serve its owner only). */
export function useDefaultAgentChoices(includePersonal: boolean): { agents: AgentPersonaConfig[]; loaded: boolean } {
  const [agents, setAgents] = useState<AgentPersonaConfig[]>([]);
  const [loaded, setLoaded] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    fetch("/v1/agents", { signal: controller.signal })
      .then((res) => (res.ok ? res.json() : []))
      .then((list: AgentPersonaConfig[]) => {
        if (Array.isArray(list)) setAgents(list);
        setLoaded(true);
      })
      .catch(() => undefined);
    return () => controller.abort();
  }, []);
  return {
    agents: agents.filter((a) => a.enabled !== false && (includePersonal || (a.scope !== "user" && !a.user_id))),
    loaded,
  };
}
