"""Run-mission runtime controls (PR #37 runtime API v2 payloads).

Two kinds of test live here, kept apart on purpose:
* tests against the real server on this branch, which does not offer the runtime commands, and
* CONTROLLED-RESPONSE tests: the browser talks to the real server, but /openapi.json and runtime
  response fields are substituted in the browser. They prove what the page sends and how it
  renders the published fields. They are NOT runtime proof; that is recorded in PR #38 against
  real backend SHAs.
"""

import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from shipos.api.server import create_app
from tests.api.test_transport import server  # noqa: F401  (pytest fixture)

RUNTIME_OPS = ["execution", "confirm_deck", "environment"]
HASH = "ab" * 32


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


def runtime_fields(enabled=True):
    """Controlled values in the shapes PR #37 publishes (school's status keys from PR #36)."""
    return {
        "execution": {
            "enabled": enabled,
            "status": {
                "enabled": enabled,
                "pending": [{"guest": "G4", "kind": "proceed", "to_mode": "recovery"}],
                "applied": [{"guest": "G3", "kind": "pause", "to_mode": "holding"}],
                "blocked": [{"guest": "G4", "kind": "proceed", "reason": "confirmation_pending"}],
                "crew_gate": None,
            },
        },
        "crew_policy": {
            "permitted": False,
            "confirm": "human",
            "confirmation": "pending",
            "blockers": ["deck_occupied", "confirmation_pending"],
            "requests": [{"variable": "deck", "to": "clear", "start_s": 0, "duration_s": 300}],
            "request_time_base": "snapshot_relative",
        },
        "environment": {
            "configuration": {
                "source": "strait_of_sicily",
                "scenario_start": "2026-10-17T08:00:00+02:00",
                "layered_water": False,
                "timezone_offset_min": 120,
                "allow_stale": False,
            },
            "provenance": {
                "kind": "cache",
                "stale": False,
                "retrieved_at": "2026-10-17T06:00:00+00:00",
                "sources": {"marine": {}, "weather": {}},
            },
            "windows": {
                "flight_deck": [
                    {"start_s": 0, "end_s": 3600, "duration_s": 3600, "latest_start_s": 1800}
                ],
                "ramp": [],
            },
            "eligibility_only": True,
        },
        "previous_runs": [{"run": 0, "final_hash": HASH}],
        "state_request_time_base": "absolute_simulation_seconds",
    }


def controlled(page, sent):
    """Advertise the runtime ops and add the runtime fields to every state the page receives."""

    def schema(route):
        response = route.fetch()
        body = response.json()
        op = body["components"]["schemas"]["Command"]["properties"]["op"]
        op["enum"] = op["enum"] + RUNTIME_OPS
        route.fulfill(response=response, body=json.dumps(body))

    def session(route):
        response = route.fetch()
        route.fulfill(response=response, body=json.dumps(response.json() | runtime_fields()))

    def commands(route):
        body = json.loads(route.request.post_data)
        sent.append(body)
        if body["op"] in RUNTIME_OPS:  # the real server here cannot run these: echo state
            state = page.evaluate("state")
            route.fulfill(json=state | runtime_fields(enabled=body.get("enabled", True)))
        else:
            route.continue_()

    # No live WebSocket: its real initial state would replace the controlled fields.
    page.route_web_socket(page.server.replace("http:", "ws:") + "/ws/*", lambda ws: None)
    page.route(f"{page.server}/openapi.json", schema)
    page.route(f"{page.server}/api/sessions", session)
    page.route(f"{page.server}/api/sessions/*/commands", commands)


def open_mission(page):
    page.goto(page.server)
    page.wait_for_function("document.querySelector('#card').options.length > 0")
    page.wait_for_function("typeof serverOps !== 'undefined'")
    page.click("#tab-mission")


# --------------------------------------------------------------- real server on this branch


def test_unsupported_controls_are_disabled_with_reasons(page):
    open_mission(page)
    if page.evaluate("['execution','confirm_deck','environment'].some(o => serverOps.includes(o))"):
        pytest.skip("this backend offers the runtime commands (PR #37 present)")
    for selector in ("#execution", "#confirmDeck", "#environment", "#envApply"):
        assert page.locator(selector).is_disabled()
        assert "does not offer it yet" in page.locator(selector).get_attribute("title")
    notes = page.locator("#capabilityNotes").inner_text()
    assert "Simulated execution unavailable" in notes and "Operator deck confirmation" in notes
    status = page.locator("#missionStatus").inner_text()
    assert "None: this server records decisions without applying them" in status
    assert page.locator("#previousRuns").is_hidden()  # no archive field on this server


def test_mission_status_separates_recommendation_from_execution(page):
    open_mission(page)
    page.select_option("#scenario", "B")
    page.wait_for_function("state.scenario === 'B'")
    page.click('[data-event="battery_report"]')
    page.wait_for_function("state.decision?.kind === 'arbitration'")
    decision = page.evaluate("state.decision")
    status = page.locator("#missionStatus").inner_text()
    assert f"Goes first: {decision['winner']}; yields: {decision['loser']}" in status
    assert f"Losing mission cost: {decision['loser']}" in status
    first = decision["state_requests"][0]
    assert f"{first['variable']} → {first['to']}" in status
    if page.evaluate("state.state_request_time_base") is None:
        assert "time basis not reported" in status  # this server does not declare one
    else:
        assert "at t = " in status  # declared absolute basis
    if page.evaluate("state.execution") is None:  # backend without runtime fields
        assert "Pending in the simulator: not reported by this server" in status
        assert "Operator confirmation\nNot reported by this server" in status
    assert page.evaluate("state.snapshot.guests.G4.mode") == "flying"  # verdict changed nothing


# --------------------------------------------------- CONTROLLED responses: UI rendering only


def test_controls_send_the_published_payloads(page):
    sent = []
    controlled(page, sent)
    open_mission(page)
    for selector in ("#execution", "#confirmDeck", "#confirmBy", "#environment"):
        assert page.locator(selector).is_enabled()

    def send(action):
        with page.expect_response(lambda r: r.url.endswith("/commands")):
            action()

    send(lambda: page.click("#execution"))  # controlled state starts with enabled=true
    page.fill("#confirmBy", "bosun")
    send(lambda: page.click("#confirmDeck"))
    page.click("#envAdvanced summary")
    page.select_option("#environment", "strait_of_sicily")
    page.fill("#envStart", "2026-10-17T08:00")
    page.fill("#envTz", "120")
    send(lambda: page.click("#envApply"))
    page.select_option("#environment", "scenario")
    send(lambda: page.click("#envApply"))
    runtime = [b for b in sent if b["op"] in RUNTIME_OPS]
    assert runtime == [
        {"op": "execution", "enabled": False, "fresh_run": True},
        {"op": "confirm_deck", "by": "bosun"},
        {
            "op": "environment",
            "fresh_run": True,
            "environment": {
                "source": "strait_of_sicily",
                "scenario_start": "2026-10-17T08:00:00+02:00",
                "layered_water": False,
                "timezone_offset_min": 120,
                "allow_stale": False,
            },
        },
        {"op": "environment", "fresh_run": True, "environment": {"source": "scenario"}},
    ]


def test_runtime_fields_render_in_distinct_rows(page):
    controlled(page, [])
    open_mission(page)
    page.wait_for_function("state.execution?.enabled === true")
    rows = page.evaluate(
        """() => Object.fromEntries([...document.querySelectorAll('#missionStatus dt')]
            .map(dt => [dt.textContent, dt.nextElementSibling.textContent]))"""
    )
    assert "G4: proceed → recovery" in rows["Requested / pending preparation"]
    assert "G3: pause → holding" in rows["Applied simulated action"]
    assert "confirmation_pending" in rows["Blocked action and reason"]
    crew = rows["Operator confirmation"]
    assert "Confirmation status: pending" in crew and "permitted by crew policy: no" in crew
    assert "deck → clear at +0 s from this snapshot for 300 s" in crew
    assert "simulated deck-camera" not in crew  # only labelled for automatic (0-crew) evidence
    page.click("#envAdvanced summary")
    env = page.locator("#environmentView").inner_text()
    assert "2026-10-17T08:00:00+02:00 (UTC+02:00)" in env
    assert "cache · retrieved 2026-10-17T06:00:00+00:00 · sources: marine, weather" in env
    assert "conditional planning eligibility only; not permission" in env
    assert "ramp: no eligible window" in env
    assert page.locator("#previousRuns").is_visible()
    link = page.locator("#previousList a").first
    assert link.get_attribute("href").endswith("/evidence?run=0")


def test_rejected_runtime_command_leaves_controls_as_the_server_reports(page):
    controlled(page, [])

    def reject(route):
        body = json.loads(route.request.post_data)
        if body["op"] == "execution":
            route.fulfill(status=422, json={"detail": "Execution unavailable: test rejection"})
        else:
            route.fallback()

    page.route(f"{page.server}/api/sessions/*/commands", reject)
    open_mission(page)
    page.wait_for_function("state.execution?.enabled === true")
    page.click("#execution")
    page.wait_for_function(
        "document.querySelector('#error').textContent.includes('test rejection')"
    )
    assert page.locator("#execution").is_checked()  # reverted to the server's state


def test_runtime_controls_are_keyboard_reachable(page):
    controlled(page, [])
    open_mission(page)
    page.focus('[data-op="shedding"]')
    page.keyboard.press("Tab")
    assert page.evaluate("document.activeElement.id") == "execution"
    assert not page.evaluate("document.documentElement.scrollWidth > window.innerWidth")
