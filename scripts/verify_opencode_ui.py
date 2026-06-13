"""Real end-to-end browser test of the OpenCode harness: drive the actual UI
(onboarding → launch an OPENCODE session → run a task) against a backend wired
to the REAL OpenCode server + a local LM Studio model, and assert the agent did
real work (file created) with the run visible in the transcript.

Unlike verify_ui.py (native, dead-model, structure-only), this needs:
  - LM Studio running with a small tool-capable model (qwen/qwen3-1.7b),
  - the `opencode` binary,
  - a backend started with AGENT_GRAPHS_CALLBACK_URL pointing at itself.

Env: AG_UI_URL (Vite), AG_OC_REPO (repo dir). Screenshots → /tmp/ag_shots/.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

SHOTS = Path("/tmp/ag_shots")
SHOTS.mkdir(parents=True, exist_ok=True)
URL = os.environ.get("AG_UI_URL", "http://127.0.0.1:5175")
REPO = os.environ.get("AG_OC_REPO", "/tmp/oc_pw_repo")


def main() -> int:
    failures: list[str] = []
    Path(REPO).mkdir(parents=True, exist_ok=True)
    hello = Path(REPO) / "hello.txt"
    if hello.exists():
        hello.unlink()

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        page.on("dialog", lambda d: d.accept("OC Squad"))

        page.goto(URL, wait_until="load")
        page.get_by_text("Launch a session").wait_for(timeout=15000)
        page.wait_for_timeout(400)

        create = page.get_by_role("button", name="Create your first team")
        if create.is_visible():
            create.click()
            page.wait_for_timeout(600)

        page.get_by_placeholder("/Users/you/code/my-project").fill(REPO)
        page.wait_for_timeout(150)
        # choose the OpenCode harness
        harness_sel = page.locator("label:has(span.field-label:text-is('Agent harness')) select")
        harness_sel.select_option("opencode")
        page.wait_for_timeout(150)
        page.get_by_role("button", name="Launch session").click()

        # control room up (opencode server boots + bun-installs the tool dep — be patient)
        page.locator("button.fab").wait_for(state="visible", timeout=60000)
        page.wait_for_timeout(800)
        page.screenshot(path=str(SHOTS / "oc_01_control_room.png"))

        # the header must show the opencode-harness chip
        if not page.get_by_text("opencode harness", exact=True).first.is_visible():
            failures.append("'opencode harness' chip not shown in header")
        else:
            print("opencode harness chip present")

        # run a real task on the lead agent
        page.get_by_text("Lead", exact=True).first.click()
        page.wait_for_timeout(300)
        page.get_by_role("button", name="Agent", exact=True).click()
        page.wait_for_timeout(300)
        page.locator("textarea").first.fill(
            "Create a file named hello.txt containing exactly the word: banana . Use the write tool, then stop."
        )
        page.get_by_role("button", name="Run").click()
        print("task submitted; waiting for the OpenCode run (server boot + model)…")

        # wait for the agent to actually create the file (the real-work signal)
        deadline = time.time() + 180
        while time.time() < deadline:
            if hello.exists():
                break
            page.wait_for_timeout(1000)
        page.wait_for_timeout(1500)
        page.screenshot(path=str(SHOTS / "oc_02_after_run.png"))

        if hello.exists():
            content = hello.read_text().strip()
            print(f"hello.txt created: {content!r}")
            if "banana" not in content:
                failures.append(f"hello.txt content unexpected: {content!r}")
        else:
            failures.append("agent did not create hello.txt within 180s")

        # the transcript should show tool activity / a completed run
        body = page.locator("body").inner_text()
        if "write" not in body.lower() and "hello.txt" not in body.lower():
            failures.append("transcript shows no sign of the write tool / file")
        else:
            print("transcript reflects the tool run")

        browser.close()

    if failures:
        print("\nFAILURES:")
        for f in failures:
            print(" -", f)
        return 1
    print("\nOK — live OpenCode harness E2E passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
