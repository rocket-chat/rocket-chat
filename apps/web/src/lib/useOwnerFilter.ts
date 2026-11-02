"use client";

import { useCallback, useEffect } from "react";
import { usePathname, useRouter } from "next/navigation";
import { useMissionStore } from "./store";
import { useAccess } from "./access";
import { useTeams } from "./teams";
import { filterTeams, parseOwnerFilter, teamIdOf, type OwnerFilter } from "./ownership";

const STORAGE_PREFIX = "rocket.settings.owner";

function remember(key: string, value: OwnerFilter): void {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    // Private windows and blocked site data: the filter then lasts until a reload.
  }
}

function recall(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

/** The settings' owner filter and its setter: the URL (`?owner=`) is the truth, the browser
 * remembers the last choice per organization for a page opened without one. */
export function useOwnerFilter(): [OwnerFilter, (owner: OwnerFilter) => void] {
  const owner = useMissionStore((s) => s.settingsOwner);
  const setOwnerInStore = useMissionStore((s) => s.setSettingsOwner);
  const router = useRouter();
  const pathname = usePathname();
  const { orgId } = useAccess();

  const setOwner = useCallback(
    (next: OwnerFilter) => {
      setOwnerInStore(next);
      if (orgId) remember(`${STORAGE_PREFIX}.${orgId}`, next);
      const params = new URLSearchParams(window.location.search);
      if (next === "all") params.delete("owner");
      else params.set("owner", next);
      const query = params.toString();
      router.replace(query ? `${pathname}?${query}` : pathname, { scroll: false });
    },
    [orgId, pathname, router, setOwnerInStore],
  );
  return [owner, setOwner];
}

/**
 * Keeps the store's filter in step with the URL on every settings page (mounted once, by the
 * navigation): a link with `?owner=` wins, a page opened without one gets the remembered choice,
 * and a team the person no longer sees falls back to everything.
 */
export function useOwnerFilterSync(): void {
  const pathname = usePathname();
  const access = useAccess();
  const { teams, loaded } = useTeams();
  const [owner, setOwner] = useOwnerFilter();
  const setOwnerInStore = useMissionStore((s) => s.setSettingsOwner);

  useEffect(() => {
    if (!access.loaded) return;
    const fromUrl = new URLSearchParams(window.location.search).get("owner");
    if (fromUrl !== null) {
      setOwnerInStore(parseOwnerFilter(fromUrl));
      return;
    }
    const remembered = parseOwnerFilter(recall(`${STORAGE_PREFIX}.${access.orgId}`));
    if (remembered !== "all") setOwner(remembered);
    else setOwnerInStore("all");
    // Only a navigation (or the identity arriving) re-reads the URL; the setter writes it itself.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pathname, access.loaded, access.orgId]);

  useEffect(() => {
    const teamId = teamIdOf(owner);
    if (loaded && teamId && !filterTeams(access, teams).some((t) => t.id === teamId)) setOwner("all");
  }, [access, loaded, owner, setOwner, teams]);
}
