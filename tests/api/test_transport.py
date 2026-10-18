"""Real transport uses locked websockets; optional browser uses local Chrome/playwright."""

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
from websockets.sync.client import connect


@pytest.fixture
def server():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "shipos.api.server:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--ws",
            "websockets-sansio",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(100):
            if process.poll() is not None:
                pytest.fail("Uvicorn exited before readiness")
            try:
                if httpx.get(base, timeout=0.2).status_code == 200:
                    break
            except httpx.TransportError:
                pass
            time.sleep(0.05)
        else:
            pytest.fail("Uvicorn readiness timed out")
        yield base
    finally:
        process.terminate()
        process.wait(timeout=10)


def test_uvicorn_websocket_round_trip(server):
    state = httpx.post(f"{server}/api/sessions").json()
    with connect(f"{server.replace('http:', 'ws:')}/ws/{state['id']}") as ws:
        assert json.loads(ws.recv(timeout=5))["mode"] == "LIVE"
        ws.send(json.dumps({"op": "admit", "card": "G4", "value": 20}))
        update = json.loads(ws.recv(timeout=5))
        assert update["decision"]["verdict"] == "no"
        assert update["decision"]["binding"]["demand"] >= 20
        ws.send(json.dumps({"op": "scenario", "scenario": "B"}))
        assert json.loads(ws.recv(timeout=5))["snapshot"]["guests"]["G4"]["battery_pct"] == 30
        ws.send(json.dumps({"op": "event", "event": "battery_report", "battery_pct": 12}))
        update = json.loads(ws.recv(timeout=5))
        assert update["snapshot"]["guests"]["G4"]["battery_pct"] == 12
        assert httpx.get(f"{server}/api/sessions/{state['id']}").json() == update
        ws.send(json.dumps({"op": "step", "ticks": 3}))
        assert json.loads(ws.recv(timeout=5))["snapshot"]["t_s"] == 3
        ws.send(json.dumps({"op": "event", "event": "genset_trip"}))
        update = json.loads(ws.recv(timeout=5))
        assert update["snapshot"]["platform"]["gensets_online"] == 1
        ws.send(json.dumps({"op": "replay"}))
        replay = json.loads(ws.recv(timeout=5))
        assert replay["replay"]["byte_identical"]
        assert replay["replay"]["snapshot"] == update["snapshot"]


def test_browser_offline_edit_reset_and_untrusted_text(server):
    playwright = pytest.importorskip("playwright.sync_api")
    chrome = Path(os.environ.get("PROGRAMFILES", "C:/Program Files")) / (
        "Google/Chrome/Application/chrome.exe"
    )
    if not chrome.exists():
        pytest.skip("Local Chrome required; no browser download during tests")
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch(executable_path=str(chrome), headless=True)
        page = browser.new_page()
        errors, external = [], []
        page.on("pageerror", lambda exc: errors.append(str(exc)))

        def local_only(route):
            if route.request.url.startswith(server + "/"):
                route.continue_()
            else:
                external.append(route.request.url)
                route.abort()

        page.route("**/*", local_only)
        page.set_viewport_size({"width": 1366, "height": 768})
        page.goto(server)
        page.wait_for_function("document.querySelector('#card').options.length > 0")
        # Laptop width: no horizontal scrolling; the primary action is in the first view.
        assert not page.evaluate("document.documentElement.scrollWidth > window.innerWidth")
        assert page.locator("#admit button").bounding_box()["y"] < 768
        # Keyboard: Tab reaches the primary action; arrow keys switch task tabs.
        page.focus("#value")
        page.keyboard.press("Tab")
        assert page.evaluate("document.activeElement === document.querySelector('#admit button')")
        page.focus("#tab-evaluate")
        page.keyboard.press("ArrowRight")
        assert page.evaluate("document.activeElement.id") == "tab-mission"
        assert page.locator("#mission").is_visible() and not page.locator("#evaluate").is_visible()
        page.keyboard.press("ArrowLeft")
        assert page.locator("#evaluate").is_visible()
        page.select_option("#card", "G4")
        page.click("#admit button")
        page.wait_for_function("document.querySelector('#decision').textContent.includes('yes_if')")
        page.fill("#value", "20")
        page.click("#admit button")
        page.wait_for_function("state.decision.verdict === 'no'")
        assert page.locator("#decision h3").first.inner_text() == "Refused"
        assert "deck.hangar.mass" in page.locator("#decision").inner_text()
        assert "limits the result" in page.locator(".gauge.limiting").inner_text()
        # Invalid input is rejected visibly and leaves the session unchanged.
        before = page.evaluate("state.final_hash")
        page.click("#advanced summary")
        page.fill("#cardJson", "{not json")
        page.click('#importCard button[type="submit"]')
        page.wait_for_function(
            "document.querySelector('#error').textContent.startsWith('Not applied')"
        )
        assert page.evaluate("state.final_hash") == before
        assert page.evaluate("state.decision.verdict") == "no"
        page.fill("#cardJson", "")
        assert page.locator('#priority option[value="patrol"]').count() == 1
        page.select_option("#priority", "patrol")
        page.wait_for_function(
            "state.priority_name === 'patrol' && state.priority.mission === 'patrol'"
        )
        page.click("#replay")
        page.wait_for_function("state.replay?.byte_identical === true")
        page.click("#reset")
        page.wait_for_function(
            "document.querySelector('#decision').textContent.includes('No current decision')"
        )
        assert page.locator("#value").input_value() == "0.9"
        if not page.locator("#loadCardJson").is_visible():
            page.click("#advanced summary")
        page.click("#loadCardJson")
        judge = json.loads(page.locator("#cardJson").input_value())
        judge["id"] = "BROWSER_JUDGE"
        judge["name"] = "<img src=x onerror=alert(1)>"
        next(m for m in judge["modes"] if m["name"] == "charging")["power_kw"] = 41
        page.fill("#cardJson", json.dumps(judge))
        page.click('#importCard button[type="submit"]')
        page.wait_for_function("state.decision?.subject === 'BROWSER_JUDGE'")
        assert judge["name"] in page.locator('#card option[value="BROWSER_JUDGE"]').text_content()
        assert page.locator("#card img").count() == 0
        page.click("#replay")
        page.wait_for_function("state.replay?.byte_identical === true")
        page.click("#reset")
        page.wait_for_function("!state.proposed_cards.BROWSER_JUDGE && state.next_t_s === 0")
        # Actual browser commands progress the real simulator and export complete evidence.
        page.select_option("#scenario", "B")
        page.wait_for_function(
            "state.scenario === 'B' && state.snapshot.guests.G4.battery_pct === 30"
        )
        assert page.locator("#mission").is_visible()  # choosing B opens the mission tab
        page.fill("#battery", "13")
        page.click('[data-event="battery_report"]')
        page.wait_for_function("state.snapshot.guests.G4.battery_pct === 13")
        assert "battery 13%" in page.locator("#guestStates").text_content()
        # The battery report runs the real D3 engine; its result is shown, not a fixture.
        page.wait_for_function("state.decision?.kind === 'arbitration'")
        decision = page.locator("#decision").inner_text()
        assert "Right of way (D3)" in decision and "computed (LIVE)" in decision
        assert "Goes first" in decision and "SAMPLE" not in decision
        page.click("details:has([data-op]) summary")
        page.click('[data-op="shedding"]')
        page.wait_for_function("state.decision?.kind === 'shedding'")
        assert "Degradation plan (D4)" in page.locator("#decision").inner_text()
        page.click("#step")
        page.wait_for_function("state.snapshot.t_s === 1")
        page.fill("#ticks", "3")
        page.click("#forward")
        page.wait_for_function("state.snapshot.t_s === 4")
        page.click('[data-event="genset_trip"]')
        page.wait_for_function("state.snapshot.platform.gensets_online === 1")
        page.click("#replay")
        page.wait_for_function("state.replay?.byte_identical === true")
        assert "byte-identical" in page.locator("#replayStatus").inner_text()
        with page.expect_download() as download:
            page.click("#export")
        saved = json.loads(Path(download.value.path()).read_text())
        assert saved["envelope"] and saved["final_hash"]
        assert "injected_events" in saved["jsonl"]
        baseline_b = page.evaluate("state.final_hash")
        page.select_option("#mode", "SAMPLE")
        page.wait_for_function("state.mode === 'SAMPLE' && state.next_t_s === 0")
        assert page.locator("#scenario").input_value() == "B"
        assert page.locator("#modeBadge").inner_text() == "SAMPLE"
        assert page.evaluate("state.snapshot.guests.G4.battery_pct") == 30
        fresh_b = page.evaluate("state.final_hash")
        assert fresh_b != baseline_b
        page.select_option("#mode", "LIVE")
        page.wait_for_function("state.mode === 'LIVE' && state.next_t_s === 0")
        assert page.locator("#scenario").input_value() == "B"
        assert page.locator("#modeBadge").inner_text() == "LIVE"
        assert page.evaluate("state.final_hash") == fresh_b
        page.click("#reset")
        page.wait_for_function("state.scenario === 'A' && state.next_t_s === 0")
        rendered = page.evaluate("""() => {
            const data = structuredClone(state);
            data.guests.G4.name = '<img src=x onerror=alert(1)>';
            data.decision = {subject:'G4', verdict:'yes', rationale:'<script>alert(1)</script>'};
            render(data);
            // Capture this local rendering atomically before another live update can render.
            return {
                label: document.querySelector('#card option[value=G4]').textContent,
                decision: document.querySelector('#decision').textContent,
                injected: document.querySelectorAll('#decision script, #decision img').length
            };
        }""")
        assert "<img src=x onerror=alert(1)>" in rendered["label"]
        assert "<script>alert(1)</script>" in rendered["decision"]
        assert rendered["injected"] == 0
        assert page.locator(".gauge").count() == 8
        assert not errors and not external
        browser.close()
