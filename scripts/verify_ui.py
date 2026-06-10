"""Drive the running frontend with Playwright and screenshot key states.

Covers the explicit flow: onboarding (no sessions) -> create team -> launch
session -> control room. Assumes backend (:8000) + Vite (:5173) are running and
the DB is fresh (empty). Screenshots in /tmp/ag_shots/.
"""

from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

SHOTS = Path("/tmp/ag_shots")
SHOTS.mkdir(parents=True, exist_ok=True)
URL = "http://127.0.0.1:5173"
REPO = "/tmp/ag_ui_repo"


def main() -> int:
    failures: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        # window.prompt -> team name
        page.on("dialog", lambda d: d.accept("Web Squad"))

        page.goto(URL, wait_until="load")
        page.get_by_text("Launch a session").wait_for(timeout=10000)
        page.wait_for_timeout(500)
        page.screenshot(path=str(SHOTS / "01_onboarding.png"))

        # create the first team (no teams yet)
        create = page.get_by_role("button", name="Create your first team")
        if create.is_visible():
            create.click()
            page.wait_for_timeout(600)

        # fill repo + launch
        page.get_by_placeholder("/Users/you/code/my-project").fill(REPO)
        page.wait_for_timeout(200)
        page.get_by_role("button", name="Launch session").click()

        # control room: the add-agent FAB should appear
        page.locator("button.fab").wait_for(state="visible", timeout=10000)
        page.wait_for_timeout(800)
        page.screenshot(path=str(SHOTS / "02_control_room.png"))

        # FAB bottom-left?
        box = page.locator("button.fab").bounding_box()
        vp = page.viewport_size
        if box and vp:
            if box["y"] < vp["height"] / 2:
                failures.append(f"FAB in TOP half (y={box['y']:.0f})")
            if box["x"] > vp["width"] / 2:
                failures.append(f"FAB on RIGHT (x={box['x']:.0f})")
            print(f"FAB at x={box['x']:.0f} y={box['y']:.0f}")

        # select lead, open Agent tab, send a message, confirm user bubble
        page.get_by_text("Lead", exact=True).first.click()
        page.wait_for_timeout(300)
        page.get_by_role("button", name="Agent").click()
        page.wait_for_timeout(300)
        page.locator("textarea").fill("Say hello in one short sentence.")
        page.get_by_role("button", name="Run").click()
        try:
            page.get_by_text("Say hello in one short sentence.").wait_for(timeout=8000)
        except Exception:
            failures.append("user message bubble did not appear")
        page.wait_for_timeout(600)
        page.screenshot(path=str(SHOTS / "03_agent_chat.png"))
        try:
            page.get_by_role("button", name="Stop").click(timeout=1500)
        except Exception:
            pass

        # task board
        page.get_by_role("button", name="Tasks").click()
        page.wait_for_timeout(500)
        page.screenshot(path=str(SHOTS / "04_task_board.png"))

        browser.close()

    if failures:
        print("FAILURES:\n  - " + "\n  - ".join(failures))
        return 1
    print("OK. Screenshots in", SHOTS)
    return 0


if __name__ == "__main__":
    sys.exit(main())
