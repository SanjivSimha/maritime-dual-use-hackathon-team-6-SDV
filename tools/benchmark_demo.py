"""Measure ShipOS demo responsiveness and run a real-browser acceptance pass on this machine.

    uv run --offline --with playwright python -m tools.benchmark_demo --out perf.json
    uv run --offline python -m tools.benchmark_demo --no-browser          # engine + API only

All wall-clock numbers come from time.perf_counter_ns (monotonic) in this process; nothing is
written into the simulator, its decision log or replay records. Boundaries:

* engine   - one call of admission.decide / arbitration.decide / shedding.plan, in process,
             inputs built beforehand. "cold" = the first call in a fresh Python process
             (imports and input loading excluded); "warm" = repeated calls in one process.
* api      - HTTP POST (or WebSocket send) until the full JSON response is received, against
             a real uvicorn server started with the launcher's arguments.
* browser  - from the click (installed Chrome via Playwright) until the result is visible in
             the page (a wait_for_function condition), including Playwright's polling.
* launch   - from starting tools/demo.ps1 until GET / answers 200 (includes uv's offline
             sync check and server import), and page load until the ship is drawn.
* sim      - simulated seconds recorded by the simulator, not wall time.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import socket
import statistics
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CHROME = (
    Path(os.environ.get("PROGRAMFILES", "C:/Program Files"))
    / "Google/Chrome/Application/chrome.exe"
)
NS_PER_MS = 1_000_000


def ms(ns: int) -> float:
    return ns / NS_PER_MS


def stats(samples_ms: list[float]) -> dict:
    s = sorted(samples_ms)
    if not s:
        return {"n": 0}
    p95 = s[max(0, -(-95 * len(s) // 100) - 1)]  # nearest-rank
    return {
        "n": len(s),
        "median_ms": round(statistics.median(s), 3),
        "p95_ms": round(p95, 3),
        "max_ms": round(s[-1], 3),
    }


def timed(fn):
    start = time.perf_counter_ns()
    result = fn()
    return ms(time.perf_counter_ns() - start), result


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


# --------------------------------------------------------------------------------- engine


def engine_cases():
    """Inputs built once, outside timing. Returns {name: zero-argument callable}."""
    import json as _json

    from shipos.contracts import WorldSnapshot
    from shipos.data import load_envelope, load_guests, load_priorities, load_ship
    from shipos.engine import admission, arbitration, shedding
    from shipos.sim.runner import load_scenario

    ship, guests, envelope = load_ship(), load_guests(), load_envelope()
    survey, sar = load_priorities("survey_primary"), load_priorities("sar_primary")
    port = WorldSnapshot.model_validate(
        _json.loads(
            (REPO / "tests/fixtures/snapshot_scenario_a_port.json").read_text(encoding="utf-8")
        )
    )
    heavy = guests["G4"].model_copy(update={"mass_t": 20.0})
    runner = load_scenario(REPO / "data/scenarios/B.yaml")
    runner.run(481)  # t=0..480: baseline battery report at t=480 (G4 18 %)
    at_report = runner.snapshot
    runner.run(241)  # through t=720: baseline generator trip
    at_trip = runner.snapshot
    # SYNTHETIC overload (not a baseline state): both charges overlap G5 with one generator.
    g = dict(at_trip.guests)
    g["G3"] = g["G3"].model_copy(update={"mode": "charging", "activity": None, "measured_kw": None})
    g["G4"] = g["G4"].model_copy(update={"mode": "charging", "measured_kw": None})
    overload = at_trip.model_copy(update={"guests": g})
    return {
        "D1 admission G4 0.9 t (yes_if)": lambda: admission.decide(
            ship, guests, port, guests["G4"], survey, envelope
        ),
        "D1 admission G4 20 t (no)": lambda: admission.decide(
            ship, guests, port, heavy, survey, envelope
        ),
        "D3 arbitration t=480 survey": lambda: arbitration.decide(
            ship, guests, at_report, survey, "G4", "recovery"
        ),
        "D3 arbitration t=480 SAR": lambda: arbitration.decide(
            ship, guests, at_report, sar, "G4", "recovery"
        ),
        "D4 shedding t=720 trip (baseline)": lambda: shedding.plan(ship, guests, at_trip, survey),
        "D4 shedding SYNTHETIC overload": lambda: shedding.plan(ship, guests, overload, survey),
    }


def engine_warm(samples: int) -> dict:
    out = {}
    for name, call in engine_cases().items():
        call()  # discard: first call in this process is reported under cold
        times, verdict = [], None
        for _ in range(samples):
            t, d = timed(call)
            times.append(t)
            verdict = d.verdict
        out[name] = {**stats(times), "verdict": verdict}
    return out


def engine_cold(samples: int) -> dict:
    out: dict[str, list[float]] = {}
    for _ in range(samples):
        proc = subprocess.run(
            [sys.executable, "-m", "tools.benchmark_demo", "--cold-engine-child"],
            cwd=REPO,
            capture_output=True,
            text=True,
            check=True,
        )
        for name, value in json.loads(proc.stdout).items():
            out.setdefault(name, []).append(value)
    return {name: stats(v) for name, v in out.items()}


def cold_child() -> None:
    result = {}
    for name, call in engine_cases().items():  # each case's first call in this fresh process
        t, _ = timed(call)
        result[name] = t
    print(json.dumps(result))


# ------------------------------------------------------------------------------------ api


class Server:
    """uvicorn with the launcher's arguments, started directly so only it is timed and killed."""

    def __init__(self):
        self.port = free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        start = time.perf_counter_ns()
        self.proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "shipos.api.server:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(self.port),
                "--ws",
                "websockets-sansio",
            ],
            cwd=REPO,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        import httpx

        while True:
            try:
                if httpx.get(self.base, timeout=0.5).status_code == 200:
                    break
            except httpx.TransportError:
                time.sleep(0.02)
            if self.proc.poll() is not None:
                raise RuntimeError("uvicorn exited")
        self.ready_ms = ms(time.perf_counter_ns() - start)

    def close(self):
        self.proc.terminate()
        self.proc.wait(timeout=10)


def api_bench(samples: int) -> dict:
    import httpx
    from websockets.sync.client import connect

    out = {"cold": {}, "warm": {}}
    ready, first_session, first_admit = [], [], []
    for _ in range(3):  # cold: fresh server processes
        server = Server()
        with httpx.Client(base_url=server.base, timeout=60) as c:
            ready.append(server.ready_ms)
            t, r = timed(lambda: c.post("/api/sessions"))
            first_session.append(t)
            sid = r.json()["id"]
            t, _ = timed(
                lambda sid=sid: c.post(
                    f"/api/sessions/{sid}/commands",
                    json={"op": "admit", "card": "G4", "value": 0.9},
                )
            )
            first_admit.append(t)
        server.close()
    out["cold"] = {
        "server start to GET / 200 (python -m uvicorn)": stats(ready),
        "first POST /api/sessions": stats(first_session),
        "first admit command": stats(first_admit),
    }

    server = Server()
    warm = {}
    try:
        with httpx.Client(base_url=server.base, timeout=60) as c:

            def cmd(sid, **body):
                r = c.post(f"/api/sessions/{sid}/commands", json=body)
                r.raise_for_status()
                return r.json()

            c.post("/api/sessions")
            warm["POST /api/sessions"] = stats(
                [timed(lambda: c.post("/api/sessions"))[0] for _ in range(samples)]
            )
            sid = c.post("/api/sessions").json()["id"]
            cmd(sid, op="admit", card="G4", value=0.9)
            warm["admit G4 (D1 + D2 + 1 tick + view)"] = stats(
                [
                    timed(lambda i=i: cmd(sid, op="admit", card="G4", value=0.9 + (i % 2) * 0.1))[0]
                    for i in range(samples)
                ]
            )
            sid_b = c.post("/api/sessions").json()["id"]
            cmd(sid_b, op="scenario", scenario="B")
            cmd(sid_b, op="arbitration")
            warm["D3 query (read-only) Scenario B t=0"] = stats(
                [timed(lambda: cmd(sid_b, op="arbitration"))[0] for _ in range(samples)]
            )
            warm["D4 query (read-only) Scenario B t=0"] = stats(
                [timed(lambda: cmd(sid_b, op="shedding"))[0] for _ in range(samples)]
            )
            # Observation stepping on fresh B runs.
            one, many = [], []
            for _ in range(max(3, samples // 5)):
                s = c.post("/api/sessions").json()["id"]
                cmd(s, op="scenario", scenario="B")
                one.append(timed(lambda s=s: cmd(s, op="step"))[0])
                many.append(timed(lambda s=s: cmd(s, op="step", ticks=120))[0])
            warm["step 1 tick (observation)"] = stats(one)
            warm["step 120 ticks (observation)"] = stats(many)
            # Execution: wall time to advance to the recovery confirmation, then confirm.
            exec_runs, confirm, battery = [], [], []
            for _ in range(3):
                s = c.post("/api/sessions").json()["id"]
                cmd(s, op="scenario", scenario="B")
                cmd(s, op="execution", enabled=True, fresh_run=True)
                start = time.perf_counter_ns()
                state = None
                while (state or {"next_t_s": 0})["next_t_s"] < 841:
                    n = min(120, 841 - (state or {"next_t_s": 0})["next_t_s"])
                    state = cmd(s, op="step", ticks=n)
                exec_runs.append(ms(time.perf_counter_ns() - start))
                t, after = timed(lambda s=s: cmd(s, op="confirm_deck", by="deck officer"))
                confirm.append(t)
                assert after["snapshot"]["guests"]["G4"]["mode"] == "recovery"
                s2 = c.post("/api/sessions").json()["id"]
                cmd(s2, op="scenario", scenario="B")
                battery.append(
                    timed(
                        lambda s2=s2: cmd(s2, op="event", event="battery_report", battery_pct=18)
                    )[0]
                )
            warm["advance execution run t=0..840 (7 HTTP step commands)"] = stats(exec_runs)
            warm["confirm_deck command (records input, applies recovery)"] = stats(confirm)
            warm["battery_report event (1 tick incl. real D3 callback)"] = stats(battery)
            # WebSocket round trip on the same session protocol.
            sid_ws = c.post("/api/sessions").json()["id"]
            with connect(f"ws://127.0.0.1:{server.port}/ws/{sid_ws}") as ws:
                ws.recv(timeout=10)
                times = []
                for i in range(samples):
                    start = time.perf_counter_ns()
                    ws.send(json.dumps({"op": "admit", "card": "G4", "value": 0.9 + (i % 2) * 0.1}))
                    json.loads(ws.recv(timeout=60))
                    times.append(ms(time.perf_counter_ns() - start))
                warm["WebSocket admit round trip"] = stats(times)
            # Canonical records carry no wall-clock timing.
            evidence = c.get(f"/api/sessions/{sid}/evidence").json()
            # The header's ship spec has link-latency fields; decisions must carry none.
            warm["_decisions_in_replay_carry_latency_ms"] = '"latency_ms"' in evidence["jsonl"]
    finally:
        server.close()
    out["warm"] = warm
    return out


# ------------------------------------------------------------------------- launch + browser


def launch_bench(samples: int) -> dict:
    """Time the documented launcher: start until GET / returns 200. Stops only its own tree."""
    import httpx

    # Start it as a user would, from a plain shell: this process runs under `uv run`, whose
    # VIRTUAL_ENV / UV_* variables would otherwise leak in (see PERFORMANCE_EVIDENCE.md).
    clean_env = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV" and k != "UV"}
    clean_env = {k: v for k, v in clean_env.items() if not k.startswith("UV_")}
    times = []
    for _ in range(samples):
        port = free_port()
        start = time.perf_counter_ns()
        proc = subprocess.Popen(
            ["powershell", "-NoProfile", "-File", str(REPO / "tools/demo.ps1"), "-Port", str(port)],
            cwd=REPO,
            env=clean_env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            while True:
                try:
                    if httpx.get(f"http://127.0.0.1:{port}/", timeout=0.5).status_code == 200:
                        break
                except httpx.TransportError:
                    time.sleep(0.02)
                if proc.poll() is not None:
                    raise RuntimeError("launcher exited")
            times.append(ms(time.perf_counter_ns() - start))
        finally:
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
    return {"tools/demo.ps1 start to GET / 200": stats(times)}


def browser_run(samples: int) -> dict:
    from playwright.sync_api import sync_playwright

    from shipos.api import adapter

    results = {"timings": {}, "acceptance": [], "findings": [], "versions": {}}
    acc = results["acceptance"]
    t_ = results["timings"]
    server = Server()

    def check(name, condition):
        if not condition:
            raise AssertionError(f"browser acceptance failed: {name}")
        acc.append(name)

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=str(CHROME), headless=True)
            results["versions"]["chrome"] = browser.version
            external = []

            def new_page():
                page = browser.new_page(
                    viewport={"width": 1366, "height": 768}, accept_downloads=True
                )

                def local(route):
                    if route.request.url.startswith(server.base + "/"):
                        route.continue_()
                    else:
                        external.append(route.request.url)
                        route.abort()

                page.route("**/*", local)
                return page

            def load(page):
                start = time.perf_counter_ns()
                page.goto(server.base)
                page.wait_for_function(
                    "document.querySelectorAll('#ship g.s-token').length > 0"
                    " && Array.isArray(serverOps)"
                )
                return ms(time.perf_counter_ns() - start)

            def act(page, action, condition):
                start = time.perf_counter_ns()
                action()
                page.wait_for_function(condition, timeout=120_000)
                return ms(time.perf_counter_ns() - start)

            def run_to(page, t):
                while page.evaluate("state.next_t_s") < t:
                    n = min(120, t - page.evaluate("state.next_t_s"))
                    target = page.evaluate("state.next_t_s") + n
                    page.fill("#ticks", str(n))
                    page.click("#forward")
                    page.wait_for_function(f"state.next_t_s === {target}", timeout=120_000)

            # Page load: first page in a new browser context is cold, reloads are warm.
            page = new_page()
            cold_load = [load(page)]
            warm_load = [load(page) for _ in range(max(3, samples // 5))]
            t_["page load to ship drawn (cold, first page)"] = stats(cold_load)
            t_["page load to ship drawn (warm reload)"] = stats(warm_load)

            # 3. Top/side selection, before any decision exists.
            page.locator('#ship g.s-token[data-gid="G2"]').first.click()
            page.click("#viewSide")
            side = page.locator('#ship g.s-token[data-gid="G2"]').first.get_attribute("class")
            page.click("#viewTop")
            top = page.locator('#ship g.s-token[data-gid="G2"]').first.get_attribute("class")
            check(
                "top/side views share the selection", "s-selected" in side and "s-selected" in top
            )

            # 1. Admission and the visible result.
            evaluate_ms = []
            for i in range(samples):
                mass = 0.9 if i % 2 == 0 else 1.0  # alternate so each sample awaits a new result
                page.select_option("#card", "G4")
                page.fill("#value", str(mass))
                evaluate_ms.append(
                    act(
                        page,
                        lambda: page.click("#admit button"),
                        f"state.proposed_cards?.G4?.mass_t === {mass}"
                        " && state.decision?.subject === 'G4'"
                        " && document.querySelector('#storyVerdict').textContent"
                        ".includes('Accepted')",
                    )
                )
            t_["Evaluate click to verdict shown (D1)"] = stats(evaluate_ms)
            check(
                "admission: G4 accepted with conditions, proposal highlighted",
                page.evaluate("state.decision.verdict") == "yes_if"
                and page.locator('#ship g.s-token.s-proposed[data-gid="G4"]').count() == 1,
            )
            page.fill("#value", "20")
            page.click("#admit button")
            page.wait_for_function("state.decision?.verdict === 'no'")
            check(
                "admission: 20 t refused, ship unaffected, limit slot outlined",
                "ship unaffected" in page.locator("#story").inner_text()
                and page.locator("#ship g.s-slot.s-limit").count() >= 1,
            )

            # Finding, recorded rather than asserted: a later update re-selects the proposal.
            page.locator('#ship g.s-token[data-gid="G2"]').first.click()
            page.select_option("#crew", "6")
            page.wait_for_function("state.snapshot.crew === 6")
            results["findings"].append(
                {"selection after a later update (clicked G2)": page.evaluate("selectedGid")}
            )
            page.select_option("#crew", "14")
            page.wait_for_function("state.snapshot.crew === 14")

            # 2. Invalid input keeps state; unfamiliar card renders as text.
            before = page.evaluate("state.final_hash")
            page.click("#advanced summary")
            page.fill("#cardJson", "{not json")
            page.click('#importCard button[type="submit"]')
            page.wait_for_function(
                "document.querySelector('#error').textContent.startsWith('Not applied')"
            )
            check(
                "invalid card JSON rejected with state and log unchanged",
                page.evaluate("state.final_hash") == before,
            )
            page.click("#loadCardJson")
            card = json.loads(page.locator("#cardJson").input_value())
            card["id"], card["name"] = "JUDGE_X", "<img src=x onerror=alert(1)>"
            card["modes"][0]["power_kw"] = card["modes"][0]["power_kw"] + 1
            page.fill("#cardJson", json.dumps(card))
            page.click('#importCard button[type="submit"]')
            page.wait_for_function("state.decision?.subject === 'JUDGE_X'")
            check(
                "unfamiliar card imported to real D1; name rendered as text",
                page.locator("#ship img, #selection img, #story img").count() == 0,
            )

            # 4-5. Both priorities: 14-crew execution, confirmation then recovery.
            d3_ms, confirm_ms = [], []
            for priority, g3 in (("survey_primary", "pause"), ("sar_primary", "suspend")):
                page.click("#reset")
                page.wait_for_function("state.scenario === 'A' && state.next_t_s === 0")
                page.select_option("#scenario", "B")
                page.wait_for_function("state.scenario === 'B'")
                page.select_option("#priority", priority)
                page.wait_for_function(f"state.priority_name === '{priority}'")
                page.check("#execution")
                page.wait_for_function("state.execution?.enabled === true && state.next_t_s === 0")
                run_to(page, 480)
                page.fill("#ticks", "1")
                d3_ms.append(
                    act(
                        page,
                        lambda: page.click("#forward"),
                        "document.querySelector('#story').textContent.includes('goes first')",
                    )
                )
                d3 = page.evaluate(
                    "state.history.map(h => h.decision)"
                    ".filter(d => d.kind === 'arbitration').at(-1)"
                )
                check(
                    f"{priority}: real D3 G4 first, G3 {g3}",
                    d3["winner"] == "G4" and d3["chosen"]["actions"][0]["kind"] == g3,
                )
                run_to(page, 841)
                check(
                    f"{priority}: preparation done, G4 still flying, operator must confirm",
                    page.evaluate("state.snapshot.guests.G4.mode") == "flying"
                    and "An operator must confirm" in page.locator("#story").inner_text(),
                )
                confirm_ms.append(
                    act(
                        page,
                        lambda: page.click("#confirmDeck"),
                        "state.snapshot.guests.G4.mode === 'recovery' && document"
                        ".querySelector('#story').textContent"
                        ".includes('changed from flying to recovery')",
                    )
                )
                check(
                    f"{priority}: confirmation then recovery shown as an observed change",
                    "r-changed"
                    in page.locator('#ship g.s-token[data-gid="G4"]').first.get_attribute("class"),
                )
            t_["timeline tick with battery report to D3 shown"] = stats(d3_ms)
            t_["Confirm click to recovery shown"] = stats(confirm_ms)

            # 9. Export and execution replay (current SAR run).
            page.click("#replay")
            page.wait_for_function("state.replay?.byte_identical === true", timeout=120_000)
            with page.expect_download() as dl:
                page.click("#export")
            bundle = json.loads(Path(dl.value.path()).read_text(encoding="utf-8"))
            check(
                "export + execution replay byte-identical (in page and offline from bundle)",
                adapter.replay_evidence(bundle)["byte_identical"]
                and "operator_confirmation" in bundle["jsonl"],
            )
            check(
                "canonical records carry no decision latency_ms",
                '"latency_ms"' not in bundle["jsonl"],
            )

            # 6. Baseline 6- and 0-crew denials.
            for crew in (6, 0):
                page.select_option("#crew", str(crew))
                page.wait_for_function(f"state.snapshot.crew === {crew}")
                # A crew change joins the current run; toggling execution starts a fresh one.
                if page.locator("#execution").is_checked():
                    page.uncheck("#execution")
                    page.wait_for_function("state.execution?.enabled === false")
                page.check("#execution")
                page.wait_for_function(
                    f"state.execution?.enabled === true && state.next_t_s === 0"
                    f" && state.snapshot.crew === {crew}"
                )
                run_to(page, 482)
                check(
                    f"crew {crew}: real D3 denial shown with reported risk",
                    "Recovery of G4 denied" in page.locator("#story").inner_text()
                    and "ditch: risk accepted" in page.locator("#story").inner_text(),
                )

            # 7. Reset archives; archived evidence downloadable unchanged.
            runs = page.evaluate("state.previous_runs.length")
            last = page.evaluate("state.final_hash")
            page.click("#reset")
            page.wait_for_function(f"state.previous_runs.length === {runs + 1}")
            with page.expect_download() as dl:
                page.locator("#previousRuns summary").click()
                page.locator("#previousList a").last.click()
            archived = json.loads(Path(dl.value.path()).read_text(encoding="utf-8"))
            check(
                "reset archives the run; ?run=N download has the same hash",
                archived["final_hash"] == last,
            )

            # 8. Captured environment through the Advanced form, replayed.
            page.select_option("#scenario", "B")
            page.wait_for_function("state.scenario === 'B'")
            page.click("#envAdvanced summary")
            page.select_option("#environment", "strait_of_sicily")
            page.fill("#envStart", "2026-10-17T08:00")
            page.fill("#envTz", "120")
            page.click("#envApply")
            page.wait_for_function(
                "state.environment?.configuration?.source === 'strait_of_sicily'"
            )
            page.fill("#ticks", "60")
            page.click("#forward")
            page.wait_for_function("state.next_t_s === 60")
            page.click("#replay")
            page.wait_for_function("state.replay?.byte_identical === true", timeout=120_000)
            check(
                "captured weather: provenance shown, run replays byte-identically",
                "open_meteo_forecast" in page.locator("#environmentView").inner_text(),
            )
            check("no external browser requests", not external)
            browser.close()
    finally:
        server.close()
    return results


# ----------------------------------------------------------------------------------- main


def machine() -> dict:
    import ctypes

    class MEM(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong),
            ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("a", ctypes.c_ulonglong),
            ("b", ctypes.c_ulonglong),
            ("c", ctypes.c_ulonglong),
            ("d", ctypes.c_ulonglong),
            ("e", ctypes.c_ulonglong),
        ]

    info = {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "processor": platform.processor(),
        "logical_cpus": os.cpu_count(),
    }
    if sys.platform == "win32":
        mem = MEM()
        mem.dwLength = ctypes.sizeof(MEM)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(mem))
        info["ram_gb"] = round(mem.ullTotalPhys / 2**30, 1)
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True, text=True)
    info["git_sha"] = sha.stdout.strip()
    return info


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--samples", type=int, default=30)
    parser.add_argument("--cold", type=int, default=5)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--cold-engine-child", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.cold_engine_child:
        cold_child()
        return 0
    report = {"machine": machine()}
    report["engine_warm"] = engine_warm(args.samples)
    report["engine_cold"] = engine_cold(args.cold)
    report["api"] = api_bench(args.samples)
    report["sim_seconds"] = {
        "battery report (deadline anchor)": 480,
        "landing deadline": 1200,
        "preparation complete (14 crew)": 840,
        "operator confirmation / recovery start": 841,
        "recovery end (G4 launching)": 1021,
        "source": "docs/SIM_EXECUTION.md; observed again in browser acceptance",
    }
    if not args.no_browser:
        report["launch"] = launch_bench(3)
        report["browser"] = browser_run(max(5, args.samples // 3))
    text = json.dumps(report, indent=2)
    if args.out:
        args.out.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
