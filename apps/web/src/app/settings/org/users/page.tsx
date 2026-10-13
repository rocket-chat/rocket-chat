"use client";

import React, { useEffect, useState } from "react";
import { Users, ShieldCheck, AlertCircle, RefreshCw } from "lucide-react";
import { useSession } from "../../../../components/AuthProvider";

interface OrgUser {
  id: string;
  email: string;
  name: string;
  role: string;
  created_at: number;
}

const ROLES = ["owner", "admin", "developer", "readonly", "guest"] as const;

const ROLE_LABEL: Record<string, string> = {
  owner: "Owner",
  admin: "Admin",
  developer: "Developer",
  readonly: "Read-only",
  guest: "Guest",
};

export default function OrgUsersPage() {
  const { data: session } = useSession();
  const userRoles = (session?.user as unknown as { roles?: string[] })?.roles || [];
  const isAdmin = !session || userRoles.includes("admin") || userRoles.includes("owner");

  const [users, setUsers] = useState<OrgUser[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [savingId, setSavingId] = useState<string | null>(null);

  const fetchUsers = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch("/v1/admin/users");
      if (!res.ok) throw new Error(`Failed to load users (${res.status})`);
      const data = await res.json();
      setUsers(data.users || []);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load users");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void fetchUsers();
  }, []);

  const changeRole = async (userId: string, role: string) => {
    setSavingId(userId);
    setError(null);
    setNotice(null);
    try {
      const res = await fetch(`/v1/admin/users/${userId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ role }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || `Failed to update role (${res.status})`);
      setUsers((prev) => prev.map((u) => (u.id === userId ? { ...u, role } : u)));
      setNotice(data.note || "Role updated. The user must reload to apply it.");
      setTimeout(() => setNotice(null), 6000);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to update role");
      // Re-sync so the UI reflects the server's actual state after a rejected change.
      void fetchUsers();
    } finally {
      setSavingId(null);
    }
  };

  return (
    <div className="max-w-5xl mx-auto space-y-6 px-4 py-8 sm:px-8">
      <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4 pb-4 border-b border-border/70">
        <div>
          <h1 className="font-display text-xl font-semibold tracking-tight text-foreground flex items-center gap-2.5">
            <Users className="w-5 h-5 text-brand" />
            Users &amp; roles
          </h1>
          <p className="max-w-2xl text-sm leading-relaxed text-neutral-600 dark:text-neutral-400 mt-1">
            Manage the members of this organization and their access level.
          </p>
        </div>
        <button
          type="button"
          onClick={() => void fetchUsers()}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-border bg-surface-elevated hover:bg-surface-elevated/80 text-xs text-neutral-300 transition-all cursor-pointer"
        >
          <RefreshCw className={`w-3.5 h-3.5 ${loading ? "animate-spin" : ""}`} />
          <span>Refresh</span>
        </button>
      </div>

      {error && (
        <div className="flex items-center gap-2 p-3 rounded-lg bg-rose-950/60 border border-rose-800/60 text-rose-400 text-xs">
          <AlertCircle className="w-4 h-4 shrink-0" />
          <span>{error}</span>
        </div>
      )}
      {notice && (
        <div
          data-testid="role-change-notice"
          className="flex items-center gap-2 p-3 rounded-lg bg-amber-950/50 border border-amber-800/60 text-amber-300 text-xs"
        >
          <ShieldCheck className="w-4 h-4 shrink-0" />
          <span>{notice}</span>
        </div>
      )}

      <div className="rounded-lg border border-border bg-surface-card overflow-hidden">
        <table className="w-full text-xs">
          <thead className="bg-surface/60 text-muted-foreground text-[10px]">
            <tr>
              <th className="text-left px-4 py-2">Member</th>
              <th className="text-left px-4 py-2">Email</th>
              <th className="text-left px-4 py-2 w-48">Role</th>
            </tr>
          </thead>
          <tbody>
            {loading && users.length === 0 ? (
              <tr>
                <td colSpan={3} className="px-4 py-6 text-center text-muted-foreground">
                  Loading members…
                </td>
              </tr>
            ) : users.length === 0 ? (
              <tr>
                <td colSpan={3} className="px-4 py-6 text-center text-muted-foreground">
                  No members found.
                </td>
              </tr>
            ) : (
              users.map((user) => (
                <tr key={user.id} className="border-t border-border/50">
                  <td className="px-4 py-2 text-foreground">{user.name || "—"}</td>
                  <td className="px-4 py-2 text-muted-foreground">{user.email}</td>
                  <td className="px-4 py-2">
                    <select
                      data-testid={`user-role-${user.id}`}
                      disabled={!isAdmin || savingId === user.id}
                      value={user.role}
                      onChange={(e) => void changeRole(user.id, e.target.value)}
                      className="w-full px-2 py-1 rounded-lg border border-border bg-void text-xs text-foreground focus:outline-none focus:border-cyan disabled:opacity-60 cursor-pointer"
                    >
                      {ROLES.map((role) => (
                        <option key={role} value={role}>
                          {ROLE_LABEL[role] ?? role}
                        </option>
                      ))}
                    </select>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      <p className="text-[11px] text-muted-foreground">
        A role change is written immediately but is carried in the user&apos;s session token, so
        the affected user must <span className="text-foreground">reload or sign in again</span>{" "}
        for it to take effect. The last administrator of an organization cannot be demoted.
      </p>
    </div>
  );
}
