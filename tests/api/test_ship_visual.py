"""Illustrated ship view.

Real-backend tests drive the actual server in installed Chrome (offline). The one
CONTROLLED rendering test re-renders a modified copy of a real state in the page; it proves
only how a record is labelled, not runtime behaviour.
"""

import json
import os
from pathlib import Path

import pytest

from tests.api.test_transport import server  # noqa: F401  (pytest fixture)


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
        page = browser.new_page(viewport={"width": 1366, "height": 768}, accept_downloads=True)
        external = []

        def local(route):
            if route.request.url.startswith(server + "/"):
                route.continue_()
            else:
                external.append(route.request.url)
                route.abort()

        page.route("**/*", local)
        page.goto(server)
        page.wait_for_function("document.querySelector('#card').options.length > 0")
        page.wait_for_function("Array.isArray(serverOps)")
        yield page
        assert not external
        browser.close()


def token(page, gid, kind=None):
    css = f'#ship g.s-token[data-gid="{gid}"]' + (f".s-{kind}" if kind else "")
    return page.locator(css)


def classes(locator):
    return locator.first.get_attribute("class").split()


def evaluate(page, card, mass=None):
    page.select_option("#card", card)
    if mass is not None:
        page.fill("#value", str(mass))
    page.click("#admit button")


def run_to(page, t):
    while page.evaluate("state.next_t_s") < t:
        n = min(120, t - page.evaluate("state.next_t_s"))
        target = page.evaluate("state.next_t_s") + n
        page.fill("#ticks", str(n))
        page.click("#forward")
        page.wait_for_function(f"state.next_t_s === {target}")


def story(page):
    return page.locator("#story").inner_text()


def start_execution(page, crew):
    page.select_option("#scenario", "B")
    page.wait_for_function("state.scenario === 'B'")
    if crew != 14:
        page.select_option("#crew", str(crew))
        page.wait_for_function(f"state.snapshot.crew === {crew}")
    page.check("#execution")
    page.wait_for_function("state.execution?.enabled === true && state.next_t_s === 0")


# ----------------------------------------------------------------------------- real backend


def test_first_screen_shows_ship_action_and_result(page):
    for selector in ("#ship", "#admit button", "#storyVerdict"):
        box = page.locator(selector).bounding_box()
        assert box["y"] >= 0 and box["y"] + min(box["height"], 60) <= 768, selector
    ship, side = page.locator("#ship").bounding_box(), page.locator("#tasks").bounding_box()
    assert ship["width"] > side["width"]  # the ship is the dominant element
    assert not page.evaluate("document.documentElement.scrollWidth > window.innerWidth")
    # Installed equipment drawn from the real snapshot placements, named in plain words.
    placed = page.evaluate("Object.keys(state.snapshot.guests)")
    for gid in placed:
        assert token(page, gid, "installed").count() >= 1
    assert "Ocean & weather module" in token(page, "G2").first.text_content()


def test_proposal_is_highlighted_in_both_views_and_selected(page):
    evaluate(page, "G4")
    page.wait_for_function("state.decision?.subject === 'G4'")
    hangar = page.evaluate("state.decision.placement.G4.slots.body")
    proposed = token(page, "G4", "proposed")
    assert proposed.count() == 1 and "r-proposed" in classes(proposed)
    assert "proposed" in page.locator("#selection").inner_text()
    assert "VTOL rescue drone" in page.locator("#selection").inner_text()
    # Only real placement differences are marked; key order in the record does not matter.
    unchanged = page.evaluate("""() => Object.entries(state.decision.placement)
        .filter(([g, p]) => state.snapshot.guests[g]
            && JSON.stringify(Object.entries(p.slots).sort()) ===
               JSON.stringify(Object.entries(state.snapshot.guests[g].placement.slots).sort())
            && p.line === state.snapshot.guests[g].placement.line)
        .map(([g]) => g)""")
    assert unchanged and all("r-moved" not in classes(token(page, g)) for g in unchanged)
    assert hangar == "hangar"
    page.click("#viewSide")
    assert page.locator("#ship").get_attribute("data-view") == "side"
    assert "r-proposed" in classes(token(page, "G4", "proposed"))
    assert "s-selected" in classes(token(page, "G4", "proposed"))
    assert page.locator("#viewSide").get_attribute("aria-pressed") == "true"


def test_selection_is_synchronised_and_keyboard_operable(page):
    token(page, "G2").first.focus()
    page.keyboard.press("Enter")
    assert "Ocean & weather module" in page.locator("#selection").inner_text()
    assert "profiling" in page.locator("#selection").inner_text()  # what it is doing now
    page.click("#viewSide")
    assert "s-selected" in classes(token(page, "G2"))
    page.click("#viewTop")
    assert "s-selected" in classes(token(page, "G2"))
    page.click("#selection .close")
    assert page.locator("#selection").is_hidden()


def test_refused_proposal_is_not_a_ship_emergency(page):
    evaluate(page, "G4", 20)
    page.wait_for_function("state.decision?.verdict === 'no'")
    text = story(page)
    assert "Proposal refused (ship unaffected)" in text
    assert "The equipment already aboard is not affected" in text
    assert "checked for the proposal in its worst case" in text
    # deck.hangar.mass: the limiting slot is outlined on the ship.
    assert page.locator("#ship g.s-slot.s-limit").count() >= 1


def test_unfamiliar_card_renders_as_text(page):
    page.click("#advanced summary")
    page.click("#loadCardJson")
    card = json.loads(page.locator("#cardJson").input_value())
    card["id"], card["name"] = "JUDGE_X", "<img src=x onerror=alert(1)>"
    page.fill("#cardJson", json.dumps(card))
    page.click('#importCard button[type="submit"]')
    page.wait_for_function("state.decision?.subject === 'JUDGE_X'")
    assert page.locator("#ship img, #selection img, #story img").count() == 0
    assert "<img src=x onerror=alert(1)>" in page.locator("#selection").inner_text()


def test_crew_14_recovery_needs_confirmation_then_shows_the_real_change(page):
    start_execution(page, 14)
    run_to(page, 482)
    assert "r-winner" in classes(token(page, "G4")) and "r-yields" in classes(token(page, "G3"))
    run_to(page, 841)
    assert "An operator must confirm" in story(page)
    assert "primary" in page.locator("#confirmDeck").get_attribute("class")
    assert page.evaluate("state.snapshot.guests.G4.mode") == "flying"
    page.click("#tab-mission")
    page.click("#confirmDeck")
    page.wait_for_function("state.snapshot.guests.G4.mode === 'recovery'")
    text = story(page)
    assert "changed from flying to recovery" in text
    assert "matched to the pending entry shown earlier in this run" in text
    assert "seconds in between are not shown" in text
    assert "r-changed" in classes(token(page, "G4"))
    assert "charging" not in token(page, "G4").first.text_content()
    page.click("#replay")
    page.wait_for_function("state.replay?.byte_identical === true")
    with page.expect_download() as download:
        page.click("#export")
    exported = json.loads(Path(download.value.path()).read_text())
    assert "operator_confirmation" in exported["jsonl"]


@pytest.mark.parametrize("crew", [6, 0])
def test_reduced_crew_denial_is_prominent(page, crew):
    start_execution(page, crew)
    run_to(page, 482)
    text = story(page)
    assert "Recovery of G4 denied" in text
    assert "Reported risk: ditch: risk accepted" in text and "not a safe continued flight" in text
    g4 = token(page, "G4")
    assert "r-denied" in classes(g4) and "Recovery denied" in g4.first.text_content()
    assert "r-changed" not in classes(g4)


# --------------------------------------------- CONTROLLED rendering (not runtime proof)


def test_unmatched_applied_record_is_labelled_as_limited(page):
    """Re-render a copy of the real state with an applied record the page never saw pending."""
    page.evaluate("""() => {
        const data = structuredClone(state);
        render(data);  // baseline for this run
        const next = structuredClone(data);
        next.snapshot.t_s += 1;
        next.execution = {enabled: true, status: {enabled: true, pending: [], blocked: [],
            applied: [{task: 99, kind: "action", t_s: next.snapshot.t_s}]}};
        render(next);
    }""")
    assert "task 99 (action)" in story(page)
    assert "carries only its number, kind and time" in story(page)
