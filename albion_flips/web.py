from __future__ import annotations

import json
import logging
import socket
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Callable, Sequence

from albion_flips.analyzer import analyze_crafting, calculate_deal_score
from albion_flips.config import AppConfig
from albion_flips.models import FlipOpportunity, PriceRecord

logger = logging.getLogger(__name__)

# Load item name dictionary if available
ITEM_NAMES: dict[str, str] = {}
names_file = Path("data/item_names.json")
if names_file.is_file():
    try:
        with open(names_file, "r", encoding="utf-8") as f:
            ITEM_NAMES = json.load(f)
    except Exception as e:
        logger.warning("Could not load item_names.json: %s", e)


def get_human_name(item_id: str) -> str:
    if item_id in ITEM_NAMES:
        return ITEM_NAMES[item_id]
    base_id = item_id.split("@")[0]
    if base_id in ITEM_NAMES:
        return ITEM_NAMES[base_id]
    if "_LEVEL" in item_id:
        clean = item_id.split("_LEVEL")[0]
        if clean in ITEM_NAMES:
            return ITEM_NAMES[clean]
    return item_id.replace("_", " ").title()


def parse_tier(item_id: str) -> int | None:
    if len(item_id) >= 2 and item_id[0] == "T" and item_id[1].isdigit():
        return int(item_id[1])
    return None


def parse_enchant(item_id: str) -> int:
    if "@" in item_id:
        try:
            return int(item_id.split("@")[-1])
        except ValueError:
            return 0
    return 0


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Albion Market Pulse - Albion Market Analyzer</title>
  <link rel="icon" type="image/x-icon" href="/favicon.ico">
  <style>
    :root {
      --bg: #0b0d14;
      --card-bg: #131722;
      --card-alt: #1a1f30;
      --border: #23293b;
      --text: #e2e8f0;
      --text-muted: #8e9bb0;
      --gold: #f59e0b;
      --green: #10b981;
      --yellow: #f59e0b;
      --red: #ef4444;
      --blue: #3b82f6;
      --purple: #a855f7;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      padding: 18px 24px;
      background: var(--bg);
      color: var(--text);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    }
    .header-bar {
      display: flex;
      justify-content: space-between;
      align-items: center;
      flex-wrap: wrap;
      gap: 14px;
      margin-bottom: 16px;
      background: var(--card-bg);
      padding: 14px 20px;
      border-radius: 12px;
      border: 1px solid var(--border);
    }
    h1 {
      margin: 0;
      color: var(--gold);
      font-size: 1.35rem;
      display: flex;
      align-items: center;
      gap: 10px;
    }
    .status-container {
      display: flex;
      align-items: center;
      gap: 14px;
      flex-wrap: wrap;
    }
    .status-badge {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      font-size: 0.82rem;
      padding: 4px 12px;
      border-radius: 999px;
      background: #064e3b;
      color: #34d399;
      font-weight: 600;
    }
    .pulse-dot {
      width: 8px;
      height: 8px;
      border-radius: 50%;
      background: #34d399;
      box-shadow: 0 0 8px #34d399;
    }
    .status-text {
      font-size: 0.82rem;
      color: var(--text-muted);
    }
    .btn {
      background: #2563eb;
      color: white;
      border: none;
      padding: 7px 15px;
      border-radius: 8px;
      font-size: 0.85rem;
      cursor: pointer;
      font-weight: 600;
      display: flex;
      align-items: center;
      gap: 6px;
      transition: all 0.15s ease;
    }
    .btn:hover { background: #1d4ed8; }
    .btn:disabled { background: #475569; cursor: not-allowed; opacity: 0.7; }
    .btn-secondary { background: #334155; }
    .btn-secondary:hover { background: #475569; }
    .btn-focus-active { background: #7c3aed; color: #fff; }
    .btn-focus-active:hover { background: #6d28d9; }

    .nav-tabs {
      display: flex;
      gap: 8px;
      margin-bottom: 14px;
    }
    .tab-btn {
      background: #171b29;
      color: var(--text-muted);
      border: 1px solid var(--border);
      padding: 8px 18px;
      border-radius: 8px;
      font-size: 0.9rem;
      cursor: pointer;
      font-weight: 600;
      transition: all 0.15s;
    }
    .tab-btn.active {
      background: var(--blue);
      color: white;
      border-color: var(--blue);
    }

    .filter-panel {
      background: var(--card-bg);
      border: 1px solid var(--border);
      padding: 14px 16px;
      border-radius: 12px;
      margin-bottom: 14px;
      display: flex;
      flex-direction: column;
      gap: 10px;
    }
    .quick-pills {
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
      align-items: center;
      padding-bottom: 8px;
      border-bottom: 1px solid var(--border);
    }
    .pill-btn {
      background: #1a1f30;
      color: var(--text-muted);
      border: 1px solid var(--border);
      padding: 4px 12px;
      border-radius: 999px;
      font-size: 0.8rem;
      cursor: pointer;
      font-weight: 600;
      transition: all 0.15s;
    }
    .pill-btn:hover {
      background: #252e47;
      color: var(--text);
    }
    .pill-btn.active {
      background: #2563eb;
      color: white;
      border-color: #2563eb;
    }
    .filter-row {
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
      align-items: center;
    }
    .search-input {
      background: #0d101a;
      border: 1px solid var(--border);
      color: var(--text);
      padding: 6px 12px;
      border-radius: 8px;
      font-size: 0.85rem;
      flex: 1;
      min-width: 220px;
    }
    .search-input:focus {
      outline: none;
      border-color: var(--blue);
    }
    .filter-select {
      background: #0d101a;
      border: 1px solid var(--border);
      color: var(--text);
      padding: 6px 10px;
      border-radius: 8px;
      font-size: 0.85rem;
    }
    .filter-select:focus {
      outline: none;
      border-color: var(--blue);
    }
    .filter-label {
      font-size: 0.78rem;
      color: var(--text-muted);
      margin-right: 4px;
    }

    table {
      width: 100%;
      border-collapse: separate;
      border-spacing: 0;
      background: var(--card-bg);
      border-radius: 12px;
      overflow: hidden;
      border: 1px solid var(--border);
      font-size: 0.88rem;
    }
    th, td {
      padding: 10px 14px;
      text-align: left;
      border-bottom: 1px solid var(--border);
    }
    th {
      background: #181d2e;
      color: #94a3b8;
      font-weight: 600;
      cursor: pointer;
      user-select: none;
      white-space: nowrap;
      font-size: 0.82rem;
      text-transform: uppercase;
      letter-spacing: 0.04em;
    }
    th:hover { color: var(--gold); }
    th.num, td.num { text-align: right; }
    tbody tr:hover { background: #191e30; }

    .item-cell {
      display: flex;
      flex-direction: row;
      align-items: center;
      gap: 10px;
    }
    .item-icon-wrapper {
      position: relative;
      width: 40px;
      height: 40px;
      flex-shrink: 0;
      background: #0f131d;
      border: 1px solid rgba(255, 255, 255, 0.08);
      border-radius: 8px;
      display: flex;
      align-items: center;
      justify-content: center;
      overflow: hidden;
      box-shadow: 0 2px 5px rgba(0,0,0,0.35);
    }
    .item-icon {
      width: 100%;
      height: 100%;
      object-fit: contain;
      display: block;
      transition: transform 0.15s ease;
    }
    .item-cell:hover .item-icon {
      transform: scale(1.1);
    }
    .item-details {
      display: flex;
      flex-direction: column;
      gap: 2px;
      min-width: 0;
    }
    .item-name {
      font-weight: 600;
      color: #ffffff;
      font-size: 0.88rem;
      text-decoration: none;
      display: inline-flex;
      align-items: center;
      gap: 5px;
      transition: color 0.15s ease;
    }
    .item-name:hover {
      color: var(--gold);
      text-decoration: underline;
    }
    .wiki-badge {
      font-size: 0.65rem;
      color: #94a3b8;
      background: rgba(255, 255, 255, 0.07);
      padding: 1px 5px;
      border-radius: 4px;
      text-decoration: none;
      font-weight: normal;
      transition: all 0.15s ease;
    }
    .item-name:hover .wiki-badge {
      color: #f59e0b;
      background: rgba(245, 158, 11, 0.2);
    }
    .item-id-sub {
      font-size: 0.70rem;
      color: #64748b;
      font-family: monospace;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }

    .badge {
      display: inline-block;
      padding: 2px 7px;
      border-radius: 6px;
      font-size: 0.72rem;
      font-weight: 700;
      text-align: center;
      line-height: 1.4;
    }
    /* Deal Rating Badges */
    .deal-s {
      background: rgba(245, 158, 11, 0.22);
      color: #f59e0b;
      border: 1px solid #f59e0b;
      box-shadow: 0 0 10px rgba(245, 158, 11, 0.35);
    }
    .deal-a {
      background: rgba(16, 185, 129, 0.2);
      color: #34d399;
      border: 1px solid #10b981;
    }
    .deal-b {
      background: rgba(234, 179, 8, 0.15);
      color: #facc15;
      border: 1px solid #ca8a04;
    }
    .deal-c {
      background: #1e293b;
      color: #94a3b8;
      border: 1px solid #475569;
    }

    .badge-tier { background: #3b82f6; color: white; margin-right: 4px; }
    .badge-q { background: #1e293b; color: #94a3b8; border: 1px solid #334155; }
    .badge-order { background: rgba(59, 130, 246, 0.15); color: #60a5fa; border: 1px solid rgba(59, 130, 246, 0.3); }
    .badge-instant { background: rgba(168, 85, 247, 0.15); color: #c084fc; border: 1px solid rgba(168, 85, 247, 0.3); }
    .badge-bonus { background: rgba(16, 185, 129, 0.18); color: #34d399; border: 1px solid #10b981; }

    .risk-high { color: var(--red); font-weight: 700; }
    .risk-medium { color: var(--yellow); font-weight: 700; }
    .risk-low { color: var(--green); font-weight: 700; }
    
    .profit-val { color: var(--green); font-weight: 700; }
    .margin-val { color: var(--gold); font-weight: 600; }
    .fresh-age { color: #34d399; font-weight: 600; }

    .pagination-bar {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-top: 14px;
      padding: 10px 14px;
      background: var(--card-bg);
      border-radius: 10px;
      border: 1px solid var(--border);
      font-size: 0.84rem;
      color: var(--text-muted);
    }
    .page-controls {
      display: flex;
      gap: 8px;
      align-items: center;
    }
    .bm-pulse-badge {
      display: inline-flex;
      align-items: center;
      gap: 4px;
      font-size: 0.72rem;
      padding: 2px 7px;
      border-radius: 999px;
      background: #065f46;
      color: #34d399;
      font-weight: 700;
      margin-left: 6px;
      border: 1px solid #059669;
      animation: pulse-green 2s infinite;
    }
    @keyframes pulse-green {
      0%, 100% { box-shadow: 0 0 0 0 rgba(52, 211, 153, 0.4); }
      50% { box-shadow: 0 0 0 5px rgba(52, 211, 153, 0); }
    }
    .badge-bm-new {
      background: #064e3b;
      color: #34d399;
      font-weight: 700;
      border: 1px solid #059669;
      padding: 2px 6px;
      border-radius: 4px;
      font-size: 0.72rem;
      display: inline-flex;
      align-items: center;
      gap: 3px;
    }
    .toast-container {
      position: fixed;
      bottom: 24px;
      right: 24px;
      z-index: 9999;
      display: flex;
      flex-direction: column;
      gap: 10px;
      pointer-events: none;
    }
    .toast-msg {
      background: #1e293b;
      border: 1px solid #10b981;
      color: #e2e8f0;
      padding: 10px 16px;
      border-radius: 8px;
      box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.5);
      font-size: 0.85rem;
      display: flex;
      align-items: center;
      gap: 8px;
      animation: slide-in 0.3s ease-out;
      pointer-events: auto;
    }
    @keyframes slide-in {
      from { transform: translateX(100%); opacity: 0; }
      to { transform: translateX(0); opacity: 1; }
    }
  </style>
</head>
<body>

  <div class="header-bar">
    <h1>
      <span>⚔️ Albion Market & Crafting Analyzer</span>
    </h1>
    <div class="status-container">
      <div class="status-badge">
        <span class="pulse-dot"></span>
        <span id="server-badge">Europe Live</span>
      </div>
      <div class="status-text">
        Last Refresh: <span id="last-refresh" style="color: var(--text); font-weight: 600;">Connecting...</span>
      </div>
      <button id="btn-browser-notif" class="btn btn-secondary" onclick="toggleBrowserNotifications()">
        🔔 Alerts: OFF
      </button>
      <button id="btn-refresh" class="btn" onclick="triggerManualRefresh()">
        <span>↻</span> Fetch Live Data
      </button>
    </div>
  </div>


  <div class="nav-tabs">
    <button id="tab-flips" class="tab-btn active" onclick="switchTab('flips')">🏆 Profitable Flips</button>
    <button id="tab-blackmarket" class="tab-btn" onclick="switchTab('blackmarket')">
      🏴‍☠️ Black Market <span id="bm-badge" class="bm-pulse-badge" style="display: none;"></span>
    </button>
    <button id="tab-crafting" class="tab-btn" onclick="switchTab('crafting')">🔨 Crafting & Refining Profit</button>
    <button id="tab-prices" class="tab-btn" onclick="switchTab('prices')">📡 Live Scanned Prices</button>
  </div>

  <div class="filter-panel">
    <!-- Quick Filters for Flips -->
    <div id="quick-flips" class="quick-pills">
      <span class="filter-label">Quick Filters:</span>
      <button class="pill-btn active" onclick="setFlipQuickFilter('all', this)">🔥 All Deals</button>
      <button class="pill-btn" onclick="setFlipQuickFilter('top_picks', this)">⭐ Top Picks (S & A Tier)</button>
      <button class="pill-btn" onclick="setFlipQuickFilter('instant', this)">⚡ Instant Sell (0 Risk)</button>
      <button class="pill-btn" onclick="setFlipQuickFilter('safe', this)">🛡️ Safe Royal Cities</button>
      <button class="pill-btn" onclick="setFlipQuickFilter('fresh', this)">🟢 Fresh Only (&lt;60m)</button>
      <button class="pill-btn" onclick="setFlipQuickFilter('high_profit', this)">💎 High Profit (>50k)</button>
    </div>

    <!-- Quick Filters for Black Market -->
    <div id="quick-blackmarket" class="quick-pills" style="display: none;">
      <span class="filter-label">BM Quick Filters:</span>
      <button class="pill-btn active" onclick="setBmFilter('all', this)">🔥 All BM Deals</button>
      <button class="pill-btn" onclick="setBmFilter('just_arrived', this)">⚡ Just Arrived (&lt;15m)</button>
      <button class="pill-btn" onclick="setBmFilter('instant', this)">⚡ Direct Buy Orders (Instant Cash)</button>
      <button class="pill-btn" onclick="setBmFilter('weapons', this)">⚔️ Weapons</button>
      <button class="pill-btn" onclick="setBmFilter('armors', this)">🛡️ Armor & Robes</button>
      <button class="pill-btn" onclick="setBmFilter('accessories', this)">🎒 Bags & Capes</button>
      <button class="pill-btn" onclick="setBmFilter('high_profit', this)">💎 100k+ Profit</button>
    </div>


    <!-- Quick Filters for Crafting -->
    <div id="quick-crafting" class="quick-pills" style="display: none;">
      <span class="filter-label">Categories:</span>
      <button class="pill-btn active" onclick="setCraftFilter('all', this)">All Recipes</button>
      <button class="pill-btn" onclick="setCraftFilter('Wood Refining', this)">🪵 Wood / Planks</button>
      <button class="pill-btn" onclick="setCraftFilter('Ore Refining', this)">⛏️ Ore / Bars</button>
      <button class="pill-btn" onclick="setCraftFilter('Fiber Refining', this)">🌾 Fiber / Cloth</button>
      <button class="pill-btn" onclick="setCraftFilter('Hide Refining', this)">🐾 Hide / Leather</button>
      <button class="pill-btn" onclick="setCraftFilter('Stone Refining', this)">🧱 Stone Blocks</button>
      <button class="pill-btn" onclick="setCraftFilter('Bag Crafting', this)">🎒 Bags</button>
      <button class="pill-btn" onclick="setCraftFilter('Cape Crafting', this)">🧣 Capes</button>
      <button class="pill-btn" onclick="setCraftFilter('Weapon', this)">⚔️ Weapons</button>
      <button class="pill-btn" onclick="setCraftFilter('Armor', this)">🛡️ Armors</button>
      <button id="btn-focus-toggle" class="pill-btn" style="margin-left: auto; border-color: var(--purple); color: #d8b4fe;" onclick="toggleFocusMode()">✨ Focus: OFF</button>
    </div>

    <div class="filter-row">
      <input type="text" id="search-box" class="search-input" placeholder="Search by name, tier (e.g. 6.2), or city..." oninput="onSearchInput()">
      
      <div>
        <span class="filter-label">Tier:</span>
        <select id="filter-tier" class="filter-select" onchange="onFilterChange()">
          <option value="all">All Tiers</option>
          <option value="3">Tier 3</option>
          <option value="4">Tier 4</option>
          <option value="5">Tier 5</option>
          <option value="6">Tier 6</option>
          <option value="7">Tier 7</option>
          <option value="8">Tier 8</option>
        </select>
      </div>

      <div>
        <span class="filter-label">Enchant:</span>
        <select id="filter-enchant" class="filter-select" onchange="onFilterChange()">
          <option value="all">All Enchants</option>
          <option value="0">.0 (Flat)</option>
          <option value="1">.1 (Green)</option>
          <option value="2">.2 (Blue)</option>
          <option value="3">.3 (Purple)</option>
        </select>
      </div>

      <div id="filter-quality-container">
        <span class="filter-label">Quality:</span>
        <select id="filter-quality" class="filter-select" onchange="onFilterChange()">
          <option value="all">All Qualities</option>
          <option value="1">Q1 Normal</option>
          <option value="2">Q2 Good</option>
          <option value="3">Q3 Outstanding</option>
          <option value="4">Q4 Excellent</option>
          <option value="5">Q5 Masterpiece</option>
        </select>
      </div>

      <div>
        <span class="filter-label">Buy/Craft City:</span>
        <select id="filter-buy-city" class="filter-select" onchange="onFilterChange()">
          <option value="all">All Cities</option>
          <option value="Bridgewatch">Bridgewatch</option>
          <option value="Fort Sterling">Fort Sterling</option>
          <option value="Lymhurst">Lymhurst</option>
          <option value="Martlock">Martlock</option>
          <option value="Thetford">Thetford</option>
          <option value="Caerleon">Caerleon</option>
          <option value="Brecilien">Brecilien</option>
        </select>
      </div>

      <div id="filter-sell-city-container">
        <span class="filter-label">Sell City:</span>
        <select id="filter-sell-city" class="filter-select" onchange="onFilterChange()">
          <option value="all">All Sell Cities</option>
          <option value="Bridgewatch">Bridgewatch</option>
          <option value="Fort Sterling">Fort Sterling</option>
          <option value="Lymhurst">Lymhurst</option>
          <option value="Martlock">Martlock</option>
          <option value="Thetford">Thetford</option>
          <option value="Caerleon">Caerleon</option>
          <option value="Brecilien">Brecilien</option>
        </select>
      </div>

      <div>
        <span class="filter-label">Max Age:</span>

        <select id="filter-age" class="filter-select" onchange="onFilterChange()">
          <option value="all">Any Age (&le; 2h)</option>
          <option value="15">&lt; 15 mins (Hot 🟢)</option>
          <option value="30">&lt; 30 mins (Fresh 🟢)</option>
          <option value="60">&lt; 60 mins (1 Hour 🟡)</option>
        </select>
      </div>

      <button class="btn btn-secondary" onclick="resetFilters()">Reset Filters</button>
    </div>

  </div>

  <!-- VIEW 1: FLIPS -->
  <div id="view-flips">
    <table id="flips-table">
      <thead>
        <tr>
          <th onclick="sortFlips('score')">Rating</th>
          <th onclick="sortFlips('item_name')">Item</th>
          <th>Tier / Q</th>
          <th onclick="sortFlips('buy_city')">Buy City</th>
          <th onclick="sortFlips('sell_city')">Sell City</th>
          <th class="num" onclick="sortFlips('buy_price', true)">Buy Price</th>
          <th class="num" onclick="sortFlips('sell_price', true)">Sell Price</th>
          <th>Exit Mode</th>
          <th class="num" onclick="sortFlips('profit_per_item', true)">Profit / Item</th>
          <th class="num" onclick="sortFlips('margin_pct', true)">ROI Margin</th>
          <th class="num" onclick="sortFlips('total_profit', true)">Total Profit</th>
          <th class="num" onclick="sortFlips('avg_daily_volume', true)">Daily Vol</th>
          <th class="num" onclick="sortFlips('data_age_minutes', true)">Data Age</th>
          <th onclick="sortFlips('risk')">Route Risk</th>
        </tr>
      </thead>
      <tbody id="flips-body">
        <tr><td colspan="14" style="text-align: center; padding: 24px; color: var(--text-muted);">Connecting to analyzer backend...</td></tr>
      </tbody>
    </table>
  </div>

  <!-- VIEW 2: BLACK MARKET -->
  <div id="view-blackmarket" style="display: none;">
    <!-- Live Arrival Feed -->
    <div id="bm-arrival-card" style="background: rgba(16, 185, 129, 0.1); border: 1px solid rgba(16, 185, 129, 0.3); border-radius: 8px; padding: 10px 16px; margin-bottom: 12px; font-size: 0.86rem; color: #a7f3d0; display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 10px;">
      <div style="display: flex; align-items: center; gap: 8px;">
        <span class="pulse-dot" style="background:#10b981; box-shadow:0 0 8px #10b981;"></span>
        <span id="bm-arrival-summary"><strong>Black Market Feed:</strong> Monitoring live AODP scans...</span>
      </div>
      <div id="bm-recent-chips" style="display: flex; gap: 6px; flex-wrap: wrap; align-items: center;"></div>
    </div>

    <div style="background: rgba(239, 68, 68, 0.12); border: 1px solid rgba(239, 68, 68, 0.35); border-radius: 8px; padding: 12px 18px; margin-bottom: 16px; font-size: 0.86rem; color: #fca5a5; display: flex; align-items: center; gap: 12px;">
      <span style="font-size: 1.35rem;">⚠️</span>
      <div>
        <strong>Caerleon Red Zone Notice:</strong> Items must be transported to Caerleon to sell at the Black Market. Red zones have full PvP loot. Mount recommendation: Armored Horse, Grizzly Bear, or Pest Lizard. Always check the hostile player counter before leaving the city!
      </div>
    </div>
    <table id="blackmarket-table">
      <thead>
        <tr>
          <th onclick="sortBlackMarket('score')">Rating</th>
          <th onclick="sortBlackMarket('item_name')">Item</th>
          <th>Tier / Q</th>
          <th onclick="sortBlackMarket('buy_city')">Buy City</th>
          <th class="num" onclick="sortBlackMarket('buy_price', true)">Buy Price</th>
          <th class="num" onclick="sortBlackMarket('sell_price', true)">BM Price</th>
          <th>Exit Mode</th>
          <th class="num" onclick="sortBlackMarket('profit_per_item', true)">Profit / Item</th>
          <th class="num" onclick="sortBlackMarket('margin_pct', true)">ROI Margin</th>
          <th class="num" onclick="sortBlackMarket('total_profit', true)">Total Profit</th>
          <th class="num" onclick="sortBlackMarket('avg_daily_volume', true)">Daily BM Vol</th>
          <th class="num" onclick="sortBlackMarket('data_age_minutes', true)">Data Age</th>
          <th>Action</th>
        </tr>
      </thead>
      <tbody id="blackmarket-body">
        <tr><td colspan="13" style="text-align: center; padding: 24px; color: var(--text-muted);">Loading Black Market buy orders & flips...</td></tr>
      </tbody>
    </table>
  </div>

  <!-- VIEW 2: CRAFTING -->
  <div id="view-crafting" style="display: none;">
    <table id="crafting-table">
      <thead>
        <tr>
          <th onclick="sortCrafting('item_name')">Crafted Item</th>
          <th>Category</th>
          <th onclick="sortCrafting('craft_city')">Craft City</th>
          <th>Ingredients & Breakdown</th>
          <th class="num" onclick="sortCrafting('effective_cost', true)">Effective Mat Cost</th>
          <th onclick="sortCrafting('sell_city')">Best Sell City</th>
          <th class="num" onclick="sortCrafting('sell_price', true)">Sell Price</th>
          <th class="num" onclick="sortCrafting('profit_per_item', true)">Profit / Craft</th>
          <th class="num" onclick="sortCrafting('margin_pct', true)">ROI Margin</th>
        </tr>
      </thead>
      <tbody id="crafting-body">
        <tr><td colspan="9" style="text-align: center; padding: 24px; color: var(--text-muted);">Calculating optimal crafting and refining recipes...</td></tr>
      </tbody>
    </table>
  </div>

  <!-- VIEW 3: LIVE SCANNED PRICES -->
  <div id="view-prices" style="display: none;">
    <table id="prices-table">
      <thead>
        <tr>
          <th>Item</th>
          <th>Tier / Q</th>
          <th>City</th>
          <th class="num">Lowest Sell Order</th>
          <th class="num">Highest Buy Order</th>
          <th class="num">Age</th>
          <th>Status</th>
        </tr>
      </thead>
      <tbody id="prices-body">
        <tr><td colspan="7" style="text-align: center; padding: 24px; color: var(--text-muted);">Loading live captured prices...</td></tr>
      </tbody>
    </table>
  </div>

  <!-- PAGINATION CONTROLS -->
  <div class="pagination-bar">
    <span id="page-summary">Showing 0 of 0 items</span>
    <div class="page-controls">
      <button id="btn-prev" class="btn btn-secondary" onclick="prevPage()" disabled>◀ Previous</button>
      <span id="page-current" style="font-weight: 600; color: var(--text);">Page 1</span>
      <button id="btn-next" class="btn btn-secondary" onclick="nextPage()" disabled>Next ▶</button>
    </div>
  </div>

  <script>
    let currentTab = 'flips';
    let flipsData = [];
    let blackmarketData = [];
    let craftingData = [];
    let focusCraftingData = [];
    let pricesData = [];
    
    // Performance: Pagination & Debounce
    let currentPage = 1;
    const pageSize = 40;
    let searchDebounceTimer = null;

    // Filters state
    let flipQuickFilter = 'all';
    let bmQuickFilter = 'all';
    let craftCategoryFilter = 'all';
    let isFocusMode = false;

    // Sorting state
    let sortKeyFlips = 'score';
    let sortAscFlips = false;
    let sortKeyBm = 'profit_per_item';
    let sortAscBm = false;
    let sortKeyCrafting = 'profit_per_item';
    let sortAscCrafting = false;

    function escapeHtml(str) {
      if (!str) return '';
      return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
    }

    function getWikiUrl(itemName, itemId) {
      if (!itemName && !itemId) return 'https://wiki.albiononline.com/';
      let target = itemName || itemId;
      let clean = target.replace(/\\s*\\(.*?\\)\\s*/g, '').trim().replace(/\\s+/g, '_');
      return `https://wiki.albiononline.com/wiki/${encodeURIComponent(clean)}`;
    }

    function renderItemCell(itemId, itemName, quality) {
      const safeName = escapeHtml(itemName || itemId);
      const safeId = escapeHtml(itemId);
      const q = quality || 1;
      const wikiUrl = getWikiUrl(itemName, itemId);
      const iconUrl = `https://render.albiononline.com/v1/item/${encodeURIComponent(itemId)}.png?quality=${q}`;

      return `
        <div class="item-cell">
          <div class="item-icon-wrapper" title="${safeName}">
            <img class="item-icon" 
                 src="${iconUrl}" 
                 alt="${safeName}" 
                 loading="lazy" 
                 onerror="this.parentElement.style.display='none';" />
          </div>
          <div class="item-details">
            <a class="item-name" href="${wikiUrl}" target="_blank" rel="noopener noreferrer" title="View ${safeName} on Albion Wiki">
              <span>${safeName}</span>
              <span class="wiki-badge" title="Open Wiki Guide">Wiki ↗</span>
            </a>
            <span class="item-id-sub">${safeId}</span>
          </div>
        </div>
      `;
    }

    function onSearchInput() {
      clearTimeout(searchDebounceTimer);
      searchDebounceTimer = setTimeout(() => {
        currentPage = 1;
        renderCurrentView();
      }, 180);
    }

    function onFilterChange() {
      currentPage = 1;
      renderCurrentView();
    }

    function resetFilters() {
      document.getElementById('search-box').value = '';
      document.getElementById('filter-tier').value = 'all';
      document.getElementById('filter-enchant').value = 'all';
      document.getElementById('filter-quality').value = 'all';
      document.getElementById('filter-buy-city').value = 'all';
      document.getElementById('filter-sell-city').value = 'all';
      if (document.getElementById('filter-age')) document.getElementById('filter-age').value = 'all';
      flipQuickFilter = 'all';
      bmQuickFilter = 'all';
      craftCategoryFilter = 'all';
      document.querySelectorAll('.quick-pills .pill-btn').forEach(btn => btn.classList.remove('active'));
      const flipPill = document.querySelector('#quick-flips .pill-btn');
      if (flipPill) flipPill.classList.add('active');
      const bmPill = document.querySelector('#quick-blackmarket .pill-btn');
      if (bmPill) bmPill.classList.add('active');
      const craftPill = document.querySelector('#quick-crafting .pill-btn');
      if (craftPill) craftPill.classList.add('active');
      currentPage = 1;
      renderCurrentView();
    }

    function setFlipQuickFilter(mode, btn) {
      flipQuickFilter = mode;
      document.querySelectorAll('#quick-flips .pill-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      currentPage = 1;
      renderCurrentView();
    }

    function setBmFilter(mode, btn) {
      bmQuickFilter = mode;
      document.querySelectorAll('#quick-blackmarket .pill-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      currentPage = 1;
      renderCurrentView();
    }

    function sortBlackMarket(key, isNumeric = false) {
      if (sortKeyBm === key) {
        sortAscBm = !sortAscBm;
      } else {
        sortKeyBm = key;
        sortAscBm = isNumeric ? false : true;
      }
      renderBlackMarket();
    }

    function copyItemName(text, btn) {
      navigator.clipboard.writeText(text).then(() => {
        const orig = btn.innerHTML;
        btn.innerHTML = '✅ Copied!';
        btn.style.color = '#34d399';
        setTimeout(() => {
          btn.innerHTML = orig;
          btn.style.color = '';
        }, 1200);
      }).catch(err => {
        console.error('Failed to copy', err);
      });
    }

    function setCraftFilter(category, btn) {
      craftCategoryFilter = category;
      document.querySelectorAll('#quick-crafting .pill-btn:not(#btn-focus-toggle)').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      currentPage = 1;
      renderCurrentView();
    }

    function toggleFocusMode() {
      isFocusMode = !isFocusMode;
      const btn = document.getElementById('btn-focus-toggle');
      if (isFocusMode) {
        btn.textContent = '✨ Focus: ACTIVE (High Return)';
        btn.classList.add('btn-focus-active');
      } else {
        btn.textContent = '✨ Focus: OFF';
        btn.classList.remove('btn-focus-active');
      }
      currentPage = 1;
      renderCurrentView();
    }

    function switchTab(tab) {
      currentTab = tab;
      document.getElementById('tab-flips').className = 'tab-btn' + (tab === 'flips' ? ' active' : '');
      document.getElementById('tab-blackmarket').className = 'tab-btn' + (tab === 'blackmarket' ? ' active' : '');
      document.getElementById('tab-crafting').className = 'tab-btn' + (tab === 'crafting' ? ' active' : '');
      document.getElementById('tab-prices').className = 'tab-btn' + (tab === 'prices' ? ' active' : '');

      document.getElementById('view-flips').style.display = (tab === 'flips') ? 'block' : 'none';
      document.getElementById('view-blackmarket').style.display = (tab === 'blackmarket') ? 'block' : 'none';
      document.getElementById('view-crafting').style.display = (tab === 'crafting') ? 'block' : 'none';
      document.getElementById('view-prices').style.display = (tab === 'prices') ? 'block' : 'none';

      document.getElementById('quick-flips').style.display = (tab === 'flips') ? 'flex' : 'none';
      document.getElementById('quick-blackmarket').style.display = (tab === 'blackmarket') ? 'flex' : 'none';
      document.getElementById('quick-crafting').style.display = (tab === 'crafting') ? 'flex' : 'none';

      // Hide or show Sell City filter container on Black Market tab (since destination is always Black Market)
      const sellCityCont = document.getElementById('filter-sell-city-container');
      if (sellCityCont) {
        sellCityCont.style.display = (tab === 'blackmarket') ? 'none' : 'block';
      }

      currentPage = 1;
      renderCurrentView();
    }

    function prevPage() {
      if (currentPage > 1) {
        currentPage--;
        renderCurrentView();
        window.scrollTo({ top: 180, behavior: 'smooth' });
      }
    }

    function nextPage() {
      currentPage++;
      renderCurrentView();
      window.scrollTo({ top: 180, behavior: 'smooth' });
    }

    function passesFilters(item, type) {
      const query = document.getElementById('search-box').value.trim().toLowerCase();
      const fTier = document.getElementById('filter-tier').value;
      const fEnchant = document.getElementById('filter-enchant').value;
      const fQuality = document.getElementById('filter-quality').value;
      const fBuyCity = document.getElementById('filter-buy-city').value;
      const fSellCity = document.getElementById('filter-sell-city').value;

      if (query) {
        let searchTarget = '';
        if (type === 'flip' || type === 'blackmarket') {
          searchTarget = (item.item_name + ' ' + item.item_id + ' ' + item.buy_city + ' ' + (item.sell_city || '')).toLowerCase();
        } else if (type === 'crafting') {
          searchTarget = (item.item_name + ' ' + item.item_id + ' ' + item.craft_city + ' ' + item.sell_city + ' ' + item.craft_type).toLowerCase();
        } else {
          searchTarget = (item.item_name + ' ' + item.item_id + ' ' + item.city).toLowerCase();
        }
        if (!searchTarget.includes(query)) return false;
      }

      if (fTier !== 'all' && Number(item.tier) !== Number(fTier)) return false;
      if (fEnchant !== 'all' && Number(item.enchant) !== Number(fEnchant)) return false;
      if (type !== 'crafting' && fQuality !== 'all' && Number(item.quality || 1) !== Number(fQuality)) return false;

      const fAgeElem = document.getElementById('filter-age');
      if (fAgeElem && fAgeElem.value !== 'all') {
        const itemAge = (type === 'flip' || type === 'blackmarket') ? Number(item.data_age_minutes) : Number(item.age_minutes);
        if (!isNaN(itemAge) && itemAge > Number(fAgeElem.value)) return false;
      }

      if (type === 'flip') {
        if (fBuyCity !== 'all' && item.buy_city !== fBuyCity) return false;
        if (fSellCity !== 'all' && item.sell_city !== fSellCity) return false;

        // Quick filters for flips
        if (flipQuickFilter === 'top_picks' && !(item.deal_tier === 'S' || item.deal_tier === 'A')) return false;
        if (flipQuickFilter === 'instant' && item.exit_type !== 'instant_sell') return false;
        if (flipQuickFilter === 'safe' && (item.risk === 'high' || item.buy_city === 'Caerleon' || item.sell_city === 'Caerleon')) return false;
        if (flipQuickFilter === 'fresh' && Number(item.data_age_minutes) > 60) return false;
        if (flipQuickFilter === 'high_profit' && Number(item.profit_per_item) < 50000) return false;

      } else if (type === 'blackmarket') {
        if (fBuyCity !== 'all' && item.buy_city !== fBuyCity) return false;

        // Quick filters for Black Market
        if (bmQuickFilter === 'just_arrived' && Number(item.data_age_minutes) > 15) return false;
        if (bmQuickFilter === 'instant' && item.exit_type !== 'instant_sell') return false;
        if (bmQuickFilter === 'weapons' && !item.item_id.includes('MAIN_') && !item.item_id.includes('2H_')) return false;
        if (bmQuickFilter === 'armors' && !item.item_id.includes('ARMOR_') && !item.item_id.includes('HEAD_') && !item.item_id.includes('SHOES_')) return false;
        if (bmQuickFilter === 'accessories' && !item.item_id.includes('BAG') && !item.item_id.includes('CAPE') && !item.item_id.includes('OFF_')) return false;
        if (bmQuickFilter === 'high_profit' && Number(item.profit_per_item) < 100000) return false;

      } else if (type === 'crafting') {
        if (fBuyCity !== 'all' && item.craft_city !== fBuyCity) return false;
        if (fSellCity !== 'all' && item.sell_city !== fSellCity) return false;

        // Category filter
        if (craftCategoryFilter !== 'all' && item.craft_type !== craftCategoryFilter) return false;

      } else {
        if (fBuyCity !== 'all' && item.city !== fBuyCity) return false;
      }

      return true;
    }

    function renderFlips() {
      const tbody = document.getElementById('flips-body');
      let filtered = flipsData.filter(f => passesFilters(f, 'flip'));

      // Sort
      filtered.sort((a, b) => {
        let va = a[sortKeyFlips];
        let vb = b[sortKeyFlips];
        if (typeof va === 'string') va = va.toLowerCase();
        if (typeof vb === 'string') vb = vb.toLowerCase();
        if (va < vb) return sortAscFlips ? -1 : 1;
        if (va > vb) return sortAscFlips ? 1 : -1;
        return 0;
      });

      const totalItems = filtered.length;
      const totalPages = Math.max(1, Math.ceil(totalItems / pageSize));
      if (currentPage > totalPages) currentPage = totalPages;

      const startIndex = (currentPage - 1) * pageSize;
      const pageSlice = filtered.slice(startIndex, startIndex + pageSize);

      updatePagination(totalItems, startIndex, pageSlice.length, totalPages);

      if (pageSlice.length === 0) {
        tbody.innerHTML = '<tr><td colspan="14" style="text-align:center; padding: 32px; color: var(--text-muted);">' +
          'No flips match the selected filters.<br><small style="margin-top:8px; display:inline-block; color:#64748b;">(Try clicking "Reset Filters" or choose "All Deals")</small>' +
          '</td></tr>';
        return;
      }

      tbody.innerHTML = pageSlice.map(f => {
        const risk = (f.risk || 'low').toLowerCase();
        const riskClass = 'risk-' + risk;
        const isOrder = (f.exit_type === 'sell_order');
        const exitBadge = isOrder 
          ? '<span class="badge badge-order">Sell Order</span>' 
          : '<span class="badge badge-instant">Instant Sell</span>';
        
        const volDisplay = (f.avg_daily_volume !== null && f.avg_daily_volume !== undefined)
          ? Math.round(f.avg_daily_volume).toLocaleString() 
          : '<span title="Low or unknown volume" style="color:var(--yellow)">n/a ⚠</span>';
        
        const tierBadge = f.tier ? `<span class="badge badge-tier">${f.tier}.${f.enchant}</span>` : '';
        const qBadge = f.quality ? `<span class="badge badge-q">Q${f.quality}</span>` : '';

        // Deal Rating Badge
        const dealClass = 'deal-' + (f.deal_tier || 'c').toLowerCase();
        const dealBadge = `<span class="badge ${dealClass}">${f.deal_label || 'DEAL'}</span>`;

        const buyP = Number(f.buy_price) || 0;
        const sellP = Number(f.sell_price) || 0;
        const profit = Number(f.profit_per_item) || 0;
        const margin = Number(f.margin_pct) || 0;
        const totalP = Number(f.total_profit) || 0;
        const age = Math.round(Number(f.data_age_minutes) || 0);
        const ageClass = (age <= 20) ? 'class="fresh-age"' : '';

        return `
          <tr>
            <td>${dealBadge}</td>
            <td>${renderItemCell(f.item_id, f.item_name, f.quality)}</td>
            <td>${tierBadge} ${qBadge}</td>
            <td>${f.buy_city || '-'}</td>
            <td>${f.sell_city || '-'}</td>
            <td class="num">${buyP.toLocaleString()}</td>
            <td class="num">${sellP.toLocaleString()}</td>
            <td>${exitBadge}</td>
            <td class="num profit-val">+${Math.round(profit).toLocaleString()}</td>
            <td class="num margin-val">+${margin.toFixed(1)}%</td>
            <td class="num profit-val">+${Math.round(totalP).toLocaleString()}</td>
            <td class="num">${volDisplay}</td>
            <td class="num" ${ageClass}>${age}m ago</td>
            <td><span class="${riskClass}">${(f.risk || 'low').toUpperCase()}</span></td>
          </tr>
        `;
      }).join('');
    }

    function renderBlackMarket() {
      const tbody = document.getElementById('blackmarket-body');
      let filtered = blackmarketData.filter(f => passesFilters(f, 'blackmarket'));

      // Sort
      filtered.sort((a, b) => {
        let va = a[sortKeyBm];
        let vb = b[sortKeyBm];
        if (typeof va === 'string') va = va.toLowerCase();
        if (typeof vb === 'string') vb = vb.toLowerCase();
        if (va < vb) return sortAscBm ? -1 : 1;
        if (va > vb) return sortAscBm ? 1 : -1;
        return 0;
      });

      const totalItems = filtered.length;
      const totalPages = Math.max(1, Math.ceil(totalItems / pageSize));
      if (currentPage > totalPages) currentPage = totalPages;

      const startIndex = (currentPage - 1) * pageSize;
      const pageSlice = filtered.slice(startIndex, startIndex + pageSize);

      updatePagination(totalItems, startIndex, pageSlice.length, totalPages);

      if (pageSlice.length === 0) {
        tbody.innerHTML = '<tr><td colspan="13" style="text-align:center; padding: 32px; color: var(--text-muted);">' +
          'No Black Market deals match the selected filters.<br><small style="margin-top:8px; display:inline-block; color:#64748b;">(Try clicking "All BM Deals" or selecting "Any Age")</small>' +
          '</td></tr>';
        return;
      }

      tbody.innerHTML = pageSlice.map(f => {
        const isOrder = (f.exit_type === 'sell_order');
        const exitBadge = isOrder 
          ? '<span class="badge badge-order" title="Sell Order listed on Black Market">Sell Order</span>' 
          : '<span class="badge badge-instant" title="Instant Payout: Sold directly into Black Market Buy Order">Instant Sell ⚡</span>';
        
        const volDisplay = (f.avg_daily_volume !== null && f.avg_daily_volume !== undefined)
          ? Math.round(f.avg_daily_volume).toLocaleString() 
          : '<span title="Low or unknown volume" style="color:var(--yellow)">n/a ⚠</span>';
        
        const tierBadge = f.tier ? `<span class="badge badge-tier">${f.tier}.${f.enchant}</span>` : '';
        const qNames = ['', 'Normal', 'Good', 'Outstanding', 'Excellent', 'Masterpiece'];
        const qLabel = qNames[f.quality] || `Q${f.quality}`;
        const qBadge = f.quality ? `<span class="badge badge-q">${qLabel}</span>` : '';

        const age = Math.round(Number(f.data_age_minutes) || 0);
        const ageClass = (age <= 20) ? 'class="fresh-age"' : '';

        let newArrivalBadge = '';
        if (f.is_new) {
          newArrivalBadge = '<span class="badge-bm-new" title="New Black Market buy order just fetched!">⚡ JUST IN</span>';
        } else if (age <= 15) {
          newArrivalBadge = '<span class="badge" style="background:#064e3b; color:#34d399; font-weight:700;">🟢 RECENT SCAN</span>';
        }

        // Deal Rating Badge
        const dealClass = 'deal-' + (f.deal_tier || 'c').toLowerCase();
        const dealBadge = `<span class="badge ${dealClass}">${f.deal_label || 'DEAL'}</span>`;

        const buyP = Number(f.buy_price) || 0;
        const sellP = Number(f.sell_price) || 0;
        const profit = Number(f.profit_per_item) || 0;
        const margin = Number(f.margin_pct) || 0;
        const totalP = Number(f.total_profit) || 0;

        const safeItemName = (f.item_name || f.item_id).replace(/'/g, "\\'");

        return `
          <tr>
            <td>${dealBadge}</td>
            <td>${renderItemCell(f.item_id, f.item_name, f.quality)}</td>
            <td>${tierBadge} ${qBadge} ${newArrivalBadge}</td>
            <td><strong>${f.buy_city || '-'}</strong></td>
            <td class="num">${buyP.toLocaleString()}</td>
            <td class="num" style="color: #60a5fa; font-weight: 600;">${sellP.toLocaleString()}</td>
            <td>${exitBadge}</td>
            <td class="num profit-val">+${Math.round(profit).toLocaleString()}</td>
            <td class="num margin-val">+${margin.toFixed(1)}%</td>
            <td class="num profit-val">+${Math.round(totalP).toLocaleString()}</td>
            <td class="num">${volDisplay}</td>
            <td class="num" ${ageClass}>${age}m ago</td>
            <td>
              <button class="btn btn-secondary" style="padding: 3px 8px; font-size: 0.75rem;" onclick="copyItemName('${safeItemName}', this)" title="Copy in-game search name">
                📋 Copy
              </button>
            </td>
          </tr>
        `;
      }).join('');
    }

    function renderCrafting() {
      const tbody = document.getElementById('crafting-body');
      const source = isFocusMode ? focusCraftingData : craftingData;
      let filtered = source.filter(c => passesFilters(c, 'crafting'));

      filtered.sort((a, b) => {
        let va = a[sortKeyCrafting];
        let vb = b[sortKeyCrafting];
        if (typeof va === 'string') va = va.toLowerCase();
        if (typeof vb === 'string') vb = vb.toLowerCase();
        if (va < vb) return sortAscCrafting ? -1 : 1;
        if (va > vb) return sortAscCrafting ? 1 : -1;
        return 0;
      });

      const totalItems = filtered.length;
      const totalPages = Math.max(1, Math.ceil(totalItems / pageSize));
      if (currentPage > totalPages) currentPage = totalPages;

      const startIndex = (currentPage - 1) * pageSize;
      const pageSlice = filtered.slice(startIndex, startIndex + pageSize);

      updatePagination(totalItems, startIndex, pageSlice.length, totalPages);

      if (pageSlice.length === 0) {
        tbody.innerHTML = '<tr><td colspan="9" style="text-align:center; padding: 32px; color: var(--text-muted);">' +
          'No crafting recipes match your current filters.<br><small style="margin-top:8px; display:inline-block; color:#64748b;">(Try selecting "All Recipes" or clearing search)</small>' +
          '</td></tr>';
        return;
      }

      tbody.innerHTML = pageSlice.map(c => {
        const tierBadge = c.tier ? `<span class="badge badge-tier">${c.tier}.${c.enchant}</span>` : '';
        const bonusTag = c.city_bonus ? '<span class="badge badge-bonus">Bonus City</span>' : '';
        const rrrPct = (c.resource_return_rate * 100).toFixed(1);
        const profit = Number(c.profit_per_item) || 0;
        const margin = Number(c.margin_pct) || 0;

        return `
          <tr>
            <td>${renderItemCell(c.item_id, c.item_name, 1)}</td>
            <td>${tierBadge} <span class="badge badge-q">${c.craft_type}</span></td>
            <td><strong>${c.craft_city}</strong> ${bonusTag}</td>
            <td>
              <div style="font-size:0.82rem; color:#cbd5e1;">${c.ingredients_desc}</div>
              <div style="font-size:0.74rem; color:#64748b;">Raw Mat: ${Math.round(c.material_cost).toLocaleString()} | RRR: ${rrrPct}%</div>
            </td>
            <td class="num">${Math.round(c.effective_cost).toLocaleString()}</td>
            <td><strong>${c.sell_city}</strong></td>
            <td class="num">${Math.round(c.sell_price).toLocaleString()}</td>
            <td class="num profit-val">+${Math.round(profit).toLocaleString()}</td>
            <td class="num margin-val">+${margin.toFixed(1)}%</td>
          </tr>
        `;
      }).join('');
    }

    function renderPrices() {
      const tbody = document.getElementById('prices-body');
      let filtered = pricesData.filter(p => passesFilters(p, 'price'));

      const totalItems = filtered.length;
      const totalPages = Math.max(1, Math.ceil(totalItems / pageSize));
      if (currentPage > totalPages) currentPage = totalPages;

      const startIndex = (currentPage - 1) * pageSize;
      const pageSlice = filtered.slice(startIndex, startIndex + pageSize);

      updatePagination(totalItems, startIndex, pageSlice.length, totalPages);

      if (pageSlice.length === 0) {
        tbody.innerHTML = '<tr><td colspan="7" style="text-align:center; padding: 32px; color: var(--text-muted);">' +
          'No price records found. Try browsing items in-game with the Albion Data Client running.' +
          '</td></tr>';
        return;
      }

      tbody.innerHTML = pageSlice.map(p => {
        const tierBadge = p.tier ? `<span class="badge badge-tier">${p.tier}.${p.enchant}</span>` : '';
        const qBadge = p.quality ? `<span class="badge badge-q">Q${p.quality}</span>` : '';
        const age = Math.round(Number(p.age_minutes) || 0);
        const ageClass = (age <= 15) ? 'class="fresh-age"' : '';
        const statusBadge = (age <= 30) 
          ? '<span class="badge" style="background:#064e3b; color:#34d399;">Active</span>' 
          : '<span class="badge" style="background:#1e293b; color:#94a3b8;">Normal</span>';

        return `
          <tr>
            <td>${renderItemCell(p.item_id, p.item_name, p.quality)}</td>
            <td>${tierBadge} ${qBadge}</td>
            <td>${p.city}</td>
            <td class="num">${p.sell_price_min > 0 ? p.sell_price_min.toLocaleString() : '<span style="color:#64748b;">-</span>'}</td>
            <td class="num">${p.buy_price_max > 0 ? p.buy_price_max.toLocaleString() : '<span style="color:#64748b;">-</span>'}</td>
            <td class="num" ${ageClass}>${age}m ago</td>
            <td>${statusBadge}</td>
          </tr>
        `;
      }).join('');
    }

    function renderCurrentView() {
      if (currentTab === 'flips') renderFlips();
      else if (currentTab === 'blackmarket') renderBlackMarket();
      else if (currentTab === 'crafting') renderCrafting();
      else renderPrices();
    }

    function updatePagination(total, start, count, totalPages) {
      const summary = document.getElementById('page-summary');
      const current = document.getElementById('page-current');
      const btnPrev = document.getElementById('btn-prev');
      const btnNext = document.getElementById('btn-next');

      if (total === 0) {
        summary.textContent = 'Showing 0 items';
        current.textContent = 'Page 1';
        btnPrev.disabled = true;
        btnNext.disabled = true;
        return;
      }

      summary.textContent = `Showing ${start + 1}–${start + count} of ${total.toLocaleString()} items`;
      current.textContent = `Page ${currentPage} of ${totalPages}`;
      btnPrev.disabled = (currentPage <= 1);
      btnNext.disabled = (currentPage >= totalPages);
    }

    function sortFlips(key, isNum = false) {
      if (sortKeyFlips === key) {
        sortAscFlips = !sortAscFlips;
      } else {
        sortKeyFlips = key;
        sortAscFlips = !isNum;
      }
      renderFlips();
    }

    function sortCrafting(key, isNum = false) {
      if (sortKeyCrafting === key) {
        sortAscCrafting = !sortAscCrafting;
      } else {
        sortKeyCrafting = key;
        sortAscCrafting = !isNum;
      }
      renderCrafting();
    }

    let prevBmNewCount = -1;

    async function fetchFlips() {
      try {
        const res = await fetch('/api/flips');
        if (!res.ok) return;
        const data = await res.json();
        
        document.getElementById('server-badge').textContent = (data.server || 'Europe').toUpperCase() + ' LIVE';
        if (data.last_refresh) {
          document.getElementById('last-refresh').textContent = data.last_refresh;
        }

        flipsData = data.flips || [];
        blackmarketData = data.blackmarket || [];
        craftingData = data.crafting || [];
        focusCraftingData = data.focus_crafting || [];
        pricesData = data.recent_prices || [];

        // Update Black Market tab badge
        const bmBadge = document.getElementById('bm-badge');
        const bmNewCount = data.bm_new_count || 0;
        if (bmBadge) {
          if (bmNewCount > 0) {
            bmBadge.style.display = 'inline-flex';
            bmBadge.innerHTML = `🟢 ${bmNewCount} New`;
          } else {
            bmBadge.style.display = 'none';
          }
        }

        // Update Black Market Live Feed Card
        const bmSummary = document.getElementById('bm-arrival-summary');
        const bmChips = document.getElementById('bm-recent-chips');
        if (bmSummary) {
          if (bmNewCount > 0) {
            bmSummary.innerHTML = `<strong>Black Market Live Feed:</strong> 🟢 <strong>${bmNewCount} items</strong> newly updated in the Black Market!`;
          } else {
            bmSummary.innerHTML = `<strong>Black Market Live Feed:</strong> All current buy orders synced (monitoring every 20s).`;
          }
        }
        if (bmChips && data.bm_recent_arrivals) {
          bmChips.innerHTML = data.bm_recent_arrivals.map(a => {
            const icon = a.item_id ? `<img src="https://render.albiononline.com/v1/item/${encodeURIComponent(a.item_id)}.png?quality=${a.quality || 1}" style="width:16px;height:16px;object-fit:contain;vertical-align:middle;margin-right:4px;" onerror="this.style.display='none';">` : '';
            return `<span class="badge" style="background:#1e293b; color:#a7f3d0; border:1px solid #059669; font-size:0.75rem; display:inline-flex; align-items:center;" title="${a.buy_city} -> Black Market">${icon}+${Math.round(a.profit).toLocaleString()} (${a.item_name} ${a.age}m ago)</span>`;
          }).join('');
        }

        // Show soft toast notification in dashboard when new BM items come in
        if (prevBmNewCount !== -1 && bmNewCount > prevBmNewCount && currentTab !== 'blackmarket') {
          showToast(`🏴‍☠️ <strong>Black Market:</strong> ${bmNewCount} new item(s) freshly fetched from market scans!`);
        }
        prevBmNewCount = bmNewCount;

        const allFlipsForAlerts = [...flipsData, ...blackmarketData];
        checkBrowserNotifications(allFlipsForAlerts, data.last_refresh);
        renderCurrentView();

      } catch (err) {
        console.error('Error fetching market data:', err);
      }
    }

    let browserNotifEnabled = false;
    const notifiedFlipsSet = new Set();

    function toggleBrowserNotifications() {
      if (!("Notification" in window)) {
        alert("This browser does not support desktop notifications.");
        return;
      }
      if (Notification.permission === "granted") {
        browserNotifEnabled = !browserNotifEnabled;
        localStorage.setItem('albion_alerts', browserNotifEnabled ? '1' : '0');
        updateNotifButton();
        if (browserNotifEnabled) {
          lastNotifiedRefresh = '';
          checkBrowserNotifications(flipsData, document.getElementById('last-refresh').textContent);
        }
      } else if (Notification.permission !== "denied") {
        Notification.requestPermission().then(permission => {
          if (permission === "granted") {
            browserNotifEnabled = true;
            localStorage.setItem('albion_alerts', '1');
            updateNotifButton();
            lastNotifiedRefresh = '';
            checkBrowserNotifications(flipsData, document.getElementById('last-refresh').textContent);
          }
        });
      } else {
        alert("Desktop notifications are blocked in your browser settings. Please allow notifications in your browser URL bar.");
      }
    }


    function updateNotifButton() {
      const btn = document.getElementById('btn-browser-notif');
      if (browserNotifEnabled) {
        btn.innerHTML = '🔔 Alerts: ON';
        btn.style.borderColor = '#10b981';
        btn.style.color = '#34d399';
      } else {
        btn.innerHTML = '🔔 Alerts: OFF';
        btn.style.borderColor = '';
        btn.style.color = '';
      }
    }

    let lastNotifiedRefresh = '';

    function checkBrowserNotifications(flips, lastRefresh) {
      if (!browserNotifEnabled || !flips || flips.length === 0) return;
      if (lastRefresh && lastRefresh === lastNotifiedRefresh) return;
      lastNotifiedRefresh = lastRefresh;

      const topFlip = flips[0];
      if (!topFlip) return;

      const name = topFlip.item_name || topFlip.item_id;
      const tierStr = topFlip.tier ? `${topFlip.tier}.${topFlip.enchant}` : '';
      const profitStr = Math.round(Number(topFlip.total_profit)).toLocaleString();

      new Notification(`🏆 Top Flip: ${name} ${tierStr}`, {
        body: `${topFlip.buy_city} ➜ ${topFlip.sell_city} | +${profitStr} silver (+${Number(topFlip.margin_pct).toFixed(1)}% ROI)`,
        tag: 'top-albion-flip',
        renotify: true,
      });
    }


    async function triggerManualRefresh() {
      const btn = document.getElementById('btn-refresh');
      btn.disabled = true;
      btn.innerHTML = '<span>↻</span> Refreshing...';

      try {
        const res = await fetch('/api/refresh', { method: 'POST' });
        if (res.ok) {
          const data = await res.json();
          flipsData = data.flips || [];
          craftingData = data.crafting || [];
          focusCraftingData = data.focus_crafting || [];
          pricesData = data.recent_prices || [];
          if (data.last_refresh) {
            document.getElementById('last-refresh').textContent = data.last_refresh;
          }
          checkBrowserNotifications(flipsData, data.last_refresh);
          renderCurrentView();

        }
      } catch (err) {
        console.error('Error triggering refresh:', err);
      } finally {
        btn.disabled = false;
        btn.innerHTML = '<span>↻</span> Fetch Live Data';
      }
    }

    // Restore alerts state if previously enabled
    if (localStorage.getItem('albion_alerts') === '1' && typeof Notification !== 'undefined' && Notification.permission === 'granted') {
      browserNotifEnabled = true;
      updateNotifButton();
    }

    function showToast(msg) {
      const container = document.getElementById('toast-container');
      if (!container) return;
      const t = document.createElement('div');
      t.className = 'toast-msg';
      t.innerHTML = msg;
      container.appendChild(t);
      setTimeout(() => {
        t.style.opacity = '0';
        t.style.transition = 'opacity 0.4s';
        setTimeout(() => t.remove(), 400);
      }, 4500);
    }

    // Initial load + poll every 5 seconds
    fetchFlips();
    setInterval(fetchFlips, 5000);
  </script>

  <div id="toast-container" class="toast-container"></div>
</body>
</html>

"""


class FlipDataStore:
    """Thread-safe storage for flip opportunities, crafting data, and recent prices."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.server: str = "europe"
        self.last_refresh: str = ""
        self.flips: list[dict[str, Any]] = []
        self.blackmarket: list[dict[str, Any]] = []
        self.bm_new_count: int = 0
        self.bm_recent_arrivals: list[dict[str, Any]] = []
        self._previous_bm_keys: set[tuple[str, int, int]] = set()
        self.crafting: list[dict[str, Any]] = []
        self.focus_crafting: list[dict[str, Any]] = []
        self.recent_prices: list[dict[str, Any]] = []
        self.refresh_callback: Callable[[], Any] | None = None

    def set_refresh_callback(self, callback: Callable[[], Any]) -> None:
        self.refresh_callback = callback

    def trigger_refresh(self) -> None:
        if self.refresh_callback:
            try:
                self.refresh_callback()
            except Exception as e:
                logger.error("Error executing refresh callback: %s", e)

    def update(
        self,
        server: str,
        last_refresh: str,
        opportunities: list[FlipOpportunity],
        prices: Sequence[PriceRecord] | None = None,
        now: datetime | None = None,
        config: AppConfig | None = None,
    ) -> None:
        if now is None:
            now = datetime.now(timezone.utc)
        elif now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)

        if config is None:
            config = AppConfig()

        serialized_flips = []
        serialized_blackmarket = []
        current_bm_keys: set[tuple[str, int, int]] = set()
        new_bm_count = 0
        recent_bm_arrivals: list[dict[str, Any]] = []

        for o in opportunities:
            tier = parse_tier(o.item_id)
            enchant = parse_enchant(o.item_id)
            human_name = get_human_name(o.item_id)

            score, tier_letter, label = calculate_deal_score(
                profit_per_item=o.profit_per_item,
                margin_pct=o.margin_pct,
                data_age_minutes=o.data_age_minutes,
                avg_daily_volume=o.avg_daily_volume,
                risk=o.risk,
                exit_type=o.exit_type,
            )

            is_new = False
            is_fresh = (o.data_age_minutes <= 15)
            if o.sell_city == "Black Market":
                bm_key = (o.item_id, o.quality, o.sell_price)
                current_bm_keys.add(bm_key)
                if self._previous_bm_keys and bm_key not in self._previous_bm_keys and o.data_age_minutes <= 60:
                    is_new = True
                    new_bm_count += 1
                elif not self._previous_bm_keys and is_fresh:
                    is_new = True
                    new_bm_count += 1

                if is_fresh or is_new:
                    recent_bm_arrivals.append({
                        "item_id": o.item_id,
                        "item_name": human_name,
                        "tier": f"{tier}.{enchant}" if tier else "",
                        "quality": o.quality,
                        "profit": o.profit_per_item,
                        "buy_city": o.buy_city,
                        "age": round(o.data_age_minutes),
                    })

            entry = {
                "item_id": o.item_id,
                "item_name": human_name,
                "tier": tier,
                "enchant": enchant,
                "quality": o.quality,
                "buy_city": o.buy_city,
                "sell_city": o.sell_city,
                "buy_price": o.buy_price,
                "sell_price": o.sell_price,
                "exit_type": o.exit_type.value,
                "profit_per_item": o.profit_per_item,
                "margin_pct": o.margin_pct,
                "total_profit": o.total_profit,
                "avg_daily_volume": o.avg_daily_volume,
                "data_age_minutes": o.data_age_minutes,
                "risk": o.risk,
                "score": score,
                "deal_tier": tier_letter,
                "deal_label": label,
                "history_missing": o.history_missing,
                "is_new": is_new,
                "is_fresh": is_fresh,
            }

            if o.sell_city == "Black Market":
                serialized_blackmarket.append(entry)
            else:
                serialized_flips.append(entry)

        if current_bm_keys:
            self._previous_bm_keys = current_bm_keys

        # Sort primarily by profit per item
        serialized_flips.sort(key=lambda x: x["profit_per_item"], reverse=True)
        serialized_flips = serialized_flips[:2500]

        serialized_blackmarket.sort(key=lambda x: x["profit_per_item"], reverse=True)
        serialized_blackmarket = serialized_blackmarket[:2500]


        serialized_prices = []
        if prices:
            for p in prices:
                best_date = None
                if p.sell_price_min > 0 and not p.sell_price_min_date.startswith("0001"):
                    try:
                        best_date = datetime.fromisoformat(p.sell_price_min_date).replace(tzinfo=timezone.utc)
                    except Exception:
                        pass
                if p.buy_price_max > 0 and not p.buy_price_max_date.startswith("0001"):
                    try:
                        b_date = datetime.fromisoformat(p.buy_price_max_date).replace(tzinfo=timezone.utc)
                        if best_date is None or b_date > best_date:
                            best_date = b_date
                    except Exception:
                        pass

                if best_date is not None:
                    age_minutes = max(0.0, (now - best_date).total_seconds() / 60.0)
                    tier = parse_tier(p.item_id)
                    enchant = parse_enchant(p.item_id)
                    human_name = get_human_name(p.item_id)
                    serialized_prices.append({
                        "item_id": p.item_id,
                        "item_name": human_name,
                        "tier": tier,
                        "enchant": enchant,
                        "quality": p.quality,
                        "city": p.city,
                        "sell_price_min": p.sell_price_min,
                        "buy_price_max": p.buy_price_max,
                        "age_minutes": age_minutes,
                    })

            # Sort prices so freshest appear at the top, cap at top 500
            serialized_prices.sort(key=lambda x: x["age_minutes"])
            serialized_prices = serialized_prices[:500]

        # Crafting calculations
        serialized_crafting: list[dict[str, Any]] = []
        serialized_focus_crafting: list[dict[str, Any]] = []
        if prices:
            craft_ops = analyze_crafting(prices, config, focus=False, item_names=ITEM_NAMES)
            for c in craft_ops[:500]:
                serialized_crafting.append({
                    "item_id": c.item_id,
                    "item_name": c.item_name,
                    "craft_type": c.craft_type,
                    "craft_city": c.craft_city,
                    "sell_city": c.sell_city,
                    "material_cost": c.material_cost,
                    "effective_cost": c.effective_cost,
                    "sell_price": c.sell_price,
                    "profit_per_item": c.profit_per_item,
                    "margin_pct": c.margin_pct,
                    "resource_return_rate": c.resource_return_rate,
                    "ingredients_desc": c.ingredients_desc,
                    "city_bonus": c.city_bonus,
                    "tier": c.tier,
                    "enchant": c.enchant,
                })

            focus_craft_ops = analyze_crafting(prices, config, focus=True, item_names=ITEM_NAMES)
            for c in focus_craft_ops[:150]:
                serialized_focus_crafting.append({
                    "item_id": c.item_id,
                    "item_name": c.item_name,
                    "craft_type": c.craft_type,
                    "craft_city": c.craft_city,
                    "sell_city": c.sell_city,
                    "material_cost": c.material_cost,
                    "effective_cost": c.effective_cost,
                    "sell_price": c.sell_price,
                    "profit_per_item": c.profit_per_item,
                    "margin_pct": c.margin_pct,
                    "resource_return_rate": c.resource_return_rate,
                    "ingredients_desc": c.ingredients_desc,
                    "city_bonus": c.city_bonus,
                    "tier": c.tier,
                    "enchant": c.enchant,
                })

        with self._lock:
            self.server = server
            self.last_refresh = last_refresh
            self.flips = serialized_flips
            self.blackmarket = serialized_blackmarket
            self.bm_new_count = new_bm_count if new_bm_count > 0 else len([x for x in serialized_blackmarket if x.get("is_fresh")])
            self.bm_recent_arrivals = sorted(recent_bm_arrivals, key=lambda x: x["age"])[:6]
            self.crafting = serialized_crafting
            self.focus_crafting = serialized_focus_crafting
            self.recent_prices = serialized_prices

    def get_data(self) -> dict[str, Any]:
        with self._lock:
            return {
                "server": self.server,
                "last_refresh": self.last_refresh,
                "flips": list(self.flips),
                "blackmarket": list(self.blackmarket),
                "bm_new_count": self.bm_new_count,
                "bm_recent_arrivals": list(self.bm_recent_arrivals),
                "crafting": list(self.crafting),
                "focus_crafting": list(self.focus_crafting),
                "recent_prices": list(self.recent_prices),
            }


class DualStackHTTPServer(HTTPServer):
    """Dual stack server listening on IPv6 and IPv4 simultaneously."""
    address_family = socket.AF_INET6

    def server_bind(self) -> None:
        try:
            self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        except (AttributeError, OSError):
            pass
        super().server_bind()


class FlipRequestHandler(BaseHTTPRequestHandler):
    store: FlipDataStore

    def log_message(self, format: str, *args: Any) -> None:
        logger.debug("%s - - [%s] %s", self.address_string(), self.log_date_time_string(), format % args)

    def do_HEAD(self) -> None:
        clean_path = self.path.split("?")[0]
        if clean_path in ("/", "/index.html"):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
        elif clean_path in ("/api/flips", "/api/refresh"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
        elif clean_path == "/favicon.ico":
            self.send_response(200)
            self.send_header("Content-Type", "image/x-icon")
            self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self) -> None:
        clean_path = self.path.split("?")[0]
        if clean_path == "/api/refresh":
            self.store.trigger_refresh()
            self._send_json(self.store.get_data())
        else:
            self.send_response(404)
            self.end_headers()

    def do_GET(self) -> None:
        clean_path = self.path.split("?")[0]
        if clean_path in ("/", "/index.html"):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.send_header("Access-Control-Allow-Origin", "*")
            content = HTML_TEMPLATE.encode("utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
        elif clean_path == "/favicon.ico":
            icon_path = Path("assets/icon.ico")
            if not icon_path.is_file():
                icon_path = Path("assets/icon.png")
            if icon_path.is_file():
                try:
                    with open(icon_path, "rb") as f:
                        data = f.read()
                    self.send_response(200)
                    self.send_header("Content-Type", "image/x-icon" if icon_path.suffix == ".ico" else "image/png")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
                except Exception:
                    pass
            self.send_response(404)
            self.end_headers()
        elif clean_path == "/api/flips":
            self._send_json(self.store.get_data())
        elif clean_path == "/api/refresh":
            self.store.trigger_refresh()
            self._send_json(self.store.get_data())
        else:
            self.send_response(404)
            self.end_headers()

    def _send_json(self, data: dict[str, Any]) -> None:
        content = json.dumps(data).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)


def start_web_server(store: FlipDataStore, port: int = 8765) -> HTTPServer:
    """Starts local dual-stack web server accessible via localhost or 127.0.0.1."""
    class BoundHandler(FlipRequestHandler):
        pass

    BoundHandler.store = store
    try:
        server: HTTPServer = DualStackHTTPServer(("::", port), BoundHandler)
    except Exception:
        server = HTTPServer(("0.0.0.0", port), BoundHandler)

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    logger.info("Local web interface running at http://localhost:%d and http://127.0.0.1:%d", port, port)
    return server
