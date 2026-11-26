"use client";

import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { ChevronDown, ChevronRight, GitBranch, Info } from "lucide-react";
import { Button } from "../ui/Button";
import { Field, Select } from "../ui/Field";
import {
  feeds,
  fetchCatalogue,
  importSkills,
  listSources,
  shortCommit,
  type Catalogue,
  type CatalogueEntry,
  type ExternalSource,
  type ImportConflict,
} from "../../lib/external-sources";
import { ownerFields, type OwnerKey } from "../../lib/ownership";
import { CHECKBOX_CLASS } from "../ui/checkbox";

interface Props {
  owner: OwnerKey;
  onImported: (names: string[]) => void;
}

interface Group {
  key: string;
  title: string;
  description: string;
  entries: CatalogueEntry[];
}

function groupsOf(catalogue: Catalogue): Group[] {
  const byPath = new Map(catalogue.entries.map((e) => [e.path, e]));
  const groups: Group[] = catalogue.plugins.map((plugin) => ({
    key: `plugin:${plugin.name}`,
    title: `Plugin ${plugin.name}`,
    description: plugin.description,
    entries: plugin.entries.map((path) => byPath.get(path)).filter((e): e is CatalogueEntry => !!e),
  }));
  const loose = catalogue.entries.filter((e) => e.plugins.length === 0);
  if (loose.length > 0) {
    groups.push({
      key: "loose",
      title: catalogue.has_manifest ? "Standalone skills" : "Skills",
      description: "",
      entries: loose,
    });
  }
  return groups.filter((g) => g.entries.length > 0);
}

function importedHere(entry: CatalogueEntry, owner: OwnerKey): boolean {
  const { scope, team_id } = ownerFields(owner);
  return entry.imported.some((i) => i.scope === scope && (scope !== "team" || i.team_id === team_id));
}

/** A checkbox for a whole group: checked when every skill is, mixed when some are. */
const GroupCheckbox: React.FC<{
  label: string;
  checked: boolean;
  mixed: boolean;
  onChange: (checked: boolean) => void;
  testId: string;
}> = ({ label, checked, mixed, onChange, testId }) => {
  const ref = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (ref.current) ref.current.indeterminate = mixed;
  }, [mixed]);
  return (
    <label className="flex cursor-pointer items-center gap-2 text-xs font-semibold text-foreground">
      <input
        ref={ref}
        type="checkbox"
        data-testid={testId}
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
        className={CHECKBOX_CLASS}
      />
      <span>{label}</span>
    </label>
  );
};

const EntryRow: React.FC<{
  entry: CatalogueEntry;
  owner: OwnerKey;
  selected: boolean;
  onToggle: (checked: boolean) => void;
}> = ({ entry, owner, selected, onToggle }) => (
  <li className="flex items-start gap-2 py-1">
    <label className="flex flex-1 cursor-pointer items-start gap-2 text-xs text-foreground">
      <input
        type="checkbox"
        data-testid={`source-entry-${entry.path}`}
        disabled={!!entry.problem}
        checked={selected}
        onChange={(e) => onToggle(e.target.checked)}
        className={`${CHECKBOX_CLASS} mt-0.5`}
      />
      <span className="min-w-0">
        <span className="font-medium">{entry.name}</span>
        {importedHere(entry, owner) && (
          <span className="ml-1.5 text-[11px] text-emerald-700 dark:text-emerald-400">already imported here</span>
        )}
        {entry.description && <span className="block text-muted-foreground">{entry.description}</span>}
        {entry.problem && <span className="block text-rose-600 dark:text-rose-400">{entry.problem}</span>}
      </span>
    </label>
    <a href={entry.url} target="_blank" rel="noreferrer" className="shrink-0 text-[11px] text-brand hover:underline">
      {entry.path}
    </a>
  </li>
);

const GroupBlock: React.FC<{
  group: Group;
  owner: OwnerKey;
  selection: Set<string>;
  setMany: (paths: string[], checked: boolean) => void;
}> = ({ group, owner, selection, setMany }) => {
  const [open, setOpen] = useState(group.key === "loose");
  const usable = group.entries.filter((e) => !e.problem).map((e) => e.path);
  const chosen = usable.filter((p) => selection.has(p)).length;
  return (
    <fieldset className="rounded-lg border border-border p-3" data-testid={`source-group-${group.key}`}>
      <legend className="sr-only">{group.title}</legend>
      <div className="flex items-center justify-between gap-2">
        <GroupCheckbox
          label={`${group.title} (${group.entries.length})`}
          checked={usable.length > 0 && chosen === usable.length}
          mixed={chosen > 0 && chosen < usable.length}
          onChange={(checked) => setMany(usable, checked)}
          testId={`source-group-check-${group.key}`}
        />
        <button
          type="button"
          onClick={() => setOpen(!open)}
          aria-expanded={open}
          aria-label={`${open ? "Hide" : "Show"} the skills of ${group.title}`}
          className="rounded p-1 text-neutral-500 hover:bg-surface-elevated"
        >
          {open ? <ChevronDown className="h-4 w-4" aria-hidden="true" /> : <ChevronRight className="h-4 w-4" aria-hidden="true" />}
        </button>
      </div>
      {group.description && <p className="mt-1 text-[12px] text-muted-foreground">{group.description}</p>}
      {open && (
        <ul className="mt-2 divide-y divide-border/60">
          {group.entries.map((entry) => (
            <EntryRow
              key={entry.path}
              entry={entry}
              owner={owner}
              selected={selection.has(entry.path)}
              onToggle={(checked) => setMany([entry.path], checked)}
            />
          ))}
        </ul>
      )}
    </fieldset>
  );
};

/** Import skills from a connected source for one owner: whole plugins or chosen skills. */
export const SourceImportPanel: React.FC<Props> = ({ owner, onImported }) => {
  const [sources, setSources] = useState<ExternalSource[] | null>(null);
  const [sourceId, setSourceId] = useState("");
  const [catalogue, setCatalogue] = useState<Catalogue | null>(null);
  const [selection, setSelection] = useState<Set<string>>(new Set());
  const [conflicts, setConflicts] = useState<ImportConflict[]>([]);
  const [merge, setMerge] = useState<Set<string>>(new Set());
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    void listSources().then(setSources);
  }, []);

  const usable = useMemo(() => (sources ?? []).filter((s) => feeds(s, owner)), [sources, owner]);

  const choose = useCallback(async (id: string) => {
    setSourceId(id);
    setCatalogue(null);
    setSelection(new Set());
    setConflicts([]);
    setMerge(new Set());
    setError(null);
    if (!id) return;
    try {
      setCatalogue(await fetchCatalogue(id));
    } catch (err) {
      setError(err instanceof Error ? err.message : "The source could not be read.");
    }
  }, []);

  const setMany = (paths: string[], checked: boolean) => {
    setSelection((current) => {
      const next = new Set(current);
      paths.forEach((p) => (checked ? next.add(p) : next.delete(p)));
      return next;
    });
    setConflicts([]);
  };

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      const result = await importSkills(sourceId, [...selection], owner, [...merge]);
      if (result.conflicts.length > 0) {
        setConflicts(result.conflicts);
      } else {
        onImported(result.imported.map((s) => s.name));
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "The skills were not imported.");
    } finally {
      setBusy(false);
    }
  };

  if (sources !== null && usable.length === 0) {
    return (
      <p className="flex items-center gap-2 rounded-lg border border-dashed border-border p-4 text-xs text-muted-foreground" data-testid="source-import-empty">
        <Info className="h-4 w-4 shrink-0" aria-hidden="true" />
        <span>
          No connected source can feed these skills yet. Connect a repository in{" "}
          <Link href="/settings/sources" className="text-brand hover:underline">Settings → Sources</Link>.
        </span>
      </p>
    );
  }

  const source = catalogue?.source;
  const unresolved = conflicts.filter((c) => !merge.has(c.path));

  return (
    <div className="space-y-3" data-testid="source-import">
      <Field label="Source" hint="Skills imported from a source follow its repository; they change there, through pull requests.">
        {(control) => (
          <Select {...control} data-testid="source-import-select" value={sourceId} onChange={(e) => void choose(e.target.value)} className="max-w-md">
            <option value="">Choose a connected source…</option>
            {usable.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name} · {s.repository} ({s.ref})
              </option>
            ))}
          </Select>
        )}
      </Field>

      {source && (
        <p className="flex items-center gap-1.5 text-[12px] text-muted-foreground">
          <GitBranch className="h-3 w-3" aria-hidden="true" />
          {source.status === "error"
            ? `Last read failed: ${source.last_error}`
            : `As of commit ${shortCommit(catalogue?.commit ?? "")}${catalogue?.has_manifest ? " · grouped by the repository's plugins" : ""}`}
        </p>
      )}
      {catalogue?.notes.map((note) => (
        <p key={note} className="text-[12px] text-amber-700 dark:text-amber-400">{note}</p>
      ))}

      {catalogue && (
        <div className="space-y-2">
          {groupsOf(catalogue).map((group) => (
            <GroupBlock key={group.key} group={group} owner={owner} selection={selection} setMany={setMany} />
          ))}
          {catalogue.entries.length === 0 && <p className="text-xs text-muted-foreground">This repository has no skill (no folder with a SKILL.md).</p>}
        </div>
      )}

      {conflicts.length > 0 && (
        <div className="space-y-2 rounded-lg border border-amber-500/40 bg-amber-500/5 p-3" data-testid="source-import-conflicts">
          <p className="text-xs font-semibold text-foreground">Some skills already exist here with the same name</p>
          {conflicts.map((c) => (
            <label key={c.path} className="flex cursor-pointer items-center gap-2 text-xs text-foreground">
              <input
                type="checkbox"
                data-testid={`source-merge-${c.path}`}
                checked={merge.has(c.path)}
                onChange={(e) =>
                  setMerge((current) => {
                    const next = new Set(current);
                    if (e.target.checked) next.add(c.path);
                    else next.delete(c.path);
                    return next;
                  })
                }
                className={CHECKBOX_CLASS}
              />
              <span>Merge “{c.name}”: the source&apos;s version replaces it, and it follows the repository from now on</span>
            </label>
          ))}
        </div>
      )}

      {error && <p className="text-xs text-rose-600 dark:text-rose-400" role="alert">{error}</p>}

      {catalogue && (
        <div className="flex justify-end">
          <Button
            size="sm"
            variant="primary"
            data-testid="source-import-submit"
            disabled={busy || selection.size === 0 || unresolved.length > 0}
            onClick={() => void submit()}
          >
            {selection.size === 1 ? "Import 1 skill" : `Import ${selection.size} skills`}
          </Button>
        </div>
      )}
    </div>
  );
};
