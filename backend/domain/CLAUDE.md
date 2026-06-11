# backend/domain/ — pure shapes + pure validation

`models.py` — the Pydantic data spine (Team/Session/AgentSpec/Capabilities/
Task/TeamGraph...). *Data only*: no I/O, no model calls, no filesystem. Teams
are reusable definitions; sessions are running instances; everything is keyed
by `team_id`/`session_id` from day one. Keep shapes trivially constructible in
tests.

`graph.py` — pure graph validation, two deliberate tiers: **structural**
(always enforced on save — a malformed graph can corrupt the runtime) vs
**runnable** (≥1 entry point, required only to launch a session, so the editor
can save drafts).

If you add behavior that touches anything outside the data itself, it belongs
in another package.
