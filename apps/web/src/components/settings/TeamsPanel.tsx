"use client";

import React, { useCallback, useEffect, useState } from "react";
import { LogOut, Plus, Search, Trash2, UserPlus, Users } from "lucide-react";
import { Button } from "../ui/Button";
import { EmptyState } from "../ui/EmptyState";
import { Field, Input, Select } from "../ui/Field";
import { Badge } from "../ui/Badge";
import { useConfirm } from "../ui/ConfirmDialog";
import { useAccess } from "../../lib/access";
import { useAuth } from "../../lib/auth-context";
import { refreshTeams, useTeams, type Team } from "../../lib/teams";
import { filterTeams, teamIdOf } from "../../lib/ownership";
import { useOwnerFilter } from "../../lib/useOwnerFilter";

interface TeamMember {
  user_id: string;
  email: string;
  name: string;
  role: string;
}

const OWNED_LABEL: Record<string, string> = {
  agents: "agents",
  skills: "skills",
  mcp_servers: "MCP servers",
  knowledge_bases: "knowledge bases",
};

async function detailOf(res: Response, fallback: string): Promise<string> {
  const body = await res.json().catch(() => ({}));
  return typeof body.detail === "string" ? body.detail : fallback;
}

/**
 * The organization's teams. Administrators create and delete them; a team's administrators
 * rename it and choose its members; anyone in a team sees who else is and may leave it.
 */
export const TeamsPanel: React.FC = () => {
  const access = useAccess();
  const { teams, loaded } = useTeams();
  const [owner] = useOwnerFilter();
  const listed = filterTeams(access, teams);
  const [selected, setSelected] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [error, setError] = useState<string | null>(null);

  // The owner filter names a team: open it.
  useEffect(() => {
    const fromFilter = teamIdOf(owner);
    if (fromFilter) setSelected(fromFilter);
  }, [owner]);

  const current = listed.find((t) => t.id === selected) ?? null;

  const create = async (event: React.FormEvent) => {
    event.preventDefault();
    setError(null);
    const res = await fetch("/v1/teams", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, description }),
    });
    if (!res.ok) {
      setError(await detailOf(res, "The team was not created."));
      return;
    }
    const created = (await res.json()) as Team;
    setName("");
    setDescription("");
    await refreshTeams();
    setSelected(created.id);
  };

  return (
    <div className="grid grid-cols-1 gap-6 lg:grid-cols-[18rem_minmax(0,1fr)]" data-testid="teams-panel">
      <div className="space-y-4">
        {access.isAdmin && (
          <form onSubmit={(e) => void create(e)} className="space-y-3 rounded-xl border border-surface-border bg-surface-card p-3" aria-label="New team">
            <Field label="New team">
              {(control) => (
                <Input {...control} data-testid="team-new-name" placeholder="Platform" maxLength={80} value={name} onChange={(e) => setName(e.target.value)} />
              )}
            </Field>
            <Field label="Description">
              {(control) => (
                <Input {...control} data-testid="team-new-description" maxLength={255} value={description} onChange={(e) => setDescription(e.target.value)} />
              )}
            </Field>
            <Button type="submit" size="sm" variant="primary" data-testid="team-create" disabled={!name.trim()}>
              <Plus className="h-3.5 w-3.5" aria-hidden="true" /> Create team
            </Button>
            {error && (
              <p role="alert" className="text-xs text-rose-600 dark:text-rose-400">
                {error}
              </p>
            )}
          </form>
        )}
        <ul className="space-y-1.5" aria-label="Teams">
          {loaded && listed.length === 0 && (
            <li className="text-xs text-neutral-600 dark:text-neutral-400">
              {access.isAdmin ? "No team yet." : "You are not in a team."}
            </li>
          )}
          {listed.map((team) => (
            <li key={team.id}>
              <button
                type="button"
                data-testid={`team-item-${team.id}`}
                aria-current={team.id === selected ? "true" : undefined}
                onClick={() => setSelected(team.id)}
                className={`w-full rounded-lg border p-2.5 text-left text-xs transition-colors ${team.id === selected ? "border-brand bg-brand/5" : "border-border/70 hover:bg-surface-elevated"}`}
              >
                <span className="flex items-center justify-between gap-1">
                  <span className="truncate font-semibold text-foreground">{team.name}</span>
                  {team.my_role && <Badge tone={team.my_role === "admin" ? "brand" : "neutral"}>{team.my_role === "admin" ? "Team admin" : "Member"}</Badge>}
                </span>
                <span className="mt-0.5 block text-[11px] text-neutral-600 dark:text-neutral-400">
                  {team.member_count} {team.member_count === 1 ? "member" : "members"}
                </span>
              </button>
            </li>
          ))}
        </ul>
      </div>
      <div className="min-w-0 rounded-xl border border-surface-border bg-surface-card p-4">
        {current ? (
          <TeamDetail key={current.id} team={current} onGone={() => setSelected(null)} />
        ) : (
          <EmptyState
            icon={<Users className="h-6 w-6" aria-hidden="true" />}
            title="Choose a team"
            description="A team owns agents, skills, MCP servers and knowledge bases that only its members use; its administrators manage them."
          />
        )}
      </div>
    </div>
  );
};

const TeamDetail: React.FC<{ team: Team; onGone: () => void }> = ({ team, onGone }) => {
  const access = useAccess();
  const { user } = useAuth();
  const [members, setMembers] = useState<TeamMember[]>([]);
  const [owned, setOwned] = useState<Record<string, number>>({});
  const [name, setName] = useState(team.name);
  const [description, setDescription] = useState(team.description);
  const [query, setQuery] = useState("");
  const [candidates, setCandidates] = useState<TeamMember[]>([]);
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirm, dialog] = useConfirm();
  const base = `/v1/teams/${encodeURIComponent(team.id)}`;

  const load = useCallback(async () => {
    const res = await fetch(`${base}/members`);
    if (!res.ok) return;
    const body = (await res.json()) as { members: TeamMember[]; owned?: Record<string, number> };
    setMembers(body.members ?? []);
    setOwned(body.owned ?? {});
  }, [base]);

  useEffect(() => {
    void load();
  }, [load]);

  const report = async (res: Response, done: string, fallback: string): Promise<boolean> => {
    if (res.ok) {
      setError(null);
      setStatus(done);
      return true;
    }
    setStatus(null);
    setError(await detailOf(res, fallback));
    return false;
  };

  const rename = async (event: React.FormEvent) => {
    event.preventDefault();
    const res = await fetch(base, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, description }),
    });
    if (await report(res, "Team saved.", "The team was not saved.")) await refreshTeams();
  };

  const search = async (event: React.FormEvent) => {
    event.preventDefault();
    const res = await fetch(`${base}/candidates?q=${encodeURIComponent(query)}`);
    const body = res.ok ? ((await res.json()) as { members: TeamMember[] }) : { members: [] };
    setCandidates(body.members ?? []);
    setStatus(body.members?.length ? null : "No member of the organization matches.");
  };

  const setRole = async (userId: string, role: string) => {
    const res = await fetch(`${base}/members/${encodeURIComponent(userId)}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ role }),
    });
    if (await report(res, "Member saved.", "The member was not saved.")) {
      setCandidates((list) => list.filter((c) => c.user_id !== userId));
      await Promise.all([load(), refreshTeams()]);
    }
  };

  const remove = async (member: TeamMember) => {
    const self = member.user_id === user?.user_id;
    const ok = await confirm({
      title: self ? `Leave ${team.name}?` : `Remove ${member.name || member.email || member.user_id}?`,
      message: self
        ? "You will no longer use the team's agents, skills, MCP servers and knowledge bases."
        : "They will no longer use the team's agents, skills, MCP servers and knowledge bases.",
      confirmLabel: self ? "Leave" : "Remove",
      tone: "danger",
    });
    if (!ok) return;
    const res = await fetch(`${base}/members/${encodeURIComponent(member.user_id)}`, { method: "DELETE" });
    if (await report(res, self ? "You left the team." : "Member removed.", "The member was not removed.")) {
      await Promise.all([load(), refreshTeams()]);
    }
  };

  const destroy = async () => {
    const ok = await confirm({
      title: `Delete ${team.name}?`,
      message: "A team that still owns agents, skills, MCP servers or knowledge bases is not deleted: move or delete them first.",
      confirmLabel: "Delete",
      tone: "danger",
    });
    if (!ok) return;
    const res = await fetch(base, { method: "DELETE" });
    if (await report(res, "Team deleted.", "The team was not deleted.")) {
      await refreshTeams();
      onGone();
    }
  };

  const ownedText = Object.entries(owned)
    .filter(([, count]) => count > 0)
    .map(([kind, count]) => `${count} ${OWNED_LABEL[kind] ?? kind}`)
    .join(", ");

  return (
    <div className="space-y-5" data-testid="team-detail">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h2 className="text-sm font-bold text-foreground" data-testid="team-detail-name">
            {team.name}
          </h2>
          <p className="text-xs text-neutral-600 dark:text-neutral-400" data-testid="team-owned">
            {ownedText ? `Owns ${ownedText}.` : "Owns nothing yet."}
          </p>
        </div>
        {access.isAdmin && (
          <Button size="sm" variant="danger" data-testid="team-delete" onClick={() => void destroy()}>
            <Trash2 className="h-3.5 w-3.5" aria-hidden="true" /> Delete team
          </Button>
        )}
      </div>

      {team.can_manage && (
        <form onSubmit={(e) => void rename(e)} className="grid gap-3 sm:grid-cols-[1fr_2fr_auto] sm:items-end" aria-label="Team details">
          <Field label="Name">
            {(control) => <Input {...control} data-testid="team-name" maxLength={80} value={name} onChange={(e) => setName(e.target.value)} />}
          </Field>
          <Field label="Description">
            {(control) => <Input {...control} maxLength={255} value={description} onChange={(e) => setDescription(e.target.value)} />}
          </Field>
          <Button type="submit" size="sm" data-testid="team-save" disabled={!name.trim()}>
            Save
          </Button>
        </form>
      )}

      {(status || error) && (
        <p role={error ? "alert" : "status"} data-testid="team-status" className={`text-xs ${error ? "text-rose-600 dark:text-rose-400" : "text-neutral-600 dark:text-neutral-400"}`}>
          {error ?? status}
        </p>
      )}

      <section aria-labelledby={`team-members-${team.id}`} className="space-y-2">
        <h3 id={`team-members-${team.id}`} className="text-xs font-semibold text-foreground">
          Members
        </h3>
        <ul className="divide-y divide-border/60 rounded-lg border border-border" data-testid="team-members">
          {members.length === 0 && <li className="px-3 py-2 text-xs text-neutral-600 dark:text-neutral-400">Nobody yet.</li>}
          {members.map((member) => {
            const self = member.user_id === user?.user_id;
            const who = member.name || member.email || member.user_id;
            return (
              <li key={member.user_id} className="flex flex-wrap items-center justify-between gap-2 px-3 py-2 text-xs" data-testid={`team-member-${member.user_id}`}>
                <span className="min-w-0">
                  <span className="block truncate text-foreground">{who}</span>
                  {member.email && member.email !== who && <span className="block truncate text-neutral-600 dark:text-neutral-400">{member.email}</span>}
                </span>
                <span className="flex items-center gap-2">
                  {team.can_manage ? (
                    <Select
                      aria-label={`Team role of ${who}`}
                      data-testid={`team-member-role-${member.user_id}`}
                      value={member.role}
                      onChange={(e) => void setRole(member.user_id, e.target.value)}
                      className="w-32"
                    >
                      <option value="member">Member</option>
                      <option value="admin">Team admin</option>
                    </Select>
                  ) : (
                    <Badge>{member.role === "admin" ? "Team admin" : "Member"}</Badge>
                  )}
                  {(team.can_manage || self) && (
                    <Button size="sm" variant="ghost" aria-label={self ? `Leave ${team.name}` : `Remove ${who}`} data-testid={`team-member-remove-${member.user_id}`} onClick={() => void remove(member)}>
                      {self ? <LogOut className="h-3.5 w-3.5" aria-hidden="true" /> : <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />}
                    </Button>
                  )}
                </span>
              </li>
            );
          })}
        </ul>
      </section>

      {team.can_manage && (
        <section aria-labelledby={`team-add-${team.id}`} className="space-y-2">
          <h3 id={`team-add-${team.id}`} className="text-xs font-semibold text-foreground">
            Add members
          </h3>
          <form onSubmit={(e) => void search(e)} className="flex items-end gap-2">
            <Field label="Find a member of the organization" className="flex-1">
              {(control) => (
                <Input {...control} data-testid="team-candidate-query" placeholder="Name or e-mail" value={query} onChange={(e) => setQuery(e.target.value)} />
              )}
            </Field>
            <Button type="submit" size="md" data-testid="team-candidate-search">
              <Search className="h-3.5 w-3.5" aria-hidden="true" /> Search
            </Button>
          </form>
          {candidates.length > 0 && (
            <ul className="divide-y divide-border/60 rounded-lg border border-border" aria-label="Members you can add">
              {candidates.map((candidate) => (
                <li key={candidate.user_id} className="flex items-center justify-between gap-2 px-3 py-2 text-xs">
                  <span className="min-w-0 truncate text-foreground">
                    {candidate.name || candidate.email} <span className="text-neutral-600 dark:text-neutral-400">{candidate.email}</span>
                  </span>
                  <Button size="sm" data-testid={`team-candidate-add-${candidate.user_id}`} onClick={() => void setRole(candidate.user_id, "member")}>
                    <UserPlus className="h-3.5 w-3.5" aria-hidden="true" /> Add
                  </Button>
                </li>
              ))}
            </ul>
          )}
        </section>
      )}
      {dialog}
    </div>
  );
};
