# ShipOS submission checklist

Documentation checkpoint: merged baseline `fcd8c586069ddbfdc9a4b6b2af8f3de61de619e2`
(PR #46), 17 October 2026. This checklist does not declare full challenge completion.
Agy owns merge/release coordination and STATUS.md; this task changes neither.

## Required written artifacts

| Artifact | Source | Submission PDF | Verified pages |
|---|---|---|---|
| Project README (limit 3) | [README.md](../README.md) | [README.pdf](README.pdf) | 2 |
| Physical Account (limit 1 plus registry) | [PHYSICAL_ACCOUNT.md](PHYSICAL_ACCOUNT.md) | [PHYSICAL_ACCOUNT.pdf](PHYSICAL_ACCOUNT.pdf) | 1 narrative + 15 registry appendix = 16 |
| Scenario C crew memo (limit 2) | [scenario_C_memo.md](scenario_C_memo.md) | [scenario_C_memo.pdf](scenario_C_memo.pdf) | 2 |

The Physical Account PDF appends the unchanged generated
[assumption registry](PHYSICAL_ACCOUNT_REGISTRY.md): 85 assumptions, 76 with literal
uses, 9 without, one dynamic lookup. No registry values or shared assumptions changed.
PDFs were rendered from the linked Markdown with Python Markdown (tables/fenced code)
and installed local Chrome via temporary Playwright tooling, then counted and
rasterized with temporary PyMuPDF. No project dependencies or browser downloads were
added. A4 portrait narratives use Arial 10 pt (account 9 pt), 15/16 mm margins;
the appendix uses A4 landscape and 8.5 pt table text. Actual PDF images were inspected
for readable text, table wrapping and page breaks. Links target repository paths,
not the rendering workstation. Keep the sources and PDFs together in the submission.

## Reproduce and retain evidence

1. Prepare Python 3.11+ and uv online, then run `uv sync --locked` from the repository
   root. Populate the cache before going offline. `.tools/uv.exe` is local tooling,
   not a committed or required repository artifact.
2. Launch offline with `tools/demo.ps1`, or the exact manual command in the
   [README](../README.md). This checkpoint additionally verified the equivalent
   command below using the existing local uv installation, dashboard HTTP 200 and
   creation of an actual Scenario A session at http://127.0.0.1:8017/:

   ```powershell
   & "C:\Users\asagb\Projects\shipos-main\.tools\uv.exe" run --locked --offline python -m uvicorn shipos.api.server:app --host 127.0.0.1 --port 8017 --ws websockets-sansio
   ```

3. Demo **B before A**. Use 14 crew and both priorities for real D3 preparation,
   explicit confirmation and applied recovery. Show 6/0-crew baseline denial rather
   than an invented landing. G4 continues recovery -> launch -> flying, not charging.
   A trip does not imply overload; observation B at minute 12 is nominally 49.5 kW.
4. In A show a changed mass and a complete unfamiliar card. Export the JSON bundle
   and JSONL, retain the final hash separately, and run execution replay. Preserve
   compatible code/callback/runtime and assumption registry: those are not serialized.
   Hash-chain verification alone does not prove the engine was re-executed.
5. Export before process exit and scenario/mode changes. Execution/environment
   configuration and Reset archive previous runs in memory; a server restart loses
   sessions. Cache selection captures provenance and uses no runtime network fetch.
   Installation windows are planning eligibility, never operational permission.

## Evidence categories and remaining submission work

- **Merged:** runtime/controller, API/UI controls, illustrated ship view, protected
  internal-resume fix and explicit UTF-8 loaders in #46. This is implementation
  status, not blanket acceptance of every challenge narrative.
- **Library/API proof:** existing tests exercise real engine inputs and applied
  simulator actions, crew gates, invalid-input rollback, isolated sessions and
  byte-identical replay. Synthetic future charging inputs are labelled separately.
- **Browser proof:** local-Chrome tests exercise real transport, controls and drawing;
  some tests deliberately substitute responses and prove only rendering/payloads.
  No browser result certifies real-vessel operation. See the PR validation evidence
  for the exact executed counts and skips at this checkpoint.
- **Missing required own card:** G6 is absent from main. Adding it and resolving
  baseline exact-five assertions requires its separately owned implementation/review.
- **Pending integration:** exact D4 metadata producer
  [PR #43](https://github.com/SanjivSimha/maritime-dual-use-hackathon-team-6-SDV/pull/43)
  remains separate. The consumer blocks missing facts. Ambiguous D3 deferrals remain
  blocked. Winner-protection lifetime is provisional and may retain indefinitely.
- **Performance evidence pending:** Claude's measurements are a separate workstream.
  Do not present modeled transition times or test duration as measured A.8 latency.
- **Final hand-in evidence:** retain the chosen final-demo bundle/JSONL/hash with its
  exact release SHA and run a disconnected rehearsal. These PDFs do not include a
  final-run export or establish that rehearsal happened. Confirm the event's upload
  fields and retain the required AI contribution/model disclosure at submission.
- **Physical scope:** engineering proxies and simulated deck/camera evidence are
  not certification. No mentor validation, ship trials, recovery-to-charge overlap
  or universal mission success is claimed. The extra 40 kW warm-water cooling idea
  is a proposal requiring validation, not a measured or optimised result.

Historical [API notes](../shipos/api/README.md) and older verification reports can
contain pre-#46 status statements; current code and this dated checkpoint take
precedence. Their editing is outside this documentation task's ownership.
