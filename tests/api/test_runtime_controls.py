"""Run-mission runtime controls: enabled only by what the server really offers, never guessed."""

import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from shipos.api.server import create_app
from tests.api.test_transport import server  # noqa: F401  (pytest fixture)

RUNTIME_OPS = ("execution", "confirm_deck", "environment")


def test_capabilities_come_from_the_server_schema():
    with TestClient(create_app()) as client:
        schema = client.get("/openapi.json").json()
    ops = schema["components"]["schemas"]["Command"]["properties"]["op"]["enum"]
    assert {"admit", "import_card", "event", "arbitration", "shedding", "replay"} <= set(ops)


@pytest.fixture
def page(server):  # noqa: F811
    playwright = pytest.importorskip("playwright.sync_api")
    chrome = Path(os.environ.get("PROGRAMFILES", "C:/Program Files")) / (
        "Google/Chrome/Application/chrome.exe"
    )
    if not chrome.exists():
        pytest.skip("Local Chrome required; no browser download during tests")
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch(executable_path=str(chrome), headless=True)
        page = browser.new_page(viewport={"width": 1366, "height": 768})
        page.route(
            "**/*",
            lambda r: r.continue_() if r.request.url.startswith(server + "/") else r.abort(),
        )
        page.server = server
        yield page
        browser.close()


def open_mission(page, schema_ops=None):
    if schema_ops is not None:
        # Simulate a newer server advertising more commands; the response body is real
        # except for the op list, so only capability detection is under test.
        def fulfil(route):
            response = route.fetch()
            schema = response.json()
            schema["components"]["schemas"]["Command"]["properties"]["op"]["enum"] = schema_ops
            route.fulfill(response=response, body=json.dumps(schema))

        page.route(f"{page.server}/openapi.json", fulfil)
    page.goto(page.server)
    page.wait_for_function("document.querySelector('#card').options.length > 0")
    page.wait_for_function(
        "!document.querySelector('#capabilityNotes').textContent.includes('checking')"
    )
    page.click("#tab-mission")


def test_unsupported_controls_are_disabled_with_reasons(page):
    open_mission(page)
    for selector in ("#execution", "#confirmDeck", "#environment"):
        assert page.locator(selector).is_disabled()
        assert "does not offer it yet" in page.locator(selector).get_attribute("title")
    notes = page.locator("#capabilityNotes").inner_text()
    assert "Simulated execution unavailable" in notes and "Operator deck confirmation" in notes
    page.click("#envAdvanced summary")
    assert "conditional planning eligibility" in page.locator("#envAdvanced").inner_text()
    assert "not reported" in page.locator("#environmentStatus").inner_text()


def test_advertised_but_unwired_commands_stay_disabled(page):
    with TestClient(create_app()) as client:
        ops = client.get("/openapi.json").json()["components"]["schemas"]["Command"]
    open_mission(page, ops["properties"]["op"]["enum"] + list(RUNTIME_OPS))
    for selector in ("#execution", "#confirmDeck", "#environment"):
        assert page.locator(selector).is_disabled()
        assert "not wired to its published payload" in page.locator(selector).get_attribute("title")
    status = page.locator("#missionStatus").inner_text()
    assert "None: this server records decisions" not in status  # no claim once it might apply


def test_mission_status_separates_recommendation_from_execution(page):
    open_mission(page)
    status = page.locator("#missionStatus").inner_text()
    assert "No D3/D4 recommendation yet" in status
    page.select_option("#scenario", "B")
    page.wait_for_function("state.scenario === 'B'")
    page.click('[data-event="battery_report"]')
    page.wait_for_function("state.decision?.kind === 'arbitration'")
    decision = page.evaluate("state.decision")
    status = page.locator("#missionStatus").inner_text()
    assert f"Goes first: {decision['winner']}; yields: {decision['loser']}" in status
    loser_costs = [c for c in decision["costs"] if c["guest"] == decision["loser"]]
    assert loser_costs and f"Losing mission cost: {decision['loser']}" in status
    first = decision["state_requests"][0]
    assert f"{first['variable']} → {first['to']}" in status and "requested, not executed" in status
    # A decided verdict is not execution: nothing is reported as applied, permitted or blocked.
    assert "None: this server records decisions without applying them" in status
    assert "whether one is required or received is not reported" in status
    assert "Blocked action and reason" in status and "Not reported by this server" in status
    assert page.evaluate("state.snapshot.guests.G4.mode") == "flying"  # unchanged by the verdict


def test_runtime_controls_are_keyboard_reachable(page):
    open_mission(page)
    page.focus('[data-op="shedding"]')
    page.keyboard.press("Tab")
    # Disabled controls are skipped; focus lands on the next usable element, not lost.
    active = page.evaluate("document.activeElement.tagName")
    assert active in ("SUMMARY", "BUTTON", "A", "INPUT", "SELECT")
    assert not page.evaluate("document.documentElement.scrollWidth > window.innerWidth")
