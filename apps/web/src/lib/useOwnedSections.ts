"use client";

import { useAccess, type Access } from "./access";
import { useTeams, type Team } from "./teams";
import { useOwnerFilter } from "./useOwnerFilter";
import { creatableOwners, defaultOwner, filterTeams, ownerKey, teamIdOf, type Owned, type OwnerFilter, type OwnerKey } from "./ownership";

export interface TeamSection<T> {
  team: Team;
  key: OwnerKey;
  items: T[];
  canManage: boolean;
}

export interface OwnedSections<T> {
  access: Access;
  teams: Team[];
  filter: OwnerFilter;
  /** The person's own objects, or null when the filter names another owner. */
  mine: T[] | null;
  /** One section per team the filter shows that owns something or that the person manages. */
  teamSections: TeamSection<T>[];
  /** The organization's objects, or null when the filter names another owner. */
  org: T[] | null;
  /** The owners the person may create for, and the one a "New" button starts with. */
  creatable: OwnerKey[];
  startOwner: OwnerKey | null;
}

/** Splits a page's objects by owner, as the owner filter shows them. ``personal``: whether the
 * person may own one of these themselves (a capability for some kinds). */
export function useOwnedSections<T extends Owned>(items: T[], personal = true): OwnedSections<T> {
  const access = useAccess();
  const { teams } = useTeams();
  const [filter] = useOwnerFilter();
  const shows = (key: OwnerKey) => filter === "all" || filter === key;
  const teamSections = filterTeams(access, teams)
    .map((team) => {
      const key: OwnerKey = `team:${team.id}`;
      return { team, key, items: items.filter((item) => ownerKey(item) === key), canManage: team.can_manage && access.canWrite };
    })
    .filter((section) => shows(section.key) && (section.items.length > 0 || section.canManage || filter === section.key));
  const creatable = creatableOwners(access, teams, personal);
  return {
    access,
    teams,
    filter,
    mine: shows("me") ? items.filter((item) => ownerKey(item) === "me") : null,
    teamSections,
    org: shows("org") ? items.filter((item) => ownerKey(item) === "org") : null,
    creatable,
    startOwner: defaultOwner(filter, creatable),
  };
}

export interface ManagedScopes {
  access: Access;
  /** Whether the organization's own part of the page is shown (administrators). */
  showOrg: boolean;
  /** The teams whose part is shown: the one the filter names, or every team the person manages. */
  teams: Team[];
  /** The team the filter names when the person does not manage it (the page says so). */
  unmanaged: Team | null;
}

/** For pages an organization configures and teams configure their own part of (keys, limits):
 * which parts the owner filter shows to this person. */
export function useManagedScopes(): ManagedScopes {
  const access = useAccess();
  const { teams } = useTeams();
  const [filter] = useOwnerFilter();
  const managed = access.canWrite ? teams.filter((t) => t.can_manage) : [];
  const named = teamIdOf(filter);
  if (named) {
    const team = managed.find((t) => t.id === named);
    return { access, showOrg: false, teams: team ? [team] : [], unmanaged: team ? null : (teams.find((t) => t.id === named) ?? null) };
  }
  return { access, showOrg: access.isAdmin, teams: filter === "org" ? [] : managed, unmanaged: null };
}
