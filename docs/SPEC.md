# Specification: Albion Market Analyzer v1

Status: ready to build. Server: Europe. Interface: terminal plus optional localhost page. Focus: city-to-city flipping. Crafting and refining are Phase 2 and must not be built in v1, but the code should not make them hard to add later.

## 1. Goal

While the user plays, show a live, ranked list of the most profitable ways to buy an item in one city and sell it in another, after tax and fees, using only fresh and plausible data.

## 2. Data flow

```
Game -> Albion Data Client (separate app, user installs it) -> AODP community database
                                                                   |
                                         this program: HTTP GET (prices + history)
                                                                   |
                          expand items -> fetch -> filter -> profit -> rank -> terminal / web page
```

This program never touches the game or its network traffic. Freshness depends on players, including the user, opening market windows in-game.

## 3. Configuration (`config.json`, copied from `config.example.json`)

| Key | Meaning | Default |
| --- | --- | --- |
| `server` | `europe`, `americas` or `asia` | `europe` |
| `cities` | cities to compare | Bridgewatch, Fort Sterling, Lymhurst, Martlock, Thetford, Caerleon |
| `qualities` | item qualities to fetch (1 = normal) | `[1]` |
| `premium` | premium account active | `true` |
| `tax_rate_premium` / `tax_rate_standard` | market tax | `0.04` / `0.08` |
| `setup_fee_rate` | fee when placing a sell order | `0.025` |
| `min_profit_silver` | hide flips below this profit per item | `500` |
| `min_margin_pct` | hide flips below this margin | `5` |
| `min_daily_volume` | average daily items sold in the destination city | `50` |
| `volume_days` | days of history averaged | `3` |
| `max_price_age_minutes` | ignore prices older than this | `120` |
| `max_margin_pct` | treat larger margins as bad data | `300` |
| `stack_size` | items used for total-profit column | `100` |
| `refresh_seconds` | loop interval in watch mode | `180` |
| `top_n` | rows to show | `25` |
| `risk` | map city to `low`, `medium` or `high` label | Caerleon high, Brecilien medium, others low |
| `notify_profit_silver` | beep / desktop notification when a flip exceeds this total profit; `null` disables | `null` |
| `web.enabled` / `web.port` | local page | `false` / `8765` |

Validation: unknown city names, negative rates or an unknown server stop the program with a clear message.

## 4. Item list (`data/items.json`)

The file defines groups that the program expands into AODP item IDs:

- `bases`: base names such as `PLANKS`, `METALBAR`.
- `tiers`: numbers such as `[4, 5, 6, 7, 8]`.
- `enchants`: `[0]` means no enchant. `[0, 1, 2, 3]` adds `_LEVEL1@1` style suffixes.
- Expansion rule: `T{tier}_{BASE}` for enchant 0, and `T{tier}_{BASE}_LEVEL{n}@{n}` for enchant n.

`extra_items` is a list of literal item IDs added as-is. The user can edit this file freely.

## 5. Profit model (pure functions in `profit.py`)

Definitions for an item in city A (buy) and city B (sell), all prices in silver:

- `buy_cost = A.sell_price_min` (instant buy).
- Exit 1, sell order: `sell_price = B.sell_price_min`.
- Exit 2, instant sell: `sell_price = B.buy_price_max`.

```latex
profit_{sellorder} = sell_{B} \cdot (1 - tax - setup) - buy_{A}
```

```latex
profit_{instant} = buy\_order_{B} \cdot (1 - tax) - buy_{A}
```

```latex
margin\% = 100 \cdot profit / buy_{A}
```

`total_profit = profit * stack_size`. Compute both exits for every pair and report the better one by default, showing which exit it is. A flip is only eligible when A != B.

## 6. Filtering rules (applied in this order)

1. Drop any price field equal to `0` or with date `0001-01-01T00:00:00`.
2. Drop a price whose date is older than `max_price_age_minutes` relative to injected `now`. Both ends of the flip must be fresh.
3. Drop flips with profit below `min_profit_silver` or margin below `min_margin_pct`.
4. Drop flips with margin above `max_margin_pct` (almost always stale or troll data).
5. Drop flips whose destination average daily volume (from history, `volume_days`) is below `min_daily_volume`. If history is missing for an item, keep it but show volume as `n/a` and mark it with a warning flag.
6. Attach `risk` labels for both cities. Do not change profit based on risk.

## 7. API client (`api.py`)

- Class `AodpClient(server, session=None, throttle=...)` with `get_prices(item_ids, cities, qualities)` and `get_history(item_ids, cities, qualities, days)`.
- Batch item IDs so the URL stays under about 4,000 characters (about 100 IDs per request is a safe start).
- Retry with exponential backoff on 429 and 5xx, at most 3 tries, honoring `Retry-After` if present.
- Cache raw responses to `.cache/` with a short TTL (default 60 seconds for prices, 6 hours for history).
- Identify politely with a `User-Agent` such as `albion-market-analyzer/0.1`.
- Provide `OfflineClient(fixtures_dir)` that serves the JSON in `tests/fixtures/` so the CLI can run without network.

## 8. Interfaces

### Terminal (`cli.py`, uses `rich`)

Arguments: `--once`, `--watch`, `--web`, `--config PATH`, `--offline DIR`, `--top N`, `--sort {profit,total,margin}`.

Table columns: Item, Buy city, Sell city, Buy price, Sell price, Exit (order or instant), Profit/item, Margin %, Total profit (stack), Avg daily volume, Data age (minutes, oldest of the two prices), Risk.

Behavior in `--watch`: redraw in place every `refresh_seconds`, show "last refresh" time and any API error without crashing, exit cleanly on Ctrl+C. If the notify threshold is set, alert once per new flip that crosses it (do not repeat every refresh).

### Web page (`web.py`, optional)

Standard library `http.server` on `127.0.0.1` only. `GET /` serves one static HTML page that polls `GET /api/flips` (JSON) every 10 seconds and renders a sortable table. No external CDN or build step.

## 9. Tests and fixtures

Fixtures in `tests/fixtures/` (already provided):

- `prices_sample.json`: AODP-format price records.
- `history_sample.json`: AODP-format history records.

Tests use `now = 2026-10-09T08:30:00` UTC, `max_price_age_minutes = 120`, premium on, setup fee 0.025, `min_profit_silver = 100`, `min_margin_pct = 5`, `min_daily_volume = 50`.

Required test cases:

| # | Case | Expected |
| --- | --- | --- |
| 1 | `profit_sellorder` with buy 1000, sell 1300, premium | 215.5 silver |
| 2 | `profit_sellorder` with buy 1000, sell 1300, no premium (8%) | 163.5 silver |
| 3 | `profit_instant` with buy 1000, buyer pays 1250, premium | 200 silver |
| 4 | Margin for case 1 | 21.55 % |
| 5 | T4_PLANKS Bridgewatch to Martlock on the sample data | included, sell-order exit better (215.5 vs 200) |
| 6 | T4_PLANKS Lymhurst price dated 2026-10-06 | excluded as stale |
| 7 | T4_METALBAR Fort Sterling 500 to Thetford 520 | excluded, profit is -13.8 |
| 8 | T4_LEATHER with a zero price and `0001-01-01` date | excluded as no data |
| 9 | A flip with margin above `max_margin_pct` | excluded as suspicious |
| 10 | Destination volume below `min_daily_volume` | excluded |
| 11 | Missing history for an item | kept, volume `n/a`, warning flag set |
| 12 | Item expansion for `PLANKS`, tiers `[4]`, enchants `[0, 1]` | `T4_PLANKS`, `T4_PLANKS_LEVEL1@1` |
| 13 | Config with an unknown city | clear error, non-zero exit |
| 14 | `AodpClient` batching 250 IDs | 3 requests, none over the URL length budget (use a fake session) |

## 10. Milestones

1. **M1 Foundation**: package skeleton, config loading and validation, item expansion, `requirements.txt`, tests 12 and 13.
2. **M2 Profit engine**: `profit.py`, filtering, ranking, tests 1 to 11 against fixtures.
3. **M3 API client**: `AodpClient`, `OfflineClient`, batching, caching, retry, test 14. Make one real call only if the network allows it; otherwise list it under "Needs verification".
4. **M4 CLI**: `--once` and `--watch` with `rich` table, offline mode, error handling.
5. **M5 Web page and notifications**: local page, JSON endpoint, optional alerts.
6. **M6 Polish**: README final check, a realistic sample session in the README, a short troubleshooting section.

Report after each milestone: what was built, tests run and their result, and anything that needs verification.

## 11. Acceptance criteria for v1

- `pytest -q` passes with all 14 required cases.
- `python -m albion_flips.cli --once --offline tests/fixtures` prints a table whose top row is T4_PLANKS Bridgewatch to Martlock with profit 215 silver per item (rounded down for display).
- On a machine with internet, `--watch` refreshes without crashing for 30 minutes, including when the API returns an error.
- The user can change server, cities, premium and thresholds only by editing `config.json`.
- No code reads game memory, injects, or captures game traffic.

## 12. Needs verification on first live run

These facts come from the AODP documentation and the author's recollection, and could not be checked from the build environment. Confirm each against a live response and update the code and `AGENTS.md` if different:

- Response field names for prices and history.
- Exact host names per server.
- Current rate limits.
- Whether quality filtering behaves as described.
- Current market tax and setup fee values in the game.
- Item IDs in `extra_items`.

## 13. Phase 2 (do not build in v1)

- Crafting and refining profit: ingredient cost, resource return rate, focus, city specialization bonuses.
- Black Market flips with their own fee rules (verify the rules first).
- Local SQLite price history and trend detection.
- Richer dashboard with filters and charts.
- Packaging as a Windows executable.
