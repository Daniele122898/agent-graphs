# Team UX Fixes — 2026-06-14

## Summary

The backend persistence layer is correct (verified: team graph saves to DB,
launch reads latest, sync works). The bugs are in the **frontend UX** and a
**debounce timing race**. This spec covers 6 fixes (A–F) in priority order.

---

## Fix A — Show Team Name in Header (CRITICAL)

**Problem:** The header shows app name, session switcher, mode chip, harness
chip, canvas/board toggle — but not which TEAM is being edited. Users have no
idea what team is active.

**Root cause:** `App.tsx` has `teams` array and `session.team_id` but never
derives/renders the team name.

**Fix:** In `App.tsx`, after `session` loads, look up the current team name:
`const teamName = teams.find(t => t.id === session.team_id)?.name`. Display it
as a `Chip` in the header (between the session switcher and the right-side
controls).

**File:** `frontend/src/App.tsx`

---

## Fix B — Flush Pending Save Before Session Launch (CRITICAL)

**Problem:** `useTeamGraph` debounces saves at 600ms. If a user edits the graph
and launches a new session (with the same team) within that 600ms window:
- The save hasn't fired → team in DB is stale
- `POST /api/sessions` reads stale team from DB → new session gets OLD graph
- Frontend canvas still shows edited graph (React state, `teamId` unchanged)

**Fix:** 
1. In `useTeamGraph.ts`, add a `flushSave()` function that:
   - Cancels any pending debounce timer
   - Immediately calls `api.putTeamGraph(teamId, snapshot)` 
   - Sets status to "saved"
   - Returns a Promise<void>
2. Expose `flushSave` in the hook's return value.
3. In `App.tsx`, thread `graph.flushSave` to both `SessionSwitcher` and
   `Onboarding` as a new `flushSave` prop.
4. Both components call `await flushSave()` before calling `api.launchSession()`.

**Additional fix:** When switching between sessions that share the same team,
`useTeamGraph` must reload because the session's pinned graph may differ from
the edited state. Add `activeSessionId` as a key to force reload on session
switch.

**Files:** `frontend/src/hooks/useTeamGraph.ts`, `frontend/src/App.tsx`,
`frontend/src/panels/SessionSwitcher.tsx`, `frontend/src/panels/Onboarding.tsx`

---

## Fix C — Team Selector in Header (HIGH)

**Problem:** After launching a session, the canvas is locked to the session's
team. Users cannot:
- Edit a different team while a session is running
- Browse the team library and pick one to edit
- See what teams exist without going to onboarding

**Fix:** Add a `<Select>` dropdown (or a custom dropdown using existing
primitives) in the header that:
1. Shows the current team name as the displayed value
2. Lists all teams from the `teams` array
3. Marks the session's team with "(active session)" 
4. When the user picks a different team, sets `activeTeamId` to that team's id
   (this triggers `useTeamGraph` to reload from that team's graph)
5. Optionally: adds a "Use for session" action (depends on Fix E)

**Important:** The team selector is for the EDITOR — it changes what graph is
shown on the canvas. It does NOT rebind the session (that's Fix E). Editing a
team that is NOT the session's team does not affect the running session
(by design: `apply_team_graph` only syncs sessions bound to the edited team).

**File:** `frontend/src/App.tsx`

---

## Fix D — Switch to New Team After "Save as Team…" (HIGH)

**Problem:** After `saveAs` creates a new team, `refresh()` reloads the team
list, but `activeTeamId` stays on the OLD team. Subsequent edits still go to
the old team.

**Fix:** After `api.createTeam(name, graph.snapshot())`, update `activeTeamId`
to the new team's id (from the API response). The `teamId` change will trigger
`useTeamGraph` to reload.

**File:** `frontend/src/App.tsx` (the `saveAs` function)

---

## Fix E — Rebind Session to Different Team (MEDIUM)

**Problem:** Sessions are permanently bound to teams at launch. Users want
flexibility to change a session's team.

**Fix (Backend):**
1. Add `rebind(team_id: str, graph: TeamGraph)` method to `Session` class
   (`runtime/sessions.py`):
   - Updates `self.team_id` and `self.graph`
   - Re-seeds `self.registry` from the new graph's nodes
   - Does NOT wipe existing agent histories (agents that exist in both graphs
     keep their history; new agents start fresh; removed agents are detached)
2. Add endpoint `POST /api/sessions/{session_id}/rebind` in `api/sessions.py`:
   - Body: `{team_id: str}`
   - Validates team exists, loads its graph
   - Calls `session.rebind(team_id, graph)`
   - Persists the change to DB (`UPDATE sessions SET team_id = ? WHERE id = ?`)
   - Returns updated `session.info()`
3. Add `api.rebindSession(sessionId, teamId)` method in `frontend/src/lib/api.ts`

**Fix (Frontend):**
Add "Use for session" action in the team selector (Fix C) that calls
`api.rebindSession(activeSessionId, teamId)`.

**Files (Backend):** `backend/runtime/sessions.py`, `backend/api/sessions.py`
**Files (Frontend):** `frontend/src/lib/api.ts`, `frontend/src/App.tsx`
**Tests:** `tests/test_teams.py`, `tests/test_multisession.py`

---

## Fix F — Improve Save Indicator (MEDIUM)

**Problem:** Status text just says "saved" — no indication of WHERE it was saved.

**Fix:** In `useTeamGraph.ts`, change status to `"saved to '${teamName}'"`. The
hook needs to know the team name. Option: accept `teamName` as a parameter to
`useTeamGraph`, or look it up from an API call.

Simplest approach: pass `teamName` from `App.tsx` into `useTeamGraph(teamId, teamName)`.

**File:** `frontend/src/hooks/useTeamGraph.ts`, `frontend/src/App.tsx`

---

## Implementation Order

1. **First batch (parallel):** Fix A + Fix B (critical, frontend) and Fix E (backend)
2. **Second batch:** Fix C + Fix D + Fix F (high/medium, frontend)
3. **Verification:** Run tests, verify UI with Playwright

---

## Files Modified

| File | Fixes | Agent |
|------|-------|-------|
| `frontend/src/App.tsx` | A, B, C, D, F | Frontend |
| `frontend/src/hooks/useTeamGraph.ts` | B, F | Frontend |
| `frontend/src/panels/SessionSwitcher.tsx` | B | Frontend |
| `frontend/src/panels/Onboarding.tsx` | B | Frontend |
| `frontend/src/lib/api.ts` | E | Frontend |
| `backend/runtime/sessions.py` | E | Backend |
| `backend/api/sessions.py` | E | Backend |
| `tests/test_teams.py` | E | Backend |
| `tests/test_multisession.py` | E | Backend |
