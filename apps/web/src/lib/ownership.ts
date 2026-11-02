import type { Access } from "./access";
import type { Team } from "./teams";

/** Who owns an object: a member (`user`), a team of the organization, or the organization. */
export type OwnerScope = "user" | "team" | "org";

/** What every owned object carries (agents, skills, MCP servers, knowledge bases). */
export interface Owned {
  scope?: string | null;
  user_id?: string | null;
  team_id?: string | null;
}

/** One owner, as the settings name it: `me`, `org` or `team:<id>`. */
export type OwnerKey = "me" | "org" | `team:${string}`;

/** The settings' owner filter: every owner, or one of them. */
export type OwnerFilter = "all" | OwnerKey;

export function ownerKey(owned: Owned): OwnerKey {
  if (owned.scope === "team" && owned.team_id) return `team:${owned.team_id}`;
  if (owned.scope === "user" || owned.user_id) return "me";
  return "org";
}

export function matchesOwner(owned: Owned, filter: OwnerFilter): boolean {
  return filter === "all" || ownerKey(owned) === filter;
}

export function parseOwnerFilter(raw: string | null | undefined): OwnerFilter {
  if (raw === "me" || raw === "org") return raw;
  if (raw && raw.startsWith("team:") && raw.length > 5) return raw as OwnerKey;
  return "all";
}

export function teamIdOf(key: OwnerFilter): string | null {
  return key.startsWith("team:") ? key.slice(5) : null;
}

/** The stored fields of an owner. */
export function ownerFields(key: OwnerKey): { scope: OwnerScope; team_id: string | null } {
  if (key === "me") return { scope: "user", team_id: null };
  if (key === "org") return { scope: "org", team_id: null };
  return { scope: "team", team_id: key.slice(5) };
}

export function ownerLabel(key: OwnerKey, teams: Team[]): string {
  if (key === "me") return "You";
  if (key === "org") return "Organization";
  return teams.find((t) => t.id === key.slice(5))?.name ?? "A team";
}

/** Who can use an object of this owner, in words. */
export function audienceOf(key: OwnerKey, teams: Team[]): string {
  if (key === "me") return "only you";
  if (key === "org") return "everyone in the organization";
  return `the members of the team ${ownerLabel(key, teams)}`;
}

/** The teams the owner filter offers: every team to an administrator, else the person's own. */
export function filterTeams(access: Access, teams: Team[]): Team[] {
  return access.isAdmin ? teams : teams.filter((t) => t.my_role !== null);
}

/**
 * The owners the person may create an object for: the organization when they administer it
 * (first: an administrator's new object is the organization's unless they choose), themselves
 * (when ``personal`` allows it), and each team they manage.
 */
export function creatableOwners(access: Access, teams: Team[], personal: boolean): OwnerKey[] {
  if (!access.canWrite) return [];
  const keys: OwnerKey[] = access.isAdmin ? ["org"] : [];
  if (personal) keys.push("me");
  teams.filter((t) => t.can_manage).forEach((t) => keys.push(`team:${t.id}`));
  return keys;
}

/** Whether the person may change an object of this owner (the API decides; this hides buttons). */
export function canManageOwner(access: Access, teams: Team[], key: OwnerKey): boolean {
  if (!access.canWrite) return false;
  if (access.isAdmin) return true;
  if (key === "me") return true;
  if (key === "org") return false;
  return teams.some((t) => t.id === key.slice(5) && t.can_manage);
}

/** The owner a "New" button starts with: the filter's when the person may create there. */
export function defaultOwner(filter: OwnerFilter, creatable: OwnerKey[]): OwnerKey | null {
  if (filter !== "all" && creatable.includes(filter)) return filter;
  return creatable[0] ?? null;
}

/** Groups in display order: yours, each team (by name), then the organization's. Empty ones go. */
export function groupByOwner<T extends Owned>(items: T[], teams: Team[]): { key: OwnerKey; label: string; items: T[] }[] {
  const keys: OwnerKey[] = ["me", ...teams.map((t) => `team:${t.id}` as OwnerKey), "org"];
  const extra = items.map(ownerKey).filter((k) => !keys.includes(k));
  const order = [...keys.slice(0, -1), ...Array.from(new Set(extra)), "org" as OwnerKey];
  return order
    .map((key) => ({
      key,
      label: key === "me" ? "Yours" : ownerLabel(key, teams),
      items: items.filter((item) => ownerKey(item) === key),
    }))
    .filter((group) => group.items.length > 0);
}
