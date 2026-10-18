# Team 6 · PoseidonOS: final deliverables

Fincantieri "Software Defined Vessel" challenge, Maritime Dual-Use Hackathon, San Francisco, 16–18 October 2026.

| Brief deliverable | Where |
|---|---|
| Source repository, runnable with one documented command (engine not mocked) | repository root: `make install` once, then `make demo` (Windows: `tools/demo.ps1`) |
| Manifest schema with the five Annex B cards **plus one card of our own invention** | [`04_manifest/`](04_manifest/): `guest_manifest.schema.json`, `cards/G1–G5.yaml`, `cards/G6.yaml` (ours: survivor-care medical module), `ADDING_A_CARD.md` |
| README ≤ 3 pages: architecture diagram, D1–D6 and what each optimises, limitations, another week | [`01_README/README.pdf`](01_README/README.pdf) (3 pp; same text as the repository `README.md`) |
| Physical Account (Annex C) with the closing question | [`02_physical_account/PHYSICAL_ACCOUNT.pdf`](02_physical_account/PHYSICAL_ACCOUNT.pdf): 1-page account + generated registry of all 96 assumptions with use sites |
| Scenario C memo, PDF ≤ 2 pages | [`03_scenario_C_memo/scenario_C_memo.pdf`](03_scenario_C_memo/scenario_C_memo.pdf) (2 pp) |
| Live demo: 90 s of Scenario B, then Scenario A with the jury's card | [`06_demo/DEMO_RUNBOOK.md`](06_demo/DEMO_RUNBOOK.md) |
| *(extra)* Validation against the brief | [`05_validation/`](05_validation/): `VALIDATION_REPORT.pdf`, `validate.py` (48/48 checks on the real engine), `validation_output.txt`, dashboard screenshots |

Markdown sources sit next to each PDF. Regenerate the registry with
`uv run python -m tools.physical_account`, and the schema with `uv run python -m tools.guest_schema`.

**Still to do by the team at hand-in:** commit this work through the normal PR flow, record the
release SHA, do a disconnected rehearsal, and add the organisers' required AI-model disclosure on the upload form.
