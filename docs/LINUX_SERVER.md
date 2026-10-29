# Linux server version (branch `linux-server`)

This branch turns Albion Market Pulse into an always-on service on a Linux server, with a small
Windows client for live data. The desktop app keeps working as before; everything new is either
additive or a bug fix that also benefits the desktop version.

## 1. Architecture

```
 Your Windows PC                               Linux server (systemd)
┌──────────────────────────┐                 ┌───────────────────────────────────────────┐
│ Albion Online            │                 │ albion_flips.server                        │
│   │ market window opened │                 │  ├─ poller: AODP every refresh_seconds     │
│   ▼                      │   HTTPS + key   │  │    (rate-limited, 24/7)                 │
│ Albion Data Client ──────┼────────────────►│  ├─ /api/ingest/<ingest_key>  (live)       │
│   (client/windows)       │                 │  ├─ SQLite: prices + daily history         │
│   └──────────────────────┼──► AODP         │  └─ dashboard (login link / public read)   │
└──────────────────────────┘     ▲           └───────────────▲───────────────────────────┘
                                 │ every player's uploads      │ Caddy / HTTPS
                                 └─────────────────────────────┘ browser, phone, friends
```

* **The server cannot see game traffic.** Only the PC running Albion can. That is why live data comes
  from the official Albion Data Client on your PC, which uploads to AODP *and* to your server.
* **Prices keep flowing when nobody uses the tool.** The server polls AODP around the clock. AODP is
  fed by every player running the Data Client, so the dashboard stays current without your PC.
  If nobody opens a market in a city for hours, prices there get old; there is no official game API
  to ask the game servers directly, and automating the game would break the Terms of Service.

## 2. What changed and why

Each point below was found by comparing this project with a second tracker built on the same data
(Silberradar) and by re-calculating the top results against live AODP data on 2026-10-29.

### 2.1 Bait prices and price spikes (`robust.py`)

**Problem.** AODP only knows the cheapest sell order and highest buy order someone saw. A single
cheap listing (typo, bait, or bought out hours ago) turns into a "huge flip". In live data, 39 of the
top 50 flips by daily profit had such a problem. Example: T6 stone block, cheapest sell order in
Lymhurst 1,011 while the 7-day average was 1,502. Ranked at ~1M silver/day; realistic value ~90k/day.
The only filter was `max_margin_pct = 300`, which this case passes easily.

**Fix.** Every price is checked against the item's 7-day volume-weighted average in that city:

| Check | Rule | What we calculate with |
| --- | --- | --- |
| Bait buy price | live < 85 % of avg7 | 95 % of avg7 |
| Inverted order book | highest buy order > cheapest sell order, seen within the same hour | 95 % of avg7 (or the buy order if no history) |
| Sell spike | live > 110 % of avg7 | 110 % of avg7 |

| No history in the destination | median avg7 of the same item in the other cities | at most 150 % of it (`sell_cap_cross`) |
| No history anywhere | nothing to compare against | live price, flagged `no_history` |

The result keeps both numbers (`raw_buy_price`/`raw_sell_price` vs. `buy_price`/`sell_price`) and a
`price_note`; the dashboard shows a "⚖" badge with a tooltip. All thresholds are in `config.json`
(`bait_floor`, `bait_replace`, `sell_cap`, `sell_cap_cross`); `robust_prices: false` restores the old behaviour.

**Why the cross-city fallback.** The first live run on a server (8,748 items, all qualities) kept
2,500 flips for the dashboard; 2,480 of them had no history in the destination city, and all 190
flips with more than 1M silver "profit" per piece were among them (e.g. a T7.3 spear listed for
15M in Bridgewatch while it trades around 4M elsewhere). They also pushed real opportunities out of
the 2,500-row list. Two fixes: the cap above, and when the list is cut to 2,500 rows, flips with
destination history are kept first (`web.py`).

### 2.2 Realistic daily profit

**Problem.** `est_daily_profit = profit × whole daily volume of the destination city`, i.e. you buy
out the entire market every day.

**Fix.** `profit × units_per_day`, where units per day is the smallest of: `market_share` (default
10 %) of the daily volume, what `capital_silver` buys (optional), and (helper available) mount
capacity × trips. One test asserted the old formula; it now asserts the new one with a comment.

### 2.3 Refining calculation

* **Enchanted refining recipe.** T5.1+ refining needs the previous tier *with the same enchantment*
  (T5.1 bar = 3× T5.1 ore + 1× T4.1 bar). The code always used the plain previous tier, which made
  enchanted refining look cheaper. Only T4.x uses plain T3 (T3 has no enchanted version).
* **No invented prices.** When the previous tier had no price, the code used `100 × (tier − 2)`
  silver (e.g. 200 for a T4 bar). Such rows are now skipped.
* **Freshness.** Flips already ignored prices older than `max_price_age_minutes`; crafting and
  refining did not. Now they do, and with a reference available the sell price is capped like in 2.1.
* **Station fee item values.** Refined resources have item value 2^tier (T3 = 8 … T8 = 256,
  from ao-bin-dumps). The generic curve gave 256 for T6 (4× too much) and 4096 for T8 (16×).

Four crafting tests called `analyze_crafting` without `now`, so they depended on the wall clock
(against the rule in AGENTS.md). They now pass the fixed `TEST_NOW`.

### 2.4 AODP rate limit (`ratelimit.py`)

**Problem.** Requests were spaced by 0.15 s, about 400/min, while AODP allows ~180/min and 300 per
5 min per IP. A full sweep (~8,700 items ≈ 90 price batches, plus history on a cold start) ran into
HTTP 429 and long backoffs. On a server the IP may also be shared with other tools.

**Fix.** A sliding-window limiter (default 170/min and 285/5 min, configurable as
`aodp_rate_per_minute` / `aodp_rate_per_5min`). History is still cached for 6 hours by the client.

### 2.5 Network exposure

**Problem.** The web server listened on all interfaces (`::`). `/api/ingest`, `/api/override` and
`/api/fulfill` have no login, so anyone on the same Wi-Fi could feed fake prices.

**Fix.** Default bind is `127.0.0.1` (`web.host`). The server mode adds authentication (below).

### 2.6 Persistent history (`storage.py`)

SQLite in `service.data_dir`: price samples only when a price changes (kept 7 days), one row per
item/city/quality/day from AODP history (kept 120 days). Provides the 7/30-day references for 2.1,
survives restarts (warm start) and also stores live-client prices. Desktop/CLI without SQLite builds
the same references from the AODP history it already downloads.

## 3. Server mode (`albion_flips/server.py`)

`python -m albion_flips.server --config /etc/albion-pulse/config.json`

* headless, logs to stdout/journald, clean shutdown on SIGTERM, keeps running through AODP errors
* the dashboard "Refresh" button wakes the poller instead of running a sweep in the web thread
* `GET /healthz` (always open), `GET /api/server_status` (sweeps, errors, AODP requests, DB size,
  seconds since the last live upload)
* `--once` runs one sweep and prints a summary; `--init-keys [--write]` creates the two keys

### Access control

| Who | How | May |
| --- | --- | --- |
| You | open `https://<host>/login?k=<access_key>` once (cookie for 400 days, HttpOnly, SameSite=Lax, Secure behind HTTPS) | everything |
| Friends | nothing, if `service.public_read: true` | view only; changes return 401 |
| Windows client | `https://<host>/api/ingest/<ingest_key>` | upload prices only |
| Anyone else | | `/healthz` and the login page |

* Ten wrong keys from one IP lock that IP for 15 minutes.
* `allow_local_ingest` lets a Data Client on the server itself post without key; requests that carry
  `X-Forwarded-For` (i.e. came through the reverse proxy) are never treated as local.
* The ingest key lives in the URL path because the Data Client appends `/marketorders.ingest` to
  every upload URL; a query string would break.

## 4. Setup

### Server (Debian/Ubuntu, Python ≥ 3.11)

```bash
git clone -b linux-server <repo-url> albion-market-pulse
cd albion-market-pulse
sudo ./deploy/linux/install.sh
```

Creates user `albion-pulse`, code in `/opt/albion-pulse`, config in `/etc/albion-pulse/config.json`
(keys generated), data in `/var/lib/albion-pulse`, and the hardened unit `albion-pulse.service`
(read-only system, writes only its data directory). Then put HTTPS in front, e.g. Caddy with
`deploy/linux/Caddyfile.example`. Re-running `install.sh` updates the code and keeps config and data.

Useful commands:

```bash
journalctl -u albion-pulse -f                  # logs
curl -s http://127.0.0.1:8765/healthz          # alive?
sudo grep -E 'access_key|ingest_key' /etc/albion-pulse/config.json
```

Set `service.public_url` (e.g. `https://pulse.example.com`) in the config, then print both links:

```bash
cd /opt/albion-pulse && sudo -u albion-pulse venv/bin/python -m albion_flips.server --config /etc/albion-pulse/config.json --show-links
```

* **Login link** `https://<host>/login?k=<access_key>`: for you; opens the dashboard for a year.
* **Pairing link** `https://<host>/api/ingest/<ingest_key>`: for the Windows client.

Anyone can run their own server the same way: every setting lives in `/etc/albion-pulse/config.json`
(region, cities, qualities, thresholds, poll interval, AODP rate, keys, public read).
New keys: `--init-keys --write`, then `systemctl restart albion-pulse` (old links stop working).

### Windows client (`AlbionMarketPulseClient.exe`)

1. Install Npcap from npcap.com with "WinPcap API-compatible Mode".
2. Download `AlbionMarketPulseClient.exe` from the GitHub release (check it against the `.sha256` file).
3. Start it. The server address is pre-filled if the release was built with a default server; paste
   the pairing link ("Paste pairing link") or type address and key, then "Save and start".
4. From then on it connects and starts automatically every time it is opened.

What it does: verifies server and key (`/healthz` + an empty upload), downloads the official Albion
Data Client from the GitHub release API **only if the SHA-256 matches the digest GitHub publishes**,
and starts it with administrator rights and uploads to AODP and your server.

Security of the client:

| Risk | Protection |
| --- | --- |
| Someone downloads the .exe and sends fake prices | No key is built in. Only the server address is; the key comes from the pairing link. |
| Key sent in clear text | Only `https://` is accepted (plain http only for `localhost`), certificates are verified. |
| Manipulated Data Client download | Official GitHub URL only, SHA-256 must match GitHub's published digest, otherwise nothing is installed or started. |
| Settings changed to a strange community target | The AODP target must be the official `pow.<region>.albion-online-data.com`. |
| Key leaks via logs or screen | The key field is masked, messages show only `abcd...xy`. Settings are stored per user in `%APPDATA%\AlbionMarketPulse\client.json`. |

Other people can use the same .exe with their own server: they type their address and paste their
own pairing link; their choice is saved and wins over the built-in default.

Script alternative: `client/windows/Start-LiveClient.bat` with `client.json` does the same steps.

### In-game overlay (like Overwolf, without injection)

Overwolf apps draw into the game by injecting a renderer into the game process. Albion's developers
accept tools that only *look and analyse*, and anything touching the game process is what anti-cheat
looks for. The overlay therefore copies what Overwolf apps feel like, not how they work:

| Overwolf feature | Here |
| --- | --- |
| Drawn on top of the game | Borderless always-on-top window (`overlay_window.py`). Needs Albion in **windowed** or **borderless window** mode. |
| Does not steal clicks | `WS_EX_TRANSPARENT` + `WS_EX_NOACTIVATE`: clicks go to the game, focus stays in the game. |
| Only visible in game | Shown only while `Albion-Online.exe` is the foreground window. |
| Hotkeys | `RegisterHotKey` (no keyboard hook): Ctrl+Shift+O show/hide, Ctrl+Shift+L unlock to move (position is saved). |
| Game events | The server's event stream: opening an item in the market makes the Data Client upload its orders, the server answers with an item card. |

Server side (`albion_flips/overlay.py`, routes in `server.py`), authorised with the ingest key the
client already has (or `session` + dashboard cookie):

* `GET /api/overlay/<key>/summary` – top 5 verified flips (with 7-day history), live-client status.
* `GET /api/overlay/<key>/item?id=T4_BAG&q=1` – item card on demand.
* `GET /api/overlay/<key>/events` – Server-Sent Events: a summary on connect and after every sweep,
  an item card on every upload (the item with the most orders = the one you opened). Keep-alive every
  15 s, at most 8 connections, a stalled overlay never slows down price ingest.

The item card shows the visible price against the 7-day average (cheap < 85 %, expensive > 115 %),
the best instant sale and the best sell order after tax and setup fee (sell orders capped at 110 % of
the 7-day average), and matching flips. Enable it in the client ("Show the in-game overlay") or start
`AlbionMarketPulseClient.exe --overlay`.

### Price history, alerts, orders, island and profile (server only)

The server adds two dashboard tabs that need its SQLite history (`albion_flips/insights.py`, pages in
`albion_flips/ui/`, injected into the desktop page by `inject_server_ui`, so desktop mode is unchanged):

* **Price History**: search an item by name, id, tier or enchantment (`bag 4`, `6.1 bow`, `T5_PLANKS`).
  *Trades / day* draws the daily average of real trades per city for 7, 30 or 120 days, with traded
  volume as bars. *Order book* draws the cheapest sell order and the best buy order over the last 7
  days. A table below compares current prices, the 7-day average and sales per day per city. Links like
  `/#history=T4_BAG` open an item directly.
* **Price Alerts**: "sell order ≤ price" or "buy order ≥ price", for one city or any city. Checked
  after every sweep and every live upload. A rule fires once and only fires again after the price has
  moved back, so a price that stays low does not ring every few minutes. Fired alerts appear as a
  toast and desktop notification in the dashboard and as a banner in the in-game overlay. Stored in
  `<data_dir>/alerts.json`, at most 100 rules.

* **My Orders** (`albion_flips/orders.py`): Albion has no API for a player's own orders and this
  project never reads the game, so orders are entered by hand (buy order, sell order, bought or sold
  instantly). Fees follow the game: orders pay the 2.5 % setup fee, sales also pay the market tax.
  Each open order is compared with its city's order book after every sweep and live upload; when a
  buy order is outbid or a sell order undercut, this is reported once in the alert feed and in the
  overlay, and the dashboard offers to beat the market by 1 silver. Changing the price of a partly
  filled order keeps the filled part at the old price as its own finished entry. Stock per item uses
  the average purchase cost and is valued at the best instant sale after tax. Stored in
  `<data_dir>/orders.json`, at most 500 entries.

* **Island & Gathering** (`albion_flips/island.py`, game data in `data/island.json`): profit per day
  for every crop and herb, every animal you can raise, and silver per hour for gathering and fishing,
  all from current market prices (bait and spike checks included) and sold in your home city or the
  best city.
  * Crops: a seed gives *crops per seed* (4.5 on average, 9 with premium) once a day and comes back
    with the chance from the game data. Watering adds the game's watering bonus to that chance (wheat
    60 % → 140 %) and costs focus; the table shows the extra silver per 10,000 focus.
  * Animals: grown animal after tax + value of a new baby (offspring chance, higher with focus)
    − baby − food. Food is the cheapest crop or herb per nutrition point; the favourite food counts
    double. Meat eaters are marked, their food is not priced yet.
  * Gathering: units per hour are the player's own estimate per tier; the table adds the blue and
    yellow zones of the home region that have nodes of that tier.
  * Settings (home city, seeds per day, watering, animals, gathering speed) stay in the browser and
    are sent as query parameters, so the server stores nothing. The seeds, crops, herbs, babies,
    grown animals, T2 resources and common fish the tab needs are added to the polled items.
  * `data/island.json` was extracted from the official game files (ao-bin-dumps). Not yet included:
    mounts built from grown animals, animal products, enchanted resources.

* **My Profile** (`albion_flips/player.py`): Albion has no API for a character's Destiny Board, so the
  player enters the highest unlocked tier per skill once (gathering per resource and fishing,
  refining per material, crafting gear/mounts/food/potions, island crops/herbs/animals), plus home
  city, premium, focus per day, island size (farms, herb gardens, pastures) and whether red zones are
  acceptable. Stored on the server in `<data_dir>/profile.json` so every device shows the same; only
  a logged-in visitor can change it.
  * *Right now for you*: the best refining and crafting results (focus or not), checked transport
    flips, crops, animals and gathering that fit those tiers. With "blue and yellow only", anything
    that buys or sells in Caerleon or at the Black Market is left out.
  * The island tab starts from the profile (seeds = 9 per farm and herb garden, animals = 9 per
    pasture, crops per seed from premium) until the player changes a value there, marks rows that
    need a higher tier and can hide them.

| Route | Access |
| --- | --- |
| `GET /api/profile` | read access (`can_edit` tells the page whether this visitor may change it) |
| `POST /api/profile` | login cookie |
| `GET /api/island?home=&mode=instant\|order&seeds=&yield=&water=0\|1&animals=&afocus=0\|1&rates=&fish_rate=` | read access |
| `GET /api/orders` | read access |
| `POST /api/orders`, `/api/orders/update`, `/api/orders/delete` | login cookie |
| `GET /api/items?q=` | read access |
| `GET /api/history?id=&q=&days=7\|30\|120` | read access |
| `GET /api/alerts` | read access (`can_edit` tells the page whether this visitor may change them) |
| `POST /api/alerts`, `POST /api/alerts/delete` | login cookie |

### Releases

`.github/workflows/windows-client.yml` runs on every published GitHub release: tests, bakes the
default server address from the repository variable `PULSE_SERVER_URL` (Settings → Secrets and
variables → Actions → Variables), builds the .exe with PyInstaller (`client/windows/pulse_client.spec`),
smoke-tests it and attaches `AlbionMarketPulseClient.exe` and its `.sha256` to the release. It can
also be started by hand (Actions → Windows client → Run workflow) and then keeps the build as an
artifact. The .exe is not code-signed, so Windows SmartScreen shows a warning on first start.

## 5. Tests

`pytest -q` runs the original 45 tests plus:

* `tests/test_server_branch.py`: robust pricing, the stone-block case, cross-city fallback, list
  cutting, references, refining fixes, item values, rate limiter, SQLite, ingest path, price merge,
  login/public read/write protection/ingest key over real HTTP, proxy handling, one full sweep.
* `tests/test_liveclient.py`: URL/key/pairing-link validation, settings and defaults, connection
  check against a real running server, release checksum rules, refusing a tampered download.
* `tests/test_insights.py`, `tests/test_orders.py`, `tests/test_island.py`, `tests/test_player.py`:
  price history, alerts, order tracker, the island calculator (seed return with and without
  watering, bait seed prices, favourite food, offspring, gathering zones) and the profile (validation,
  storage, tier locks, safe-zone filter, island defaults), with the routes over real HTTP.

Verified on 2026-10-29: 91 tests on Windows (Python 3.12) and Linux (Debian 13, Python 3.13);
`install.sh` on Debian 13 (systemd exposure score 3.0 "OK"); a full sweep against live AODP
(8,748 items, 306k price points, 94 s at a reduced 60 requests/min); login, write protection and
ingest key end to end; the real Data Client 0.1.61 download with checksum; the built .exe
(`--version`, `--check` with valid and invalid key, window opens).

## 6. Needs verification

* `TODO(verify)` item values of gear (only refined resources are verified).
* `Setup-Windows.bat` (desktop setup) still downloads Data Client v0.1.59 as a zip; newer releases
  (0.1.61) no longer ship that zip. The live client and `Start-LiveClient.ps1` use the release API instead.
* AODP rate limits are taken from AGENTS.md and the AODP docs; 429 responses are still retried.
