"""AGENTS.md / CLAUDE.md project-context loading (Claude Code-style memory):
reading a file injects the context files governing it — once per conversation,
root-first, AGENTS.md shadowing CLAUDE.md per directory."""

from __future__ import annotations

from pathlib import Path

from backend.agents.context_files import ProjectContext, governing_context_files
from backend.agents.tools import DevTools
from backend.domain.models import Capabilities


def _repo(tmp_path: Path) -> Path:
    (tmp_path / "backend" / "api").mkdir(parents=True)
    (tmp_path / "CLAUDE.md").write_text("ROOT RULES")
    (tmp_path / "backend" / "CLAUDE.md").write_text("BACKEND CLAUDE RULES")
    (tmp_path / "backend" / "AGENTS.md").write_text("BACKEND AGENT RULES")
    (tmp_path / "backend" / "api" / "CLAUDE.md").write_text("API RULES")
    (tmp_path / "backend" / "api" / "routes.py").write_text("x = 1\n")
    (tmp_path / "top.py").write_text("y = 2\n")
    return tmp_path


def _tools(repo: Path) -> DevTools:
    return DevTools(repo, Capabilities(), project_context=ProjectContext(repo))


def test_governing_files_walk_root_to_leaf_and_agents_md_wins(tmp_path):
    repo = _repo(tmp_path)
    files = governing_context_files(repo, repo / "backend" / "api" / "routes.py")
    assert files == [
        repo / "CLAUDE.md",
        repo / "backend" / "AGENTS.md",  # shadows backend/CLAUDE.md entirely
        repo / "backend" / "api" / "CLAUDE.md",
    ]
    assert governing_context_files(repo, Path("/elsewhere/f.py")) == []


def test_read_injects_delimited_scoped_blocks_with_token_last(tmp_path):
    repo = _repo(tmp_path)
    out = _tools(repo).read_file("backend/api/routes.py")
    # all three governing files, general -> specific, before the file body
    assert out.index("ROOT RULES") < out.index("BACKEND AGENT RULES") < out.index("API RULES")
    assert "BACKEND CLAUDE RULES" not in out  # AGENTS.md shadows it
    assert "[project context from CLAUDE.md — these instructions apply ONLY to the whole repository]" in out
    assert (
        "[project context from backend/AGENTS.md — these instructions apply ONLY to "
        "the 'backend/' folder and everything below it]" in out
    )
    assert "[end of project context from backend/api/CLAUDE.md]" in out
    # the edit token MUST stay the last line — models copy the trailing token
    assert out.splitlines()[-1].startswith("[edit-token ")


def test_each_context_file_enters_the_conversation_once(tmp_path):
    repo = _repo(tmp_path)
    tools = _tools(repo)
    first = tools.read_file("top.py")
    assert "ROOT RULES" in first
    again = tools.read_file("top.py")
    assert "ROOT RULES" not in again
    # a deeper read adds only the NEW context, not the already-loaded root
    deeper = tools.read_file("backend/api/routes.py")
    assert "ROOT RULES" not in deeper
    assert "BACKEND AGENT RULES" in deeper and "API RULES" in deeper


def test_reading_a_context_file_itself_does_not_self_inject(tmp_path):
    repo = _repo(tmp_path)
    tools = _tools(repo)
    out = tools.read_file("backend/AGENTS.md")
    # the body is the content being read; no wrapped duplicate of itself
    assert "[project context from backend/AGENTS.md" not in out
    assert "ROOT RULES" in out  # the root context still arrives
    # and it counts as loaded: later reads under backend/ don't re-inject it
    later = tools.read_file("backend/api/routes.py")
    assert "BACKEND AGENT RULES" not in later


def test_reset_lets_context_reenter_after_history_clear(tmp_path):
    repo = _repo(tmp_path)
    ctx = ProjectContext(repo)
    tools = DevTools(repo, Capabilities(), project_context=ctx)
    assert "ROOT RULES" in tools.read_file("top.py")
    assert "ROOT RULES" not in tools.read_file("top.py")
    ctx.reset()  # what RunningAgent.replace_history does on clear/summarize
    assert "ROOT RULES" in tools.read_file("top.py")


def test_oversized_context_files_are_truncated(tmp_path):
    repo = _repo(tmp_path)
    (repo / "CLAUDE.md").write_text("R" * 50_000)
    out = _tools(repo).read_file("top.py")
    assert "... (truncated)" in out
    assert len(out) < 20_000


def test_devtools_without_a_tracker_injects_nothing(tmp_path):
    repo = _repo(tmp_path)
    out = DevTools(repo, Capabilities()).read_file("backend/api/routes.py")
    assert "project context" not in out


# --- through the real worker (the production wiring) ---------------------------


async def test_worker_runs_inject_context_and_clear_reinjects(conn, fake_clock, repo):
    """End-to-end on a real RunningAgent: the model's read_file result carries
    the governing CLAUDE.md once, and clearing the history (replace_history)
    lets it re-enter the next run's context."""
    from pydantic_ai.messages import TextPart, ToolCallPart, ToolReturnPart

    from backend.domain.models import AgentSpec, GraphNode, TeamGraph
    from backend.runtime.sessions import SessionManager
    from backend.runtime.workers import RunningAgent
    from backend.storage.teams import TeamStore
    from tests.conftest import make_sequence_model

    (repo / "CLAUDE.md").write_text("ROOT RULES")
    (repo / "main.py").write_text("z = 3\n")

    teams = TeamStore(conn, clock=fake_clock)
    spec = AgentSpec(id="a", name="A", capabilities=Capabilities.from_level("read-write"))
    team = teams.create("T", TeamGraph(nodes=[GraphNode(spec=spec)]))
    session = SessionManager(conn, clock=fake_clock).create_session(
        team_id=team.id, repo_path=repo, graph=team.graph
    )
    # one read per run: run 1 starts at turn 0, run 2 (with history) at turn 2,
    # run 3 (cleared history) back at turn 0
    model = make_sequence_model(
        [
            [ToolCallPart("read_file", {"path": "main.py"})],
            [TextPart("done")],
            [ToolCallPart("read_file", {"path": "main.py"})],
            [TextPart("done again")],
        ]
    )
    ra = RunningAgent(session=session, spec=spec, model=model)
    session.registry.attach_running("a", ra)

    def tool_returns() -> list[str]:
        return [
            str(p.content)
            for m in ra.messages
            for p in getattr(m, "parts", [])
            if isinstance(p, ToolReturnPart) and p.tool_name == "read_file"
        ]

    await ra.run_once("read main.py")
    assert "ROOT RULES" in tool_returns()[0]

    # same conversation: the block must not repeat
    await ra.run_once("read it again")
    assert "ROOT RULES" not in tool_returns()[1]

    # cleared conversation: the guidance is gone from context, so it returns
    ra.replace_history([])
    await ra.run_once("read once more")
    assert "ROOT RULES" in tool_returns()[0]
    await ra.stop()
