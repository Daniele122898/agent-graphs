import { useEffect, useState } from "react";
import { api, type TeamRow } from "../lib/api";
import { Button, Field, Select, TextInput } from "../lib/ui";
import type { SessionInfo } from "../lib/types";

// Switch between running sessions and launch new ones. Sessions/teams are owned
// by App and passed in; this just drives selection + the launch popover.
export default function SessionSwitcher({
  activeSessionId,
  sessions,
  teams,
  onSwitch,
  onLaunched,
}: {
  activeSessionId: string | null;
  sessions: SessionInfo[];
  teams: TeamRow[];
  onSwitch: (id: string) => void;
  onLaunched: (id: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [teamId, setTeamId] = useState("");
  const [repo, setRepo] = useState("");
  const [mode, setMode] = useState<"parallel" | "serial">("parallel");
  const [error, setError] = useState<string | null>(null);
  const [launching, setLaunching] = useState(false);

  useEffect(() => {
    if (teams.length && !teams.some((t) => t.id === teamId)) setTeamId(teams[0].id);
  }, [teams, teamId]);

  const repoName = (p: string) => p.split("/").filter(Boolean).pop() || p;

  const launch = async () => {
    if (!teamId || !repo.trim()) return;
    setLaunching(true);
    setError(null);
    try {
      const s = await api.launchSession(teamId, repo.trim(), mode);
      if (s.warning) window.alert(s.warning);
      setOpen(false);
      setRepo("");
      onLaunched(s.id);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLaunching(false);
    }
  };

  return (
    <div style={{ position: "relative", display: "flex", alignItems: "center", gap: 8 }}>
      <Select
        value={activeSessionId ?? ""}
        onChange={(e) => onSwitch(e.target.value)}
        style={{ width: "auto", minWidth: 180 }}
      >
        {sessions.map((s) => (
          <option key={s.id} value={s.id}>
            {repoName(s.repo_path)} · {s.mode}
          </option>
        ))}
      </Select>
      <Button variant="ghost" size="sm" onClick={() => setOpen((v) => !v)}>
        + Session
      </Button>

      {open && (
        <div
          className="card"
          style={{ position: "absolute", top: 42, left: 0, width: 320, padding: 16, zIndex: 20, display: "flex", flexDirection: "column", gap: 12 }}
        >
          <strong style={{ fontSize: 13 }}>Launch a session</strong>
          <Field label="Team">
            <Select value={teamId} onChange={(e) => setTeamId(e.target.value)}>
              {teams.map((t) => (
                <option key={t.id} value={t.id}>{t.name}</option>
              ))}
            </Select>
          </Field>
          <Field label="Repository path">
            <TextInput value={repo} onChange={(e) => setRepo(e.target.value)} placeholder="/path/to/repo" />
          </Field>
          <Field label="Mode">
            <Select value={mode} onChange={(e) => setMode(e.target.value as "parallel" | "serial")}>
              <option value="parallel">parallel</option>
              <option value="serial">serial</option>
            </Select>
          </Field>
          {error && (
            <div style={{ fontSize: 12, color: "var(--danger, #c2341d)" }}>Launch failed: {error}</div>
          )}
          <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
            <Button variant="ghost" size="sm" onClick={() => setOpen(false)}>Cancel</Button>
            <Button variant="primary" size="sm" onClick={launch} disabled={!repo.trim() || launching}>
              {launching ? "Launching…" : "Launch"}
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
