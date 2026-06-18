"""Browser regression for the team-save data-loss class (the bug the team-UX
changes both fixed and re-introduced): editing a team then switching the header
team-selector within the autosave debounce window must NOT drop the edits. The
hook's flush-on-switch must persist the outgoing team before the canvas reloads.

Assumes a backend + Vite are up (defaults below); point at an isolated stack via
AG_UI_URL. Drives the real app and asserts via the API that the edit persisted.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request

from playwright.sync_api import sync_playwright

URL = os.environ.get("AG_UI_URL", "http://localhost:5181")
REPO = "/tmp/ag_team_save_repo"


def _get(path):
    return json.load(urllib.request.urlopen(f"{URL}{path}"))


def _post(path, body):
    req = urllib.request.Request(
        f"{URL}{path}", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}, method="POST"
    )
    return json.load(urllib.request.urlopen(req))


def main() -> int:
    failures: list[str] = []
    with sync_playwright() as p:
        page = p.chromium.launch().new_page(viewport={"width": 1280, "height": 800})
        page.on("dialog", lambda d: d.accept("Save Test Team"))

        # onboard → create a team + launch a (native) session
        page.goto(URL, wait_until="load")
        page.get_by_text("Launch a session").wait_for(timeout=15000)
        create = page.get_by_role("button", name="Create your first team")
        if create.is_visible():
            create.click()
            page.wait_for_timeout(600)
        page.get_by_placeholder("/Users/you/code/my-project").fill(REPO)
        page.wait_for_timeout(200)
        # UI default is now opencode; this autosave check is harness-independent,
        # so launch native to stay hermetic (no opencode server needed).
        page.locator("label:has(span.field-label:text-is('Agent harness')) select").select_option("native")
        page.get_by_role("button", name="Launch session").click()
        page.locator("button.fab").wait_for(state="visible", timeout=15000)

        team_a = _get("/api/teams")["teams"][0]["id"]
        # a second team so the selector can switch
        team_b = _post("/api/teams", {"name": "Team B"})["id"]
        nodes_a_before = len(_get(f"/api/teams/{team_a}/graph")["nodes"])

        # reload so the header selector lists both teams
        page.goto(URL, wait_until="load")
        page.locator("button.fab").wait_for(state="visible", timeout=15000)
        selector = page.locator("header select").first
        selector.select_option(value=team_a)  # edit team A
        page.wait_for_timeout(200)

        # EDIT: add an agent to team A (a pending debounced save)…
        page.locator("button.fab").click()
        page.wait_for_timeout(120)  # let the save effect stash the snapshot (< 600ms debounce)
        # …then IMMEDIATELY switch the selector to B (within the debounce window)…
        selector.select_option(value=team_b)
        page.wait_for_timeout(150)
        # …and back to A. With the flush-on-switch fix, A's edit was persisted.
        selector.select_option(value=team_a)
        page.wait_for_timeout(800)

        nodes_a_after = len(_get(f"/api/teams/{team_a}/graph")["nodes"])
        if nodes_a_after != nodes_a_before + 1:
            failures.append(
                f"DATA LOSS: team A had {nodes_a_before} nodes, added 1, but after a "
                f"switch-away-and-back it has {nodes_a_after} (edit dropped on team switch)"
            )
        else:
            print(f"OK: edit persisted across team switch ({nodes_a_before} → {nodes_a_after} nodes)")

        # header coherence: the two dropdowns are distinguished by captions, and
        # the save indicator is compact (a short word, NOT "saved — {team name}").
        header = page.locator("header")
        for cap in ("Team", "Session"):
            if not header.get_by_text(cap, exact=True).first.is_visible():
                failures.append(f"header is missing the '{cap}' caption (team/session dropdowns indistinguishable)")
        dot = page.locator("header .savedot")
        if not dot.first.is_visible():
            failures.append("compact save indicator (.savedot) not in the header")
        else:
            txt = dot.first.inner_text()
            if len(txt) > 14:  # "Saved"/"Saving…"/"Save failed" — never the long team name
                failures.append(f"save indicator too long ({txt!r}) — should not include the team name")
            else:
                print("OK: header has Team/Session captions + a compact save dot:", repr(txt))

        page.context.browser.close()

    if failures:
        print("FAILURES:\n  - " + "\n  - ".join(failures))
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
