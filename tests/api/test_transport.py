"""Optional real transport/browser checks using websockets and playwright overlays.

The locked environment lacks a WebSocket backend. Extras here are explicit test
overlays, not changes to the project dependency graph. Browser test uses local Chrome.
"""

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest


@pytest.fixture
def server():
    pytest.importorskip("websockets", reason="Dedicated WebSocket dependency PR needed")
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
            "websockets",
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
    from websockets.sync.client import connect

    state = httpx.post(f"{server}/api/sessions").json()
    with connect(f"{server.replace('http:', 'ws:')}/ws/{state['id']}") as ws:
        assert json.loads(ws.recv(timeout=5))["mode"] == "LIVE"
        ws.send(json.dumps({"op": "admit", "card": "G4", "value": 20}))
        update = json.loads(ws.recv(timeout=5))
        assert update["decision"]["verdict"] == "no"
        assert update["decision"]["binding"]["demand"] >= 20


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
        page.goto(server)
        page.wait_for_function("document.querySelector('#card').options.length > 0")
        page.select_option("#card", "G4")
        page.click("#admit button")
        page.wait_for_function("document.querySelector('#decision').textContent.includes('yes_if')")
        page.fill("#value", "20")
        page.click("#admit button")
        page.wait_for_function("document.querySelector('#decision h3').textContent.includes('no')")
        assert "deck.hangar.mass" in page.locator("#decision").inner_text()
        page.click("#reset")
        page.wait_for_function(
            "document.querySelector('#decision').textContent.includes('No current decision')"
        )
        assert page.locator("#value").input_value() == "0.9"
        page.evaluate("""() => {
            const data = structuredClone(state);
            data.guests.G4.name = '<img src=x onerror=alert(1)>';
            data.decision = {subject:'G4', verdict:'yes', rationale:'<script>alert(1)</script>'};
            render(data);
        }""")
        assert (
            page.locator("#card option[value=G4]")
            .text_content()
            .endswith("<img src=x onerror=alert(1)>")
        )
        assert "<script>alert(1)</script>" in page.locator("#decision").inner_text()
        assert page.locator("#decision script, #decision img").count() == 0
        assert page.locator(".gauge").count() == 8
        assert not errors and not external
        browser.close()
