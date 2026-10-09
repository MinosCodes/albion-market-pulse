# AGENTS.md: Albion Market Analyzer

Read this file first, then `docs/SPEC.md`. Keep this file under 12,000 characters.

## What this project is

A local Python tool that runs in a terminal (plus an optional localhost web page) while the user plays Albion Online. Every few minutes it pulls market prices from the community Albion Online Data Project (AODP) API, calculates city-to-city flip profit after market tax and fees, filters out stale or unrealistic data, and shows a ranked table of the best flips.

Target user: one player on Windows 10/11, Europe server, premium account. Code must also run on Linux and macOS.

## Hard rules (never break these)

1. **Never read the game's memory, inject code, hook the game process, or automate game input.** That risks the user's account and is out of scope.
2. **Never capture or decode game network traffic in this project.** The separate open-source Albion Data Client (github.com/ao-data/albiondata-client) does that and uploads to AODP. This project only reads the public AODP HTTP API.
3. **Respect AODP rate limits.** Batch item IDs per request, cache responses, and throttle. Treat the limits as roughly 180 requests per minute and 300 per 5 minutes and verify against the AODP docs before shipping.
4. **No secrets, accounts or paid services.** The AODP API needs no key.
5. **Do not invent API fields or item IDs.** If a field or ID is not confirmed by a live response or the AODP / ao-bin-dumps sources, mark it `TODO(verify)` and tell the user.

## Stack

- Python 3.11 or newer.
- Runtime dependencies: `requests`, `rich`. Web page uses the standard library `http.server` only.
- Dev dependencies: `pytest`.
- No frameworks, no database in v1 (JSON cache files under `.cache/`).

## Layout

```
albion-market-analyzer/
  AGENTS.md               this file
  README.md               user setup guide (Windows first)
  config.example.json     copy to config.json (config.json is gitignored)
  data/items.json         item groups the program expands into AODP item IDs
  docs/SPEC.md            full specification, formulas, milestones, acceptance criteria
  docs/KICKOFF_PROMPT.md  prompt used to start the build
  albion_flips/           package (create it)
    config.py  api.py  models.py  profit.py  analyzer.py  cli.py  web.py  notify.py
  tests/                  pytest tests
  tests/fixtures/         sample AODP responses with hand-computed expected results
```

## Commands

```
python -m venv .venv
.venv\Scripts\activate          (Windows)   |   source .venv/bin/activate   (Linux/macOS)
pip install -r requirements.txt
pytest -q                        # must pass before any milestone is reported done
python -m albion_flips.cli --once            # one refresh, print table, exit
python -m albion_flips.cli --watch           # refresh in a loop
python -m albion_flips.cli --watch --web     # loop plus http://127.0.0.1:8765
```

## Code conventions

- Type hints everywhere, dataclasses for records, `from __future__ import annotations`.
- Profit math lives only in `profit.py` as pure functions with no I/O, so it is trivially testable.
- Time is injected (`now: datetime` parameter) so tests do not depend on the clock. All timestamps are UTC.
- Money is integer silver until the final display step. Round profit down to whole silver for display only.
- Network code is isolated in `api.py` behind a small class that tests can replace with a fake.
- Log with the `logging` module. No bare `print` outside `cli.py` output.
- Small functions, docstrings that state units and assumptions.

## AODP API facts used here (confirm with the first live call)

- Hosts by server: Europe `europe.albion-online-data.com`, Americas `west.albion-online-data.com`, Asia `east.albion-online-data.com`.
- Prices: `GET /api/v2/stats/prices/{ITEM_ID,ITEM_ID,...}.json?locations=Bridgewatch,Martlock&qualities=1`
  Record fields: `item_id, city, quality, sell_price_min, sell_price_min_date, sell_price_max, sell_price_max_date, buy_price_min, buy_price_min_date, buy_price_max, buy_price_max_date`.
  A price of `0` or a date of `0001-01-01T00:00:00` means no data. Dates have no timezone suffix and are UTC.
- History: `GET /api/v2/stats/history/{ITEM_ID,...}.json?locations=...&qualities=1&time-scale=24`
  Record: `location, item_id, quality, data: [{item_count, avg_price, timestamp}]`.
- Enchanted items use IDs like `T4_PLANKS_LEVEL1@1`.
- Item ID reference: github.com/ao-data/ao-bin-dumps (formatted/items.txt).

## Domain rules (defaults, all configurable)

- Market tax: 4% with premium, 8% without. Setup fee on sell orders: 2.5%. These can change in game updates, so they live in config, never hard-coded in formulas.
- "Buy" cost in city A is the lowest sell order there (`sell_price_min`), i.e. instant buy.
- Two exit modes in city B: list a sell order at `sell_price_min` (pays tax + setup fee), or instant-sell into the best buy order `buy_price_max` (pays tax only).
- Risk flag per city comes from config (Caerleon high, Brecilien medium, royal cities low). It is a label only; it never changes the profit number.

## Definition of done (per milestone)

1. Code written to match `docs/SPEC.md`.
2. `pytest -q` passes, including the fixture-based tests listed in the spec.
3. `python -m albion_flips.cli --once --offline tests/fixtures` works without network and prints the expected top flip.
4. A short note in `README.md` for any new command or setting.
5. Anything unverified is listed under "Needs verification" in the final report.

## Do not

- Do not add features from the Phase 2 list in `docs/SPEC.md` until v1 is accepted.
- Do not add heavy dependencies (pandas, FastAPI, Electron, databases) in v1.
- Do not rewrite or reformat files you were not asked to touch.
