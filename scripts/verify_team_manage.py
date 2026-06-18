"""Browser verification for the Team Manager (rename / describe / delete /
create + search + block-if-bound), plus the opencode harness default.

Drives the real app: onboards a session, opens the header "Manage teams" dialog,
and exercises every action, asserting via the API that each change persisted.
The team a session is bound to must show an "in use" tag with delete DISABLED
(the backend 409s anyway); an unbound team must delete cleanly.

Assumes a backend + Vite are up; point at an isolated stack via AG_UI_URL
(defaults to :5181, e.g. a fresh backend on :8011 + `npm run dev -- --port 5181`
with AG_BACKEND=http://127.0.0.1:8011). Screenshots in /tmp/ag_shots/.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

URL = os.environ.get("AG_UI_URL", "http://localhost:5181")
REPO = "/tmp/ag_team_manage_repo"
SHOTS = Path("/tmp/ag_shots")
SHOTS.mkdir(parents=True, exist_ok=True)


def _teams():
    return json.load(urllib.request.urlopen(f"{URL}/api/teams"))["teams"]


def main() -> int:
    failures: list[str] = []
    Path(REPO).mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        page = p.chromium.launch().new_page(viewport={"width": 1280, "height": 860})
        page.on("dialog", lambda d: d.accept("QA Team"))  # window.prompt → first team name

        # --- onboard: create a team + launch a session (native, hermetic) ------
        page.goto(URL, wait_until="load")
        page.get_by_text("Launch a session").wait_for(timeout=10000)
        create = page.get_by_role("button", name="Create your first team")
        if create.is_visible():
            create.click()
            page.wait_for_timeout(500)

        # opencode is the NEW default; assert it, then pick native for this flow
        harness_sel = page.locator("label:has(span.field-label:text-is('Agent harness')) select")
        if harness_sel.input_value() != "opencode":
            failures.append(f"onboarding harness default {harness_sel.input_value()!r}, expected opencode")
        harness_sel.select_option("native")
        page.get_by_placeholder("/Users/you/code/my-project").fill(REPO)
        page.get_by_role("button", name="Launch session").click()
        page.locator("button.fab").wait_for(state="visible", timeout=10000)
        page.wait_for_timeout(500)

        # --- open the Manager from the header ----------------------------------
        page.locator("button[title^='Manage teams']").click()
        dialog = page.get_by_role("dialog", name="Manage teams")
        dialog.wait_for(state="visible", timeout=4000)
        page.wait_for_timeout(300)
        page.screenshot(path=str(SHOTS / "tm_01_open.png"))

        # --- create a new team (inline form: name + description) ---------------
        page.get_by_role("button", name="+ New team").click()
        page.get_by_label("New team name").fill("Docs Crew")
        page.get_by_label("New team description").fill("writes the manuals")
        page.get_by_role("button", name="Create", exact=True).click()
        page.wait_for_timeout(600)
        created = [t for t in _teams() if t["name"] == "Docs Crew"]
        if not created or created[0]["description"] != "writes the manuals":
            failures.append(f"create did not persist name+description: {created}")
        else:
            print("create: 'Docs Crew' + description persisted")
        page.screenshot(path=str(SHOTS / "tm_02_created.png"))

        rows = page.locator(".team-row")
        if rows.count() < 2:
            failures.append(f"expected ≥2 team rows, got {rows.count()}")
            print("FAILURES:\n  - " + "\n  - ".join(failures))
            return 1

        # row 0 = the session's team (QA Team), row 1 = Docs Crew (created 2nd)
        session_row, docs_row = rows.nth(0), rows.nth(1)

        # --- the session's team is "in use": chip + WHICH session, delete OFF --
        if not session_row.get_by_text("in use").is_visible():
            failures.append("session's team is missing the 'in use' tag")
        # names the bound session (repo · mode) so the user knows where to rebind
        if not session_row.get_by_text("ag_team_manage_repo · parallel", exact=False).is_visible():
            failures.append("in-use team does not name the bound session (repo · mode)")
        else:
            print("in-use team names its session (repo · mode)")
        # agent count is surfaced (starter team has 1 lead)
        if not session_row.get_by_text("1 agent", exact=False).first.is_visible():
            failures.append("team row does not show the agent count")
        else:
            print("team row shows agent count")
        del_btn = session_row.get_by_label("Delete team")
        if not del_btn.is_disabled():
            failures.append("delete is ENABLED on the in-use (session) team — must be blocked")
        else:
            print("block-if-bound: in-use team's delete is disabled + tagged")

        # --- rename Docs Crew inline (description must be preserved) -----------
        name_in = docs_row.get_by_label("Team name")
        name_in.fill("Docs Team")
        name_in.press("Enter")  # commit (blur)
        page.wait_for_timeout(500)
        t = next((x for x in _teams() if x["id"] == created[0]["id"]), None)
        if not t or t["name"] != "Docs Team" or t["description"] != "writes the manuals":
            failures.append(f"rename did not persist / clobbered description: {t}")
        else:
            print("rename: name updated, description preserved")

        # --- edit description inline (name must be preserved) ------------------
        desc_in = docs_row.get_by_label("Team description")
        desc_in.fill("now the API reference")
        desc_in.press("Enter")
        page.wait_for_timeout(500)
        t = next((x for x in _teams() if x["id"] == created[0]["id"]), None)
        if not t or t["description"] != "now the API reference" or t["name"] != "Docs Team":
            failures.append(f"description edit did not persist / clobbered name: {t}")
        else:
            print("describe: description updated, name preserved")

        # --- search filters the list ------------------------------------------
        page.get_by_label("Search teams").fill("docs")
        page.wait_for_timeout(300)
        if page.locator(".team-row").count() != 1:
            failures.append(f"search 'docs' should leave 1 row, got {page.locator('.team-row').count()}")
        else:
            print("search: filters to matching team")
        page.screenshot(path=str(SHOTS / "tm_03_search.png"))
        page.get_by_label("Search teams").fill("")
        page.wait_for_timeout(200)

        # --- delete the unbound team (two-step inline confirm) -----------------
        rows.nth(1).get_by_label("Delete team").click()
        page.wait_for_timeout(150)
        rows.nth(1).get_by_role("button", name="Delete", exact=True).click()
        page.wait_for_timeout(600)
        if any(x["name"] == "Docs Team" for x in _teams()):
            failures.append("unbound team was not deleted")
        else:
            print("delete: unbound team removed")
        page.screenshot(path=str(SHOTS / "tm_04_after_delete.png"))

        page.get_by_role("button", name="Close").click()
        page.wait_for_timeout(200)
        if page.get_by_role("dialog", name="Manage teams").is_visible():
            failures.append("dialog did not close on Close")

        page.context.browser.close()

    if failures:
        print("FAILURES:\n  - " + "\n  - ".join(failures))
        return 1
    print("OK. Screenshots in", SHOTS)
    return 0


if __name__ == "__main__":
    sys.exit(main())
