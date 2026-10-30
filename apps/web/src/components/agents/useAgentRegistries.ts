"use client";

import { useEffect, useState } from "react";
import type { CustomSkillConfig, KnowledgeBaseConfig, McpServerConfig } from "../../types/mission";
import type { Credential } from "../settings/CredentialsPanel";

export interface CatalogTool {
  name: string;
  description: string;
  category: string;
  toolset: string | null;
  /** read, edit, command or publish: the approval the tool needs. */
  permission: string;
}

export interface SandboxType {
  id: string;
  name: string;
  is_default: boolean;
}

export interface AgentRegistries {
  tools: CatalogTool[];
  mcpServers: McpServerConfig[];
  skills: CustomSkillConfig[];
  knowledgeBases: KnowledgeBaseConfig[];
  sandboxes: SandboxType[];
  orgMaxTurns: number | null;
  /** Credentials this person may bind: the organization's and shared ones (admins), or their own. */
  credentials: Credential[];
}

const EMPTY: AgentRegistries = {
  tools: [],
  mcpServers: [],
  skills: [],
  knowledgeBases: [],
  sandboxes: [],
  orgMaxTurns: null,
  credentials: [],
};

async function json<T>(url: string, fallback: T): Promise<T> {
  try {
    const res = await fetch(url);
    return res.ok ? ((await res.json()) as T) : fallback;
  } catch {
    return fallback;
  }
}

/** Everything the agent editor offers to choose from, loaded once per page. */
export function useAgentRegistries(isAdmin: boolean): AgentRegistries {
  const [registries, setRegistries] = useState<AgentRegistries>(EMPTY);
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const [mcp, skills, kbs, tools, limits, sandboxes, mine, org] = await Promise.all([
        json<McpServerConfig[]>("/v1/mcp/servers", []),
        json<CustomSkillConfig[]>("/v1/skills", []),
        json<KnowledgeBaseConfig[]>("/v1/knowledge/bases", []),
        json<{ tools?: CatalogTool[] }>("/v1/tools", {}),
        json<{ max_turns?: number }>("/v1/agents/limits", {}),
        json<{ sandboxes?: SandboxType[] }>("/v1/sandboxes", {}),
        json<Credential[]>("/v1/user/credentials", []),
        isAdmin
          ? json<{ credentials?: Credential[]; shared_by_members?: Credential[] }>("/v1/admin/credentials", {})
          : Promise.resolve({} as { credentials?: Credential[]; shared_by_members?: Credential[] }),
      ]);
      if (cancelled) return;
      setRegistries({
        mcpServers: Array.isArray(mcp) ? mcp : [],
        skills: Array.isArray(skills) ? skills : [],
        knowledgeBases: Array.isArray(kbs) ? kbs : [],
        tools: tools.tools ?? [],
        orgMaxTurns: typeof limits.max_turns === "number" ? limits.max_turns : null,
        sandboxes: sandboxes.sandboxes ?? [],
        credentials: [...(org.credentials ?? []), ...(org.shared_by_members ?? []), ...(Array.isArray(mine) ? mine : [])],
      });
    })();
    return () => {
      cancelled = true;
    };
  }, [isAdmin]);
  return registries;
}

/** The name an MCP server's tool has inside a turn: ``mcp__<server slug>__<tool>`` (64 chars at
 * most), built exactly as the API does, so the agent's tool list matches what it runs with. */
export function mcpToolName(serverName: string, tool: string): string {
  const slug =
    serverName
      .replace(/[^a-zA-Z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "")
      .toLowerCase() || "server";
  return `mcp__${slug}__${tool.replace(/[^a-zA-Z0-9_-]/g, "_")}`.slice(0, 64);
}
