import { useEffect, useMemo, useRef, useState } from "react";
import { api, type TeamRow } from "../lib/api";
import { Button, IconButton, TextInput } from "../lib/ui";
import type { SessionInfo } from "../lib/types";

// A session's human label — its repo folder + mode, matching SessionSwitcher.
const repoName = (p: string) => p.split("/").filter(Boolean).pop() || p;
const sessionLabel = (s: SessionInfo) => `${repoName(s.repo_path)} · ${s.mode}`;

// Team library manager — the one place to rename, describe, delete and create
// teams. Reachable from the header (TEAM zone) and from Onboarding, so teams can
// be tidied whether or not a session is running. Search + descriptions need a
// list surface (a kebab popover couldn't host them), which is why this is a
// dialog rather than an inline menu.
export default function ManageTeamsDialog({
  open,
  teams,
  sessions,
  onClose,
  onChanged,
}: {
  open: boolean;
  teams: TeamRow[];
  sessions: SessionInfo[];
  onClose: () => void;
  onChanged: () => Promise<void> | void;
}) {
  const [query, setQuery] = useState("");
  const [creating, setCreating] = useState(false);

  // Close on Escape (matches the standard dialog convention).
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  // Reset the transient search/create state each time the dialog opens.
  useEffect(() => {
    if (open) {
      setQuery("");
      setCreating(false);
    }
  }, [open]);

  // Which sessions are bound to each team — drives the "in use" tag, names the
  // blocker so the user can go rebind/close it, and disables delete (the backend
  // is the source of truth and 409s anyway).
  const boundByTeam = useMemo(() => {
    const map: Record<string, SessionInfo[]> = {};
    for (const s of sessions) (map[s.team_id] ??= []).push(s);
    return map;
  }, [sessions]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return teams;
    return teams.filter(
      (t) =>
        t.name.toLowerCase().includes(q) || (t.description ?? "").toLowerCase().includes(q)
    );
  }, [teams, query]);

  if (!open) return null;

  return (
    <div className="modal-overlay" onMouseDown={onClose}>
      <div
        className="card modal"
        role="dialog"
        aria-modal="true"
        aria-label="Manage teams"
        onMouseDown={(e) => e.stopPropagation()}
      >
        <header className="modal-head">
          <div>
            <h2 style={{ fontSize: 16 }}>Manage teams</h2>
            <p className="muted" style={{ fontSize: 12.5, marginTop: 2 }}>
              Rename, describe or delete your team templates.
            </p>
          </div>
          <IconButton title="Close" onClick={onClose} aria-label="Close">
            <CloseIcon />
          </IconButton>
        </header>

        <div className="modal-toolbar">
          <div style={{ position: "relative", flex: 1 }}>
            <span className="search-icon">
              <SearchIcon />
            </span>
            <TextInput
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search teams…"
              style={{ paddingLeft: 30 }}
              aria-label="Search teams"
            />
          </div>
          <Button variant="primary" size="sm" onClick={() => setCreating((v) => !v)}>
            + New team
          </Button>
        </div>

        {creating && (
          <CreateTeamRow
            onCancel={() => setCreating(false)}
            onCreated={async () => {
              setCreating(false);
              await onChanged();
            }}
          />
        )}

        <div className="modal-body">
          {filtered.length === 0 ? (
            <p className="muted" style={{ textAlign: "center", padding: "28px 0", fontSize: 13 }}>
              {teams.length === 0 ? "No teams yet — create your first one." : "No teams match your search."}
            </p>
          ) : (
            filtered.map((t) => (
              <TeamRowItem
                key={t.id}
                team={t}
                bound={boundByTeam[t.id] ?? []}
                onChanged={onChanged}
              />
            ))
          )}
        </div>
      </div>
    </div>
  );
}

// One editable team row. Name + description commit on blur (or Enter) when they
// actually changed; an empty name is rejected (reverts to the stored value).
// Delete is a two-step inline confirm and surfaces the backend's block-if-bound
// 409 in place rather than as an alert.
function TeamRowItem({
  team,
  bound,
  onChanged,
}: {
  team: TeamRow;
  bound: SessionInfo[];
  onChanged: () => Promise<void> | void;
}) {
  const [name, setName] = useState(team.name);
  const [description, setDescription] = useState(team.description ?? "");
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Keep local fields in sync if the team is refreshed from the server.
  useEffect(() => {
    setName(team.name);
    setDescription(team.description ?? "");
  }, [team.name, team.description]);

  // Drop a stale delete error once the blocker is resolved elsewhere (e.g. the
  // user rebinds the session) — the bound count changing is that signal.
  useEffect(() => {
    setError(null);
  }, [bound.length]);

  const commitName = async () => {
    const next = name.trim();
    if (!next) {
      setName(team.name); // empty names aren't allowed — revert
      return;
    }
    if (next === team.name) return;
    await api.updateTeam(team.id, { name: next });
    await onChanged();
  };

  const commitDescription = async () => {
    if (description === (team.description ?? "")) return;
    await api.updateTeam(team.id, { description });
    await onChanged();
  };

  const remove = async () => {
    setError(null);
    try {
      await api.deleteTeam(team.id);
      await onChanged();
    } catch (e) {
      // Most commonly the block-if-bound 409 — show its message inline.
      setError(humanizeError(e));
      setConfirming(false);
    }
  };

  const inUse = bound.length > 0;
  const usedByLabel = bound.map(sessionLabel).join(", ");
  const agentsLabel = `${team.agent_count} ${team.agent_count === 1 ? "agent" : "agents"}`;

  return (
    <div className="team-row">
      <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column", gap: 6 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <TextInput
            value={name}
            onChange={(e) => setName(e.target.value)}
            onBlur={commitName}
            onKeyDown={(e) => {
              if (e.key === "Enter") e.currentTarget.blur();
            }}
            aria-label="Team name"
            style={{ fontWeight: 600, maxWidth: 240 }}
          />
          {inUse && (
            <span className="chip chip-warning" title={`Used by ${usedByLabel}`}>
              in use
            </span>
          )}
        </div>
        {/* who/what is inside — agent count, and which session holds it (so the
            user knows where to go rebind before a delete) */}
        <div style={{ fontSize: 11.5, color: "var(--text-faint)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
          {agentsLabel}
          {inUse && <> · used by <span style={{ color: "var(--warning)" }}>{usedByLabel}</span> — rebind or close it to delete</>}
        </div>
        <TextInput
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          onBlur={commitDescription}
          onKeyDown={(e) => {
            if (e.key === "Enter") e.currentTarget.blur();
          }}
          placeholder="What is this team set up to do?"
          aria-label="Team description"
          style={{ fontSize: 12.5, color: "var(--text-muted)" }}
        />
        {error && <span style={{ fontSize: 12, color: "var(--danger)" }}>{error}</span>}
      </div>

      <div style={{ flexShrink: 0, display: "flex", alignItems: "center", gap: 6 }}>
        {confirming ? (
          <>
            <Button variant="ghost" size="sm" onClick={() => setConfirming(false)}>
              Cancel
            </Button>
            <Button variant="danger" size="sm" onClick={remove}>
              Delete
            </Button>
          </>
        ) : (
          <IconButton
            title={inUse ? `Used by ${usedByLabel} — rebind or close it first` : "Delete team"}
            disabled={inUse}
            onClick={() => {
              setError(null);
              setConfirming(true);
            }}
            aria-label="Delete team"
          >
            <TrashIcon />
          </IconButton>
        )}
      </div>
    </div>
  );
}

// Inline create form (name + optional description). Cleaner than a window.prompt
// and lets the description be set at birth.
function CreateTeamRow({
  onCancel,
  onCreated,
}: {
  onCancel: () => void;
  onCreated: () => Promise<void> | void;
}) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [busy, setBusy] = useState(false);
  const ref = useRef<HTMLInputElement>(null);

  useEffect(() => {
    ref.current?.focus();
  }, []);

  const create = async () => {
    if (!name.trim()) return;
    setBusy(true);
    try {
      // Omit the graph → backend seeds a starter lead agent (launchable).
      await api.createTeam({ name: name.trim(), description: description.trim() });
      await onCreated();
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="team-row team-row-new">
      <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column", gap: 6 }}>
        <TextInput
          ref={ref}
          value={name}
          onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") create();
          }}
          placeholder="Team name"
          aria-label="New team name"
          style={{ fontWeight: 600, maxWidth: 240 }}
        />
        <TextInput
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") create();
          }}
          placeholder="What is this team set up to do? (optional)"
          aria-label="New team description"
          style={{ fontSize: 12.5 }}
        />
      </div>
      <div style={{ flexShrink: 0, display: "flex", alignItems: "center", gap: 6 }}>
        <Button variant="ghost" size="sm" onClick={onCancel}>
          Cancel
        </Button>
        <Button variant="primary" size="sm" onClick={create} disabled={busy || !name.trim()}>
          {busy ? "…" : "Create"}
        </Button>
      </div>
    </div>
  );
}

function humanizeError(e: unknown): string {
  const raw = e instanceof Error ? e.message : String(e);
  // api.json throws "409 Conflict: <body>" — keep just the human part.
  const m = raw.match(/^\d+ [^:]+:\s*(.*)$/s);
  return m ? m[1] : raw;
}

function CloseIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <path d="M4 4l8 8M12 4l-8 8" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
    </svg>
  );
}

function SearchIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <circle cx="7" cy="7" r="4.5" stroke="currentColor" strokeWidth="1.5" />
      <path d="M10.5 10.5L14 14" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
    </svg>
  );
}

function TrashIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <path d="M3 4.5h10M6.5 4.5V3.5a1 1 0 0 1 1-1h1a1 1 0 0 1 1 1v1M5 4.5l.5 8a1 1 0 0 0 1 .95h3a1 1 0 0 0 1-.95l.5-8"
        stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
