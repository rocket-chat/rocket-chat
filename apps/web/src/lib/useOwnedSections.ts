"use client";

import { useAccess, type Access } from "./access";
import { useTeams, type Team } from "./teams";
import { useOwnerFilter } from "./useOwnerFilter";
import { creatableOwners, defaultOwner, filterTeams, ownerKey, type Owned, type OwnerFilter, type OwnerKey } from "./ownership";

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
