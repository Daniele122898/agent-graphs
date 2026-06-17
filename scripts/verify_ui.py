"""Drive the running frontend with Playwright and screenshot key states.

Covers the explicit flow: onboarding (no sessions) -> create team -> launch
session -> control room. Assumes backend (:8000) + Vite (:5173) are running and
the DB is fresh (empty). Screenshots in /tmp/ag_shots/.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

SHOTS = Path("/tmp/ag_shots")
SHOTS.mkdir(parents=True, exist_ok=True)
# Override to point at an isolated stack (e.g. AG_UI_URL=http://127.0.0.1:5174)
# when the default ports are taken by a live dev session.
URL = os.environ.get("AG_UI_URL", "http://127.0.0.1:5173")
REPO = "/tmp/ag_ui_repo"
# The backend is reachable through the Vite proxy at the same origin.
API = URL


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
        # the harness selector must be present (native | opencode); we launch
        # native (default) so the rest of the flow is harness-independent.
        harness_sel = page.locator("label:has(span.field-label:text-is('Agent harness')) select")
        if not harness_sel.is_visible():
            failures.append("Agent harness selector missing from onboarding")
        else:
            opts = harness_sel.locator("option").all_inner_texts()
            if not any("opencode" in o for o in opts) or not any("native" in o for o in opts):
                failures.append(f"harness options missing native/opencode: {opts}")
        page.get_by_role("button", name="Launch session").click()

        # control room: the add-agent FAB should appear
        page.locator("button.fab").wait_for(state="visible", timeout=10000)
        page.wait_for_timeout(800)
        page.screenshot(path=str(SHOTS / "02_control_room.png"))

        # the "+ Session" popover (SessionSwitcher) must ALSO carry the harness
        # selector — distinct launch path from onboarding.
        page.get_by_role("button", name="+ Session").click()
        page.wait_for_timeout(300)
        sw_harness = page.locator("label:has(span.field-label:text-is('Agent harness')) select")
        if not sw_harness.is_visible():
            failures.append("Agent harness selector missing from the '+ Session' popover")
        else:
            print("'+ Session' popover has the harness selector")
        page.get_by_role("button", name="Cancel").click()
        page.wait_for_timeout(200)

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
        page.get_by_role("button", name="Agent", exact=True).click()
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
        # per-task timeout field (hours) — set a distinctive value and confirm it
        # persists onto the created task via the API.
        timeout_input = page.locator("input[type='number']").first
        if not timeout_input.is_visible():
            failures.append("per-task timeout (hours) field missing from the New Task dialog")
        else:
            timeout_input.fill("3")
        page.get_by_role("button", name="Create task").click()
        page.wait_for_timeout(800)
        page.screenshot(path=str(SHOTS / "05_task_board.png"))
        try:
            sid = json.load(urllib.request.urlopen(f"{API}/api/sessions"))["sessions"][0]["id"]
            tasks = json.load(urllib.request.urlopen(f"{API}/api/tasks?session_id={sid}"))["tasks"]
            newest = tasks[-1]
            if newest.get("timeout_hours") != 3:
                failures.append(f"task timeout_hours did not persist (got {newest.get('timeout_hours')})")
            else:
                print("per-task timeout (hours) field persisted to the task")
        except Exception as e:  # noqa: BLE001
            failures.append(f"could not verify task timeout persistence: {e}")
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
        page.get_by_role("button", name="Agent", exact=True).click()
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

        # model backend picker: the Capabilities tab has a Backend dropdown
        # ABOVE the model dropdown; switching to DeepSeek reveals the thinking
        # controls (toggle + effort) and the choice persists into the spec.
        page.get_by_role("button", name="Capabilities").click()
        page.wait_for_timeout(500)

        def field_select(label: str):
            """The <select> inside a ui.Field by its label text (the wrapping
            <label> association is not exposed to get_by_label)."""
            return page.locator(f"label:has(span.field-label:text-is('{label}')) select")

        backend_sel = field_select("Backend")
        model_sel = field_select("Model")
        if not backend_sel.is_visible():
            failures.append("Backend dropdown missing in Capabilities tab")
        else:
            bb_box, mb_box = backend_sel.bounding_box(), model_sel.bounding_box()
            if bb_box and mb_box and bb_box["y"] > mb_box["y"]:
                failures.append("Backend dropdown is BELOW the model dropdown")
            page.screenshot(path=str(SHOTS / "17_capabilities_backend.png"))
            backend_sel.select_option("deepseek")
            page.wait_for_timeout(800)  # model list fetch (live API or error)
            think_sel = field_select("Thinking")
            if not think_sel.is_visible():
                failures.append("Thinking dropdown missing after switching to DeepSeek")
            else:
                think_sel.select_option("on")
                page.wait_for_timeout(300)
                effort_sel = field_select("Thinking effort")
                if not effort_sel.is_visible():
                    failures.append("Thinking effort dropdown missing with thinking on")
                else:
                    effort_sel.select_option("max")
                    page.wait_for_timeout(900)  # debounced graph save

                    teams = json.load(urllib.request.urlopen(f"{API}/api/teams"))["teams"]
                    graph = json.load(
                        urllib.request.urlopen(f"{API}/api/teams/{teams[0]['id']}/graph")
                    )
                    lead_spec = next(n["spec"] for n in graph["nodes"] if n["spec"]["id"] == "lead")
                    if not (
                        lead_spec["model"].startswith("deepseek:")
                        and lead_spec["thinking"] is True
                        and lead_spec["thinking_effort"] == "max"
                    ):
                        failures.append(f"backend/thinking choice did not persist: {lead_spec}")
                    else:
                        print("backend switch + thinking choice persisted to the graph")
            page.screenshot(path=str(SHOTS / "18_deepseek_thinking.png"))
            # back to the local default so later steps don't depend on a key
            backend_sel.select_option("lmstudio")
            page.wait_for_timeout(900)

        # floating edges + edge selection: add a second agent, draw A→B and
        # B→A, confirm the two paths differ (reciprocal arcs, not an overlap),
        # then click one edge and confirm the sidebar jumps to the Links tab.
        page.get_by_role("button", name="Canvas").click()
        page.wait_for_timeout(400)
        page.locator("button.fab").click()
        page.wait_for_timeout(500)
        lead = page.locator(".react-flow__node", has_text="Lead").first
        newbie = page.locator(".react-flow__node", has_text="New Agent").first
        # park the new node BELOW the lead (staying left of the sidebar, which
        # may be wide after the resize test) so both handles stay reachable
        nb = newbie.bounding_box()
        lb = lead.bounding_box()
        if nb and lb:
            # The spawn position can land (partly) under the sidebar; a grab
            # there hits the sidebar instead of the node and silently fails
            # (it text-selects the panel). Grab a point that is verifiably on
            # the node AND left of the sidebar, verify the node actually
            # moved, and retry a couple of times if it didn't.
            tabs_box = page.locator("div.tabs").first.bounding_box()
            sidebar_left = tabs_box["x"] if tabs_box else (vp["width"] * 0.55 if vp else 700)
            target = (min(lb["x"] + 90, sidebar_left - 300), lb["y"] + 240)
            for _attempt in range(3):
                nb = newbie.bounding_box()
                if not nb:
                    break
                grab_x = min(nb["x"] + nb["width"] / 2, sidebar_left - 20)
                page.mouse.move(grab_x, nb["y"] + 10)
                page.mouse.down()
                page.mouse.move(target[0], target[1], steps=8)
                page.mouse.up()
                page.wait_for_timeout(300)
                moved = newbie.bounding_box()
                if moved and abs(moved["x"] - nb["x"]) + abs(moved["y"] - nb["y"]) > 40:
                    break
            # clear any accidental text selection so later clicks are clean
            page.evaluate("window.getSelection()?.removeAllRanges()")

            def drag_handle(frm, to, frm_sel, to_sel):
                src = frm.locator(frm_sel).first.bounding_box()
                dst = to.locator(to_sel).first.bounding_box()
                if not src or not dst:
                    return False
                page.mouse.move(src["x"] + src["width"] / 2, src["y"] + src["height"] / 2)
                page.mouse.down()
                page.mouse.move(dst["x"] + dst["width"] / 2, dst["y"] + dst["height"] / 2, steps=10)
                page.mouse.up()
                page.wait_for_timeout(300)
                return True

            drag_handle(lead, newbie, ".react-flow__handle-right", ".react-flow__handle-left")
            drag_handle(newbie, lead, ".react-flow__handle-right", ".react-flow__handle-left")
            page.wait_for_timeout(400)
            paths = page.locator(".react-flow__edge path.react-flow__edge-path")
            if paths.count() < 2:
                failures.append(f"expected 2 edges after reciprocal connect, got {paths.count()}")
            else:
                # geometric separation, not string difference — reversed paths
                # can differ as strings while overlapping exactly
                centers = page.eval_on_selector_all(
                    ".react-flow__edge path.react-flow__edge-path",
                    "els => els.map(e => { const b = e.getBBox(); return [b.x + b.width/2, b.y + b.height/2]; })",
                )
                dist = ((centers[0][0] - centers[1][0]) ** 2 + (centers[0][1] - centers[1][1]) ** 2) ** 0.5
                if dist < 12:
                    failures.append(f"reciprocal edges overlap (midpoint distance {dist:.1f}px)")
                else:
                    print(f"reciprocal edges render {dist:.0f}px apart")
            page.screenshot(path=str(SHOTS / "13_reciprocal_edges.png"))

            # bend handle: drag one edge's midpoint dot PERPENDICULAR to the
            # node axis (drag along the axis projects to ~0 and snaps back)
            bends = page.locator("div[title='Drag to bend this link']")
            bb = bends.first.bounding_box()
            lb2 = lead.bounding_box()
            nb2 = newbie.bounding_box()
            if bb and lb2 and nb2:
                ax = (nb2["x"] + nb2["width"] / 2) - (lb2["x"] + lb2["width"] / 2)
                ay = (nb2["y"] + nb2["height"] / 2) - (lb2["y"] + lb2["height"] / 2)
                alen = (ax * ax + ay * ay) ** 0.5 or 1
                px_, py_ = -ay / alen, ax / alen
                sx = bb["x"] + bb["width"] / 2
                sy = bb["y"] + bb["height"] / 2
                page.mouse.move(sx, sy)
                page.mouse.down()
                page.mouse.move(sx + px_ * 90, sy + py_ * 90, steps=6)
                page.mouse.up()
                page.wait_for_timeout(400)
                after_bb = bends.first.bounding_box()
                moved = after_bb and abs(after_bb["x"] - bb["x"]) + abs(after_bb["y"] - bb["y"]) > 25
                if moved:
                    print("edge bend handle drags")
                else:
                    failures.append("edge bend handle did not move on drag")
                page.screenshot(path=str(SHOTS / "13b_edge_bent.png"))
            else:
                failures.append("edge bend handle or nodes not found")

            # click an edge → sidebar should land on the Links tab with the
            # row. Click an exact point ON the curve (bbox centers sit off a
            # bent path), away from the midpoint bend handle.
            pt = page.eval_on_selector(
                ".react-flow__edge path.react-flow__edge-path",
                """e => {
                    const p = e.getPointAtLength(e.getTotalLength() * 0.3);
                    const m = e.getScreenCTM();
                    return { x: p.x * m.a + p.y * m.c + m.e, y: p.x * m.b + p.y * m.d + m.f };
                }""",
            )
            page.mouse.click(pt["x"], pt["y"])
            page.wait_for_timeout(400)
            active_tab = page.locator("button.tab-active").inner_text()
            if active_tab != "Links":
                failures.append(f"edge click landed on '{active_tab}' tab, expected Links")
            elif not page.get_by_text("Can consult").first.is_visible():
                failures.append("Links tab content missing after edge click")
            else:
                print("edge click → Links tab with editable link")
            page.screenshot(path=str(SHOTS / "14_edge_selected.png"))

            # delete an agent via the red trash in the sidebar tab bar: it must
            # remove the node AND every link to/from it, and persist.
            nodes_before = page.locator(".react-flow__node").count()
            page.locator(".react-flow__node", has_text="New Agent").first.click()
            page.wait_for_timeout(300)
            trash = page.get_by_role("button", name="Delete agent New Agent")
            if not trash.is_visible():
                failures.append("delete (trash) button not shown for the selected agent")
            else:
                trash.click()  # the page dialog handler auto-accepts the confirm
                page.wait_for_timeout(900)  # debounced save
                nodes_after = page.locator(".react-flow__node").count()
                edges_after = page.locator(".react-flow__edge path.react-flow__edge-path").count()
                if nodes_after != nodes_before - 1:
                    failures.append(f"agent not removed: {nodes_before} -> {nodes_after} nodes")
                if edges_after != 0:
                    failures.append(f"links to the deleted agent remain: {edges_after} edges")
                # persisted?
                teams = json.load(urllib.request.urlopen(f"{API}/api/teams"))["teams"]
                graph = json.load(urllib.request.urlopen(f"{API}/api/teams/{teams[0]['id']}/graph"))
                if len(graph["nodes"]) != nodes_before - 1 or len(graph["edges"]) != 0:
                    failures.append(
                        f"delete not persisted: {len(graph['nodes'])} nodes / {len(graph['edges'])} edges"
                    )
                if not failures or "delete" not in failures[-1]:
                    print("delete agent → node + all its links removed and persisted")
            page.screenshot(path=str(SHOTS / "19_after_delete.png"))
        else:
            failures.append("could not locate nodes for the edge test")


        browser.close()

    if failures:
        print("FAILURES:\n  - " + "\n  - ".join(failures))
        return 1
    print("OK. Screenshots in", SHOTS)
    return 0


if __name__ == "__main__":
    sys.exit(main())
