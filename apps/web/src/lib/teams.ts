"use client";

import { useEffect, useState } from "react";

/** A team of the organization, with the signed-in member's place in it. */
export interface Team {
  id: string;
  name: string;
  description: string;
  member_count: number;
  my_role: "admin" | "member" | null;
  /** Whether the member may rename it and manage its members and objects. */
  can_manage: boolean;
}

export interface TeamsState {
  teams: Team[];
  loaded: boolean;
}

const NONE: TeamsState = { teams: [], loaded: false };

let cached: TeamsState | null = null;
let pending: Promise<TeamsState> | null = null;
const listeners = new Set<(state: TeamsState) => void>();

async function fetchTeams(): Promise<TeamsState> {
  try {
    const res = await fetch("/v1/teams", { cache: "no-store" });
    if (!res.ok) return { teams: [], loaded: true };
    const body = (await res.json()) as { teams?: Team[] };
    return { teams: body.teams ?? [], loaded: true };
  } catch {
    return { teams: [], loaded: true };
  }
}

/** Read the teams again (after creating, renaming or joining one). */
export function refreshTeams(): Promise<TeamsState> {
  pending = fetchTeams().then((state) => {
    cached = state;
    pending = null;
    listeners.forEach((listener) => listener(state));
    return state;
  });
  return pending;
}

/** The organization's teams, shared by every settings component that names owners. */
export function useTeams(): TeamsState {
  const [state, setState] = useState<TeamsState>(cached ?? NONE);
  useEffect(() => {
    listeners.add(setState);
    if (cached) setState(cached);
    else void (pending ?? refreshTeams());
    return () => {
      listeners.delete(setState);
    };
  }, []);
  return state;
}
