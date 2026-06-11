# backend/storage/ — SQLite persistence

`db.py` owns *schema and connections only*; table CRUD lives with the component
that owns the table — `teams.py` (TeamStore) and `agent_state.py`
(AgentStateStore) here, the task store in `runtime/tasks.py`, the message log
in `agents/a2a.py`.

- **`DEFAULT_DB_PATH` is `backend/db.sqlite`** (one level above this package) —
  it holds the user's experimentation data; never relocate or delete it.
- Clocks are injected (`util.iso_now` default) so timestamps are deterministic
  in tests.
- Agent histories serialize via `ModelMessagesTypeAdapter` (stable JSON), so a
  stored history can be handed straight back as `message_history`.
- Schema changes: `init_db` is idempotent `CREATE TABLE IF NOT EXISTS` — there
  is no migration framework; adding columns needs a manual migration story for
  the user's existing db.sqlite.
