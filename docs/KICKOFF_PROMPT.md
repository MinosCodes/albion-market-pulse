# Kickoff prompt for Antigravity

Open this folder as the workspace, then paste everything below the line into the agent chat (Agent Manager or the editor's agent panel).

---

You are building the Albion Market Analyzer in this workspace.

Start by reading `AGENTS.md` and `docs/SPEC.md` in full. They are the source of truth for scope, formulas, filtering rules, file layout, milestones and acceptance criteria. Also read `config.example.json`, `data/items.json` and the two files in `tests/fixtures/`.

Before writing code, produce a short implementation plan as an artifact: the modules you will create, the public function and class signatures in `profit.py`, `api.py` and `analyzer.py`, and how you will inject time for tests. Wait for my approval of the plan only if something in the spec is contradictory or missing; otherwise continue.

Then build milestones M1 to M6 from the spec, in order. For each milestone:

1. Write the code and its tests.
2. Run `pytest -q` and fix failures before moving on.
3. Report in a few lines: what you built, which tests passed, and anything that needs verification.

Rules that override everything else:

- Do not read game memory, inject code, hook the game, automate input, or capture game network traffic. This project only reads the public AODP HTTP API.
- Do not invent API fields, item IDs or fee values. If you cannot confirm something, mark it `TODO(verify)` in the code and list it in your report.
- Keep dependencies to `requests`, `rich` and `pytest`. No frameworks and no database in v1.
- Do not build anything from the Phase 2 list.

The profit numbers in `docs/SPEC.md` section 9 were computed by hand. Your implementation must reproduce them exactly (for example 215.5 silver for buy 1000, sell 1300, premium, sell-order exit). If your result differs, assume your code is wrong, not the spec, and tell me if you believe the spec has an error.

If you have network access, make one real call to the AODP prices endpoint for `T4_PLANKS` in Martlock on the Europe host, compare the response to the field names in the spec, and report any difference. If you do not have network access, say so and move on.

Finish by running the offline demo from the acceptance criteria and showing its output, then summarize what is done, what is untested against live data, and what I should check when I run it on my own PC.
