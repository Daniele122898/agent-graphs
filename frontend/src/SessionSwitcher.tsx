import { useEffect, useState } from "react";
import { api, type TeamRow } from "./api";
import type { SessionInfo } from "./types";

// Switch between running sessions and launch new ones (a team bound to a repo).
// The runtime already supports N concurrent sessions; this is the launch flow.
export default function SessionSwitcher({
  activeSessionId,
  teams,
  onSwitch,
  onLaunched,
}: {
  activeSessionId: string | null;
  teams: TeamRow[];
  onSwitch: (id: string) => void;
  onLaunched: (id: string) => void;
}) {
  const [sessions, setSessions] = useState<SessionInfo[]>([]);
  const [launching, setLaunching] = useState(false);
  const [teamId, setTeamId] = useState("");
  const [repo, setRepo] = useState("");
  const [mode, setMode] = useState<"parallel" | "serial">("parallel");

  const refresh = () => api.listSessions().then((r) => setSessions(r.sessions)).catch(() => {});
  useEffect(() => {
    refresh();
  }, [activeSessionId]);
  useEffect(() => {
    if (teams.length && !teamId) setTeamId(teams[0].id);
  }, [teams, teamId]);

  const launch = async () => {
    if (!teamId || !repo.trim()) return;
    const s = await api.launchSession(teamId, repo, mode);
    if (s.warning) window.alert(s.warning);
    setLaunching(false);
    setRepo("");
    await refresh();
    onLaunched(s.id);
  };

  return (
    <span style={{ fontSize: 12, display: "flex", alignItems: "center", gap: 6 }}>
      <select value={activeSessionId ?? ""} onChange={(e) => onSwitch(e.target.value)} title="Active session">
        {sessions.map((s) => (
          <option key={s.id} value={s.id}>
            {s.id.slice(0, 10)} · {s.repo_path.split("/").pop()} · {s.mode}
          </option>
        ))}
      </select>
      <button onClick={() => setLaunching((v) => !v)} style={{ fontSize: 11, padding: "2px 8px", cursor: "pointer" }}>
        + session
      </button>
      {launching && (
        <span style={{ display: "flex", gap: 4, alignItems: "center" }}>
          <select value={teamId} onChange={(e) => setTeamId(e.target.value)}>
            {teams.map((t) => (
              <option key={t.id} value={t.id}>
                {t.name}
              </option>
            ))}
          </select>
          <input value={repo} onChange={(e) => setRepo(e.target.value)} placeholder="/path/to/repo" style={{ width: 160, padding: 3 }} />
          <select value={mode} onChange={(e) => setMode(e.target.value as "parallel" | "serial")}>
            <option value="parallel">parallel</option>
            <option value="serial">serial</option>
          </select>
          <button onClick={launch} disabled={!repo.trim()} style={{ padding: "2px 8px", cursor: "pointer" }}>
            launch
          </button>
        </span>
      )}
    </span>
  );
}
