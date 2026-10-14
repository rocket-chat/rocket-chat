"use client";

import React from "react";
import {
  ChevronDown,
  ChevronRight,
  ExternalLink,
  Globe,
  Pencil,
  Radio,
  RefreshCw,
  Server,
  Terminal,
  Trash2,
  Zap,
} from "lucide-react";
import { McpMemberEnv } from "./McpMemberEnv";
import { McpRunnerStatus, McpServerConfig } from "../../../types/mission";

function TransportIcon({ transport: t }: { transport: string }) {
  switch (t) {
    case "stdio":
      return <Terminal className="w-3.5 h-3.5 text-amber-400" />;
    case "sse":
      return <Radio className="w-3.5 h-3.5 text-cyan" />;
    case "streamable_http":
    case "http":
      return <Globe className="w-3.5 h-3.5 text-blue-400" />;
    case "websocket":
      return <Zap className="w-3.5 h-3.5 text-emerald-400" />;
    default:
      return <Server className="w-3.5 h-3.5 text-neutral-400" />;
  }
}

interface McpServerCardProps {
  server: McpServerConfig;
  canManage: boolean;
  expanded: boolean;
  probing: boolean;
  onToggle: () => void;
  onProbe: (id: string) => void;
  onDelete: (id: string, name: string) => void;
  /** Open the edit form. */
  onEdit: (server: McpServerConfig) => void;
  onAuthorize: (id: string) => void;
  /** The MCP runner, for command servers (null while unknown). */
  runner: McpRunnerStatus | null;
  /** The member's own environment values changed. */
  onMemberEnvChanged: () => void;
}

/** One connected MCP server: its status, tools and the actions its owner may take. */
export const McpServerCard: React.FC<McpServerCardProps> = ({
  server,
  canManage,
  expanded: isExpanded,
  probing,
  onToggle,
  onProbe,
  onDelete,
  onEdit,
  onAuthorize,
  runner,
  onMemberEnvChanged,
}) => {
  const isOAuth = server.auth_type === "oauth2";

  return (
    <div
      className="rounded-lg border border-border bg-surface-card hover:border-brand/30 transition-all overflow-hidden"
    >
      <div className="p-3.5 flex items-center justify-between gap-3">
        <div
          className="flex items-center gap-3 cursor-pointer flex-1 min-w-0"
          onClick={() => onToggle()}
        >
          <div className="p-2 rounded-lg bg-surface-elevated border border-border shrink-0">
            <TransportIcon transport={server.transport} />
          </div>
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <span className="text-xs font-semibold text-foreground truncate">
                {server.name}
              </span>
              <span className="px-1.5 py-0.2 rounded-lg text-[9px] bg-surface border border-border text-neutral-400">
                {server.transport}
              </span>
              <span
                className={`inline-flex items-center gap-1 px-1.5 py-0.2 rounded-lg text-[9px] ${
                  server.status === "connected"
                    ? "bg-emerald-500/10 text-emerald-400 border border-emerald-500/20"
                    : server.status === "error"
                    ? "bg-rose-500/10 text-rose-400 border border-rose-500/20"
                    : "bg-neutral-500/10 text-neutral-400 border border-neutral-500/20"
                }`}
              >
                <span
                  className={`w-1 h-1 rounded-full ${
                    server.status === "connected" ? "bg-emerald-400" : "bg-neutral-400"
                  }`}
                />
                {server.status}
              </span>
            </div>
            <p className="text-[10px] text-muted-foreground truncate mt-0.5">
              {server.endpoint_or_command}
            </p>
          </div>
        </div>

        <div className="flex items-center gap-2 shrink-0">
          {isOAuth && (canManage || server.oauth_mode === "member") && (
            <button
              type="button"
              onClick={() => onAuthorize(server.id)}
              className="px-2 py-1 rounded-lg border border-brand/30 bg-brand/10 hover:bg-brand/20 text-brand text-[10px] font-semibold transition-all flex items-center gap-1 cursor-pointer"
              title="Authenticate via OAuth 2.1 PKCE"
            >
              <ExternalLink className="w-3 h-3" />
              <span>
                {server.oauth_mode === "member"
                  ? server.oauth_status === "connected"
                    ? "REConnect my account"
                    : "Connect my account"
                  : "OAUTH CONNECT"}
              </span>
            </button>
          )}

          <button
            type="button"
            onClick={() => onProbe(server.id)}
            disabled={probing}
            className="px-2 py-1 rounded-lg border border-border bg-surface-elevated hover:bg-surface-elevated/80 text-[10px] text-neutral-300 transition-all flex items-center gap-1 cursor-pointer disabled:opacity-50"
            title="Refresh / probe active tools"
          >
            <RefreshCw
              className={`w-3 h-3 ${probing ? "animate-spin text-brand" : ""}`}
            />
            <span>{server.tools_count ?? 0} TOOLS</span>
          </button>

          {canManage && !server.is_builtin && (
            <button
              type="button"
              onClick={() => onEdit(server)}
              className="p-1 rounded-lg hover:bg-surface-elevated text-neutral-500 hover:text-foreground transition-colors cursor-pointer"
              title="Edit server"
              aria-label={`Edit ${server.name}`}
            >
              <Pencil className="w-3.5 h-3.5" />
            </button>
          )}

          {canManage && (
            <button
              type="button"
              onClick={() => onDelete(server.id, server.name)}
              className="p-1 rounded-lg hover:bg-rose-950/40 text-neutral-500 hover:text-rose-400 transition-colors cursor-pointer"
              title="Remove server"
            >
              <Trash2 className="w-3.5 h-3.5" />
            </button>
          )}

          <button
            type="button"
            onClick={() => onToggle()}
            aria-label={isExpanded ? "Collapse server details" : "Expand server details"}
            aria-expanded={isExpanded}
            className="p-1 rounded-lg hover:bg-surface-elevated text-neutral-400 cursor-pointer"
          >
            {isExpanded ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
          </button>
        </div>
      </div>

      {isExpanded && (
        <div className="px-4 py-3 border-t border-border bg-surface/40 space-y-3">
          {server.transport === "stdio" && (
            <div data-testid="command-details" className="space-y-1.5 text-xs text-neutral-600 dark:text-neutral-300">
              <p>
                Command server: started on the MCP runner for each turn that uses it and stopped when the turn ends.
                {server.scope === "org" && (server.overridable_env?.length ?? 0) > 0 &&
                  ` Members can set their own ${server.overridable_env?.join(", ")}.`}
              </p>
              {runner && !runner.available && (
                <p role="status" className="rounded-lg border border-amber-500/40 bg-amber-500/10 p-2 text-amber-700 dark:text-amber-300">
                  {runner.detail} Its tools are unavailable until the runner is.
                </p>
              )}
              {canManage && server.install_command && (
                <pre className="max-h-32 overflow-auto rounded-lg border border-border/60 bg-surface-card p-2 font-mono text-[11px]">
                  {server.install_command}
                </pre>
              )}
            </div>
          )}
          {server.transport === "stdio" && server.scope === "org" && (server.overridable_env?.length ?? 0) > 0 && (
            <McpMemberEnv serverId={server.id} onChanged={onMemberEnvChanged} />
          )}
          {server.guidance && (
            <div>
              <span className="text-xs font-medium text-neutral-600 dark:text-neutral-300">
                Custom Agent Guidance:
              </span>
              <p className="text-xs text-neutral-300 mt-0.5 bg-surface-card p-2 rounded-lg border border-border/60">
                {server.guidance}
              </p>
            </div>
          )}

          <div>
            <div className="text-xs font-medium text-neutral-600 dark:text-neutral-300 mb-1.5">
              Exposed Tool Schemas ({server.tools?.length || 0}):
            </div>
            {server.tools && server.tools.length > 0 ? (
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 max-h-48 overflow-y-auto">
                {server.tools.map((t, idx) => (
                  <div
                    key={idx}
                    className="p-2 rounded-lg bg-surface-card/80 border border-border/60 text-xs space-y-0.5"
                  >
                    <div className="font-semibold text-brand flex items-center gap-1.5">
                      <Zap className="w-3 h-3 text-brand" />
                      <span>{t.name}</span>
                    </div>
                    <p className="text-[10px] text-muted-foreground line-clamp-2">
                      {t.description || "No description provided"}
                    </p>
                    {t.parameters_summary && (
                      <div className="text-[9px] text-neutral-400 truncate">
                        {t.parameters_summary}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            ) : (
              <div className="text-xs text-neutral-500 py-1 italic">
                No tools currently indexed. Run live probe to sync.
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
};
