"use client";

import { useEffect, useState } from "react";

/** A capability an organization grants per role (`access` settings); the API enforces each. */
export type Capability =
  | "create_personal_agents"
  | "bring_own_keys"
  | "use_bypass_mode"
  | "register_mcp"
  | "publish_as_bot"
  | "manage_spend_limits";

export interface Access {
  orgId: string;
  orgName: string;
  role: string;
  roles: string[];
  capabilities: Capability[];
  isAdmin: boolean;
  canWrite: boolean;
  loaded: boolean;
}

const NONE: Access = {
  orgId: "",
  orgName: "",
  role: "viewer",
  roles: [],
  capabilities: [],
  isAdmin: false,
  canWrite: false,
  loaded: false,
};

let cached: Access | null = null;
let pending: Promise<Access> | null = null;
const listeners = new Set<(access: Access) => void>();

interface AccessResponse {
  org_id: string;
  org_name: string;
  role: string;
  roles: string[];
  capabilities: Capability[];
}

async function fetchAccess(): Promise<Access> {
  try {
    const res = await fetch("/v1/me/access", { cache: "no-store" });
    if (!res.ok) return { ...NONE, loaded: true };
    const data = (await res.json()) as AccessResponse;
    const roles = data.roles ?? [];
    return {
      orgId: data.org_id,
      orgName: data.org_name,
      role: data.role,
      roles,
      capabilities: data.capabilities ?? [],
      isAdmin: roles.includes("admin") || roles.includes("owner"),
      canWrite: roles.some((r) => ["owner", "admin", "member", "guest"].includes(r)),
      loaded: true,
    };
  } catch {
    return { ...NONE, loaded: true };
  }
}

/** Forget the cached access (after switching organization or a role change). */
export function refreshAccess(): Promise<Access> {
  pending = fetchAccess().then((access) => {
    cached = access;
    pending = null;
    listeners.forEach((listener) => listener(access));
    return access;
  });
  return pending;
}

/** The caller's role and capabilities in the active organization, from the server. */
export function useAccess(): Access {
  const [access, setAccess] = useState<Access>(cached ?? NONE);
  useEffect(() => {
    listeners.add(setAccess);
    if (cached) setAccess(cached);
    else void (pending ?? refreshAccess());
    return () => {
      listeners.delete(setAccess);
    };
  }, []);
  return access;
}

export function hasCapability(access: Access, capability: Capability): boolean {
  return access.capabilities.includes(capability);
}
