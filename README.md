# ⚔️ Albion Market Analyzer & Arbitrage Engine

A real-time market analysis tool and interactive web dashboard for **Albion Online**. It monitors prices from the community **Albion Online Data Project (AODP)**, calculates real net flip profits after market taxes and listing setup fees, detects Black Market arbitrage opportunities, and evaluates crafting profitability.

**100% Safe & Terms of Service Compliant**: It **never** reads game memory, injects code, hooks the client, or sends automated input. It solely queries the public AODP community REST API.

---

## 🌟 Key Features

- 🏙️ **City-to-City Flips**: Detect profitable trade routes across Bridgewatch, Fort Sterling, Lymhurst, Martlock, Thetford, and Caerleon.
- 🏴‍☠️ **Black Market Arbitrage**: Find high-demand items to buy in Royal cities and flip directly to Caerleon's Black Market buy orders.
  - **Live Arrival Feed**: Badges items that were recently scanned (`⚡ JUST IN`, `🟢 RECENT SCAN`).
  - **Filter by Freshness**: Quickly isolate fresh market orders (`<15m` old) before other players fill them.
- ⚒️ **Crafting Profit Calculator**: Real-time margin calculations factoring in local city return rate bonuses (15.2% Royal / 24.8% Caerleon) and focus crafting (43.5% / 47.9%).
- 🌐 **Interactive Web Dashboard**: Fast, responsive dark-mode dashboard running locally at `http://localhost:8765`.
- ⚙️ **Configurable Tax & Fees**: Accurately factors in Premium tax (4%), non-premium tax (8%), and listing setup fees (2.5%).

---

## 🏗️ Architecture: How Data Flows

```
┌────────────────────────┐
│     Albion Online      │ (Player opens Market / Black Market)
└───────────┬────────────┘
            │  Encrypted UDP game packets
            ▼
┌────────────────────────┐
│  Albion Data Client    │ (Sniffs market response packets locally)
└───────────┬────────────┘
            │  Uploads public price JSON
            ▼
┌────────────────────────┐
│   Albion Online Data   │ (AODP public community database)
│     Project (AODP)     │
└───────────┬────────────┘
            │  REST API calls (cached & throttled)
            ▼
┌────────────────────────┐
│ Albion Market Analyzer │ (Calculates taxes, profits, margins, scores)
└───────────┬────────────┘
            │  JSON API & Websockets/Polling
            ▼
┌────────────────────────┐
│  Local Web Dashboard   │ http://localhost:8765
└────────────────────────┘
```

---

## 🚀 Quickstart: How to Host Locally

### Prerequisites

- **Python 3.11** or newer ([python.org](https://www.python.org/downloads/))
- **Git** ([git-scm.com](https://git-scm.com/))

### 1. Clone & Set Up Virtual Environment

```bash
# Clone the repository
git clone <YOUR_GITHUB_REPO_URL>
cd albion-market-analyzer

# Create virtual environment
python -m venv .venv

# Activate virtual environment
# Windows (Command Prompt):
.venv\Scripts\activate.bat
# Windows (PowerShell):
.venv\Scripts\Activate.ps1
# macOS / Linux:
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### 2. Configure Settings

Copy the example configuration file:

```bash
# Windows:
copy config.example.json config.json

# macOS / Linux:
cp config.example.json config.json
```

Open `config.json` and adjust:
- `"server"`: Set to `"europe"` (Albion Europe), `"west"` (Albion Americas), or `"east"` (Albion Asia).
- `"premium"`: Set to `true` if your character has active Premium (reduces tax to 4%).

### 3. Run the Analyzer & Web Dashboard

To launch continuous watch mode with the interactive web dashboard:

```bash
python -m albion_flips.cli --watch --web
```

Open your browser to:
👉 **[http://localhost:8765](http://localhost:8765)**

To run a single terminal scan without the web server:
```bash
python -m albion_flips.cli --once
```

---

## 📡 How to Feed Live Info Into It

Because the tool reads from the **Albion Online Data Project (AODP)** community database, market data relies on players visiting marketplaces and broadcasting prices.

When you run the **Albion Data Client** in the background, every marketplace tab you open in-game will instantly push fresh prices to AODP, and the analyzer will pick them up within seconds!

### Step 1: Install Albion Data Client

1. Download the latest client for your operating system from the official GitHub:
   👉 **[https://github.com/ao-data/albiondata-client/releases](https://github.com/ao-data/albiondata-client/releases)**
2. System specific setup:
   - **Windows**:
     - Install **Npcap** in "WinPcap API-compatible Mode" (download from [npcap.com](https://npcap.com/)).
     - Extract `albiondata-client-windows-amd64.zip`.
     - Right-click `albiondata-client.exe` and select **Run as Administrator**.
   - **macOS**:
     - Extract the client binary.
     - Because packet capture requires root permissions to access `/dev/bpf`, run:
       ```bash
       sudo ./albiondata-client-executable
       ```
     - (Or double click `start-live-client.command` if on macOS).
   - **Linux**:
     - Install `libpcap`: `sudo apt install libpcap-dev`
     - Run: `sudo ./albiondata-client`

### Step 2: Feed Data in Albion Online

1. With **Albion Data Client** running, launch **Albion Online** and log in.
2. Travel to any marketplace (e.g. Lymhurst, Martlock, Fort Sterling) or the **Caerleon Black Market**.
3. Open the Market board:
   - Scroll through categories or search items (e.g. Armor, Staves, Bags).
   - Click through quality or tier tabs.
4. Watch the **Albion Data Client** console window: you will see logs confirming packets decoded and sent:
   ```text
   Ingested market orders for 25 items -> uploaded to AODP
   ```
5. Switch to your **Albion Market Analyzer Dashboard** (`http://localhost:8765`):
   - The dashboard updates every few seconds.
   - Newly scanned Black Market buy orders immediately display the `⚡ JUST IN` badge and appear in the **Live Black Market Arrival Feed** banner!

---

## 🖥️ Web Dashboard Guide

| Tab | Description |
| --- | --- |
| **All Flips** | Master table showing highest profit-per-item and margin deals across all Royal cities. |
| **Black Market** | Arbitrage routes targeting Caerleon's Black Market. Includes freshness filter pills (`⚡ Just Arrived (<15m)`) and live arrival feed. |
| **Crafting** | Compares raw material buy prices with finished gear sell prices factoring in city return rates. |
| **Focus Crafting** | Analyzes profit margins when using Focus points (high return rates 43.5%–47.9%). |
| **Live Ticker** | Real-time prices of recent item scans across all monitored cities. |

### Deal Scoring & Tiers

- 🟢 **S-Tier (INSTANT PROFIT / HIGH VOL)**: High margin, rapid sales volume, recent scan.
- 🟢 **A-Tier (GOOD DEAL)**: Solid profit with healthy daily transaction counts.
- 🟡 **B-Tier (MODERATE)**: Decent profit, moderate volume or older scan data.
- ⚠️ **C-Tier (SPECULATIVE)**: High profit margin but low daily volume; proceed with caution.

---

## ⚙️ Configuration Reference (`config.json`)

```json
{
  "server": "europe",                   // "europe", "west", or "east"
  "cities": [                           // Monitored markets
    "Bridgewatch", "Fort Sterling", "Lymhurst", "Martlock", "Thetford", "Caerleon"
  ],
  "qualities": [1],                     // 1=Normal, 2=Good, 3=Outstanding, 4=Excellent, 5=Masterpiece
  "premium": true,                      // true = 4% tax, false = 8% tax
  "tax_rate_premium": 0.04,             // In-game premium sales tax
  "tax_rate_standard": 0.08,            // In-game standard sales tax
  "setup_fee_rate": 0.025,              // Order listing setup fee (2.5%)
  "min_profit_silver": 500,             // Minimum profit threshold in silver
  "min_margin_pct": 5,                  // Minimum percentage return on investment
  "max_margin_pct": 300,                // Filters out erroneous or manipulated outlier prices
  "min_daily_volume": 50,               // Minimum average volume to consider liquid
  "volume_days": 3,                     // Historical days to average volume
  "max_price_age_minutes": 120,         // Discard price quotes older than X minutes
  "stack_size": 100,                    // Multiplier for calculating total batch profit
  "refresh_seconds": 180,               // Auto-refresh interval (default 3m; daemon supports down to 20s)
  "top_n": 25,                          // Number of items to display in CLI
  "risk": {                             // Route risk ratings
    "Bridgewatch": "low",
    "Fort Sterling": "low",
    "Lymhurst": "low",
    "Martlock": "low",
    "Thetford": "low",
    "Brecilien": "medium",
    "Caerleon": "high"
  },
  "notify_profit_silver": null,         // Profit threshold for notifications (null disables audio/popups)
  "web": {
    "enabled": false,
    "port": 8765                        // Local port for the web dashboard
  }
}
```

---

## 🧪 Testing

The project includes unit and integration tests covering profit math, tax calculations, AODP API throttles, and Black Market arbitrage:

```bash
pytest -q
```

---

## 🛡️ Terms of Service & Safety

1. **No Memory Reading**: Does not touch the `Albion-Online` process or memory.
2. **No Automation**: No bots, macros, or automated inputs are used.
3. **Open Community Data**: Consumes the same public AODP data as community websites (Albion Online 2D, Albion Free Market).
