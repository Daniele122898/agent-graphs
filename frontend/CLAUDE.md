# frontend/ — React + Vite + TypeScript control room

React 18 + Vite + `@xyflow/react` (React Flow). No unit-test runner — the
contract is **type-check + build**, plus **visual Playwright verification**.

## Run, build, verify
```bash
npm run dev        # Vite :5173, proxies /api /health /events to backend :8000
npm run build      # tsc -b && vite build — MUST pass (strict, noUnusedLocals)
```
**IMPORTANT — verify UI changes in a real browser, do not trust the build alone.**
With both servers up and a fresh DB:
```bash
../.venv/bin/python ../scripts/verify_ui.py    # from frontend/, or run from repo root
```
It drives the real app with Playwright (chromium) through the whole flow
(onboarding → create team → launch session → control room → agent chat → task
board), asserts structure, and writes screenshots to `/tmp/ag_shots/`. **Then
Read those PNGs to actually look at the result** — that's how UI work is
confirmed here. Extend the script when you add UI worth checking.

**Autosave / debounce / state-switch changes need a *behavioral* browser
regression, not just a build.** A type-check + build passes happily while edits
are silently dropped (the team-switch data-loss bug shipped green). Prove it in
the browser: `scripts/verify_team_save.py` (edit → switch the team-selector
within the 600 ms debounce → switch back → assert via the API the edit survived)
is the focused regression; fold new such checks into it or `verify_ui.py`.

## Design system (YOU MUST use it — no raw HTML controls)
- Tokens live in `src/index.css` as CSS variables (`--primary` #2563eb, neutrals, radii, shadows). Light theme, single blue accent, role-based colors, WCAG-aware contrast.
- Reusable primitives in `src/lib/ui.tsx`: `Button` (primary/secondary/ghost/danger), `IconButton`, `Select`, `TextInput`, `TextArea`, `Field`, `Chip`. **Use these instead of bare `<button>`/`<select>`/`<input>`** so styling stays coherent. The add-agent control is a bottom-left `.fab`.
- Keep it light theme (user preference). Reach for tokens/`var(--...)`, not hardcoded hex, in inline styles.
- **Chips are single-line tokens — `.chip` is `white-space: nowrap`.** A chip must hold a SHORT token; never put a sentence or a name that can grow in one (the "saved — {team}" and "native harness" chips wrapped to two ugly lines). Long/variable text → truncate (`overflow:hidden; text-overflow:ellipsis; min-width:0` inside a flex row) with a `title`, or use a fixed indicator like `.savedot`. Header helpers in `index.css`: `.hzone`/`.hcap`/`.hdiv` (captioned, divided control zones), `.savedot` (state dot + word), `.runtag` (the "running" team marker), `.chip-button` (a chip that acts as a toggle), `.kbd` (keyboard-hint key).
- **Chat composers send on Enter, newline on Shift+Enter** (standard chat UX). The Agent tab's textarea does this via `onKeyDown` (guard `e.nativeEvent.isComposing` so an IME confirm doesn't send), with a `.kbd` hint. Any new message composer must follow the same convention.

## Architecture & non-obvious decisions (the *why*)
- **Session-centric.** App revolves around an **active session** (persisted in `localStorage`, key `ag.activeSessionId`, reconciled against the live list). When there is no session, `Onboarding.tsx` runs the explicit create-team → launch-session flow. There is no default session — the backend won't invent one.
- **Header IA — two captioned, divided zones (`App.tsx`).** TEAM vs SESSION were once two identical bare `<Select>`s; they now read as distinct things: a `.hdiv` hairline separates each `.hzone`, and each carries a tiny `.hcap` caption ("Team" / "Session"). **TEAM zone** = what the canvas is EDITING: the team `<Select>` (width capped so a long name can't blow up the bar) + a compact **`SaveDot`** (a coloured state dot + one word: "Saved" / "Saving…" / "Save failed" — NOT the old wrapping `"saved — {long team name}"` chip; the team it refers to is the adjacent select) + either a green **`runtag` "running"** (when you're editing the session's own team) or the **"↻ Use for session"** primary button (when editing a different team — `api.rebindSession()` + `setSession(info)`) + a **`CopyIcon` `IconButton`** = "Save as new team" (a PURE FORK: `createTeam(snapshot)` then `setActiveTeamId(copy)`, never rebinds the session — adopt explicitly via "↻ Use for session"; conflating fork with adopt surprised users). **SESSION zone** = what's RUNNING: `SessionSwitcher`'s session `<Select>` + "+ New". Right zone = session settings (mode toggle `chip-button`, harness `chip`) + the Canvas|Tasks view toggle. Keep this grouping when adding header controls — put a team action in the team zone, a session action in the session zone.
- **`lib/api.ts` holds the active session id as module state** (`setActiveSession`); session-scoped calls append `?session_id=` via `withSession`. This keeps components from threading the id everywhere. `useEvents(sessionId)` opens the SSE `/events` connection per session and reconnects on switch.
- **`useTeamGraph(teamId)`** owns the React Flow node/edge state, the debounced save, and is **team-scoped** (`/api/teams/{id}/graph`). Editing the session's team syncs the running session (the canvas doubles as the live control room). `status` is a SHORT string ("loading…"/"saving…"/"saved"/"save error: …") — no team name (the header `SaveDot` + selector show which team). It flushes the outgoing team on teamId-change/unmount (the data-loss guard, above) and exposes `flushSave()` for the same-team launch race.
- **`canvas/graphMapping.ts` is the single backend⇄React Flow conversion point.** The full `AgentSpec` lives in `node.data.spec`; `position` is the only UI-owned field. Don't convert formats anywhere else.
- **Edges are decorated at render time; only the dragged bend persists.** `Canvas` maps every edge to the `floating` type (`FloatingEdge.tsx`: a quadratic through a midpoint displaced perpendicular to the node axis, border-anchored toward that midpoint) and adds arrow markers. Reciprocal A⇄B pairs arc apart automatically: the perpendicular axis flips with edge direction, so the SAME default offset lands on opposite sides — do NOT add a per-edge canonical sign (it cancels the separation; this bug shipped once). The midpoint dot is a drag handle; the displacement persists as `GraphEdge.curve` (0 = auto), everything else stays render-time. Node handles exist only for drag-to-connect.
- **Selecting an edge selects its SOURCE agent** and passes `focusEdgeId` down: the sidebar jumps to the Links tab and autofocuses that link's row. Edge labels are `pointerEvents: none` so clicks reach the selectable path beneath — don't make them interactive.
- TS types in `src/lib/types.ts` mirror the backend by hand; the backend graph round-trip test guards the wire format.
- The Agent tab transcript is **chat bubbles** (user right/blue, agent left); the user's own prompt shows because the backend emits a `user_message` SSE event at run start. SSE event types are registered in `useEvents.ts` — add new ones there.
- **The transcript = persisted history + live tail.** On open, the Agent tab fetches `GET /api/agent/{id}/history` (the stored conversation rendered as the same row shapes, plus the system-context sections). The live tail is cut by event **`seq`** (a module-scoped monotonic counter in `useEvents` — NEVER an array index, the events array resets on remount/session switch) at the last `agent_done`/`agent_error` for the agent — *not* at "now", so a mid-run mount keeps the in-flight run visible. After a run finishes, history is re-fetched (≈400ms delay covers the persist) so the transcript converges. Clear/Summarize re-fetch and reset the baseline.
- **ask_user questions** render as an amber card above the transcript (options as buttons + free-form input per question, one submit). Open questions are fetched from `GET /api/questions` (page-reload case) and re-fetched on `user_question`/`user_question_done` SSE events; the `waiting-on-user` lifecycle shows a purple "needs you" badge on the canvas node.
- **Harness toggle at launch**: BOTH launch paths carry an "Agent harness" select (native | opencode) passed to `api.launchSession` — `Onboarding.tsx` (first session) AND `SessionSwitcher.tsx`'s "+ Session" popover (subsequent sessions). Keep them in sync: a field added to one launch form must be added to the other. The active session's `harness` shows as an "opencode" chip in the header when not native. The control room is harness-agnostic — both backends publish the same bus event shapes, so transcript/todos/questions/lifecycle/delegation render identically. Don't special-case the harness in render code.
- **A debounced writer MUST own flush-on-switch + flush-on-unmount — never push that onto call sites (YOU MUST).** A debounced autosave whose flush is a separately-invoked function is a data-loss trap: every control that changes the edited entity has to remember to flush first, forgetting it is silent (clean type-check, no error), and the count of such controls only grows. That is exactly how the header team-selector dropped edits — it called `setActiveTeamId` without flushing and the autosave cleanup *cancelled* the pending save. The rule, implemented in `useTeamGraph`: the hook that owns the unsaved buffer owns an effect keyed on the entity id whose CLEANUP flushes the OUTGOING entity (`teamId` change OR unmount; a `dirty`/`snapshotRef` pair holds what to save; the `dirty` guard makes mount/StrictMode a no-op; the flush targets the OUTGOING id so it can't clobber the incoming load). `flushSave()` remains only for the no-id-change case (`await` before a session launch on the *current* team; `SessionSwitcher` uses it; `Onboarding` does not — no pre-launch editor there). When you add ANY new debounced network writer, route it through this pattern (ideally a shared `useDebouncedAutosave` hook) — do NOT open-code `setTimeout(() => api.put…)` with a cancel-only cleanup. Regression: `scripts/verify_team_save.py`.
- **The Capabilities tab's model picker is provider-driven**: a Backend dropdown (from `GET /api/providers`) sits ABOVE the model dropdown (`GET /api/providers/{id}/models`); thinking on/off + effort controls appear only when the provider's metadata says it supports them, and switching backends resets the spec's thinking fields (semantics are backend-specific). Listing failures render as a muted inline note, never break the tab.

## Layout
```
src/
  App.tsx, main.tsx, index.css   shell + entry + design tokens
  lib/        api.ts (HTTP client + session state), types.ts (backend mirrors), ui.tsx (primitives)
  hooks/      useEvents.ts (SSE), useTeamGraph.ts (React Flow state + debounced save)
  canvas/     Canvas.tsx, AgentNode.tsx, FloatingEdge.tsx, graphMapping.ts
  panels/     Sidebar.tsx, TaskBoard.tsx, NewTaskDialog.tsx, Onboarding.tsx, SessionSwitcher.tsx
  panels/tabs/  AgentTab, CapabilitiesTab, LinksTab, PersonaTab, StatsTab
```
