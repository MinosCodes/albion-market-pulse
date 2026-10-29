# Desktop mode and full reference

The desktop mode runs everything on your own PC: a native window or a local web dashboard at
`http://localhost:8765`, fed by AODP and, optionally, by the Albion Data Client on the same PC.
For a 24/7 server with a Windows live client and in-game overlay see [LINUX_SERVER.md](LINUX_SERVER.md).

## Quickstart: How to Host Locally

### Prerequisites

- **Python 3.11** or newer ([python.org](https://www.python.org/downloads/))
- **Git** ([git-scm.com](https://git-scm.com/))

### 1. Clone & Set Up Virtual Environment

```bash
# Clone the repository
git clone https://github.com/MinosCodes/albion-market-pulse.git
cd albion-market-pulse

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

## Windows Setup Guide: Albion Data Client (Step-by-Step)

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

### Windows Troubleshooting
- **`Error: No interfaces found` or `cannot open adapter`**:
  - Reinstall Npcap from [npcap.com](https://npcap.com/) and make sure the box **`Install Npcap in WinPcap API-compatible Mode`** is checked.
- **Windows Firewall Alert**:
  - If Windows Defender Firewall asks to allow network access for `albiondata-client.exe`, select **Allow Access** for Private Networks.
- **Python not recognized in CMD**:
  - Download Python from [python.org](https://www.python.org/downloads/) and re-run the installer, selecting **Modify** ➔ Check **`[X] Add python.exe to PATH`**.

---

## Web Dashboard Guide

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

## Configuration Reference (`config.json`)

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
