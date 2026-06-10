"""Drive the running frontend with Playwright and screenshot key states.

Covers the explicit flow: onboarding (no sessions) -> create team -> launch
session -> control room. Assumes backend (:8000) + Vite (:5173) are running and
the DB is fresh (empty). Screenshots in /tmp/ag_shots/.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

SHOTS = Path("/tmp/ag_shots")
SHOTS.mkdir(parents=True, exist_ok=True)
# Override to point at an isolated stack (e.g. AG_UI_URL=http://127.0.0.1:5174)
# when the default ports are taken by a live dev session.
URL = os.environ.get("AG_UI_URL", "http://127.0.0.1:5173")
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

        # sidebar resize: drag the handle 200px left and confirm it widens
        tabs = page.locator("div.tabs")
        before = tabs.bounding_box()
        handle = page.get_by_title("Drag to resize")
        hb = handle.bounding_box()
        if hb and before:
            page.mouse.move(hb["x"] + hb["width"] / 2, hb["y"] + 300)
            page.mouse.down()
            page.mouse.move(hb["x"] - 200, hb["y"] + 300, steps=5)
            page.mouse.up()
            page.wait_for_timeout(300)
            after = tabs.bounding_box()
            if not after or after["width"] < before["width"] + 150:
                failures.append(
                    f"sidebar did not widen on drag (before={before['width']:.0f}, "
                    f"after={(after or {}).get('width', 0):.0f})"
                )
            else:
                print(f"sidebar resized {before['width']:.0f} -> {after['width']:.0f}")
        else:
            failures.append("sidebar resize handle not found")
        page.screenshot(path=str(SHOTS / "04_sidebar_resized.png"))

        # task board: create a task, open its detail drawer
        page.get_by_role("button", name="Tasks").click()
        page.wait_for_timeout(500)
        task_prompt = "Verify the detail drawer shows this full prompt text."
        page.get_by_placeholder("What should the team do?").fill(task_prompt)
        page.get_by_role("button", name="Create task").click()
        page.wait_for_timeout(800)
        page.screenshot(path=str(SHOTS / "05_task_board.png"))
        try:
            page.locator("button[aria-label^='Task:']").first.click()
            page.get_by_text("PROMPT", exact=True).wait_for(timeout=4000)
            if not page.get_by_text(task_prompt).last.is_visible():
                failures.append("task detail drawer does not show the full prompt")
        except Exception:
            failures.append("task detail drawer did not open on card click")
        page.wait_for_timeout(300)
        page.screenshot(path=str(SHOTS / "06_task_detail.png"))

        # retry button on a blocked task — only reachable when the model is
        # down (run the isolated stack with AGENT_GRAPHS_LMSTUDIO_URL at a dead
        # port so the task blocks fast); skipped when the task keeps running.
        retry = page.get_by_role("button", name="Retry task")
        try:
            retry.wait_for(state="visible", timeout=15000)
            page.screenshot(path=str(SHOTS / "07_task_blocked.png"))
            with page.expect_response(lambda r: r.url.endswith("/retry")) as resp:
                retry.click()
            if resp.value.status != 200:
                failures.append(f"retry endpoint returned {resp.value.status}")
            else:
                print("retry: button clicked, endpoint returned 200")
            page.wait_for_timeout(600)
            page.screenshot(path=str(SHOTS / "08_task_retry.png"))
        except Exception:
            print("retry: task never reached blocked (model reachable?) — check skipped")

        # agent history view: reload the app, reopen the agent — the system
        # context (what every model request carries) must render from the
        # backend, with the Clear/Summarize controls present
        page.goto(URL, wait_until="load")
        page.locator("button.fab").wait_for(state="visible", timeout=10000)
        page.get_by_text("Lead", exact=True).first.click()
        page.wait_for_timeout(300)
        page.get_by_role("button", name="Agent").click()
        page.wait_for_timeout(600)
        try:
            ctx = page.get_by_text("System context", exact=False).first
            ctx.wait_for(timeout=5000)
            ctx.click()
            page.wait_for_timeout(300)
            if not page.get_by_text("Today's date", exact=False).first.is_visible():
                failures.append("system context detail does not show the environment section")
        except Exception:
            failures.append("System context section missing in Agent tab")
        for name in ("Clear", "Summarize"):
            if not page.get_by_role("button", name=name).first.is_visible():
                failures.append(f"{name} button missing in Agent tab")
        page.screenshot(path=str(SHOTS / "09_agent_context.png"))

        browser.close()

    if failures:
        print("FAILURES:\n  - " + "\n  - ".join(failures))
        return 1
    print("OK. Screenshots in", SHOTS)
    return 0


if __name__ == "__main__":
    sys.exit(main())
