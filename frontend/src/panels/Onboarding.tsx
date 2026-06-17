import { useEffect, useState } from "react";
import { api, type TeamRow } from "../lib/api";
import { Button, Field, Select, TextInput } from "../lib/ui";

// Shown when there is no active session. Guides the explicit flow: define a team
// (graph + agents), then launch a session that binds it to a repo. Nothing is
// auto-created — this is where a team/session first comes into being.
export default function Onboarding({
  teams,
  onChanged,
  onLaunched,
  flushSave,
}: {
  teams: TeamRow[];
  onChanged: () => Promise<void> | void;
  onLaunched: (sessionId: string) => void;
  flushSave: () => Promise<void>;
}) {
  const [teamId, setTeamId] = useState("");
  const [repo, setRepo] = useState("");
  const [mode, setMode] = useState<"parallel" | "serial">("parallel");
  const [harness, setHarness] = useState<"native" | "opencode">("native");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (teams.length && !teams.some((t) => t.id === teamId)) setTeamId(teams[0].id);
  }, [teams, teamId]);

  const createTeam = async () => {
    const name = window.prompt("Name your team:", "My Team");
    if (!name) return;
    const t = await api.createTeam(name); // starter graph (one lead agent)
    await onChanged();
    setTeamId(t.id);
  };

  const launch = async () => {
    if (!teamId || !repo.trim()) return;
    setBusy(true);
    setError(null);
    try {
      await flushSave();
      const s = await api.launchSession(teamId, repo.trim(), mode, harness);
      onLaunched(s.id);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div style={{ flex: 1, display: "grid", placeItems: "center", padding: 24 }}>
      <div className="card" style={{ width: 460, maxWidth: "100%", padding: 28 }}>
        <h2 style={{ fontSize: 20 }}>Launch a session</h2>
        <p className="muted" style={{ marginTop: 6, marginBottom: 22, fontSize: 13.5 }}>
          A session binds a <strong>team</strong> (your agent graph) to a <strong>repo</strong> on disk.
          The team then works in that folder.
        </p>

        {teams.length === 0 ? (
          <div style={{ textAlign: "center", padding: "14px 0 6px" }}>
            <p className="muted" style={{ marginBottom: 14 }}>You don't have any teams yet.</p>
            <Button variant="primary" size="lg" onClick={createTeam}>
              + Create your first team
            </Button>
          </div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
            <Field label="Team">
              <div style={{ display: "flex", gap: 8 }}>
                <Select value={teamId} onChange={(e) => setTeamId(e.target.value)}>
                  {teams.map((t) => (
                    <option key={t.id} value={t.id}>{t.name}</option>
                  ))}
                </Select>
                <Button variant="secondary" onClick={createTeam} style={{ whiteSpace: "nowrap" }}>
                  + New
                </Button>
              </div>
            </Field>

            <Field label="Repository path">
              <TextInput
                value={repo}
                onChange={(e) => setRepo(e.target.value)}
                placeholder="/Users/you/code/my-project"
              />
            </Field>

            <Field label="Execution mode">
              <Select value={mode} onChange={(e) => setMode(e.target.value as "parallel" | "serial")}>
                <option value="parallel">parallel — model calls run concurrently</option>
                <option value="serial">serial — one model call at a time (low-spec)</option>
              </Select>
            </Field>

            <Field label="Agent harness">
              <Select value={harness} onChange={(e) => setHarness(e.target.value as "native" | "opencode")}>
                <option value="native">native — built-in Pydantic AI engine</option>
                <option value="opencode">opencode — drive a headless OpenCode server</option>
              </Select>
            </Field>

            {error && <p style={{ color: "var(--danger)", fontSize: 12.5 }}>{error}</p>}

            <Button variant="primary" size="lg" onClick={launch} disabled={busy || !repo.trim()}>
              {busy ? "Launching…" : "Launch session"}
            </Button>
          </div>
        )}
      </div>
    </div>
  );
}
