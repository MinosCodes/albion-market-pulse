# ⚔️ Albion Market Analyzer & Arbitrage Engine

A real-time market analysis tool and interactive web dashboard for **Albion Online**. It monitors prices from the community **Albion Online Data Project (AODP)**, calculates real net flip profits after market taxes and listing setup fees, detects Black Market arbitrage opportunities, and evaluates crafting profitability.

**100% Safe & Terms of Service Compliant**: It **never** reads game memory, injects code, hooks the client, or sends automated input. It solely queries the public AODP community REST API.

---

## 🌟 Key Features

- 💻 **Native Desktop Companion App**: Launches in a standalone desktop window (Microsoft Edge WebView2 on Windows 10/11, WebKit on macOS) without browser tabs or address bar clutter. Launch with 1-click via `run_desktop.bat` (Windows), `run_desktop.sh` (macOS/Linux), or `python run_desktop.py`.
- ⚖️ **Transport Weight & Mount Carry Calculator**:
  - **Silver / kg Density**: Shows net profit per kilogram so you can pack maximum value into every transport run.
  - **Mount Presets**: T3–T8 Transport Oxen (672kg – 4,923kg), T5 Armored Horse (800kg - Fast/Safe), T7 Pest Lizard (1,100kg), T8 Grizzly Bear (2,500kg - Tank), or Custom kg.
  - **Full Trip Profit**: Automatically calculates how many units fit on your mount and total trip earnings.
  - **CLI Support**: Rank trade routes by density: `python -m albion_flips.cli --sort density`.
- 📋 **1-Click Copy In-Game Search Name**: Click the 📋 icon next to any item in Flips, Black Market, Refining, or Crafting to copy the clean name directly to your clipboard. Tab into Albion Online, hit `Ctrl+V` in the market search box, and trade immediately.
- ⭐ **Watchlist / Pinned Items**: Click the ★ star button on any item to pin it to your personal Watchlist tab for fast daily tracking across all Royal Cities and the Black Market.
- 🪙 **Station Usage Fee (Nutrition Tax)**: Enter the current city plot fee per 100 nutrition (e.g. 500 silver / 100 nutrition, or 0 on personal island) to view exact net crafting and refining margins.
- 🏙️ **City-to-City Flips**: Detect profitable trade routes across Bridgewatch, Fort Sterling, Lymhurst, Martlock, Thetford, and Caerleon.
- 🏴‍☠️ **Black Market Arbitrage**: Find high-demand items to buy in Royal cities and flip directly to Caerleon's Black Market buy orders.
  - **Live Arrival Feed**: Badges items that were recently scanned (`⚡ JUST IN`, `🟢 RECENT SCAN`).
  - **Filter by Freshness**: Quickly isolate fresh market orders (`<15m` old) before other players fill them.
- ⚒️ **Crafting Profit Calculator**: Real-time margin calculations factoring in local city return rate bonuses (15.2% Royal / 24.8% Caerleon), focus crafting (43.5% / 47.9%), and station fees.
- 🔮 **Enchanting & Mass Enchanting Arbitrage**:
  - **Artifact Foundry Recipes**: Calculates exact material counts (192 for 2H Weapons, 144 for 1H Weapons, 96 for Armor & Bags, 48 for Helmets, Boots, Capes & Off-hands) across `.0 ➜ .1`, `.1 ➜ .2`, `.2 ➜ .3`, `.0 ➜ .2`, and `.0 ➜ .3` transitions.
  - **Live Material Ticker**: Live-scanned rune, soul, and relic prices with 1-click manual price overrides and live sniffer detection.
  - **Mass Batch Profit Simulator**: Configurable batch sizing (10x, 25x, 50x, 100x, or custom) calculating total material cost, mass profit, and ROI margin.
  - **Dual Selling Modes**: Supports local city listing (with tax & setup fee) as well as direct Black Market buy order arbitrage.
  - **1-Click Copy**: Dedicated copy buttons for both the gear item and the exact enchanting material.
- ⚡ **Live Network Sniffer Integration**: Built-in status indicator and guide for pairing with the open-source `albiondata-client` for real-time 0-second market updates.
- 🌐 **Interactive Web Dashboard**: Fast, responsive dark-mode dashboard running locally at `http://localhost:8765`.
- ⚙️ **Configurable Tax & Fees**: Accurately factors in Premium tax (4%), non-premium tax (8%), and listing setup fees (2.5%).


---

## 🏗️ Architecture: How Data Flows

```
┌────────────────────────┐
│     Albion Online      │ (Player opens Market / Black Market)
└───────────┬────────────┘
            │  UDP game packets
            ▼
┌────────────────────────┐
│  Albion Data Client    │
└─────┬────────────┬─────┘
      │            │
      │ (Public)   │ (Direct Local Ingest: < 2ms)
      │            ▼
      │     ┌────────────────────────┐
      │     │ Albion Market Pulse    │ ⚡ 0-second Live Pipe
      │     │ (Local Web Server)     │
      ▼     └───────────┬────────────┘
┌───────────┐           │
│   AODP    │           │
│ Community │           │
│ Database  │           │
└─────┬─────┘           │
      │ (Background)    │
      ▼                 │
┌───────────────────────┴┐
│  Local Web Dashboard   │ http://localhost:8765 (⚡ 0s Sniffer Badges)
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

### 3. Run the Analyzer & Desktop Companion App

#### Option A: 1-Click Desktop App (Native Window)
- **Windows**:
  - Double-click **`run_desktop.bat`** (or `python run_desktop.py`).
  - Launches in a standalone Microsoft Edge WebView2 desktop window without browser tabs!
- **macOS / Linux**:
  - Run **`./run_desktop.sh`** (or `python3 run_desktop.py`).
  - Launches in a native WebKit desktop window.

#### Option B: Terminal Command (CLI or Web Dashboard)
To launch continuous watch mode with the interactive web dashboard from your command line:

```bash
python -m albion_flips.cli --watch --web
```

Open your browser to:
👉 **[http://localhost:8765](http://localhost:8765)**

To launch directly as a desktop window via CLI:
```bash
python -m albion_flips.cli --desktop
```

To run a single terminal scan without the web server:
```bash
python -m albion_flips.cli --once

# Rank by Silver / kg carry density (for mount transport runs):
python -m albion_flips.cli --once --sort density

# Rank by estimated daily silver turnover (profit × sales volume):
python -m albion_flips.cli --once --sort daily

# Skip dead items (volume < 1 or unrecorded history):
python -m albion_flips.cli --once --skip-dead

# Filter for liquid items only (≥ 10 sold per day) sorted by volume:
python -m albion_flips.cli --once --min-volume 10 --sort volume
```


---

## 🪟 Windows Setup Guide: Albion Data Client (Step-by-Step)

The **Albion Data Client** passively sniffs market response packets sent by the game server when you open the market in Albion Online and uploads them to the community database.

Follow these simple steps on Windows:

### 1. Install Npcap (Crucial Driver)
The Data Client requires the **Npcap** packet capture driver:
1. Download the installer from: 👉 **[https://npcap.com/#download](https://npcap.com/#download)**
2. Run the installer (`npcap-x.xx.exe`).
3. ⚠️ **VERY IMPORTANT STEP**: During installation, on the "Installation Options" screen, make sure you check:
   - **`[X] Install Npcap in WinPcap API-compatible Mode`**
4. Complete the installation.

### 2. Auto-Install Albion Market Pulse
In the project folder on Windows, simply double-click:
👉 **`Setup-Windows.bat`**

This automated script will:
- Set up the Python virtual environment and dependencies.
- Automatically download and extract `albiondata-client.exe` from GitHub if not already present.
- Create an **`Albion Market Pulse`** shortcut on your Desktop with the custom Albion medallion icon.

### 3. Running & Ingesting Prices (0-Second Latency)
1. Double-click the **`Albion Market Pulse`** shortcut on your Desktop (or run `Start-Albion-Pulse.bat`).
2. A Windows UAC prompt may appear asking to allow `albiondata-client.exe` — click **Yes** (Admin rights are required by Npcap to capture network adapter packets).
3. The launcher automatically starts:
   - **Albion Data Client** configured with dual-upload (`-i "https+pow://...,http://127.0.0.1:8765/api/ingest"`), feeding both the community and your local dashboard in `<2ms`.
   - **Albion Market Pulse** web engine with 0-second live calculation.
   - Pops open **`http://localhost:8765`** in your browser.
4. Launch **Albion Online** and walk up to any Marketplace or the Caerleon Black Market.
5. In the **Albion Data Client** console window, you will see real-time confirmations:
   ```text
   INFO Ingested 25 market orders -> Sent to Albion Online Data Project & Local Ingest
   ```
6. In your dashboard, the top **Sniffer: ACTIVE** pill turns pulsating neon green, and scanned items instantly appear with the **`⚡ 0s (SNIFFER)`** badge!

### 🛠️ Windows Troubleshooting
- **`Error: No interfaces found` or `cannot open adapter`**:
  - Reinstall Npcap from [npcap.com](https://npcap.com/) and make sure the box **`Install Npcap in WinPcap API-compatible Mode`** is checked.
- **Windows Firewall Alert**:
  - If Windows Defender Firewall asks to allow network access for `albiondata-client.exe`, select **Allow Access** for Private Networks.
- **Python not recognized in CMD**:
  - Download Python from [python.org](https://www.python.org/downloads/) and re-run the installer, selecting **Modify** ➔ Check **`[X] Add python.exe to PATH`**.

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
