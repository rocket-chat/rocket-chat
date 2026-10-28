import type { ArtifactItem, ToolCallData } from "../types/mission";

const ARTIFACT_TOOLS = new Set(["create_artifact", "update_artifact"]);

/** A deliverable the conversation shows as a card: what a call made, or what an event reported. */
export interface ArtifactMention {
  /** Known once the call has answered (`art_…`). */
  id: string | null;
  title: string;
  filename: string;
  version: number | null;
  change: "created" | "updated";
  state: "saving" | "saved" | "failed";
}

export const isArtifactCall = (call: ToolCallData | undefined): call is ToolCallData =>
  !!call && ARTIFACT_TOOLS.has(call.name);

const text = (value: unknown): string => (typeof value === "string" ? value : "");

/** Reads the tool's own answer ("- Artifact ID: art_…", "(version 2)", "- Version: 2"). */
function fromResult(result: string): { id: string | null; title: string; version: number | null } {
  const id = /Artifact ID:\s*(art_[A-Za-z0-9]+)/.exec(result)?.[1] ?? null;
  const title = /^Artifact '(.+)' (?:created|updated) successfully/m.exec(result)?.[1] ?? "";
  const version = /\(version (\d+)\)|Version:\s*(\d+)/.exec(result);
  return { id, title, version: version ? Number(version[1] ?? version[2]) : null };
}

/** What an artifact call shows, from its arguments and result (and the session's artifacts for an update). */
export function describeArtifactCall(call: ToolCallData, known: ArtifactItem[]): ArtifactMention {
  const args = call.arguments ?? {};
  const result = text(call.result);
  const parsed = fromResult(result);
  const failed = call.status === "error" || result.startsWith("Error") || /could not be/.test(result);
  const id = parsed.id ?? (text(args.artifact_id) || null);
  const stored = id ? known.find((artifact) => artifact.id === id) : undefined;
  return {
    id,
    title: text(args.title) || parsed.title || stored?.title || "Artifact",
    filename: text(args.filename) || stored?.filename || "",
    version: parsed.version ?? stored?.version ?? null,
    change: call.name === "update_artifact" ? "updated" : "created",
    state: call.status === "pending" ? "saving" : failed ? "failed" : "saved",
  };
}

/** What an `artifact_created` / `artifact_updated` event shows when no call of this conversation made it. */
export function mentionOfArtifact(artifact: ArtifactItem, change: "created" | "updated"): ArtifactMention {
  return {
    id: artifact.id,
    title: artifact.title,
    filename: artifact.filename,
    version: artifact.version,
    change,
    state: "saved",
  };
}
