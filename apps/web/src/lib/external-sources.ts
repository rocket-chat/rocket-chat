import type { OwnerKey, OwnerScope } from "./ownership";
import { ownerFields } from "./ownership";

/** A repository connected as a source of skills (`/v1/external-sources`). */
export interface ExternalSource {
  id: string;
  name: string;
  repository: string;
  ref: string;
  /** `app`: read with the organization's GitHub App; `member`: with its owner's own account. */
  auth: "app" | "member";
  scope: OwnerScope;
  user_id: string | null;
  team_id: string | null;
  status: "pending" | "ok" | "error";
  last_commit: string;
  last_synced_at: number | null;
  last_error: string;
  url: string;
  has_manifest: boolean;
  plugin_count: number;
  skill_count: number;
  imported_count: number;
  can_manage: boolean;
  notes: string[];
}

export interface CatalogueImport {
  skill_id: string;
  scope: OwnerScope;
  team_id: string | null;
  user_id: string | null;
  status: string;
}

export interface CatalogueEntry {
  path: string;
  name: string;
  description: string;
  plugins: string[];
  files: string[];
  problem: string;
  url: string;
  imported: CatalogueImport[];
}

export interface CataloguePlugin {
  name: string;
  description: string;
  entries: string[];
}

export interface Catalogue {
  source: ExternalSource;
  commit: string;
  has_manifest: boolean;
  plugins: CataloguePlugin[];
  entries: CatalogueEntry[];
  notes: string[];
}

export interface ImportConflict {
  path: string;
  name: string;
  skill_id: string;
}

export interface ImportResult {
  imported: { id: string; name: string }[];
  conflicts: ImportConflict[];
}

async function detailOf(res: Response, fallback: string): Promise<string> {
  const body = (await res.json().catch(() => ({}))) as { detail?: unknown };
  return typeof body.detail === "string" ? body.detail : fallback;
}

export async function listSources(): Promise<ExternalSource[]> {
  const res = await fetch("/v1/external-sources");
  return res.ok ? ((await res.json()) as ExternalSource[]) : [];
}

export async function fetchCatalogue(sourceId: string): Promise<Catalogue> {
  const res = await fetch(`/v1/external-sources/${sourceId}/catalogue`);
  if (!res.ok) throw new Error(await detailOf(res, "The source could not be read."));
  return (await res.json()) as Catalogue;
}

export async function importSkills(
  sourceId: string,
  paths: string[],
  owner: OwnerKey,
  merge: string[],
): Promise<ImportResult> {
  const res = await fetch(`/v1/external-sources/${sourceId}/import`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ paths, merge, ...ownerFields(owner) }),
  });
  if (!res.ok) throw new Error(await detailOf(res, "The skills were not imported."));
  return (await res.json()) as ImportResult;
}

export async function sendJson<T>(url: string, method: string, body?: unknown): Promise<T> {
  const res = await fetch(url, {
    method,
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) throw new Error(await detailOf(res, "The request failed."));
  return (await res.json()) as T;
}

/** Whether skills of this source may go to this owner: a source read with a member's own
 * account only feeds that member's personal skills. */
export function feeds(source: ExternalSource, owner: OwnerKey): boolean {
  return source.auth !== "member" || owner === "me";
}

export function shortCommit(commit: string): string {
  return commit.slice(0, 7);
}
