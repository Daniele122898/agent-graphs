# Decision Log

A running record of decisions taken during the build and *why*, so they can be
reviewed and challenged later. Newest entries at the bottom of each phase.
Format: `YYYY-MM-DD — decision — rationale — reversibility`.

---

## Phase 0 — Skeleton + seams

### 2026-06-09 — Session kickoff decisions (from the user, before starting)

- **Checkpointing: run straight through all phases, stop only if blocked.**
  User is away; wants maximum progress with a reviewable trail (this log +
  per-chunk commits + plan.md ticks). Reversible: they can challenge any commit.
- **Live-model testing: FunctionModel/TestModel is primary; the local model at
  `http://127.0.0.1:1234` is to be used SPARINGLY.** User's laptop and the local
  model are weak. So the automated suite is fully deterministic and token-free;
  real-model validation is a thin, env-gated, off-by-default tier I only run by
  hand when genuinely needed. Live endpoint is OpenAI-compatible; best
  tool-caller available is `qwen2.5-coder-7b-instruct-mlx`.
- **Git: trunk renamed `master`→`main`; commit each logical chunk locally; no
  remote/push** (none configured). Matches the repo's configured main branch.

### 2026-06-09 — Toolchain choices

- **Python 3.13 + stdlib `venv` + `pip` + `pyproject.toml`** rather than
  `uv`/`poetry`. Reason: `uv`/`poetry` are not installed and the user is away —
  installing global tooling unprompted is intrusive. `venv`+`pip` is already
  present and sufficient for a local single-user project. Reversible: can adopt
  `uv` later without changing the package layout.
- **Project layout: `backend/` and `frontend/` at repo root** (not a nested
  `agentteam/`). Reason: this repo *is* the project; an extra nesting level adds
  nothing. The spec's `agentteam/` was illustrative. Module names follow the
  spec's suggested structure otherwise.
- **SQLite via the stdlib `sqlite3` module**, file at `backend/db.sqlite`
  (gitignored). Zero extra deps, matches spec. Schema applied idempotently on
  startup (`CREATE TABLE IF NOT EXISTS`) — no migration framework yet.
- **`pydantic-ai-slim[openai]` (resolved to 1.106.0), not full `pydantic-ai`.**
  Slim core includes `FunctionModel`/`TestModel` (our test seam) and the OpenAI
  provider covers LM Studio's OpenAI-compatible endpoint. Avoids pulling every
  provider SDK we don't use. Reversible: add extras as providers are needed.
- **Module named `models_domain.py`, not `models.py`** (spec's name), to avoid
  collision with the Phase 2 `models.py` (per-agent *model* resolution). Domain
  data shapes vs. LLM-model wiring are different concerns; keeping the names
  distinct prevents confusion.
- **`create_app(db_path, repo_path)` factory** so tests inject a temp DB + temp
  repo and exercise the real lifespan via FastAPI `TestClient`. The clock is
  injected into `TeamStore`/`SessionManager` (`fake_clock` fixture) for
  deterministic timestamps. This is the dependency-injection discipline the spec
  requires for `FunctionModel` tests later.
- **Default session repo = gitignored `workspace/`** (overridable via
  `AGENT_GRAPHS_REPO`). Gives agents a real folder to edit out of the box
  without pointing at anything important. The auto-created team has a single
  entry-point "lead" agent so a team always satisfies the ">=1 entry point" rule.

### 2026-06-09 — Phase 0 complete

- 13 tests pass; real uvicorn server verified (`/health`, `/api/session`,
  `/api/team`); frontend builds (`tsc -b && vite build`). The per-session
  ownership of lock/gateway/bus/registry is asserted by
  `test_infrastructure_is_per_session_not_global` — the "nothing is a global
  singleton" invariant is now test-enforced, not just intended.
