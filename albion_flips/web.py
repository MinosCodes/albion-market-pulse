from __future__ import annotations

import json
import logging
import socket
import threading
import time
from dataclasses import replace
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from socketserver import ThreadingMixIn
from typing import Any, Callable, Sequence

from albion_flips.analyzer import MAX_QUALITY_ROLLS, analyze_crafting, analyze_enchanting, analyze_flips, calculate_age_minutes, quality_chances, calculate_deal_score, compute_daily_volume, deduplicate_prices
from albion_flips.config import AppConfig
from albion_flips.models import FlipOpportunity, HistoryPoint, HistoryRecord, PriceRecord
from albion_flips.names import human_name as get_human_name, load_item_names
from albion_flips.paths import CACHE_DIR, asset_file, data_file
from albion_flips.weights import MOUNT_CAPACITIES, get_item_weight

logger = logging.getLogger(__name__)

ITEM_NAMES: dict[str, str] = load_item_names()


def _load_albion_ids() -> dict[int, str]:
    """Numeric game item index -> unique name, from ao-bin-dumps formatted/items.txt.

    The data client identifies items in market-history uploads only by this index
    (AlbionId); the AODP backend resolves it the same way.
    """
    ids: dict[int, str] = {}
    try:
        with open(data_file("items.txt"), "r", encoding="utf-8") as f:
            for line in f:
                head, _, rest = line.partition(":")
                if head.strip().isdigit():
                    ids[int(head)] = rest.split(":")[0].strip()
    except OSError as e:
        logger.warning("Could not load items.txt (needed for live sales history): %s", e)
    return ids


ALBION_ID_TO_ITEM: dict[int, str] = _load_albion_ids()
# .NET DateTime ticks at the Unix epoch; market history timestamps use ticks.
_TICKS_AT_EPOCH = 621355968000000000


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
    .ing-list { display: flex; flex-wrap: wrap; gap: 4px; margin-bottom: 3px; }
    .ing-chip { display: inline-flex; align-items: center; gap: 3px; padding: 1px 6px 1px 2px; background: rgba(30, 41, 59, 0.7); border: 1px solid rgba(148, 163, 184, 0.25); border-radius: 6px; font-size: 0.76rem; color: #e2e8f0; white-space: nowrap; }
    .ing-chip img { width: 26px; height: 26px; object-fit: contain; }
    .ing-chip.ing-live { border-color: #38bdf8; box-shadow: 0 0 4px rgba(56, 189, 248, 0.45); }
    .ing-chip.ing-noreturn { border-style: dashed; }
    .ing-chip small { color: #94a3b8; }
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
    /* Top Progress Bar for API Calls */
    #top-progress-bar {
      position: fixed;
      top: 0;
      left: 0;
      height: 3px;
      width: 0%;
      background: linear-gradient(90deg, #f59e0b, #10b981);
      box-shadow: 0 0 10px rgba(16, 185, 129, 0.7);
      z-index: 9999;
      opacity: 0;
      transition: width 0.35s ease, opacity 0.4s ease;
      pointer-events: none;
    }

    /* Sync Indicator Badge in Header */
    .sync-indicator {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      font-size: 0.78rem;
      padding: 4px 11px;
      border-radius: 999px;
      font-weight: 600;
      transition: all 0.3s ease;
      background: #1a1f30;
      color: #94a3b8;
      border: 1px solid var(--border);
    }
    .sync-indicator.syncing {
      background: rgba(245, 158, 11, 0.16);
      color: #f59e0b;
      border-color: #f59e0b;
      box-shadow: 0 0 10px rgba(245, 158, 11, 0.35);
    }
    .sync-indicator.syncing .sync-dot {
      background: #f59e0b;
      animation: pulse-dot-anim 0.8s infinite alternate;
    }
    .sync-indicator.synced {
      background: rgba(16, 185, 129, 0.2);
      color: #34d399;
      border-color: #10b981;
      box-shadow: 0 0 14px rgba(16, 185, 129, 0.45);
    }
    .sync-indicator.synced .sync-dot {
      background: #10b981;
      box-shadow: 0 0 8px #10b981;
    }
    .sync-dot {
      width: 7px;
      height: 7px;
      border-radius: 50%;
      background: #64748b;
      display: inline-block;
      transition: background 0.3s ease, box-shadow 0.3s ease;
    }

    /* Refresh Button States & Animations */
    .btn-refreshing {
      background: #1e293b !important;
      color: #f59e0b !important;
      border-color: #f59e0b !important;
      cursor: not-allowed;
      box-shadow: 0 0 12px rgba(245, 158, 11, 0.35) !important;
    }
    .btn-refreshing .refresh-icon {
      display: inline-block;
      animation: spin-anim 0.9s linear infinite;
    }
    .btn-success-flash {
      background: #064e3b !important;
      color: #34d399 !important;
      border-color: #10b981 !important;
      box-shadow: 0 0 16px rgba(16, 185, 129, 0.6) !important;
      transition: all 0.3s ease;
    }
    @keyframes spin-anim {
      100% { transform: rotate(360deg); }
    }
    @keyframes pulse-dot-anim {
      from { transform: scale(0.85); opacity: 0.6; }
      to { transform: scale(1.25); opacity: 1; }
    }
    .flash-highlight {
      animation: text-flash 1.8s ease-out;
    }
    @keyframes text-flash {
      0% { color: #34d399; text-shadow: 0 0 10px #10b981; }
      100% { color: var(--text); text-shadow: none; }
    }

    /* Fulfill, Edit Price, Refresh Item & Action Buttons */
    .btn-fulfill {
      background: rgba(239, 68, 68, 0.15) !important;
      border: 1px solid rgba(239, 68, 68, 0.45) !important;
      color: #fca5a5 !important;
      border-radius: 4px;
      padding: 3px 8px;
      font-size: 0.73rem;
      cursor: pointer;
      transition: all 0.15s ease;
      white-space: nowrap;
    }
    .btn-fulfill:hover {
      background: rgba(239, 68, 68, 0.4) !important;
      border-color: #ef4444 !important;
      color: #ffffff !important;
      box-shadow: 0 0 8px rgba(239, 68, 68, 0.4);
    }
    .btn-edit-price {
      background: transparent;
      border: none;
      color: #94a3b8;
      cursor: pointer;
      padding: 1px 4px;
      font-size: 0.75rem;
      vertical-align: middle;
      transition: color 0.15s;
    }
    .btn-edit-price:hover {
      color: #38bdf8;
    }
    .btn-refresh-item {
      background: transparent;
      border: none;
      color: #64748b;
      cursor: pointer;
      padding: 1px 4px;
      font-size: 0.75rem;
      transition: transform 0.2s, color 0.2s;
    }
    .btn-refresh-item:hover {
      color: #10b981;
      transform: rotate(90deg);
    }
    .btn-refresh-item.spinning {
      display: inline-block;
      animation: spin-anim 0.6s linear infinite;
      color: #38bdf8;
    }
    tr.row-fade-out {
      opacity: 0 !important;
      transform: translateX(40px) !important;
      transition: all 0.3s ease !important;
    }

    /* Overrides Modal */
    .modal-overlay {
      position: fixed;
      top: 0;
      left: 0;
      right: 0;
      bottom: 0;
      background: rgba(0, 0, 0, 0.75);
      backdrop-filter: blur(4px);
      z-index: 9999;
      display: flex;
      align-items: center;
      justify-content: center;
    }
    .modal-box {
      background: #0f172a;
      border: 1px solid #334155;
      border-radius: 12px;
      padding: 24px;
      max-width: 600px;
      width: 90%;
      max-height: 80vh;
      overflow-y: auto;
    /* Star / Watchlist & 1-Click Copy */
    .star-btn {
      background: none;
      border: none;
      color: #64748b;
      font-size: 1.15rem;
      cursor: pointer;
      padding: 0 4px;
      line-height: 1;
      transition: color 0.15s, transform 0.15s;
    }
    .star-btn:hover {
      color: #fbbf24;
      transform: scale(1.25);
    }
    .star-btn.starred {
      color: #f59e0b;
      text-shadow: 0 0 8px rgba(245, 158, 11, 0.6);
    }
    .btn-copy-name {
      background: rgba(255, 255, 255, 0.06);
      border: 1px solid var(--border);
      color: var(--text-muted);
      border-radius: 4px;
      font-size: 0.72rem;
      padding: 1px 6px;
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      gap: 3px;
      transition: all 0.15s ease;
    }
    .btn-copy-name:hover {
      background: rgba(59, 130, 246, 0.25);
      border-color: var(--blue);
      color: #ffffff;
    }

    /* Mount Carry Capacity Toolbar */
    .mount-bar {
      display: flex;
      align-items: center;
      flex-wrap: wrap;
      gap: 10px;
      background: #141826;
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 8px 14px;
      margin-bottom: 12px;
      font-size: 0.85rem;
    }
    .mount-label {
      font-weight: 700;
      color: #fbbf24;
      display: inline-flex;
      align-items: center;
      gap: 5px;
    }
    .mount-info {
      color: var(--text-muted);
      margin-right: 6px;
    }

    /* Sniffer Pill */
    .sniffer-pill {
      background: rgba(16, 185, 129, 0.12);
      border: 1px solid rgba(16, 185, 129, 0.35);
      color: #34d399;
      padding: 4px 10px;
      border-radius: 999px;
      font-size: 0.76rem;
      font-weight: 600;
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      gap: 6px;
      transition: all 0.2s;
    }
    .sniffer-pill:hover {
      background: rgba(16, 185, 129, 0.25);
      box-shadow: 0 0 10px rgba(16, 185, 129, 0.4);
    }
    .badge-sniffer {
      background: #14532d;
      color: #86efac;
      border: 1px solid #22c55e;
      font-weight: 700;
      animation: pulse-green 1.8s infinite;
    }
    @keyframes pulse-green {
      0% { box-shadow: 0 0 0 0 rgba(34, 197, 94, 0.6); }
      70% { box-shadow: 0 0 0 6px rgba(34, 197, 94, 0); }
      100% { box-shadow: 0 0 0 0 rgba(34, 197, 94, 0); }
    }
    .batch-btn.active {
      background: #8b5cf6 !important;
      border-color: #a78bfa !important;
      color: #ffffff !important;
      font-weight: 700;
    }
    .mat-chip {
      background: #1e293b;
      border: 1px solid #334155;
      border-radius: 6px;
      padding: 6px 10px;
      display: flex;
      justify-content: space-between;
      align-items: center;
      transition: all 0.2s;
    }
    .mat-chip:hover {
      border-color: #8b5cf6;
      background: rgba(139, 92, 246, 0.15);
    }
    .mat-chip-name {
      font-weight: 600;
      color: #f1f5f9;
      font-size: 0.78rem;
    }
    .mat-chip-price {
      font-weight: 700;
      color: #38bdf8;
      font-size: 0.82rem;
    }
  </style>

</head>
<body>
  <div id="top-progress-bar"></div>
  <div id="zone-banner" style="display:none; margin: 0 0 12px; padding: 12px 18px; border-radius: 8px; background: rgba(245, 158, 11, 0.14); border: 1px solid #f59e0b; color: #fde68a; font-size: 0.9rem; line-height: 1.5;">
    ⚠️ <strong>The sniffer can't see your market scans yet.</strong> The Albion Data Client only learns which city you're in when you
    <strong>change zone</strong>, and until then it throws away every market you open. Change zone once in-game
    (for example walk out through the city gate and back, take a travel portal, or visit your island), then open the markets again.
    <span style="color:#fbbf24;">Tip: start this app before you log in to the game to avoid this.</span>
  </div>

  <div class="header-bar">
    <h1>
      <span style="display:inline-flex; align-items:center; gap:10px;"><img src="/logo.svg" alt="" width="34" height="34" style="display:block;">Albion Market & Crafting Analyzer</span>
    </h1>
    <div class="status-container">
      <div id="sync-indicator" class="sync-indicator" title="AODP Market API Sync Status">
        <span class="sync-dot"></span>
        <span id="sync-text">Live Sync</span>
      </div>
      <div class="status-badge">
        <span class="pulse-dot"></span>
        <span id="server-badge">Europe Live</span>
      </div>
      <div id="sniffer-pill" class="sniffer-pill" onclick="openSnifferModal()" title="Live Packet Sniffer Status (Click for Guide & Test)">
        <span>⚡ Sniffer: Connect (0s Ingest)</span>
        <span style="opacity: 0.7;">ℹ️</span>
      </div>
      <div class="status-text">
        Last Refresh: <span id="last-refresh" style="color: var(--text); font-weight: 600;">Connecting...</span>
      </div>
      <button id="btn-overrides" class="btn btn-secondary" style="display: none; border-color: #f59e0b; color: #fbbf24;" onclick="openOverridesManager()" title="Manage fulfilled orders & price overrides">
        🏷️ Overrides (<span id="override-count">0</span>)
      </button>
      <button id="btn-premium" class="btn btn-secondary" onclick="togglePremium()" title="Market tax taken from every sale: 4% with Premium, 8% without. All profits are shown after this tax.">
        💎 Premium Tax 4%
      </button>
      <button id="btn-browser-notif" class="btn btn-secondary" onclick="toggleBrowserNotifications()">
        🔔 Alerts: OFF
      </button>
      <button id="btn-refresh" class="btn" onclick="triggerManualRefresh()">
        <span class="refresh-icon">↻</span> <span id="btn-refresh-text">Fetch Live Data</span>
      </button>
    </div>
  </div>


  <div class="nav-tabs">
    <button id="tab-flips" class="tab-btn active" onclick="switchTab('flips')">🏆 Profitable Flips</button>
    <button id="tab-blackmarket" class="tab-btn" onclick="switchTab('blackmarket')">
      🏴‍☠️ Black Market <span id="bm-badge" class="bm-pulse-badge" style="display: none;"></span>
    </button>
    <button id="tab-crafting" class="tab-btn" onclick="switchTab('crafting')">🔨 Crafting & Refining Profit</button>
    <button id="tab-enchanting" class="tab-btn" onclick="switchTab('enchanting')">
      🔮 Enchanting & Foundry <span id="enchant-badge" class="badge" style="background:#8b5cf6; color:#fff; font-size:0.72rem; margin-left:4px; display:none;">0</span>
    </button>
    <button id="tab-watchlist" class="tab-btn" onclick="switchTab('watchlist')">⭐ Watchlist <span id="watchlist-badge" class="badge" style="background:#f59e0b; color:#000; font-size:0.72rem; margin-left:4px; display:none;">0</span></button>
    <button id="tab-prices" class="tab-btn" onclick="switchTab('prices')">📡 Live Scanned Prices</button>
  </div>


  <div class="filter-panel">
    <!-- Quick Filters for Flips -->
    <div id="quick-flips" class="quick-pills">
      <span class="filter-label">Quick Filters:</span>
      <button class="pill-btn active" onclick="setFlipQuickFilter('all', this)">🔥 All Deals</button>
      <button class="pill-btn" onclick="setFlipQuickFilter('top_picks', this)">⭐ Top Picks (S & A Tier)</button>
      <button class="pill-btn" onclick="setFlipQuickFilter('high_demand', this)">🚀 High Demand (≥5/day)</button>
      <button class="pill-btn" onclick="setFlipQuickFilter('active_vol', this)">⚡ Active Only (Skip Dead)</button>
      <button class="pill-btn" onclick="setFlipQuickFilter('instant', this)">⚡ Instant Sell (0 Risk)</button>
      <button class="pill-btn" onclick="setFlipQuickFilter('safe', this)">🛡️ Safe Royal Cities</button>
      <button class="pill-btn" onclick="setFlipQuickFilter('fresh', this)">🟢 Fresh Only (&lt;60m)</button>
      <button class="pill-btn" onclick="setFlipQuickFilter('high_profit', this)">💎 High Profit (>50k)</button>
    </div>

    <!-- Quick Filters for Black Market -->
    <div id="quick-blackmarket" class="quick-pills" style="display: none;">
      <span class="filter-label">BM Quick Filters:</span>
      <button class="pill-btn active" onclick="setBmFilter('all', this)">🔥 All BM Deals</button>
      <button class="pill-btn" onclick="setBmFilter('high_demand', this)">🚀 High BM Demand (≥5/day)</button>
      <button class="pill-btn" onclick="setBmFilter('active_vol', this)">⚡ Active Only (Skip Dead)</button>
      <button class="pill-btn" onclick="setBmFilter('fresh_only', this)">🟢 Fresh Only (&le;20m)</button>
      <button class="pill-btn" onclick="setBmFilter('just_arrived', this)">⚡ Just Arrived (&lt;15m)</button>
      <button class="pill-btn" onclick="setBmFilter('instant', this)">⚡ Direct Buy Orders (Instant Cash)</button>
      <button class="pill-btn" onclick="setBmFilter('royal', this)">👑 Royal Items</button>
      <button class="pill-btn" onclick="setBmFilter('weapons', this)">⚔️ Weapons</button>
      <button class="pill-btn" onclick="setBmFilter('armors', this)">🛡️ Armor & Robes</button>
      <button class="pill-btn" onclick="setBmFilter('accessories', this)">🎒 Bags & Capes</button>
      <button class="pill-btn" onclick="setBmFilter('high_profit', this)">💎 100k+ Profit</button>
    </div>


    <!-- Quick Filters for Crafting -->
    <div id="quick-crafting" class="quick-pills" style="display: none;">
      <span class="filter-label">Categories:</span>
      <button class="pill-btn active" onclick="setCraftFilter('all', this)">All Recipes</button>
      <button class="pill-btn" onclick="setCraftFilter('Royal Crafting', this)">👑 Royal Gear</button>
      <button class="pill-btn" onclick="setCraftFilter('Wood Refining', this)">🪵 Wood / Planks</button>
      <button class="pill-btn" onclick="setCraftFilter('Ore Refining', this)">⛏️ Ore / Bars</button>
      <button class="pill-btn" onclick="setCraftFilter('Fiber Refining', this)">🌾 Fiber / Cloth</button>
      <button class="pill-btn" onclick="setCraftFilter('Hide Refining', this)">🐾 Hide / Leather</button>
      <button class="pill-btn" onclick="setCraftFilter('Stone Refining', this)">🧱 Stone Blocks</button>
      <button class="pill-btn" onclick="setCraftFilter('Bag Crafting', this)">🎒 Bags</button>
      <button class="pill-btn" onclick="setCraftFilter('Cape Crafting', this)">🧣 Capes</button>
      <button class="pill-btn" onclick="setCraftFilter('Faction Capes', this)">🚩 Faction Capes</button>
      <button class="pill-btn" onclick="setCraftFilter('Weapon', this)">⚔️ Weapons</button>
      <button class="pill-btn" onclick="setCraftFilter('Armor', this)">🛡️ Armors</button>
      <button class="pill-btn" onclick="setCraftFilter('Off-hand', this)">📖 Off-hands</button>
      <button class="pill-btn" onclick="setCraftFilter('Consumable', this)">🧪 Potions &amp; Food</button>
      <button class="pill-btn" onclick="setCraftFilter('Tools & Gathering', this)">⛏️ Tools &amp; Gathering</button>
      <button class="pill-btn" onclick="setCraftFilter('Transmutation', this)" title="Raw resource transmutation (next tier or enchantment) for silver">🔁 Transmutation</button>
      <button id="btn-focus-toggle" class="pill-btn" style="margin-left: auto; border-color: var(--purple); color: #d8b4fe;" onclick="toggleFocusMode()">✨ Focus: OFF</button>
      <div style="display: flex; align-items: center; gap: 8px; margin-top: 8px; width: 100%;">
        <span class="filter-label" style="color: #fbbf24;">🪙 Station Fee / 100 Nutrition:</span>
        <input id="station-fee-input" type="number" value="500" min="0" max="2500" step="25" style="width: 85px; padding: 3px 8px; background: #0f131f; border: 1px solid var(--border); color: #fff; border-radius: 6px; font-size: 0.85rem;" onchange="updateStationFee(this.value)" />
        <span style="font-size: 0.8rem; color: var(--text-muted);">(Set 0 if crafting on your private island)</span>
        <span class="filter-label" style="color: #c4b5fd; margin-left: 14px;" title="Crafted items roll a quality: 68.9% Normal, 25% Good, 5% Outstanding, 1% Excellent, 0.1% Masterpiece. Specialization and focus give extra rolls (best one kept).">🎲 Quality rolls:</span>
        <select id="quality-rolls" class="filter-select" onchange="setQualityRolls(this.value)" title="How many quality rolls you get when crafting (best one kept)">
          <option value="1">1 roll (no spec)</option>
          <option value="2">2 rolls (some spec / focus)</option>
          <option value="3">3 rolls (high spec + focus)</option>
          <option value="4">4 rolls (max spec + focus)</option>
        </select>
        <button class="pill-btn" onclick="sortCrafting('avg_profit', true)" title="Sort by average profit, counting better quality rolls">🎲 Sort by avg profit</button>
      </div>
    </div>

    <!-- Quick Filters for Enchanting -->
    <div id="quick-enchanting" class="quick-pills" style="display: none; flex-wrap: wrap; gap: 6px;">
      <span class="filter-label" style="color: #c4b5fd;">🔮 Step:</span>
      <button class="pill-btn active" onclick="setEnchantStep('all', this)">All Steps</button>
      <button class="pill-btn" onclick="setEnchantStep('0->1', this)">🟢 .0 ➜ .1 (Runes)</button>
      <button class="pill-btn" onclick="setEnchantStep('1->2', this)">🔵 .1 ➜ .2 (Souls)</button>
      <button class="pill-btn" onclick="setEnchantStep('2->3', this)">🟣 .2 ➜ .3 (Relics)</button>
      <button class="pill-btn" onclick="setEnchantStep('0->2', this)">⚡ .0 ➜ .2 (Direct)</button>
      <button class="pill-btn" onclick="setEnchantStep('0->3', this)">🔥 .0 ➜ .3 (Full)</button>
      <span class="filter-label" style="margin-left: 10px; color: #93c5fd;">Slot:</span>
      <button class="pill-btn active" onclick="setEnchantSlot('all', this)">All Slots</button>
      <button class="pill-btn" onclick="setEnchantSlot('Bag', this)">🎒 Bags</button>
      <button class="pill-btn" onclick="setEnchantSlot('Weapon', this)">⚔️ Weapons</button>
      <button class="pill-btn" onclick="setEnchantSlot('Armor', this)">🛡️ Armor</button>
      <button class="pill-btn" onclick="setEnchantSlot('Cape', this)">🧣 Capes</button>
      <button class="pill-btn" onclick="setEnchantSlot('Helmet', this)">🪖 Helmets</button>
      <button class="pill-btn" onclick="setEnchantSlot('Boots', this)">🥾 Boots</button>
      <button class="pill-btn" onclick="setEnchantSlot('Off-hand', this)">🛡️ Off-hands</button>
      <span class="filter-label" style="margin-left: 10px; color: #a7f3d0;">Profit:</span>
      <button class="pill-btn active" onclick="setEnchantStatus('profitable', this)">🟢 Profitable Only</button>
      <button class="pill-btn" onclick="setEnchantStatus('high', this)">🔥 High Margin (&ge;20%)</button>
      <button class="pill-btn" onclick="setEnchantStatus('all', this)">All Deals</button>
      <span class="filter-label" style="margin-left: 10px; color: #fdba74;">Demand:</span>
      <button class="pill-btn active" onclick="setEnchantDemand('all', this)">All Volume</button>
      <button class="pill-btn" onclick="setEnchantDemand('active', this)">🛡️ Hide Dead (&ge;1/d)</button>
      <button class="pill-btn" onclick="setEnchantDemand('high', this)">🔥 High Demand (&ge;10/d)</button>
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
        <span class="filter-label" id="filter-buy-city-label">Buy City:</span>
        <select id="filter-buy-city" class="filter-select" onchange="onFilterChange()">
          <option value="all">All Cities</option>
          <option value="Bridgewatch">Bridgewatch</option>
          <option value="Fort Sterling">Fort Sterling</option>
          <option value="Lymhurst">Lymhurst</option>
          <option value="Martlock">Martlock</option>
          <option value="Thetford">Thetford</option>
          <option value="Caerleon">Caerleon</option>
          <option value="Brecilien">Brecilien</option>
          <option value="Black Market">Black Market</option>
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
          <option value="Black Market">Black Market</option>
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

      <div>
        <span class="filter-label">Liquidity:</span>
        <select id="filter-volume" class="filter-select" onchange="onFilterChange()">
          <option value="all">Any Volume</option>
          <option value="skip_dead">⚡ Skip Dead (&ge; 1/d)</option>
          <option value="5">🟢 Moderate (&ge; 5/d)</option>
          <option value="10">🔥 High Demand (&ge; 10/d)</option>
          <option value="25">🚀 Very High (&ge; 25/d)</option>
          <option value="50">🌊 Massive (&ge; 50/d)</option>
        </select>
      </div>

      <button id="btn-sniffer-only" class="pill-btn" onclick="toggleSnifferOnly()" title="Only profitable deals that use prices captured by your sniffer in the last hour, best profit first">📡 Sniffer Scans Only</button>
      <button class="btn btn-secondary" onclick="resetFilters()">Reset Filters</button>
    </div>
  </div>

  <div class="mount-bar" id="mount-toolbar">
    <span class="mount-label">⚖️ Transport Mount:</span>
    <select id="mount-select" onchange="onMountChange(this.value)" style="background: #0f131f; color: #fff; border: 1px solid var(--border); border-radius: 6px; padding: 4px 8px; font-size: 0.85rem;">
      <option value="1607">T5 Transport Ox (1,607 kg)</option>
      <option value="672">T3 Transport Ox (672 kg)</option>
      <option value="1075">T4 Transport Ox (1,075 kg)</option>
      <option value="2364">T6 Transport Ox (2,364 kg)</option>
      <option value="3425">T7 Transport Ox (3,425 kg)</option>
      <option value="4923">T8 Transport Ox (4,923 kg)</option>
      <option value="800">T5 Armored Horse (800 kg - Safe)</option>
      <option value="1100">T7 Pest Lizard (1,100 kg - Fast)</option>
      <option value="2500">T8 Grizzly Bear (2,500 kg - Tank)</option>
      <option value="custom">Custom Weight...</option>
    </select>
    <input id="mount-custom" type="number" placeholder="kg" style="display:none; width: 85px; background: #0f131f; color: #fff; border: 1px solid var(--border); border-radius: 6px; padding: 4px 8px; font-size: 0.85rem;" onchange="onCustomMountChange(this.value)" />
    <span class="mount-info">Max Load: <b id="mount-cap-disp" style="color: #60a5fa;">1,607</b> kg</span>
    <button class="pill-btn" onclick="sortFlips('profit_per_kg', true)" title="Sort by Silver profit per Kilogram of weight">⚡ Sort by Silver/kg</button>
    <button class="pill-btn" onclick="sortFlips('mount_trip', true)" title="Sort by Total Trip Profit for current mount load">💰 Sort by Mount Trip</button>
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
          <th class="num" onclick="sortFlips('profit_per_kg', true)" title="Silver profit per Kilogram of item weight">Silver / kg ⚖️</th>
          <th class="num" onclick="sortFlips('mount_trip', true)" title="Total Trip Profit for current mount load">Mount Trip 💰</th>
          <th class="num" onclick="sortFlips('est_daily_profit', true)" title="Estimated daily silver turnover based on daily volume">Est. Daily Silver ⚡</th>
          <th class="num" onclick="sortFlips('avg_daily_volume', true)">Daily Vol</th>
          <th class="num" onclick="sortFlips('data_age_minutes', true)">Data Age</th>
          <th onclick="sortFlips('risk')">Route Risk</th>
          <th>Action</th>
        </tr>
      </thead>
      <tbody id="flips-body">
        <tr><td colspan="18" style="text-align: center; padding: 24px; color: var(--text-muted);">Connecting to analyzer backend...</td></tr>
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
          <th class="num" onclick="sortBlackMarket('profit_per_kg', true)" title="Silver profit per Kilogram of item weight">Silver / kg ⚖️</th>
          <th class="num" onclick="sortBlackMarket('mount_trip', true)" title="Total Trip Profit for current mount load">Mount Trip 💰</th>
          <th class="num" onclick="sortBlackMarket('est_daily_profit', true)" title="Estimated daily silver sold to Black Market based on daily volume">Est. Daily Silver ⚡</th>
          <th class="num" onclick="sortBlackMarket('avg_daily_volume', true)">Daily BM Vol</th>
          <th class="num" onclick="sortBlackMarket('data_age_minutes', true)">Data Age</th>
          <th>Action</th>
        </tr>
      </thead>
      <tbody id="blackmarket-body">
        <tr><td colspan="16" style="text-align: center; padding: 24px; color: var(--text-muted);">Loading Black Market buy orders & flips...</td></tr>
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
          <th title="Materials are bought in the craft city; artifacts and royal sigils show the city to buy them in">Ingredients (bought in craft city)</th>
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

  <!-- VIEW: ENCHANTING -->
  <div id="view-enchanting" style="display: none;">
    <!-- Live Artifact Materials Ticker Card -->
    <div style="background: rgba(139, 92, 246, 0.08); border: 1px solid rgba(139, 92, 246, 0.3); border-radius: 8px; padding: 12px 16px; margin-bottom: 14px;">
      <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; flex-wrap: wrap; gap: 10px;">
        <div style="display: flex; align-items: center; gap: 8px;">
          <span style="font-size: 1.15rem;">🔮</span>
          <div>
            <strong style="color: #c4b5fd; font-size: 0.94rem;">Artifact Foundry Materials (Live Market Prices):</strong>
            <div style="font-size: 0.76rem; color: #94a3b8; margin-top: 1px;">Live scanned from your local sniffer / AODP. Click ✏️ on any rune/soul/relic to override price.</div>
          </div>
        </div>
        <div style="display: flex; align-items: center; gap: 10px; flex-wrap: wrap;">
          <span style="font-size: 0.8rem; color: #cbd5e1; font-weight: 600;">⚡ Mass Batch Size:</span>
          <div class="batch-selector" style="display: inline-flex; gap: 4px;">
            <button class="pill-btn batch-btn active" onclick="setEnchantBatch(10, this)">10x</button>
            <button class="pill-btn batch-btn" onclick="setEnchantBatch(25, this)">25x</button>
            <button class="pill-btn batch-btn" onclick="setEnchantBatch(50, this)">50x</button>
            <button class="pill-btn batch-btn" onclick="setEnchantBatch(100, this)">100x</button>
            <input id="custom-batch-input" type="number" min="1" max="9999" placeholder="Custom" style="width: 70px; padding: 2px 8px; font-size: 0.78rem; background: #0f172a; border: 1px solid #475569; border-radius: 4px; color: #f8fafc;" onchange="setEnchantBatch(this.value, null)">
          </div>
        </div>
      </div>
      <!-- Material price chips grid -->
      <div id="artifact-mats-grid" style="display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap: 8px;">
        <!-- Populated dynamically by renderArtifactMats() -->
      </div>
    </div>

    <!-- Live Sniffer Sales & Activity Radar (0s Latency) -->
    <div id="enchanting-sniffer-radar" style="background: rgba(15, 23, 42, 0.85); border: 1px solid rgba(56, 189, 248, 0.35); border-radius: 8px; padding: 12px 16px; margin-bottom: 14px; box-shadow: 0 4px 12px rgba(0, 0, 0, 0.3);">
      <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; flex-wrap: wrap; gap: 8px;">
        <div style="display: flex; align-items: center; gap: 8px;">
          <span class="pulse-dot" style="background: #38bdf8; box-shadow: 0 0 8px #38bdf8;"></span>
          <strong style="color: #7dd3fc; font-size: 0.94rem;">⚡ Live Sniffer Sales &amp; Demand Radar:</strong>
          <span style="font-size: 0.76rem; color: #94a3b8;">Real-time stream of incoming market packet scans. Verifies demand velocity to protect you from dead items.</span>
        </div>
        <div style="font-size: 0.78rem; color: #64748b;" id="radar-last-ping">Awaiting live market packets...</div>
      </div>
      <div id="radar-latest-event" style="padding: 10px 14px; background: rgba(30, 41, 59, 0.6); border-radius: 6px; border: 1px solid rgba(255, 255, 255, 0.05); margin-bottom: 8px;">
        <div style="color: #94a3b8; font-size: 0.82rem; display: flex; align-items: center; gap: 8px;">
          <span>📡 <em>Open your in-game market and click an item or click the chart/graph icon to sniff live prices and sales velocity instantly.</em></span>
        </div>
      </div>
      <div id="radar-recent-events" style="display: flex; gap: 6px; flex-wrap: wrap; align-items: center;"></div>
    </div>

    <!-- Enchanting Table -->
    <table id="enchanting-table">
      <thead>
        <tr>
          <th onclick="sortEnchanting('is_profitable')">Rating</th>
          <th onclick="sortEnchanting('item_name')">Item</th>
          <th onclick="sortEnchanting('item_type')">Slot</th>
          <th onclick="sortEnchanting('step_label')">Enchant Step</th>
          <th>Required Materials (Foundry)</th>
          <th class="num" onclick="sortEnchanting('base_item_price', true)">Base Buy Cost</th>
          <th class="num" onclick="sortEnchanting('enchant_mat_total_cost', true)">Mats Cost</th>
          <th class="num" onclick="sortEnchanting('total_cost', true)" title="Base Item Buy Cost + Total Materials Cost">Total Cost 💰</th>
          <th>Sell City</th>
          <th class="num" onclick="sortEnchanting('sell_price', true)">Enchanted Sell Price</th>
          <th class="num" onclick="sortEnchanting('profit_per_item', true)">Profit / Item</th>
          <th class="num" onclick="sortEnchanting('margin_pct', true)">ROI Margin</th>
          <th class="num" onclick="sortEnchanting('mass_profit', true)" id="th-mass-profit">Mass Profit (10x) ⚡</th>
          <th class="num" onclick="sortEnchanting('target_daily_volume', true)" title="Daily sales volume in destination city to prevent enchanting dead items">Daily Sales 📈</th>
          <th class="num" onclick="sortEnchanting('data_age_minutes', true)">Data Age</th>
          <th>Action</th>
        </tr>
      </thead>
      <tbody id="enchanting-body">
        <tr><td colspan="16" style="text-align: center; padding: 28px; color: var(--text-muted);">Loading live enchanting opportunities...</td></tr>
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

  <!-- VIEW 5: WATCHLIST -->
  <div id="view-watchlist" style="display: none;">
    <div style="background: rgba(245, 158, 11, 0.08); border: 1px solid rgba(245, 158, 11, 0.25); border-radius: 8px; padding: 10px 16px; margin-bottom: 12px; font-size: 0.88rem; color: #fde68a; display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 8px;">
      <span>⭐ <strong>Watchlist:</strong> Every item you starred: its flips, Black Market, crafting and enchanting deals, and its latest prices.</span>
      <button class="btn btn-secondary" style="padding: 3px 10px; font-size: 0.78rem;" onclick="clearWatchlist()">Clear Watchlist</button>
    </div>
    <table id="watchlist-table">
      <thead>
        <tr>
          <th onclick="sortWatchlist('score')">Rating</th>
          <th onclick="sortWatchlist('item_name')">Item</th>
          <th>Tier / Q</th>
          <th onclick="sortWatchlist('buy_city')">Buy City</th>
          <th onclick="sortWatchlist('sell_city')">Sell City</th>
          <th class="num" onclick="sortWatchlist('buy_price', true)">Buy Price</th>
          <th class="num" onclick="sortWatchlist('sell_price', true)">Sell Price</th>
          <th>Exit Mode</th>
          <th class="num" onclick="sortWatchlist('profit_per_item', true)">Profit / Item</th>
          <th class="num" onclick="sortWatchlist('margin_pct', true)">ROI Margin</th>
          <th class="num" onclick="sortWatchlist('total_profit', true)">Total Profit</th>
          <th class="num" onclick="sortWatchlist('profit_per_kg', true)" title="Silver profit per Kilogram of item weight">Silver / kg ⚖️</th>
          <th class="num" onclick="sortWatchlist('mount_trip', true)" title="Total Trip Profit for current mount load">Mount Trip 💰</th>
          <th class="num" onclick="sortWatchlist('est_daily_profit', true)">Est. Daily Silver ⚡</th>
          <th class="num" onclick="sortWatchlist('avg_daily_volume', true)">Daily Vol</th>
          <th class="num" onclick="sortWatchlist('data_age_minutes', true)">Data Age</th>
          <th>Action</th>
        </tr>
      </thead>
      <tbody id="watchlist-body">
        <tr><td colspan="17" style="text-align: center; padding: 32px; color: var(--text-muted);">No items in your watchlist yet. Click the ★ star icon next to any item to pin it here!</td></tr>
      </tbody>
    </table>
    <div id="watchlist-extra"></div>
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

  <!-- OVERRIDES MANAGER MODAL -->
  <div id="overrides-modal" class="modal-overlay" style="display: none;" onclick="closeOverridesModal(event)">

    <div class="modal-box" onclick="event.stopPropagation()">
      <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 14px;">
        <h3 style="margin: 0; color: #f8fafc; font-size: 1.15rem;">🏷️ Active Price & Fulfillment Overrides</h3>
        <button class="btn btn-secondary" onclick="closeOverridesModal()" style="padding: 2px 8px;">✕</button>
      </div>
      <p style="font-size: 0.85rem; color: #94a3b8; margin-bottom: 14px; line-height: 1.4;">
        These items were marked fulfilled or had their prices manually overwritten in-game. They prevent stale AODP market records from showing phantom profits.
      </p>
      <div id="overrides-list" style="margin-bottom: 20px; max-height: 280px; overflow-y: auto;">
        <!-- Filled dynamically -->
      </div>
      <div style="display: flex; justify-content: space-between; align-items: center; pt: 8px;">
        <button class="btn" onclick="clearAllOverrides()" style="background: #991b1b; border-color: #ef4444; color: #fee2e2;">
          🗑️ Clear All Overrides
        </button>
        <button class="btn btn-secondary" onclick="closeOverridesModal()">
          Close
        </button>
      </div>
    </div>
  </div>

  <!-- SNIFFER GUIDE MODAL -->
  <!-- SNIFFER GUIDE & CONTROL CENTER MODAL -->
  <div id="sniffer-modal" class="modal-overlay" style="display: none;" onclick="closeSnifferModal(event)">
    <div class="modal-box" onclick="event.stopPropagation()" style="max-width: 620px;">
      <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 14px;">
        <h3 style="margin: 0; color: #f8fafc; font-size: 1.15rem; display: flex; align-items: center; gap: 8px;">
          <span>⚡ Live Network Sniffing & 0-Second Ingestion</span>
        </h3>
        <button class="btn btn-secondary" onclick="closeSnifferModal()" style="padding: 2px 8px;">✕</button>
      </div>

      <!-- Background Albion Data Client (managed by the app) -->
      <div id="client-card" style="background: rgba(15, 23, 42, 0.7); border: 1px solid #334155; border-radius: 8px; padding: 12px 16px; margin-bottom: 14px;">
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
          <span style="font-size: 0.85rem; color: #94a3b8; text-transform: uppercase; font-weight: 700; letter-spacing: 0.5px;">Background Data Client</span>
          <span id="client-state" style="font-size: 0.85rem; font-weight: 700; color: #94a3b8;">-</span>
        </div>
        <div id="client-message" style="font-size: 0.82rem; color: #cbd5e1; line-height: 1.45;"></div>
        <div id="client-location" style="font-size: 0.82rem; margin-top: 4px;"></div>
        <a id="client-help" href="https://npcap.com/#download" target="_blank" rel="noopener" style="display: none; font-size: 0.82rem; color: #60a5fa; margin-top: 6px;">⬇️ Download Npcap (npcap.com)</a>
        <div style="display: flex; gap: 8px; margin-top: 10px; flex-wrap: wrap;">
          <button class="btn btn-primary" onclick="snifferAction('start')" style="padding: 5px 12px; font-size: 0.8rem;">▶ Start</button>
          <button class="btn btn-secondary" onclick="snifferAction('restart')" style="padding: 5px 12px; font-size: 0.8rem;">↻ Restart</button>
          <button class="btn btn-secondary" onclick="snifferAction('stop')" style="padding: 5px 12px; font-size: 0.8rem;">■ Stop</button>
          <button class="btn btn-secondary" onclick="snifferAction('show')" style="padding: 5px 12px; font-size: 0.8rem;">🪟 Show client window</button>
        </div>
        <details style="margin-top: 8px;">
          <summary style="font-size: 0.78rem; color: #94a3b8; cursor: pointer;">Client log</summary>
          <pre id="client-log" style="background: #0f172a; border: 1px solid #334155; border-radius: 6px; padding: 8px 10px; font-size: 0.72rem; color: #94a3b8; max-height: 140px; overflow: auto; margin: 6px 0 0; white-space: pre-wrap;"></pre>
        </details>
      </div>

      <!-- Live Connection Status Card -->
      <div style="background: rgba(15, 23, 42, 0.7); border: 1px solid #334155; border-radius: 8px; padding: 12px 16px; margin-bottom: 14px;">
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
          <span style="font-size: 0.85rem; color: #94a3b8; text-transform: uppercase; font-weight: 700; letter-spacing: 0.5px;">Live Local Pipe Status</span>
          <span id="modal-sniffer-status" style="font-size: 0.85rem; font-weight: 700; color: #94a3b8;">⚪ WAITING FOR PACKETS...</span>
        </div>
        <div style="display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; font-size: 0.82rem;">
          <div style="background: #1e293b; padding: 8px 10px; border-radius: 6px;">
            <div style="color: #94a3b8; font-size: 0.75rem;">Orders Captured:</div>
            <div id="modal-sniffer-orders" style="font-size: 1.05rem; font-weight: 700; color: #f8fafc; margin-top: 2px;">0</div>
          </div>
          <div style="background: #1e293b; padding: 8px 10px; border-radius: 6px;">
            <div style="color: #94a3b8; font-size: 0.75rem;">Last City:</div>
            <div id="modal-sniffer-city" style="font-size: 1.05rem; font-weight: 700; color: #60a5fa; margin-top: 2px;">-</div>
          </div>
          <div style="background: #1e293b; padding: 8px 10px; border-radius: 6px;">
            <div style="color: #94a3b8; font-size: 0.75rem;">Pipe Latency:</div>
            <div style="font-size: 1.05rem; font-weight: 700; color: #34d399; margin-top: 2px;">&lt; 2 ms</div>
          </div>
        </div>
        <button class="btn btn-primary" onclick="sendTestSnifferPacket()" style="width: 100%; margin-top: 10px; background: #059669; border-color: #10b981; font-weight: 700; font-size: 0.82rem; padding: 7px 12px;">
          🧪 Send Test Sniffer Market Packet (Verify 0s Ingest & UI Badge)
        </button>
      </div>

      <div style="font-size: 0.85rem; color: #cbd5e1; line-height: 1.5; display: flex; flex-direction: column; gap: 10px;">
        <div style="background: rgba(16, 185, 129, 0.1); border: 1px solid rgba(16, 185, 129, 0.3); border-radius: 8px; padding: 10px 14px; color: #a7f3d0;">
          💡 <strong>Why default mode took 3 minutes:</strong> AODP community public servers throttle and cache incoming uploads in 3–5 minute aggregation buckets. When you pass our local ingest endpoint, packets are delivered to this screen in <strong>0.001 seconds</strong>!
        </div>

        <p style="margin: 0;">
          On Windows the app starts the Albion Data Client for you (card above). To run it by hand instead
          (macOS / Linux, or with <code>"sniffer": {"enabled": false}</code> in config.json):
        </p>

        <!-- macOS / Linux Command -->
        <div>
          <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px;">
            <span style="font-size: 0.78rem; color: #94a3b8; font-weight: 600;">🍎 macOS / Linux (Terminal):</span>
            <button class="btn btn-secondary" style="padding: 2px 8px; font-size: 0.72rem;" onclick="copyToClipboard('sudo ./albiondata-client-executable -i &quot;https+pow://albion-online-data.com,http://127.0.0.1:8765/api/ingest&quot;', this)">📋 Copy</button>
          </div>
          <pre style="background: #0f172a; border: 1px solid #334155; border-radius: 6px; padding: 8px 10px; font-size: 0.75rem; color: #38bdf8; overflow-x: auto; margin: 0;"><code>sudo ./albiondata-client-executable -i "https+pow://albion-online-data.com,http://127.0.0.1:8765/api/ingest"</code></pre>
        </div>

        <!-- Windows Command -->
        <div>
          <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px;">
            <span style="font-size: 0.78rem; color: #94a3b8; font-weight: 600;">🪟 Windows (Command Prompt / PowerShell):</span>
            <button class="btn btn-secondary" style="padding: 2px 8px; font-size: 0.72rem;" onclick="copyToClipboard('albiondata-client.exe -i &quot;https+pow://albion-online-data.com,http://127.0.0.1:8765/api/ingest&quot;', this)">📋 Copy</button>
          </div>
          <pre style="background: #0f172a; border: 1px solid #334155; border-radius: 6px; padding: 8px 10px; font-size: 0.75rem; color: #38bdf8; overflow-x: auto; margin: 0;"><code>albiondata-client.exe -i "https+pow://albion-online-data.com,http://127.0.0.1:8765/api/ingest"</code></pre>
        </div>

        <p style="font-size: 0.78rem; color: #94a3b8; margin: 0;">
          <em>Note: Reading incoming game packets with albiondata-client does NOT modify game memory or automate input, keeping you 100% compliant with Sandbox Interactive TOS.</em>
        </p>
      </div>

      <div style="margin-top: 14px; text-align: right;">
        <button class="btn btn-secondary" onclick="closeSnifferModal()">Close</button>
      </div>
    </div>
  </div>

  <script>
    let currentTab = 'flips';

    let flipsData = [];
    let blackmarketData = [];
    let craftingData = [];
    let snifferCraftingData = [];
    let snifferFocusCraftingData = [];
    let focusCraftingData = [];
    let enchantingData = [];
    let artifactMatsData = {};
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
    let enchantBatchSize = 10;
    let enchantStepFilter = 'all';
    let enchantSlotFilter = 'all';
    let enchantStatusFilter = 'profitable';
    let enchantDemandFilter = 'all';
    let liveSnifferEventsData = [];
    let snifferOnly = false;
    let zoneWarningShown = false;
    let qualityRolls = 1;
    try { qualityRolls = Math.min(4, Math.max(1, parseInt(localStorage.getItem('albion_quality_rolls') || '1'))); } catch (e) {}
    let premiumTax = true;
    let marketTaxRate = 0.04;

    // Albion Analyser features: Mount capacity, Station tax fee, and Watchlist
    let selectedMountCap = parseFloat(localStorage.getItem('albion_mount_cap') || '1607');
    let currentStationFee = parseFloat(localStorage.getItem('albion_station_fee') || '500');
    let watchlist = new Set(JSON.parse(localStorage.getItem('albion_watchlist') || '[]'));
    let watchlistPrices = {};
    let watchlistNames = {};
    let watchlistSynced = false;
    let watchlistPending = 0;

    // Sorting state
    let sortKeyFlips = 'score';
    let sortAscFlips = false;
    let sortKeyBm = 'profit_per_item';
    let sortAscBm = false;
    let sortKeyCrafting = 'profit_per_item';
    let sortAscCrafting = false;
    let sortKeyEnchant = 'profit_per_item';
    let sortAscEnchant = false;
    let sortKeyWatchlist = 'score';
    let sortAscWatchlist = false;

    function escapeHtml(str) {
      if (!str) return '';
      return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
    }

    function isStarred(itemId) {
      return watchlist.has(itemId);
    }

    async function saveWatchlist(change) {
      // The app keeps the watchlist on disk; the desktop window's browser storage is wiped on exit.
      try { localStorage.setItem('albion_watchlist', JSON.stringify(Array.from(watchlist))); } catch (e) {}
      watchlistPending++;
      try {
        await fetch('/api/watchlist', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(change) });
      } catch (e) {
        console.error('Could not save watchlist', e);
      } finally {
        watchlistPending--;
      }
    }

    function syncWatchlist(data) {
      if (!Array.isArray(data.watchlist) || watchlistPending > 0) return;
      watchlistPrices = data.watchlist_prices || {};
      watchlistNames = data.watchlist_names || {};
      if (!watchlistSynced) {
        watchlistSynced = true;
        // Carry over stars saved by older versions in browser storage.
        const missing = Array.from(watchlist).filter(id => !data.watchlist.includes(id));
        if (missing.length) {
          data.watchlist.forEach(id => watchlist.add(id));
          saveWatchlist({ add: missing });
          updateWatchlistBadge();
          return;
        }
      }
      const changed = data.watchlist.length !== watchlist.size || data.watchlist.some(id => !watchlist.has(id));
      if (changed) {
        watchlist = new Set(data.watchlist);
        try { localStorage.setItem('albion_watchlist', JSON.stringify(data.watchlist)); } catch (e) {}
        updateWatchlistBadge();
      }
    }

    function toggleStar(itemId, btn) {
      if (watchlist.has(itemId)) {
        watchlist.delete(itemId);
        saveWatchlist({ remove: [itemId] });
        showToast(`Removed <b>${escapeHtml(itemId)}</b> from Watchlist`);
        if (btn) btn.classList.remove('starred');
      } else {
        watchlist.add(itemId);
        saveWatchlist({ add: [itemId] });
        showToast(`⭐ Added <b>${escapeHtml(itemId)}</b> to Watchlist!`);
        if (btn) btn.classList.add('starred');
      }
      updateWatchlistBadge();
      renderCurrentView();
    }

    function clearWatchlist() {
      if (confirm('Clear all pinned items from your watchlist?')) {
        watchlist.clear();
        saveWatchlist({ clear: true });
        updateWatchlistBadge();
        renderCurrentView();
        showToast('Watchlist cleared.');
      }
    }

    function updateWatchlistBadge() {
      const badge = document.getElementById('watchlist-badge');
      if (badge) {
        badge.textContent = watchlist.size;
        badge.style.display = watchlist.size > 0 ? 'inline-block' : 'none';
      }
    }

    function onMountChange(val) {
      const customInput = document.getElementById('mount-custom');
      if (val === 'custom') {
        if (customInput) customInput.style.display = 'inline-block';
      } else {
        if (customInput) customInput.style.display = 'none';
        selectedMountCap = parseFloat(val) || 1607;
        localStorage.setItem('albion_mount_cap', selectedMountCap);
        const disp = document.getElementById('mount-cap-disp');
        if (disp) disp.textContent = Math.round(selectedMountCap).toLocaleString();
        renderCurrentView();
      }
    }

    function onCustomMountChange(val) {
      const num = parseFloat(val);
      if (num && num > 0) {
        selectedMountCap = num;
        localStorage.setItem('albion_mount_cap', selectedMountCap);
        const disp = document.getElementById('mount-cap-disp');
        if (disp) disp.textContent = Math.round(selectedMountCap).toLocaleString();
        renderCurrentView();
      }
    }

    function updateStationFee(val) {
      const fee = parseFloat(val);
      if (!isNaN(fee) && fee >= 0) {
        currentStationFee = fee;
        localStorage.setItem('albion_station_fee', currentStationFee);
        if (currentTab === 'crafting') renderCrafting();
      }
    }

    function openSnifferModal() {
      const m = document.getElementById('sniffer-modal');
      if (m) m.style.display = 'flex';
    }

    function closeSnifferModal(e) {
      if (!e || e.target.id === 'sniffer-modal' || e.target.tagName === 'BUTTON') {
        const m = document.getElementById('sniffer-modal');
        if (m) m.style.display = 'none';
      }
    }

    let prevSnifferPackets = 0;
    let snifferProcess = { state: 'disabled' };

    const CLIENT_STATE_LABELS = {
      running: ['🟢 RUNNING', '#86efac'],
      starting: ['🟡 STARTING...', '#fde68a'],
      downloading: ['⬇️ DOWNLOADING...', '#93c5fd'],
      stopped: ['⚪ STOPPED', '#94a3b8'],
      needs_npcap: ['🔴 NPCAP NEEDED', '#fca5a5'],
      missing: ['🔴 CLIENT MISSING', '#fca5a5'],
      external: ['🟠 OTHER CLIENT RUNNING', '#fdba74'],
      error: ['🔴 ERROR', '#fca5a5'],
      unsupported: ['⚪ MANUAL (non-Windows)', '#94a3b8'],
      disabled: ['⚪ DISABLED IN CONFIG', '#94a3b8'],
    };

    function updateSnifferProcess(p) {
      snifferProcess = p || { state: 'disabled' };
      const [label, color] = CLIENT_STATE_LABELS[snifferProcess.state] || [snifferProcess.state, '#94a3b8'];
      const stateEl = document.getElementById('client-state');
      if (stateEl) { stateEl.textContent = label + (snifferProcess.elevated ? ' (ADMIN)' : ''); stateEl.style.color = color; }
      const msgEl = document.getElementById('client-message');
      if (msgEl) msgEl.textContent = snifferProcess.message || (snifferProcess.state === 'disabled' ? 'Automatic start is turned off in config.json ("sniffer": {"enabled": false}).' : '');
      const helpEl = document.getElementById('client-help');
      if (helpEl) {
        helpEl.style.display = snifferProcess.help_url ? 'inline-block' : 'none';
        if (snifferProcess.help_url) helpEl.href = snifferProcess.help_url;
      }
      const logEl = document.getElementById('client-log');
      if (logEl) logEl.textContent = (snifferProcess.log_tail || []).join('\\n') || '(no output yet)';

      const needsZone = !!snifferProcess.needs_zone_change;
      const banner = document.getElementById('zone-banner');
      if (banner) banner.style.display = needsZone ? 'block' : 'none';
      const locEl = document.getElementById('client-location');
      if (locEl) {
        if (needsZone) {
          locEl.innerHTML = '<span style="color:#fbbf24;">📍 In-game location: unknown. Change zone once so market scans are recorded.</span>';
        } else if (snifferProcess.location_id) {
          locEl.innerHTML = `<span style="color:#86efac;">📍 In-game location: ${escapeHtml(snifferProcess.location_city || snifferProcess.location_id)}</span>`;
        } else {
          locEl.textContent = '';
        }
      }
      if (needsZone && !zoneWarningShown) {
        zoneWarningShown = true;
        showToast('⚠️ <strong>Sniffer:</strong> change zone once in-game, your market scans are being ignored until then.');
      } else if (!needsZone) {
        zoneWarningShown = false;
      }
    }

    async function snifferAction(action) {
      try {
        const res = await fetch('/api/sniffer/' + action, { method: 'POST' });
        const d = await res.json();
        if (d.sniffer_process) updateSnifferProcess(d.sniffer_process);
        if (d.status === 'disabled') showToast('Background Data Client is disabled in config.json');
        setTimeout(fetchFlips, 1500);
      } catch (e) {
        showToast(`❌ ${e}`);
      }
    }

    function updateSnifferStatus(s) {
      const pill = document.getElementById('sniffer-pill');
      const mStatus = document.getElementById('modal-sniffer-status');
      const mOrders = document.getElementById('modal-sniffer-orders');
      const mCity = document.getElementById('modal-sniffer-city');

      if (mOrders) mOrders.textContent = (s.packet_count || 0).toLocaleString();
      if (mCity) mCity.textContent = s.last_city || '-';

      if (s && s.active) {
        if (pill) {
          pill.style.background = '#14532d';
          pill.style.color = '#86efac';
          pill.style.borderColor = '#22c55e';
          pill.style.boxShadow = '0 0 10px rgba(34, 197, 94, 0.4)';
          pill.innerHTML = `🟢 <b>Sniffer: ACTIVE</b> (${s.last_city || 'Live'} • ${s.packet_count} orders)`;
        }
        if (mStatus) {
          mStatus.innerHTML = `<span style="color:#86efac; font-weight:700;">🟢 ACTIVE (Receiving Packets)</span>`;
        }
      } else if (s && s.packet_count > 0) {
        if (pill) {
          pill.style.background = '#1e293b';
          pill.style.color = '#cbd5e1';
          pill.style.borderColor = '#475569';
          pill.style.boxShadow = 'none';
          pill.innerHTML = `⚡ Sniffer: Standby (${s.packet_count} orders)`;
        }
        if (mStatus) {
          mStatus.innerHTML = `<span style="color:#cbd5e1; font-weight:600;">⚪ STANDBY (Last: ${s.seconds_since_last || 0}s ago)</span>`;
        }
      } else {
        if (pill) {
          pill.style.background = '';
          pill.style.color = '';
          pill.style.borderColor = '';
          pill.style.boxShadow = 'none';
          const st = snifferProcess.state;
          if (st === 'running') {
            pill.innerHTML = `🟢 Sniffer: Running • open the market in-game`;
          } else if (st === 'needs_npcap' || st === 'error' || st === 'missing' || st === 'external') {
            pill.style.borderColor = '#ef4444';
            pill.innerHTML = `🔴 Sniffer: ${(CLIENT_STATE_LABELS[st] || [st])[0].replace(/^\\S+ /, '')} (click)`;
          } else if (st === 'starting' || st === 'downloading') {
            pill.innerHTML = `🟡 Sniffer: ${st === 'downloading' ? 'Downloading client...' : 'Starting...'}`;
          } else {
            pill.innerHTML = `⚡ Sniffer: Connect (0s Ingest)`;
          }
        }
        if (mStatus) {
          mStatus.innerHTML = `<span style="color:#94a3b8;">⚪ WAITING FOR PACKETS...</span>`;
        }
      }
      if (pill && snifferProcess && snifferProcess.needs_zone_change) {
        pill.style.background = '#451a03';
        pill.style.color = '#fde68a';
        pill.style.borderColor = '#f59e0b';
        pill.style.boxShadow = '0 0 10px rgba(245, 158, 11, 0.4)';
        pill.innerHTML = '⚠️ <b>Sniffer: change zone in-game</b>';
      }
    }

    async function sendTestSnifferPacket() {
      try {
        const testPayload = {
          Orders: [
            {
              Id: Date.now(),
              ItemTypeId: "T4_BAG",
              LocationId: "3008", // Martlock
              QualityLevel: 1,
              UnitPriceSilver: 12500000, // 1,250 silver
              Amount: 10,
              AuctionType: "offer"
            },
            {
              Id: Date.now() + 1,
              ItemTypeId: "T4_BAG",
              LocationId: "3003", // Black Market
              QualityLevel: 1,
              UnitPriceSilver: 38000000, // 3,800 silver
              Amount: 5,
              AuctionType: "request"
            }
          ]
        };
        const res = await fetch('/api/ingest', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(testPayload)
        });
        if (res.ok) {
          const d = await res.json();
          showToast(`⚡ <strong>Test Sniffer Success!</strong> Ingested ${d.ingested} orders (Martlock & Black Market) in 0.001s!`);
          fetchFlips();
        }
      } catch (e) {
        showToast(`❌ Test failed: ${e}`);
      }
    }

    function renderAgeCell(f, isBm = false) {
      if (f.is_live_sniffer || (f.sniffer_age_seconds !== undefined && f.sniffer_age_seconds !== null && f.sniffer_age_seconds < 180)) {
        const s = (f.sniffer_age_seconds !== undefined && f.sniffer_age_seconds !== null) ? f.sniffer_age_seconds : 0;
        return `<span class="badge badge-sniffer" title="Direct 0-second live packet captured from your local in-game market scan">⚡ ${s}s (SNIFFER)</span>`;
      }
      const age = Math.round(Number(f.data_age_minutes !== undefined ? f.data_age_minutes : (f.age_minutes || 0)));
      if (age <= 1) {
        return `<span class="badge" style="background:#14532d; color:#86efac; font-weight:700;">🟢 &lt;1m ago</span>`;
      }
      if (age <= 15) {
        return `<span class="badge" style="background:#1e3a8a; color:#93c5fd; font-weight:700;">🟢 ${age}m ago</span>`;
      }
      const bmWarning = (isBm && age > 20) 
        ? ' <span class="badge" style="background: rgba(245, 158, 11, 0.2); color: #fbbf24; font-size: 0.68rem;" title="Scanned ' + age + 'm ago. High-profit Black Market orders are frequently fulfilled quickly in-game!">⚠️ Verify in BM</span>' 
        : '';
      const ageClass = age > 120 ? 'class="stale-data"' : '';
      return `<span ${ageClass}>${age}m ago</span>${bmWarning}`;
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
      const starred = isStarred(safeId);

      return `
        <div class="item-cell">
          <button class="star-btn ${starred ? 'starred' : ''}" onclick="event.stopPropagation(); toggleStar('${safeId}', this)" title="${starred ? 'Remove from Watchlist' : 'Star to pin to Watchlist'}">★</button>
          <div class="item-icon-wrapper" title="${safeName}">
            <img class="item-icon" 
                 src="${iconUrl}" 
                 alt="${safeName}" 
                 loading="lazy" 
                 onerror="this.parentElement.style.display='none';" />
          </div>
          <div class="item-details">
            <div style="display:inline-flex; align-items:center; gap:6px;">
              <a class="item-name" href="${wikiUrl}" target="_blank" rel="noopener noreferrer" title="View ${safeName} on Albion Wiki">
                <span>${safeName}</span>
                <span class="wiki-badge" title="Open Wiki Guide">Wiki ↗</span>
              </a>
              <button class="btn-copy-name" onclick="event.stopPropagation(); copyItemName('${safeName}', this)" title="1-Click Copy clean name for in-game Market search (Ctrl+V)">📋</button>
            </div>
            <div style="display:flex; align-items:center; gap:4px;">
              <span class="item-id-sub">${safeId}</span>
              <button class="btn-refresh-item" onclick="event.stopPropagation(); refreshSingleItem('${safeId}', this)" title="Instantly refresh live AODP price for this item">🔄</button>
            </div>
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
      if (document.getElementById('filter-volume')) document.getElementById('filter-volume').value = 'all';
      flipQuickFilter = 'all';
      bmQuickFilter = 'all';
      craftCategoryFilter = 'all';
      snifferOnly = false;
      const snifBtn = document.getElementById('btn-sniffer-only');
      if (snifBtn) { snifBtn.classList.remove('active'); snifBtn.textContent = '📡 Sniffer Scans Only'; }
      enchantStepFilter = 'all';
      enchantSlotFilter = 'all';
      enchantStatusFilter = 'profitable';
      enchantDemandFilter = 'all';
      document.querySelectorAll('.quick-pills .pill-btn').forEach(btn => btn.classList.remove('active'));
      const flipPill = document.querySelector('#quick-flips .pill-btn');
      if (flipPill) flipPill.classList.add('active');
      const bmPill = document.querySelector('#quick-blackmarket .pill-btn');
      if (bmPill) bmPill.classList.add('active');
      const craftPill = document.querySelector('#quick-crafting .pill-btn');
      if (craftPill) craftPill.classList.add('active');
      document.querySelectorAll('#quick-enchanting button').forEach(b => {
        const oc = b.getAttribute('onclick') || '';
        if (oc.includes("'all'") || oc.includes("'profitable'")) {
          b.classList.add('active');
        } else {
          b.classList.remove('active');
        }
      });
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
        showToast(`📋 Copied "<b>${escapeHtml(text)}</b>" to clipboard! (Ctrl+V in Albion Market)`);
        if (btn) {
          const orig = btn.innerHTML;
          btn.innerHTML = '✅ Copied!';
          btn.style.color = '#34d399';
          setTimeout(() => {
            btn.innerHTML = orig;
            btn.style.color = '';
          }, 1200);
        }
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

    function toggleSnifferOnly() {
      snifferOnly = !snifferOnly;
      const btn = document.getElementById('btn-sniffer-only');
      btn.classList.toggle('active', snifferOnly);
      btn.textContent = snifferOnly ? '📡 Sniffer Scans Only: ON' : '📡 Sniffer Scans Only';
      if (snifferOnly) {
        // Best deals first
        sortKeyFlips = 'profit_per_item'; sortAscFlips = false;
        sortKeyBm = 'profit_per_item'; sortAscBm = false;
        sortKeyCrafting = 'profit_per_item'; sortAscCrafting = false;
        sortKeyEnchant = 'profit_per_item'; sortAscEnchant = false;
      }
      currentPage = 1;
      renderCurrentView();
    }

    function updatePremiumButton(data) {
      if (typeof data.premium !== 'boolean') return;
      premiumTax = data.premium;
      marketTaxRate = Number(data.tax_rate) || (premiumTax ? 0.04 : 0.08);
      const btn = document.getElementById('btn-premium');
      if (!btn) return;
      const pct = Math.round(marketTaxRate * 1000) / 10;
      btn.textContent = premiumTax ? `💎 Premium Tax ${pct}%` : `🪙 Normal Tax ${pct}% (No Premium)`;
      btn.style.borderColor = premiumTax ? '#a855f7' : '';
      btn.style.color = premiumTax ? '#d8b4fe' : '';
    }

    async function togglePremium() {
      const btn = document.getElementById('btn-premium');
      if (btn) btn.disabled = true;
      try {
        const res = await fetch('/api/settings', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ premium: !premiumTax }),
        });
        if (res.ok) {
          const data = await res.json();
          updatePremiumButton(data);
          await fetchFlips();
          showToast(premiumTax ? '💎 Premium: profits now use the 4% market tax' : '🪙 No Premium: profits now use the 8% market tax');
        }
      } finally {
        if (btn) btn.disabled = false;
      }
    }

    function renderIngredients(c) {
      const list = c.ingredients || [];
      if (!list.length) return `<div style="font-size:0.82rem; color:#cbd5e1;">${escapeHtml(c.ingredients_desc)}</div>`;
      return '<div class="ing-list">' + list.map(ing => {
        const icon = `https://render.albiononline.com/v1/item/${encodeURIComponent(ing.id)}.png?quality=1`;
        const live = ing.is_live_sniffer;
        const ageTxt = live ? ` • 📡 sniffed ${Math.round((ing.sniffer_age_seconds || 0) / 60)}m ago` : '';
        const retTxt = (ing.returnable ? '' : ' • not returned (no return rate)') + (ing.bought_elsewhere ? ` • buy in ${ing.city} (not sold everywhere)` : '');
        const title = `${ing.count}x ${ing.name} @ ${Number(ing.unit_price).toLocaleString()} each in ${ing.city} = ${(ing.count * ing.unit_price).toLocaleString()}${retTxt}${ageTxt}`;
        return `<span class="ing-chip${live ? ' ing-live' : ''}${ing.returnable ? '' : ' ing-noreturn'}" title="${escapeHtml(title)}">` +
          `<img src="${icon}" loading="lazy" alt="" onerror="this.style.display='none';">` +
          `<b>${ing.count}x</b> ${Number(ing.unit_price).toLocaleString()}${live ? ' 📡' : ''}` +
          (ing.bought_elsewhere ? ` <small style="color:#fbbf24;">🛒 buy in ${escapeHtml(ing.city)}</small>` : '') + `</span>`;
      }).join('') + '</div>';
    }

    function returnRateTitle(c) {
      const rates = c.city_return_rates || {};
      const rows = Object.entries(rates).sort((a, b) => b[1] - a[1]).map(([city, r]) => `${city}: ${(r * 100).toFixed(1)}%`);
      return rows.length ? 'Return rate for this item by crafting city:' + String.fromCharCode(10) + rows.join(String.fromCharCode(10)) : 'No returnable materials in this recipe';
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
      const tabEnchant = document.getElementById('tab-enchanting');
      if (tabEnchant) tabEnchant.className = 'tab-btn' + (tab === 'enchanting' ? ' active' : '');
      const tabWatch = document.getElementById('tab-watchlist');
      if (tabWatch) tabWatch.className = 'tab-btn' + (tab === 'watchlist' ? ' active' : '');
      document.getElementById('tab-prices').className = 'tab-btn' + (tab === 'prices' ? ' active' : '');

      document.getElementById('view-flips').style.display = (tab === 'flips') ? 'block' : 'none';
      document.getElementById('view-blackmarket').style.display = (tab === 'blackmarket') ? 'block' : 'none';
      document.getElementById('view-crafting').style.display = (tab === 'crafting') ? 'block' : 'none';
      const viewEnchant = document.getElementById('view-enchanting');
      if (viewEnchant) viewEnchant.style.display = (tab === 'enchanting') ? 'block' : 'none';
      const viewWatch = document.getElementById('view-watchlist');
      if (viewWatch) viewWatch.style.display = (tab === 'watchlist') ? 'block' : 'none';
      document.getElementById('view-prices').style.display = (tab === 'prices') ? 'block' : 'none';

      const mountBar = document.getElementById('mount-toolbar');
      if (mountBar) mountBar.style.display = (tab === 'flips' || tab === 'blackmarket' || tab === 'watchlist') ? 'flex' : 'none';

      document.getElementById('quick-flips').style.display = (tab === 'flips') ? 'flex' : 'none';
      document.getElementById('quick-blackmarket').style.display = (tab === 'blackmarket') ? 'flex' : 'none';
      document.getElementById('quick-crafting').style.display = (tab === 'crafting') ? 'flex' : 'none';
      const quickEnchant = document.getElementById('quick-enchanting');
      if (quickEnchant) quickEnchant.style.display = (tab === 'enchanting') ? 'flex' : 'none';

      // Update City Filter label and visibility depending on active tab
      const buyCityLabel = document.getElementById('filter-buy-city-label');
      const sellCityCont = document.getElementById('filter-sell-city-container');

      if (tab === 'prices') {
        if (buyCityLabel) buyCityLabel.textContent = 'City / Market:';
        if (sellCityCont) sellCityCont.style.display = 'none';
      } else if (tab === 'blackmarket') {
        if (buyCityLabel) buyCityLabel.textContent = 'Buy City:';
        if (sellCityCont) sellCityCont.style.display = 'none';
      } else if (tab === 'crafting') {
        if (buyCityLabel) buyCityLabel.textContent = 'Craft City:';
        if (sellCityCont) sellCityCont.style.display = 'block';
      } else if (tab === 'enchanting') {
        if (buyCityLabel) buyCityLabel.textContent = 'Foundry City:';
        if (sellCityCont) sellCityCont.style.display = 'block';
      } else {
        if (buyCityLabel) buyCityLabel.textContent = 'Buy City:';
        if (sellCityCont) sellCityCont.style.display = 'block';
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

      if (snifferOnly) {
        if (!item.is_live_sniffer) return false;
        if (type !== 'price' && !(Number(item.profit_per_item) > 0)) return false;
      }
      if (fTier !== 'all' && Number(item.tier) !== Number(fTier)) return false;
      if (fEnchant !== 'all' && Number(item.enchant) !== Number(fEnchant)) return false;
      if (type !== 'crafting' && fQuality !== 'all' && Number(item.quality || 1) !== Number(fQuality)) return false;

      const fAgeElem = document.getElementById('filter-age');
      if (fAgeElem && fAgeElem.value !== 'all') {
        const itemAge = (type === 'flip' || type === 'blackmarket') ? Number(item.data_age_minutes) : Number(item.age_minutes);
        if (!isNaN(itemAge) && itemAge > Number(fAgeElem.value)) return false;
      }

      const fVolElem = document.getElementById('filter-volume');
      if (fVolElem && fVolElem.value !== 'all' && (type === 'flip' || type === 'blackmarket')) {
        const vol = item.avg_daily_volume;
        if (fVolElem.value === 'skip_dead') {
          if (vol === null || vol === undefined || Number(vol) < 1.0) return false;
        } else {
          const minVol = Number(fVolElem.value);
          if (vol === null || vol === undefined || Number(vol) < minVol) return false;
        }
      }

      if (type === 'flip') {
        if (fBuyCity !== 'all' && item.buy_city !== fBuyCity) return false;
        if (fSellCity !== 'all' && item.sell_city !== fSellCity) return false;

        // Quick filters for flips
        if (flipQuickFilter === 'top_picks' && !(item.deal_tier === 'S' || item.deal_tier === 'A')) return false;
        if (flipQuickFilter === 'high_demand' && (item.avg_daily_volume === null || item.avg_daily_volume === undefined || Number(item.avg_daily_volume) < 5.0)) return false;
        if (flipQuickFilter === 'active_vol' && (item.avg_daily_volume === null || item.avg_daily_volume === undefined || Number(item.avg_daily_volume) < 1.0)) return false;
        if (flipQuickFilter === 'instant' && item.exit_type !== 'instant_sell') return false;
        if (flipQuickFilter === 'safe' && (item.risk === 'high' || item.buy_city === 'Caerleon' || item.sell_city === 'Caerleon')) return false;
        if (flipQuickFilter === 'fresh' && Number(item.data_age_minutes) > 60) return false;
        if (flipQuickFilter === 'high_profit' && Number(item.profit_per_item) < 50000) return false;

      } else if (type === 'blackmarket') {
        if (fBuyCity !== 'all' && item.buy_city !== fBuyCity) return false;

        // Quick filters for Black Market
        if (bmQuickFilter === 'high_demand' && (item.avg_daily_volume === null || item.avg_daily_volume === undefined || Number(item.avg_daily_volume) < 5.0)) return false;
        if (bmQuickFilter === 'active_vol' && (item.avg_daily_volume === null || item.avg_daily_volume === undefined || Number(item.avg_daily_volume) < 1.0)) return false;
        if (bmQuickFilter === 'fresh_only' && Number(item.data_age_minutes) > 20) return false;
        if (bmQuickFilter === 'just_arrived' && Number(item.data_age_minutes) > 15) return false;
        if (bmQuickFilter === 'instant' && item.exit_type !== 'instant_sell') return false;
        if (bmQuickFilter === 'royal' && !item.item_id.includes('ROYAL')) return false;
        if (bmQuickFilter === 'weapons' && !item.item_id.includes('MAIN_') && !item.item_id.includes('2H_')) return false;
        if (bmQuickFilter === 'armors' && !item.item_id.includes('ARMOR_') && !item.item_id.includes('HEAD_') && !item.item_id.includes('SHOES_')) return false;
        if (bmQuickFilter === 'accessories' && !item.item_id.includes('BAG') && !item.item_id.includes('CAPE') && !item.item_id.includes('OFF_')) return false;
        if (bmQuickFilter === 'high_profit' && Number(item.profit_per_item) < 100000) return false;

      } else if (type === 'crafting') {
        // Only crafts that make money after the station fee: as Normal, or on average
        // once better quality rolls are counted.
        if (craftBestProfit(item) <= 0) return false;
        if (fBuyCity !== 'all' && item.craft_city !== fBuyCity) return false;
        if (fSellCity !== 'all' && item.sell_city !== fSellCity) return false;

        // Category filter
        if (craftCategoryFilter !== 'all') {
          if (craftCategoryFilter === 'Cape Crafting') {
            if (item.craft_type !== 'Cape Crafting' && item.craft_type !== 'Faction Capes') return false;
          } else if (item.craft_type !== craftCategoryFilter) {
            return false;
          }
        }

      } else {
        if (fBuyCity !== 'all' && item.city !== fBuyCity) return false;
      }

      return true;
    }

    // Robust pricing (server branch): show when a live price was not trusted.
    function priceNoteBadge(f) {
      if (!f || !f.price_note) return '';
      const labels = {bait: 'Bait price', inverted_book: 'Order book mismatch', sell_cap: 'Sell price capped',
                      sell_cap_cross: 'Capped (other cities)', no_history: 'No price history'};
      const parts = f.price_note.split('+').map(k => labels[k] || k);
      const tip = 'Market showed buy ' + (f.raw_buy_price || '-') + ' / sell ' + (f.raw_sell_price || '-') +
        (f.ref_avg7 ? ', 7-day average ' + f.ref_avg7 : '') + '. Calculated with ' + f.buy_price + ' / ' + f.sell_price + '.';
      return ' <span class="badge" style="background:#3b2f12;color:#fbbf24;border:1px solid #92400e" title="' + tip + '">&#9878; ' + parts.join(', ') + '</span>';
    }

    function renderFlips() {
      const tbody = document.getElementById('flips-body');
      let filtered = flipsData.filter(f => passesFilters(f, 'flip'));

      // Sort
      filtered.sort((a, b) => {
        let va, vb;
        if (sortKeyFlips === 'mount_trip') {
          const wA = Math.max(0.05, Number(a.weight) || 0.5);
          const wB = Math.max(0.05, Number(b.weight) || 0.5);
          va = Math.floor(selectedMountCap / wA) * (Number(a.profit_per_item) || 0);
          vb = Math.floor(selectedMountCap / wB) * (Number(b.profit_per_item) || 0);
        } else if (sortKeyFlips === 'profit_per_kg') {
          va = Number(a.profit_per_kg) || ((Number(a.profit_per_item) || 0) / Math.max(0.05, Number(a.weight) || 0.5));
          vb = Number(b.profit_per_kg) || ((Number(b.profit_per_item) || 0) / Math.max(0.05, Number(b.weight) || 0.5));
        } else {
          va = a[sortKeyFlips];
          vb = b[sortKeyFlips];
        }
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
        tbody.innerHTML = '<tr><td colspan="18" style="text-align:center; padding: 32px; color: var(--text-muted);">' +
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
        
        let volDisplay = '';
        if (f.avg_daily_volume !== null && f.avg_daily_volume !== undefined) {
          const v = Number(f.avg_daily_volume);
          if (v >= 50) {
            volDisplay = `<span class="badge" style="background:#14532d; color:#86efac; font-weight:700;" title="🔥 Extremely high demand: ~${Math.round(v)} sold daily">🔥 ${Math.round(v).toLocaleString()}/d</span>`;
          } else if (v >= 10) {
            volDisplay = `<span class="badge" style="background:#1e3a8a; color:#93c5fd; font-weight:600;" title="⚡ Active volume: ~${Math.round(v)} sold daily">⚡ ${Math.round(v).toLocaleString()}/d</span>`;
          } else if (v >= 2) {
            volDisplay = `<span style="color:#e2e8f0;" title="Normal volume: ~${Math.round(v)} sold daily">${Math.round(v).toLocaleString()}/d</span>`;
          } else if (v > 0) {
            volDisplay = `<span style="color:#fbbf24;" title="Slow moving: ~${v.toFixed(1)} sold daily">🐢 ${v.toFixed(1)}/d</span>`;
          } else {
            volDisplay = `<span class="badge" style="background:#450a0a; color:#fca5a5;" title="Dead item: 0 sales in last 3 days">💀 Dead (0/d)</span>`;
          }
        } else {
          volDisplay = '<span style="color:#94a3b8; font-size:0.78rem;" title="Low or unrecorded sales history">💤 No Hist</span>';
        }

        const estDaily = Number(f.est_daily_profit) || 0;
        const estDailyDisplay = estDaily > 0 
          ? `<span class="profit-val" style="font-weight:700;">+${Math.round(estDaily).toLocaleString()}</span>` 
          : '<span style="color:#64748b;">-</span>';
        
        const tierBadge = f.tier ? `<span class="badge badge-tier">${f.tier}.${f.enchant}</span>` : '';
        const qBadge = f.quality ? `<span class="badge badge-q">Q${f.quality}</span>` : '';

        // Deal Rating Badge
        const dealClass = 'deal-' + (f.deal_tier || 'c').toLowerCase();
        const dealBadge = `<span class="badge ${dealClass}">${f.deal_label || 'DEAL'}</span>` + priceNoteBadge(f);

        const buyP = Number(f.buy_price) || 0;
        const sellP = Number(f.sell_price) || 0;
        const profit = Number(f.profit_per_item) || 0;
        const margin = Number(f.margin_pct) || 0;
        const totalP = Number(f.total_profit) || 0;
        const age = Math.round(Number(f.data_age_minutes) || 0);
        const ageClass = (age <= 20) ? 'class="fresh-age"' : '';
        const safeItemName = (f.item_name || f.item_id).replace(/'/g, "\\'");

        // Mount Carry & Silver/kg calculations
        const weight = Math.max(0.05, Number(f.weight) || 0.5);
        const profitKg = Number(f.profit_per_kg) || (profit / weight);
        const maxMountUnits = Math.max(1, Math.floor(selectedMountCap / weight));
        const mountTripProfit = maxMountUnits * profit;

        let densityBadge = '';
        if (profitKg >= 10000) {
          densityBadge = `<span class="badge" style="background:#064e3b; color:#34d399; font-weight:700;" title="High density: ~${Math.round(profitKg).toLocaleString()} Silver/kg (${weight}kg each)">⚡ ${Math.round(profitKg).toLocaleString()}/kg</span>`;
        } else if (profitKg >= 3000) {
          densityBadge = `<span class="badge" style="background:#1e3a8a; color:#93c5fd; font-weight:600;" title="Medium density: ~${Math.round(profitKg).toLocaleString()} Silver/kg (${weight}kg each)">${Math.round(profitKg).toLocaleString()}/kg</span>`;
        } else {
          densityBadge = `<span style="color:#94a3b8;" title="Silver density: ~${Math.round(profitKg).toLocaleString()} Silver/kg (${weight}kg each)">${Math.round(profitKg).toLocaleString()}/kg</span>`;
        }

        const tripDisplay = `<span class="profit-val" title="Full load: ${maxMountUnits.toLocaleString()} units (${weight}kg each) on ${Math.round(selectedMountCap).toLocaleString()}kg mount">+${Math.round(mountTripProfit).toLocaleString()}</span> <small style="color:#64748b;">(${maxMountUnits.toLocaleString()}x)</small>`;

        return `
          <tr>
            <td>${dealBadge}</td>
            <td>${renderItemCell(f.item_id, f.item_name, f.quality)}</td>
            <td>${tierBadge} ${qBadge}</td>
            <td>${f.buy_city || '-'}</td>
            <td>${f.sell_city || '-'}</td>
            <td class="num">${buyP.toLocaleString()}</td>
            <td class="num">
              ${sellP.toLocaleString()}
              <button class="btn-edit-price" onclick="promptOverridePrice('${f.item_id}', '${f.sell_city}', ${f.quality || 1}, ${sellP})" title="Overwrite price if different in-game">✏️</button>
            </td>
            <td>${exitBadge}</td>
            <td class="num profit-val">+${Math.round(profit).toLocaleString()}</td>
            <td class="num margin-val">+${margin.toFixed(1)}%</td>
            <td class="num profit-val">+${Math.round(totalP).toLocaleString()}</td>
            <td class="num">${densityBadge}</td>
            <td class="num">${tripDisplay}</td>
            <td class="num">${estDailyDisplay}</td>
            <td class="num">${volDisplay}</td>
            <td class="num">${renderAgeCell(f)}</td>
            <td><span class="${riskClass}">${(f.risk || 'low').toUpperCase()}</span></td>
            <td style="white-space: nowrap;">
              <button class="btn btn-secondary" style="padding: 3px 8px; font-size: 0.75rem;" onclick="copyItemName('${safeItemName}', this)" title="Copy search name">
                📋 Copy
              </button>
              <button class="btn-fulfill" style="margin-left: 4px;" onclick="markOrderFulfilled('${f.item_id}', '${f.sell_city}', ${f.quality || 1}, this)" title="Order fulfilled or price changed in-game? Click to remove this deal & overwrite price">
                ✓ Fulfilled
              </button>
            </td>
          </tr>
        `;
      }).join('');

    }

    function renderBlackMarket() {
      const tbody = document.getElementById('blackmarket-body');
      let filtered = blackmarketData.filter(f => passesFilters(f, 'blackmarket'));

      // Sort
      filtered.sort((a, b) => {
        let va, vb;
        if (sortKeyBm === 'mount_trip') {
          const wA = Math.max(0.05, Number(a.weight) || 0.5);
          const wB = Math.max(0.05, Number(b.weight) || 0.5);
          va = Math.floor(selectedMountCap / wA) * (Number(a.profit_per_item) || 0);
          vb = Math.floor(selectedMountCap / wB) * (Number(b.profit_per_item) || 0);
        } else if (sortKeyBm === 'profit_per_kg') {
          va = Number(a.profit_per_kg) || ((Number(a.profit_per_item) || 0) / Math.max(0.05, Number(a.weight) || 0.5));
          vb = Number(b.profit_per_kg) || ((Number(b.profit_per_item) || 0) / Math.max(0.05, Number(b.weight) || 0.5));
        } else {
          va = a[sortKeyBm];
          vb = b[sortKeyBm];
        }
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
        tbody.innerHTML = '<tr><td colspan="16" style="text-align:center; padding: 32px; color: var(--text-muted);">' +
          'No Black Market deals match the selected filters.<br><small style="margin-top:8px; display:inline-block; color:#64748b;">(Try clicking "All BM Deals" or selecting "Any Age")</small>' +
          '</td></tr>';
        return;
      }

      tbody.innerHTML = pageSlice.map(f => {
        const isOrder = (f.exit_type === 'sell_order');
        const exitBadge = isOrder 
          ? '<span class="badge badge-order" title="Sell Order listed on Black Market">Sell Order</span>' 
          : '<span class="badge badge-instant" title="Instant Payout: Sold directly into Black Market Buy Order">Instant Sell ⚡</span>';
        
        let volDisplay = '';
        if (f.avg_daily_volume !== null && f.avg_daily_volume !== undefined) {
          const v = Number(f.avg_daily_volume);
          if (v >= 50) {
            volDisplay = `<span class="badge" style="background:#14532d; color:#86efac; font-weight:700;" title="🔥 Extremely high Black Market demand: ~${Math.round(v)} bought daily">🔥 ${Math.round(v).toLocaleString()}/d</span>`;
          } else if (v >= 10) {
            volDisplay = `<span class="badge" style="background:#1e3a8a; color:#93c5fd; font-weight:600;" title="⚡ Active Black Market: ~${Math.round(v)} bought daily">⚡ ${Math.round(v).toLocaleString()}/d</span>`;
          } else if (v >= 2) {
            volDisplay = `<span style="color:#e2e8f0;" title="Normal volume: ~${Math.round(v)} bought daily">${Math.round(v).toLocaleString()}/d</span>`;
          } else if (v > 0) {
            volDisplay = `<span style="color:#fbbf24;" title="Slow moving: ~${v.toFixed(1)} bought daily">🐢 ${v.toFixed(1)}/d</span>`;
          } else {
            volDisplay = `<span class="badge" style="background:#450a0a; color:#fca5a5;" title="Dead item: 0 sales in last 3 days">💀 Dead (0/d)</span>`;
          }
        } else {
          volDisplay = '<span style="color:#94a3b8; font-size:0.78rem;" title="Low or unrecorded sales history">💤 No Hist</span>';
        }

        const estDaily = Number(f.est_daily_profit) || 0;
        const estDailyDisplay = estDaily > 0 
          ? `<span class="profit-val" style="font-weight:700;">+${Math.round(estDaily).toLocaleString()}</span>` 
          : '<span style="color:#64748b;">-</span>';
        
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
        const dealBadge = `<span class="badge ${dealClass}">${f.deal_label || 'DEAL'}</span>` + priceNoteBadge(f);

        const buyP = Number(f.buy_price) || 0;
        const sellP = Number(f.sell_price) || 0;
        const profit = Number(f.profit_per_item) || 0;
        const margin = Number(f.margin_pct) || 0;
        const totalP = Number(f.total_profit) || 0;

        // Mount Carry & Silver/kg calculations
        const weight = Math.max(0.05, Number(f.weight) || 0.5);
        const profitKg = Number(f.profit_per_kg) || (profit / weight);
        const maxMountUnits = Math.max(1, Math.floor(selectedMountCap / weight));
        const mountTripProfit = maxMountUnits * profit;

        let densityBadge = '';
        if (profitKg >= 10000) {
          densityBadge = `<span class="badge" style="background:#064e3b; color:#34d399; font-weight:700;" title="High silver density: ~${Math.round(profitKg).toLocaleString()} Silver/kg (${weight}kg each)">⚡ ${Math.round(profitKg).toLocaleString()}/kg</span>`;
        } else if (profitKg >= 3000) {
          densityBadge = `<span class="badge" style="background:#1e3a8a; color:#93c5fd; font-weight:600;" title="Medium silver density: ~${Math.round(profitKg).toLocaleString()} Silver/kg (${weight}kg each)">${Math.round(profitKg).toLocaleString()}/kg</span>`;
        } else {
          densityBadge = `<span style="color:#94a3b8;" title="Silver density: ~${Math.round(profitKg).toLocaleString()} Silver/kg (${weight}kg each)">${Math.round(profitKg).toLocaleString()}/kg</span>`;
        }

        const tripDisplay = `<span class="profit-val" title="Full load: ${maxMountUnits.toLocaleString()} units (${weight}kg each) on ${Math.round(selectedMountCap).toLocaleString()}kg mount">+${Math.round(mountTripProfit).toLocaleString()}</span> <small style="color:#64748b;">(${maxMountUnits.toLocaleString()}x)</small>`;

        const safeItemName = (f.item_name || f.item_id).replace(/'/g, "\\'");

        return `
          <tr>
            <td>${dealBadge}</td>
            <td>${renderItemCell(f.item_id, f.item_name, f.quality)}</td>
            <td>${tierBadge} ${qBadge} ${newArrivalBadge}</td>
            <td><strong>${f.buy_city || '-'}</strong></td>
            <td class="num">${buyP.toLocaleString()}</td>
            <td class="num" style="color: #60a5fa; font-weight: 600;">
              ${sellP.toLocaleString()}
              <button class="btn-edit-price" onclick="promptOverridePrice('${f.item_id}', '${f.sell_city || 'Black Market'}', ${f.quality || 1}, ${sellP})" title="Overwrite price if lower/different in-game">✏️</button>
            </td>
            <td>${exitBadge}</td>
            <td class="num profit-val">+${Math.round(profit).toLocaleString()}</td>
            <td class="num margin-val">+${margin.toFixed(1)}%</td>
            <td class="num profit-val">+${Math.round(totalP).toLocaleString()}</td>
            <td class="num">${densityBadge}</td>
            <td class="num">${tripDisplay}</td>
            <td class="num">${estDailyDisplay}</td>
            <td class="num">${volDisplay}</td>
            <td class="num">${renderAgeCell(f, true)}</td>
            <td style="white-space: nowrap;">
              <button class="btn btn-secondary" style="padding: 3px 8px; font-size: 0.75rem;" onclick="copyItemName('${safeItemName}', this)" title="Copy in-game search name">
                📋 Copy
              </button>
              <button class="btn-fulfill" style="margin-left: 4px;" onclick="markOrderFulfilled('${f.item_id}', '${f.sell_city || 'Black Market'}', ${f.quality || 1}, this)" title="Order fulfilled or gone in-game? Click to remove this deal & overwrite price">
                ✓ Fulfilled
              </button>
            </td>
          </tr>
        `;
      }).join('');

    }

    function craftStationCost(c) {
      const itemVal = Number(c.item_value) || 64.0;
      return itemVal * 0.1125 * (currentStationFee / 100.0);
    }

    function craftNetProfit(c) {
      return (Number(c.profit_per_item) || 0) - craftStationCost(c);
    }

    const QUALITY_ODDS = [0.689, 0.25, 0.05, 0.01, 0.001];
    const QUALITY_NAMES = ['Normal', 'Good', 'Outstanding', 'Excellent', 'Masterpiece'];

    function qualityChances(rolls) {
      // Best of `rolls` rolls is kept.
      const out = []; let cum = 0, prev = 0;
      for (const odd of QUALITY_ODDS) {
        cum = Math.min(1, cum + odd);
        const best = Math.pow(cum, rolls);
        out.push(best - prev); prev = best;
      }
      return out;
    }

    function craftAvgProfit(c) {
      const nets = c.quality_nets || [];
      if (!nets.length) return craftNetProfit(c);
      const ch = qualityChances(qualityRolls);
      const revenue = nets.reduce((sum, n, i) => sum + (ch[i] || 0) * (Number(n) || 0), 0);
      return revenue - (Number(c.effective_cost) || 0) - craftStationCost(c);
    }

    function craftBestProfit(c) {
      return Math.max(c.sell_city ? craftNetProfit(c) : -Infinity, craftAvgProfit(c));
    }

    function qualityBreakdown(c) {
      const nets = c.quality_nets || [];
      if (!nets.length) return '';
      const ch = qualityChances(qualityRolls);
      const cost = (Number(c.effective_cost) || 0) + craftStationCost(c);
      const nl = String.fromCharCode(10);
      return `If it comes out (${qualityRolls} roll${qualityRolls > 1 ? 's' : ''}):` + nl + nets.map((n, i) => {
        const where = (c.quality_exits || [])[i] || 'no buyer';
        const prof = (Number(n) || 0) - cost;
        return `${QUALITY_NAMES[i]} (${(ch[i] * 100).toFixed(ch[i] < 0.01 ? 2 : 1)}%): ${prof >= 0 ? '+' : ''}${Math.round(prof).toLocaleString()}  [${where}]`;
      }).join(nl);
    }

    function setQualityRolls(v) {
      qualityRolls = Math.min(4, Math.max(1, parseInt(v) || 1));
      try { localStorage.setItem('albion_quality_rolls', String(qualityRolls)); } catch (e) {}
      currentPage = 1;
      renderCurrentView();
    }

    function craftForCity(c, city) {
      // The row shows the cheapest craft city; with a Craft City filter, show that city's numbers.
      if (city === 'all' || c.craft_city === city) return c;
      const opt = (c.city_options || []).find(o => o.city === city);
      if (!opt) return null;
      const ingredients = (c.ingredients || []).map((ing, i) => ({
        ...ing, unit_price: opt.prices[i], city: opt.cities[i],
        is_live_sniffer: !!(opt.sniffed && opt.sniffed[i]), sniffer_age_seconds: null,
      }));
      return {
        ...c, craft_city: city, effective_cost: opt.effective_cost, profit_per_item: opt.profit_per_item,
        margin_pct: opt.margin_pct, resource_return_rate: opt.resource_return_rate, returned_value: opt.returned_value,
        city_bonus: opt.city_bonus, ingredients,
        // per item, like the server's value (recipes can make several items)
        material_cost: c.material_cost * ingredients.reduce((sum, ing) => sum + ing.count * ing.unit_price, 0)
          / Math.max(1, (c.ingredients || []).reduce((sum, ing) => sum + ing.count * ing.unit_price, 0)),
        is_live_sniffer: ingredients.some(ing => ing.is_live_sniffer) || c.output_sniffed,
      };
    }

    function renderCrafting() {
      const tbody = document.getElementById('crafting-body');
      // Sniffer mode: every material price comes from a sniffer scan in the craft city
      // (artifacts / royal sigils from whichever sniffed city is cheapest).
      const source = snifferOnly
        ? (isFocusMode ? snifferFocusCraftingData : snifferCraftingData)
        : (isFocusMode ? focusCraftingData : craftingData);
      const craftCity = document.getElementById('filter-buy-city').value;
      let filtered = source.map(c => craftForCity(c, craftCity)).filter(c => c && passesFilters(c, 'crafting'));

      filtered.sort((a, b) => {
        const craftSortValue = (c) => sortKeyCrafting === 'profit_per_item' ? (c.sell_city ? craftNetProfit(c) : -Infinity)
          : sortKeyCrafting === 'avg_profit' ? craftAvgProfit(c) : c[sortKeyCrafting];
        let va = craftSortValue(a);
        let vb = craftSortValue(b);
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
          (snifferOnly
            ? 'No profitable crafts from sniffer prices yet.<br><small style="margin-top:8px; display:inline-block; color:#64748b;">(Open the market in the city you craft in and look at each material; in this mode every material price must come from your sniffer)</small>'
            : 'No crafting recipes match your current filters.<br><small style="margin-top:8px; display:inline-block; color:#64748b;">(Try selecting "All Recipes" or clearing search)</small>') +
          '</td></tr>';
        return;
      }

      tbody.innerHTML = pageSlice.map(c => {
        const tierBadge = c.tier ? `<span class="badge badge-tier">${c.tier}.${c.enchant}</span>` : '';
        const bonusTag = c.city_bonus ? '<span class="badge badge-bonus">Bonus City</span>' : '';
        const rrrPct = (c.resource_return_rate * 100).toFixed(1);
        
        // Station usage fee calculation
        const stationCost = craftStationCost(c);
        const netProfit = craftNetProfit(c);
        const effectiveCost = Number(c.effective_cost) || 1;
        const netMargin = (effectiveCost > 0) ? (netProfit / effectiveCost * 100) : 0;

        return `
          <tr>
            <td>${renderItemCell(c.item_id, c.item_name, 1)}${c.is_live_sniffer ? `<div><span class="badge" style="background:#0c4a6e; color:#7dd3fc; font-size:0.7rem;" title="${c.output_sniffed ? 'Sell price' : 'Material price(s)'} captured by your sniffer">📡 Sniffer ${Math.round((c.sniffer_age_seconds || 0) / 60)}m ago</span></div>` : ''}</td>
            <td>${tierBadge} <span class="badge badge-q">${c.craft_type}</span></td>
            <td title="${escapeHtml(returnRateTitle(c))}"><strong>${c.craft_city}</strong> ${bonusTag}
              <div style="font-size:0.74rem; color:#a7f3d0;">Return rate ${rrrPct}%</div>
            </td>
            <td>
              ${renderIngredients(c)}
              <div style="font-size:0.74rem; color:#64748b;">Raw Mat: ${Math.round(c.material_cost).toLocaleString()} | ${rrrPct}% returned: -${Math.round(c.returned_value || 0).toLocaleString()}${c.craft_silver ? ` | silver +${Math.round(c.craft_silver).toLocaleString()}` : ''}</div>
            </td>
            <td class="num" title="Material cost after the ${rrrPct}% resource return (plus crafting silver), per item">${Math.round(c.effective_cost).toLocaleString()}</td>
            <td>${c.sell_city ? `<strong>${c.sell_city}</strong>` : `<strong style="color:#fbbf24;" title="${escapeHtml(qualityBreakdown(c))}">🎲 Better qualities only</strong>`}
              <div style="font-size:0.74rem; color:${c.daily_volume == null ? '#64748b' : (c.daily_volume >= 5 ? '#4ade80' : (c.daily_volume >= 1 ? '#fbbf24' : '#f87171'))};">${c.daily_volume == null ? 'no sales data' : '~' + c.daily_volume.toLocaleString() + ' sold/day'}</div>
            </td>
            <td class="num">${c.sell_city ? Math.round(c.sell_price).toLocaleString() : '<span style="color:#64748b;">no Normal buyer</span>'}
              ${c.sell_city ? `<br><small style="color:#64748b;">${c.sell_instant ? 'instant sell' : 'sell order'}</small>` : ''}</td>
            <td class="num ${netProfit >= 0 ? 'profit-val' : 'stale-data'}">
              ${netProfit >= 0 ? '+' : ''}${Math.round(netProfit).toLocaleString()}
              ${(() => { const avg = craftAvgProfit(c); const onlyAvg = !c.sell_city || netProfit <= 0;
                return `<br><small style="color:${avg >= 0 ? '#c4b5fd' : '#f87171'}; cursor:help;" title="${escapeHtml(qualityBreakdown(c))}">🎲 avg ${avg >= 0 ? '+' : ''}${Math.round(avg).toLocaleString()}${onlyAvg ? ' (gamble on quality)' : ''}</small>`; })()}
              <br><small style="color:#f87171;" title="${c.sell_instant ? 'Market tax' : 'Market tax + 2.5% setup fee'} at ${(marketTaxRate * 100).toFixed(0)}% (${premiumTax ? 'Premium' : 'no Premium'}) already deducted">tax -${Math.round(c.tax_paid || 0).toLocaleString()}</small>
              ${stationCost > 0 ? `<br><small style="color:#f59e0b;" title="Station usage fee deducted at ${currentStationFee}/100 nutrition">station -${Math.round(stationCost).toLocaleString()}</small>` : ''}
            </td>
            <td class="num margin-val">${netMargin >= 0 ? '+' : ''}${netMargin.toFixed(1)}%</td>
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
            <td class="num">${renderAgeCell(p)}</td>
            <td>${statusBadge}</td>
          </tr>
        `;
      }).join('');
    }

    function sortWatchlist(key, isNum = false) {
      if (sortKeyWatchlist === key) {
        sortAscWatchlist = !sortAscWatchlist;
      } else {
        sortKeyWatchlist = key;
        sortAscWatchlist = !isNum;
      }
      renderWatchlist();
    }

    function renderWatchlist() {
      const tbody = document.getElementById('watchlist-body');
      if (!tbody) return;

      const allDeals = [...flipsData, ...blackmarketData];
      const seen = new Set();
      const dedupedDeals = [];
      for (const d of allDeals) {
        const k = d.item_id + '|' + d.buy_city + '|' + d.sell_city + '|' + (d.quality || 1);
        if (!seen.has(k)) {
          seen.add(k);
          dedupedDeals.push(d);
        }
      }

      let filtered = dedupedDeals.filter(f => isStarred(f.item_id));

      filtered.sort((a, b) => {
        let va, vb;
        if (sortKeyWatchlist === 'mount_trip') {
          const wA = Math.max(0.05, Number(a.weight) || 0.5);
          const wB = Math.max(0.05, Number(b.weight) || 0.5);
          va = Math.floor(selectedMountCap / wA) * (Number(a.profit_per_item) || 0);
          vb = Math.floor(selectedMountCap / wB) * (Number(b.profit_per_item) || 0);
        } else if (sortKeyWatchlist === 'profit_per_kg') {
          va = Number(a.profit_per_kg) || ((Number(a.profit_per_item) || 0) / Math.max(0.05, Number(a.weight) || 0.5));
          vb = Number(b.profit_per_kg) || ((Number(b.profit_per_item) || 0) / Math.max(0.05, Number(b.weight) || 0.5));
        } else {
          va = a[sortKeyWatchlist];
          vb = b[sortKeyWatchlist];
        }
        if (typeof va === 'string') va = va.toLowerCase();
        if (typeof vb === 'string') vb = vb.toLowerCase();
        if (va < vb) return sortAscWatchlist ? -1 : 1;
        if (va > vb) return sortAscWatchlist ? 1 : -1;
        return 0;
      });

      const totalItems = filtered.length;
      const totalPages = Math.max(1, Math.ceil(totalItems / pageSize));
      if (currentPage > totalPages) currentPage = totalPages;

      const startIndex = (currentPage - 1) * pageSize;
      const pageSlice = filtered.slice(startIndex, startIndex + pageSize);

      updatePagination(totalItems, startIndex, pageSlice.length, totalPages);

      renderWatchlistExtra(filtered);

      if (watchlist.size === 0) {
        tbody.innerHTML = '<tr><td colspan="17" style="text-align:center; padding: 40px; color: var(--text-muted);">' +
          '<div style="font-size:1.5rem; margin-bottom:8px;">⭐</div>' +
          '<strong>Your Watchlist is currently empty.</strong><br>' +
          '<span style="color:#64748b; font-size:0.84rem; display:inline-block; margin-top:6px;">Click the ★ star icon next to any item in Flips, Black Market, Crafting or Enchanting to follow it here!</span>' +
          '</td></tr>';
        return;
      }
      if (filtered.length === 0) {
        tbody.innerHTML = '<tr><td colspan="17" style="text-align:center; padding: 18px; color: var(--text-muted);">' +
          'No flip or Black Market deal for your starred items right now. Their other deals and latest prices are listed below.</td></tr>';
        return;
      }

      tbody.innerHTML = pageSlice.map(f => {
        const isOrder = (f.exit_type === 'sell_order');
        const exitBadge = isOrder 
          ? '<span class="badge badge-order">Sell Order</span>' 
          : '<span class="badge badge-instant">Instant Sell</span>';

        const buyP = Number(f.buy_price) || 0;
        const sellP = Number(f.sell_price) || 0;
        const profit = Number(f.profit_per_item) || 0;
        const margin = Number(f.margin_pct) || 0;
        const totalP = Number(f.total_profit) || 0;
        const age = Math.round(Number(f.data_age_minutes) || 0);
        const ageClass = (age <= 20) ? 'class="fresh-age"' : '';

        // Deal Rating Badge
        const dealClass = 'deal-' + (f.deal_tier || 'c').toLowerCase();
        const dealBadge = `<span class="badge ${dealClass}">${f.deal_label || 'DEAL'}</span>` + priceNoteBadge(f);

        // Mount Carry & Silver/kg calculations
        const weight = Math.max(0.05, Number(f.weight) || 0.5);
        const profitKg = Number(f.profit_per_kg) || (profit / weight);
        const maxMountUnits = Math.max(1, Math.floor(selectedMountCap / weight));
        const mountTripProfit = maxMountUnits * profit;

        let densityBadge = '';
        if (profitKg >= 10000) {
          densityBadge = `<span class="badge" style="background:#064e3b; color:#34d399; font-weight:700;" title="High density: ~${Math.round(profitKg).toLocaleString()} Silver/kg">⚡ ${Math.round(profitKg).toLocaleString()}/kg</span>`;
        } else if (profitKg >= 3000) {
          densityBadge = `<span class="badge" style="background:#1e3a8a; color:#93c5fd; font-weight:600;">${Math.round(profitKg).toLocaleString()}/kg</span>`;
        } else {
          densityBadge = `<span style="color:#94a3b8;">${Math.round(profitKg).toLocaleString()}/kg</span>`;
        }

        const tripDisplay = `<span class="profit-val">+${Math.round(mountTripProfit).toLocaleString()}</span> <small style="color:#64748b;">(${maxMountUnits.toLocaleString()}x)</small>`;
        const safeItemName = (f.item_name || f.item_id).replace(/'/g, "\\'");
        const vol = f.avg_daily_volume ? `${Math.round(f.avg_daily_volume)}/d` : '-';

        return `
          <tr>
            <td>${dealBadge}</td>
            <td>${renderItemCell(f.item_id, f.item_name, f.quality)}</td>
            <td><span class="badge badge-tier">${f.tier || 4}.${f.enchant || 0}</span></td>
            <td>${f.buy_city || '-'}</td>
            <td><strong>${f.sell_city || '-'}</strong></td>
            <td class="num">${buyP.toLocaleString()}</td>
            <td class="num">${sellP.toLocaleString()}</td>
            <td>${exitBadge}</td>
            <td class="num profit-val">+${Math.round(profit).toLocaleString()}</td>
            <td class="num margin-val">+${margin.toFixed(1)}%</td>
            <td class="num profit-val">+${Math.round(totalP).toLocaleString()}</td>
            <td class="num">${densityBadge}</td>
            <td class="num">${tripDisplay}</td>
            <td class="num">+${Math.round(f.est_daily_profit || 0).toLocaleString()}</td>
            <td class="num">${vol}</td>
            <td class="num">${renderAgeCell(f)}</td>
            <td>
              <button class="btn btn-secondary" style="padding: 3px 8px; font-size: 0.75rem;" onclick="copyItemName('${safeItemName}', this)" title="Copy search name">
                📋 Copy
              </button>
            </td>
          </tr>
        `;
      }).join('');
    }

    function renderWatchlistExtra(flipRows) {
      const box = document.getElementById('watchlist-extra');
      if (!box) return;
      if (watchlist.size === 0) { box.innerHTML = ''; return; }
      const fmt = v => Math.round(Number(v) || 0).toLocaleString();
      const crafts = craftingData.filter(c => watchlist.has(c.item_id));
      const enchants = enchantingData.filter(e => e.is_profitable && (watchlist.has(e.base_item_id) || watchlist.has(e.target_item_id)));
      const withDeal = new Set([...flipRows.map(f => f.item_id), ...crafts.map(c => c.item_id),
                                ...enchants.flatMap(e => [e.base_item_id, e.target_item_id])]);
      const names = { ...watchlistNames };
      [...flipsData, ...blackmarketData, ...craftingData, ...pricesData].forEach(r => { if (r.item_id && r.item_name) names[r.item_id] = r.item_name; });
      enchantingData.forEach(e => { names[e.base_item_id] = names[e.base_item_id] || e.item_name; });
      const section = (title, head, rows) => rows.length ? `
        <h3 style="margin:18px 0 8px; font-size:0.95rem; color:#fde68a;">${title}</h3>
        <table><thead><tr>${head.map(h => `<th>${h}</th>`).join('')}</tr></thead><tbody>${rows.join('')}</tbody></table>` : '';

      const craftRows = crafts.map(c => {
        const net = craftNetProfit(c);
        return `<tr><td>${renderItemCell(c.item_id, c.item_name, 1)}</td><td><strong>${escapeHtml(c.craft_city)}</strong></td>
          <td>${renderIngredients(c)}</td><td class="num">${fmt(c.effective_cost)}</td><td>${escapeHtml(c.sell_city)}</td>
          <td class="num">${fmt(c.sell_price)}</td><td class="num ${net >= 0 ? 'profit-val' : 'stale-data'}">${net >= 0 ? '+' : ''}${fmt(net)}</td></tr>`;
      });
      const enchantRows = enchants.map(e => `<tr><td>${renderItemCell(e.base_item_id, e.item_name, 1)}</td><td>${escapeHtml(e.step_label)}</td>
          <td>${escapeHtml(e.city)}</td><td class="num">${fmt(e.total_cost)}</td><td>${escapeHtml(e.sell_city)}</td>
          <td class="num">${fmt(e.sell_price)}</td><td class="num ${e.is_profitable ? 'profit-val' : 'stale-data'}">${e.profit_per_item > 0 ? '+' : ''}${fmt(e.profit_per_item)}</td></tr>`);

      const qNames = ['', 'Normal', 'Good', 'Outstanding', 'Excellent', 'Masterpiece'];
      const priceRows = Array.from(watchlist).filter(id => !withDeal.has(id)).map(id => {
        const quotes = watchlistPrices[id] || [];
        const list = quotes.length
          ? quotes.map(q => `<div style="font-size:0.8rem; white-space:nowrap;"><strong>${escapeHtml(q.city)}</strong>${q.quality > 1 ? ` <small style="color:#94a3b8;">${qNames[q.quality] || 'Q' + q.quality}</small>` : ''}: ` +
              `sell ${q.sell_price_min ? fmt(q.sell_price_min) : '-'} / buy ${q.buy_price_max ? fmt(q.buy_price_max) : '-'}` +
              `${q.age_minutes != null ? ` <small style="color:#64748b;">(${q.age_minutes < 60 ? q.age_minutes + 'm' : Math.round(q.age_minutes / 60) + 'h'} ago)</small>` : ''}</div>`).join('')
          : '<span style="color:#64748b; font-size:0.8rem;">No prices yet: open this item in the in-game market (sniffer) or press Fetch Live Data.</span>';
        return `<tr><td>${renderItemCell(id, names[id] || id, 1)}</td><td>${list}</td></tr>`;
      });

      box.innerHTML =
        section('🔨 Crafting deals', ['Item', 'Craft City', 'Materials', 'Mat Cost', 'Sell City', 'Sell Price', 'Profit'], craftRows) +
        section('🔮 Enchanting deals', ['Item', 'Step', 'Buy City', 'Total Cost', 'Sell City', 'Sell Price', 'Profit'], enchantRows) +
        section('📡 Starred items with no deal right now (latest prices)', ['Item', 'Prices by city (lowest sell order / highest buy order)'], priceRows);
    }

    function setEnchantBatch(qty, btn) {
      const q = Math.max(1, parseInt(qty) || 10);
      enchantBatchSize = q;
      document.querySelectorAll('.batch-btn').forEach(b => b.classList.remove('active'));
      if (btn) {
        btn.classList.add('active');
        const customInput = document.getElementById('custom-batch-input');
        if (customInput) customInput.value = '';
      }
      const thMass = document.getElementById('th-mass-profit');
      if (thMass) thMass.innerHTML = `Mass Profit (${enchantBatchSize}x) ⚡`;
      renderEnchanting();
    }

    function setEnchantStep(step, btn) {
      enchantStepFilter = step;
      document.querySelectorAll('#quick-enchanting button').forEach(b => {
        if (b.getAttribute('onclick') && b.getAttribute('onclick').includes('setEnchantStep')) {
          b.classList.remove('active');
        }
      });
      if (btn) btn.classList.add('active');
      currentPage = 1;
      renderEnchanting();
    }

    function setEnchantSlot(slot, btn) {
      enchantSlotFilter = slot;
      document.querySelectorAll('#quick-enchanting button').forEach(b => {
        if (b.getAttribute('onclick') && b.getAttribute('onclick').includes('setEnchantSlot')) {
          b.classList.remove('active');
        }
      });
      if (btn) btn.classList.add('active');
      currentPage = 1;
      renderEnchanting();
    }

    function setEnchantStatus(status, btn) {
      enchantStatusFilter = status;
      document.querySelectorAll('#quick-enchanting button').forEach(b => {
        if (b.getAttribute('onclick') && b.getAttribute('onclick').includes('setEnchantStatus')) {
          b.classList.remove('active');
        }
      });
      if (btn) btn.classList.add('active');
      currentPage = 1;
      renderEnchanting();
    }

    function setEnchantDemand(demand, btn) {
      enchantDemandFilter = demand;
      document.querySelectorAll('#quick-enchanting button').forEach(b => {
        if (b.getAttribute('onclick') && b.getAttribute('onclick').includes('setEnchantDemand')) {
          b.classList.remove('active');
        }
      });
      if (btn) btn.classList.add('active');
      currentPage = 1;
      renderEnchanting();
    }

    function sortEnchanting(key, isNum = false) {
      if (sortKeyEnchant === key) {
        sortAscEnchant = !sortAscEnchant;
      } else {
        sortKeyEnchant = key;
        sortAscEnchant = !isNum;
      }
      renderEnchanting();
    }

    function renderArtifactMats() {
      const grid = document.getElementById('artifact-mats-grid');
      if (!grid) return;
      if (!artifactMatsData || Object.keys(artifactMatsData).length === 0) {
        grid.innerHTML = '<div style="color: #64748b; font-size: 0.8rem; padding: 4px;">Awaiting live artifact material prices... (Will populate automatically upon market scan)</div>';
        return;
      }

      const matsList = Object.values(artifactMatsData);
      matsList.sort((a, b) => {
        if (a.tier !== b.tier) return a.tier - b.tier;
        const typeOrder = { rune: 1, soul: 2, relic: 3 };
        return (typeOrder[a.type] || 4) - (typeOrder[b.type] || 4);
      });

      grid.innerHTML = matsList.map(m => {
        const typeColors = {
          rune: '#4ade80',
          soul: '#60a5fa',
          relic: '#c084fc',
        };
        const dotColor = typeColors[m.type] || '#cbd5e1';
        const sourceBadge = (m.source === 'live')
          ? '<span class="badge badge-sniffer" style="padding:1px 5px; font-size:0.65rem;" title="Live direct packet scan">⚡ Live</span>'
          : ((m.source === 'override') ? '<span class="badge" style="background:#451a03; color:#fde047; padding:1px 5px; font-size:0.65rem;">✏️ Custom</span>' : '');

        const safeMatName = escapeHtml(m.name);
        return `
          <div class="mat-chip" title="${safeMatName} (${m.id}) in ${m.city || 'Market'}">
            <div style="display: flex; align-items: center; gap: 6px; overflow: hidden;">
              <span style="display:inline-block; width:8px; height:8px; border-radius:50%; background:${dotColor}; flex-shrink:0;"></span>
              <span class="mat-chip-name" style="white-space: nowrap; text-overflow: ellipsis; overflow: hidden;">T${m.tier} ${m.type.toUpperCase()}</span>
              ${sourceBadge}
            </div>
            <div style="display: flex; align-items: center; gap: 4px;">
              <span class="mat-chip-price">${m.price ? m.price.toLocaleString() : '-'}s</span>
              <button class="btn-edit-price" onclick="promptOverridePrice('${m.id}', '${m.city || 'Martlock'}', 1, ${m.price || 0})" title="Override price for ${safeMatName}">✏️</button>
            </div>
          </div>
        `;
      }).join('');
    }

    function passesEnchantFilters(e) {
      const search = (document.getElementById('search-box')?.value || '').toLowerCase().trim();
      const tierFilter = document.getElementById('filter-tier')?.value || 'all';
      const cityFilter = document.getElementById('filter-buy-city')?.value || 'all';
      const sellCityFilter = document.getElementById('filter-sell-city')?.value || 'all';

      if (search) {
        const name = (e.item_name || '').toLowerCase();
        const id = (e.base_item_id || '').toLowerCase();
        const tid = (e.target_item_id || '').toLowerCase();
        const mat = (e.enchant_mat_name || '').toLowerCase();
        if (!name.includes(search) && !id.includes(search) && !tid.includes(search) && !mat.includes(search)) {
          return false;
        }
      }

      if (snifferOnly && !(e.is_live_sniffer && e.is_profitable)) {
        return false;
      }

      if (tierFilter !== 'all' && String(e.tier) !== tierFilter) {
        return false;
      }

      if (cityFilter !== 'all' && e.city.toLowerCase() !== cityFilter.toLowerCase()) {
        return false;
      }

      if (sellCityFilter !== 'all' && e.sell_city.toLowerCase() !== sellCityFilter.toLowerCase()) {
        return false;
      }

      if (enchantStepFilter !== 'all') {
        const step = `${e.from_enchant}->${e.to_enchant}`;
        if (step !== enchantStepFilter) return false;
      }

      if (enchantSlotFilter !== 'all') {
        if (enchantSlotFilter === 'Weapon') {
          if (!e.item_type.includes('Weapon')) return false;
        } else if (e.item_type !== enchantSlotFilter) {
          return false;
        }
      }

      if (enchantStatusFilter === 'profitable') {
        if (!e.is_profitable) return false;
      } else if (enchantStatusFilter === 'high') {
        if (!e.is_profitable || e.margin_pct < 20) return false;
      }

      if (enchantDemandFilter === 'active') {
        if (e.liquidity_status === 'dead' || (e.target_daily_volume !== null && e.target_daily_volume < 1.0)) {
          return false;
        }
      } else if (enchantDemandFilter === 'high') {
        if (e.target_daily_volume === null || e.target_daily_volume < 10.0) {
          return false;
        }
      }

      return true;
    }

    function renderLiquidityBadge(vol, status) {
      if (vol === null || vol === undefined) {
        return `<span style="color: #64748b; font-size: 0.78rem;" title="No sales history recorded yet. Click chart/graph icon in-game to sniff 28-day history">❓ Untracked</span>`;
      }
      if (status === 'dead' || vol < 1.0) {
        return `<div style="display: flex; flex-direction: column; align-items: flex-end; gap: 2px;">
          <span style="color: #f87171; font-weight: 700; font-size: 0.83rem;">${vol.toFixed(1)}/d</span>
          <span class="badge" style="background: #450a0a; color: #fca5a5; font-size: 0.65rem; border: 1px solid #7f1d1d;" title="Dead item! Very low or zero daily sales volume. High risk of unsold stock!">⚠️ DEAD ITEM</span>
        </div>`;
      }
      if (status === 'high' || vol >= 20.0) {
        return `<div style="display: flex; flex-direction: column; align-items: flex-end; gap: 2px;">
          <span style="color: #34d399; font-weight: 700; font-size: 0.85rem;">🔥 ${Math.round(vol)}/d</span>
          <span class="badge" style="background: #064e3b; color: #6ee7b7; font-size: 0.65rem; border: 1px solid #059669;" title="High demand item! Rapid turnover.">HIGH DEMAND</span>
        </div>`;
      }
      if (status === 'active' || vol >= 5.0) {
        return `<div style="display: flex; flex-direction: column; align-items: flex-end; gap: 2px;">
          <span style="color: #60a5fa; font-weight: 700; font-size: 0.85rem;">📈 ${Math.round(vol)}/d</span>
          <span class="badge" style="background: #1e3a8a; color: #93c5fd; font-size: 0.65rem;" title="Active trade velocity.">ACTIVE</span>
        </div>`;
      }
      return `<div style="display: flex; flex-direction: column; align-items: flex-end; gap: 2px;">
        <span style="color: #fbbf24; font-size: 0.83rem;">🟡 ${vol.toFixed(1)}/d</span>
        <span class="badge" style="background: #451a03; color: #fde047; font-size: 0.65rem;" title="Slow turnover. Enchant in small batches.">SLOW</span>
      </div>`;
    }

    function renderSnifferRadar(events) {
      const pingEl = document.getElementById('radar-last-ping');
      const latestEl = document.getElementById('radar-latest-event');
      const recentEl = document.getElementById('radar-recent-events');
      if (!latestEl || !recentEl) return;

      if (!events || events.length === 0) {
        if (pingEl) pingEl.textContent = 'Awaiting live market packets...';
        latestEl.innerHTML = '<div style="color: #94a3b8; font-size: 0.82rem;">📡 <em>Open your in-game market and click an item or click the chart/graph icon to sniff live prices and sales velocity instantly.</em></div>';
        recentEl.innerHTML = '';
        return;
      }

      const latest = events[0];
      if (pingEl) pingEl.textContent = `Latest packet: ${latest.time || 'Just now'}`;

      let verdictColor = '#94a3b8';
      let verdictBg = 'rgba(255, 255, 255, 0.05)';
      let verdictBorder = '#334155';
      if (latest.liquidity_status === 'high') {
        verdictColor = '#34d399';
        verdictBg = 'rgba(6, 78, 59, 0.4)';
        verdictBorder = '#059669';
      } else if (latest.liquidity_status === 'active') {
        verdictColor = '#60a5fa';
        verdictBg = 'rgba(30, 58, 138, 0.4)';
        verdictBorder = '#2563eb';
      } else if (latest.liquidity_status === 'slow') {
        verdictColor = '#fbbf24';
        verdictBg = 'rgba(69, 26, 3, 0.4)';
        verdictBorder = '#d97706';
      } else if (latest.liquidity_status === 'dead') {
        verdictColor = '#f87171';
        verdictBg = 'rgba(69, 10, 10, 0.5)';
        verdictBorder = '#dc2626';
      } else if (latest.liquidity_status === 'material') {
        verdictColor = '#c084fc';
        verdictBg = 'rgba(88, 28, 135, 0.4)';
        verdictBorder = '#9333ea';
      }

      const iconUrl = `https://render.albiononline.com/v1/item/${encodeURIComponent(latest.item_id)}.png?quality=1`;
      const priceText = latest.price ? `${latest.price.toLocaleString()}s (${latest.price_type})` : 'Price Scanned';
      const volText = latest.daily_volume !== null ? ` • Volume: <strong>${latest.daily_volume.toFixed(1)} sold/day</strong>` : '';

      latestEl.innerHTML = `
        <div style="display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 10px;">
          <div style="display: flex; align-items: center; gap: 10px;">
            <div style="width: 38px; height: 38px; background: #0b0f19; border: 1px solid rgba(255, 255, 255, 0.1); border-radius: 6px; display: flex; align-items: center; justify-content: center; overflow: hidden; flex-shrink: 0;">
              <img src="${iconUrl}" style="width: 100%; height: 100%; object-fit: contain;" onerror="this.style.display='none';">
            </div>
            <div>
              <div style="font-weight: 700; color: #f8fafc; font-size: 0.9rem; display: flex; align-items: center; gap: 6px;">
                <span>${escapeHtml(latest.item_name)}</span>
                <span class="badge badge-tier" style="font-size: 0.68rem;">${latest.step_label}</span>
                <span style="font-size: 0.75rem; color: #94a3b8; font-weight: normal;">in <strong>${latest.city}</strong></span>
              </div>
              <div style="font-size: 0.78rem; color: #cbd5e1; margin-top: 2px;">
                ${priceText}${volText}
              </div>
            </div>
          </div>
          <div style="padding: 6px 12px; border-radius: 6px; background: ${verdictBg}; border: 1px solid ${verdictBorder}; color: ${verdictColor}; font-size: 0.82rem; font-weight: 700;">
            ${latest.verdict}
          </div>
        </div>
      `;

      if (events.length > 1) {
        recentEl.innerHTML = '<span style="font-size: 0.72rem; color: #64748b; margin-right: 4px;">Recent Scans:</span>' + events.slice(1, 8).map(ev => {
          let badgeColor = '#94a3b8';
          if (ev.liquidity_status === 'high') badgeColor = '#34d399';
          else if (ev.liquidity_status === 'active') badgeColor = '#60a5fa';
          else if (ev.liquidity_status === 'slow') badgeColor = '#fbbf24';
          else if (ev.liquidity_status === 'dead') badgeColor = '#f87171';
          else if (ev.liquidity_status === 'material') badgeColor = '#c084fc';
          const shortName = ev.item_name.length > 18 ? ev.item_name.substring(0, 16) + '..' : ev.item_name;
          const volStr = ev.daily_volume !== null ? `${ev.daily_volume.toFixed(1)}/d` : ev.step_label;
          return `<span class="badge" style="background: #1e293b; color: ${badgeColor}; border: 1px solid rgba(255,255,255,0.08); font-size: 0.72rem; cursor: pointer;" onclick="document.getElementById('search-box').value='${escapeHtml(ev.item_name)}'; onSearchInput();" title="Click to filter table for ${escapeHtml(ev.item_name)} in ${ev.city}">${shortName} (${volStr})</span>`;
        }).join('');
      } else {
        recentEl.innerHTML = '';
      }
    }

    function renderEnchanting() {
      renderArtifactMats();
      renderSnifferRadar(liveSnifferEventsData);
      const tbody = document.getElementById('enchanting-body');
      if (!tbody) return;

      let filtered = enchantingData.filter(passesEnchantFilters);

      filtered.sort((a, b) => {
        let va, vb;
        if (sortKeyEnchant === 'mass_profit') {
          va = (Number(a.profit_per_item) || 0) * enchantBatchSize;
          vb = (Number(b.profit_per_item) || 0) * enchantBatchSize;
        } else {
          va = a[sortKeyEnchant];
          vb = b[sortKeyEnchant];
        }
        if (typeof va === 'string') va = va.toLowerCase();
        if (typeof vb === 'string') vb = vb.toLowerCase();
        if (va < vb) return sortAscEnchant ? -1 : 1;
        if (va > vb) return sortAscEnchant ? 1 : -1;
        return 0;
      });

      const totalItems = filtered.length;
      const totalPages = Math.max(1, Math.ceil(totalItems / pageSize));
      if (currentPage > totalPages) currentPage = totalPages;

      const startIndex = (currentPage - 1) * pageSize;
      const pageSlice = filtered.slice(startIndex, startIndex + pageSize);

      updatePagination(totalItems, startIndex, pageSlice.length, totalPages);

      if (pageSlice.length === 0) {
        tbody.innerHTML = '<tr><td colspan="16" style="text-align:center; padding: 32px; color: var(--text-muted);">' +
          'No enchanting opportunities match your current filters.<br><small style="margin-top:8px; display:inline-block; color:#64748b;">(Try selecting "All Deals" or switching Enchant Step)</small>' +
          '</td></tr>';
        return;
      }

      tbody.innerHTML = pageSlice.map(e => {
        const isHigh = (e.margin_pct >= 25 && e.profit_per_item >= 4000);
        let ratingBadge = '';
        if (isHigh) {
          ratingBadge = `<span class="badge" style="background:#064e3b; color:#34d399; font-weight:700;">🔥 MASS PROFIT</span>`;
        } else if (e.is_profitable) {
          ratingBadge = `<span class="badge" style="background:#14532d; color:#86efac; font-weight:700;">🟢 PROFITABLE</span>`;
        } else if (e.profit_per_item === 0) {
          ratingBadge = `<span class="badge" style="background:#334155; color:#94a3b8;">⚪ BREAK-EVEN</span>`;
        } else {
          ratingBadge = `<span class="badge" style="background:#450a0a; color:#fca5a5;">❌ LOSS</span>`;
        }

        const massProfitVal = Math.round(e.profit_per_item * enchantBatchSize);
        const massProfitDisplay = `<span class="num ${massProfitVal > 0 ? 'profit-val' : 'stale-data'}" style="font-weight:800;">${massProfitVal > 0 ? '+' : ''}${massProfitVal.toLocaleString()}</span> <small style="color:#64748b;">(${enchantBatchSize}x)</small>`;

        const safeItemName = (e.item_name || e.base_item_id).replace(/'/g, "\\'");
        const primaryMatName = (e.enchant_mat_name || '').split(' (')[0].replace(/'/g, "\\'");

        const isBm = (e.sell_city === 'Black Market');
        const sellCityDisplay = isBm 
          ? `<strong style="color: #60a5fa;">🏴‍☠️ Black Market</strong>` 
          : `<strong>${e.sell_city}</strong>`;

        return `
          <tr>
            <td>${ratingBadge}</td>
            <td>${renderItemCell(e.base_item_id, e.item_name, 1)}</td>
            <td><span class="badge badge-tier">${e.item_type}</span></td>
            <td><span class="badge" style="background:#1e1b4b; color:#c4b5fd; font-weight:700;">${e.step_label}</span></td>
            <td>
              <div style="display:flex; align-items:center; gap:4px; flex-wrap:wrap;">${(e.enchant_mat_id || '').split(' + ').filter(Boolean).map(m => `<img src="https://render.albiononline.com/v1/item/${encodeURIComponent(m)}.png?quality=1" loading="lazy" alt="" style="width:26px;height:26px;object-fit:contain;" title="${escapeHtml(m)}" onerror="this.style.display='none';">`).join('')}
                <span style="font-size:0.83rem; color:#f8fafc; font-weight:600;">${escapeHtml(e.enchant_mat_name)}</span></div>
            </td>
            <td class="num">${e.base_item_price.toLocaleString()} <small style="color:#94a3b8;">(${e.city})</small></td>
            <td class="num">${e.enchant_mat_total_cost.toLocaleString()}</td>
            <td class="num" style="font-weight:700; color:#e2e8f0;">${e.total_cost.toLocaleString()}</td>
            <td>${sellCityDisplay}</td>
            <td class="num">
              ${e.sell_price.toLocaleString()}
              <button class="btn-edit-price" onclick="promptOverridePrice('${e.target_item_id}', '${e.sell_city}', 1, ${e.sell_price})" title="Override price if different in-game">✏️</button>
            </td>
            <td class="num ${e.is_profitable ? 'profit-val' : 'stale-data'}" style="font-weight:700;">
              ${e.profit_per_item > 0 ? '+' : ''}${e.profit_per_item.toLocaleString()}
              <br><small style="color:#f87171; font-weight:400;" title="Market tax at ${(marketTaxRate * 100).toFixed(0)}% (${premiumTax ? 'Premium' : 'no Premium'}) and fees already deducted">tax -${Math.max(0, Math.round((e.sell_price || 0) - (e.net_revenue || 0))).toLocaleString()}</small>
            </td>
            <td class="num ${e.is_profitable ? 'margin-val' : 'stale-data'}">
              ${e.margin_pct > 0 ? '+' : ''}${e.margin_pct.toFixed(1)}%
            </td>
            <td class="num">${massProfitDisplay}</td>
            <td class="num">${renderLiquidityBadge(e.target_daily_volume, e.liquidity_status)}</td>
            <td class="num">${renderAgeCell(e, isBm)}</td>
            <td style="white-space: nowrap;">
              <button class="btn btn-secondary" style="padding: 3px 7px; font-size: 0.73rem;" onclick="copyItemName('${safeItemName}', this)" title="Copy Item Search Name">
                📋 Item
              </button>
              <button class="btn btn-secondary" style="padding: 3px 7px; font-size: 0.73rem; margin-left:3px;" onclick="copyItemName('${primaryMatName}', this)" title="Copy Materials In-Game Search Name">
                📋 Mats
              </button>
              <button class="btn-fulfill" style="margin-left: 3px;" onclick="markOrderFulfilled('${e.target_item_id}', '${e.sell_city}', 1, this)" title="Order gone in-game?">
                ✓
              </button>
            </td>
          </tr>
        `;
      }).join('');
    }

    function renderCurrentView() {
      if (currentTab === 'flips') renderFlips();
      else if (currentTab === 'blackmarket') renderBlackMarket();
      else if (currentTab === 'crafting') renderCrafting();
      else if (currentTab === 'enchanting') renderEnchanting();
      else if (currentTab === 'watchlist') renderWatchlist();
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
    let lastSeenRefresh = null;

    async function fetchFlips() {
      try {
        const res = await fetch('/api/flips');
        if (!res.ok) return;
        const data = await res.json();
        
        document.getElementById('server-badge').textContent = (data.server || 'Europe').toUpperCase() + ' LIVE';
        if (data.last_refresh) {
          if (lastSeenRefresh && data.last_refresh !== lastSeenRefresh) {
            flashSyncSuccess();
          }
          lastSeenRefresh = data.last_refresh;
          document.getElementById('last-refresh').textContent = data.last_refresh;
        }

        flipsData = data.flips || [];
        blackmarketData = data.blackmarket || [];
        craftingData = data.crafting || [];
        focusCraftingData = data.focus_crafting || [];
        snifferCraftingData = data.sniffer_crafting || [];
        snifferFocusCraftingData = data.sniffer_focus_crafting || [];
        enchantingData = data.enchanting || [];
        artifactMatsData = data.artifact_materials || {};
        pricesData = data.recent_prices || [];
        liveSnifferEventsData = data.live_sniffer_events || [];

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

        // Update Enchanting tab badge
        const enchBadge = document.getElementById('enchant-badge');
        const enchCount = data.enchant_count || 0;
        if (enchBadge) {
          if (enchCount > 0) {
            enchBadge.style.display = 'inline-block';
            enchBadge.textContent = enchCount;
          } else {
            enchBadge.style.display = 'none';
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
        updateOverridesBadge(data);
        updatePremiumButton(data);
        syncWatchlist(data);

        // Update live sniffer indicator and status modal
        if (data.sniffer_process) updateSnifferProcess(data.sniffer_process);
        if (data.sniffer_status) {
          updateSnifferStatus(data.sniffer_status);
          const currentPackets = data.sniffer_status.packet_count || 0;
          if (prevSnifferPackets > 0 && currentPackets > prevSnifferPackets) {
            const added = currentPackets - prevSnifferPackets;
            showToast(`⚡ <strong>Live Sniffer:</strong> Captured ${added} order(s) for <strong>${data.sniffer_status.last_city || 'In-Game'}</strong> in 0s!`);
          }
          prevSnifferPackets = currentPackets;
        }

        renderSnifferRadar(liveSnifferEventsData);
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


    function flashSyncSuccess() {
      const syncInd = document.getElementById('sync-indicator');
      const syncText = document.getElementById('sync-text');
      const lastRefElem = document.getElementById('last-refresh');

      if (syncInd) syncInd.className = 'sync-indicator synced';
      if (syncText) syncText.textContent = '✓ Synced Just Now';
      if (lastRefElem) {
        lastRefElem.classList.remove('flash-highlight');
        void lastRefElem.offsetWidth; // trigger reflow
        lastRefElem.classList.add('flash-highlight');
      }

      setTimeout(() => {
        if (syncInd && !syncInd.classList.contains('syncing')) {
          syncInd.className = 'sync-indicator';
          if (syncText) syncText.textContent = 'Live Sync';
        }
      }, 3000);
    }

    function setApiRefreshing(isRefreshing, customMsg) {
      const bar = document.getElementById('top-progress-bar');
      const btn = document.getElementById('btn-refresh');
      const btnText = document.getElementById('btn-refresh-text');
      const syncInd = document.getElementById('sync-indicator');
      const syncText = document.getElementById('sync-text');

      if (isRefreshing) {
        if (bar) {
          bar.style.opacity = '1';
          bar.style.width = '70%';
        }
        if (btn) {
          btn.disabled = true;
          btn.classList.add('btn-refreshing');
          btn.classList.remove('btn-success-flash');
        }
        if (btnText) btnText.textContent = customMsg || 'Refreshing API...';
        if (syncInd) syncInd.className = 'sync-indicator syncing';
        if (syncText) syncText.textContent = 'Querying AODP...';
      } else {
        // Visual indicator when the API call ends
        if (bar) {
          bar.style.width = '100%';
          setTimeout(() => {
            bar.style.opacity = '0';
            setTimeout(() => { bar.style.width = '0%'; }, 400);
          }, 350);
        }
        if (btn) {
          btn.classList.remove('btn-refreshing');
          btn.classList.add('btn-success-flash');
        }
        if (btnText) btnText.textContent = '✓ Live Data Updated!';

        flashSyncSuccess();

        // Reset button state smoothly after 2.5s
        setTimeout(() => {
          if (btn) {
            btn.disabled = false;
            btn.classList.remove('btn-success-flash');
          }
          if (btnText) btnText.textContent = 'Fetch Live Data';
        }, 2500);
      }
    }

    async function triggerManualRefresh() {
      setApiRefreshing(true, 'Querying AODP Prices...');

      try {
        const res = await fetch('/api/refresh', { method: 'POST' });
        if (res.ok) {
          const data = await res.json();
          flipsData = data.flips || [];
          blackmarketData = data.blackmarket || [];
          craftingData = data.crafting || [];
          focusCraftingData = data.focus_crafting || [];
          pricesData = data.recent_prices || [];
          if (data.last_refresh) {
            document.getElementById('last-refresh').textContent = data.last_refresh;
            lastSeenRefresh = data.last_refresh;
          }
          checkBrowserNotifications(flipsData, data.last_refresh);
          updateOverridesBadge(data);
          renderCurrentView();

          setApiRefreshing(false);
          showToast(`✓ <strong>API Refresh Complete:</strong> Synced ${flipsData.length} flips & ${blackmarketData.length} Black Market deals.`);
        } else {
          setApiRefreshing(false);
          showToast(`⚠️ <strong>API Notice:</strong> Server returned HTTP ${res.status}`);
        }
      } catch (err) {
        console.error('Error triggering refresh:', err);
        setApiRefreshing(false);
        showToast(`❌ <strong>Sync Failed:</strong> Could not connect to local market server.`);
      }
    }

    let currentOverrides = [];

    async function markOrderFulfilled(itemId, city, quality, btn) {
      if (btn) {
        btn.textContent = '✓ Done!';
        btn.disabled = true;
        const tr = btn.closest('tr');
        if (tr) tr.classList.add('row-fade-out');
      }

      showToast(`✓ Order marked as fulfilled for ${itemId} (${city})!`);

      try {
        const res = await fetch('/api/fulfill', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ item_id: itemId, city: city, quality: quality })
        });
        if (res.ok) {
          const data = await res.json();
          flipsData = data.flips || [];
          blackmarketData = data.blackmarket || [];
          craftingData = data.crafting || [];
          focusCraftingData = data.focus_crafting || [];
          pricesData = data.recent_prices || [];
          updateOverridesBadge(data);
          renderCurrentView();
        }
      } catch (err) {
        console.error('Failed to mark order fulfilled:', err);
      }
    }

    async function promptOverridePrice(itemId, city, quality, currentPrice) {
      const input = prompt(`Enter current price for ${itemId} in ${city}:\n(Enter 0 if the order is fulfilled or completely gone)`, currentPrice);
      if (input === null) return;
      const cleanNum = parseInt(input.replace(/[^0-9]/g, ''), 10);
      if (isNaN(cleanNum)) {
        alert('Please enter a valid numeric silver price.');
        return;
      }

      showToast(`Updating price for ${itemId}...`);
      try {
        const res = await fetch('/api/override', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ item_id: itemId, city: city, quality: quality, price: cleanNum })
        });
        if (res.ok) {
          const data = await res.json();
          flipsData = data.flips || [];
          blackmarketData = data.blackmarket || [];
          craftingData = data.crafting || [];
          focusCraftingData = data.focus_crafting || [];
          pricesData = data.recent_prices || [];
          updateOverridesBadge(data);
          renderCurrentView();
          if (cleanNum === 0) {
            showToast(`✓ Order for ${itemId} marked as fulfilled (price set to 0).`);
          } else {
            showToast(`✓ Price for ${itemId} in ${city} updated to ${cleanNum.toLocaleString()} silver!`);
          }
        }
      } catch (err) {
        console.error('Failed to override price:', err);
      }
    }

    async function refreshSingleItem(itemId, btn) {
      if (btn) btn.classList.add('spinning');
      showToast(`Fetching latest AODP prices for ${itemId}...`);
      try {
        const res = await fetch('/api/refresh_item', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ item_id: itemId })
        });
        if (btn) btn.classList.remove('spinning');
        if (res.ok) {
          const data = await res.json();
          flipsData = data.flips || [];
          blackmarketData = data.blackmarket || [];
          craftingData = data.crafting || [];
          focusCraftingData = data.focus_crafting || [];
          pricesData = data.recent_prices || [];
          updateOverridesBadge(data);
          renderCurrentView();
          showToast(`✓ Refreshed ${itemId} with latest live prices.`);
        }
      } catch (err) {
        if (btn) btn.classList.remove('spinning');
        console.error('Failed to refresh item:', err);
      }
    }

    function updateOverridesBadge(data) {
      const count = data.overrides_count || 0;
      currentOverrides = data.overrides || [];
      const btn = document.getElementById('btn-overrides');
      const countElem = document.getElementById('override-count');
      if (btn && countElem) {
        countElem.textContent = count;
        btn.style.display = (count > 0) ? 'inline-flex' : 'none';
      }
    }

    function openOverridesManager() {
      const modal = document.getElementById('overrides-modal');
      const list = document.getElementById('overrides-list');
      if (!modal || !list) return;

      if (!currentOverrides || currentOverrides.length === 0) {
        list.innerHTML = '<div style="color: #64748b; padding: 12px 0;">No active overrides or fulfilled markers.</div>';
      } else {
        list.innerHTML = currentOverrides.map(o => {
          const label = (o.type === 'fulfilled') ? '🔴 Order Fulfilled' : `✏️ Price: ${o.price.toLocaleString()}`;
          return `
            <div style="display: flex; justify-content: space-between; align-items: center; padding: 8px 12px; background: #1e293b; border-radius: 6px; margin-bottom: 6px; font-size: 0.84rem;">
              <div>
                <strong>${escapeHtml(o.item_name || o.item_id)}</strong> 
                <span style="color:#94a3b8;">(${o.city} Q${o.quality})</span>
              </div>
              <span class="badge" style="background: rgba(245, 158, 11, 0.2); color: #fbbf24;">${label}</span>
            </div>
          `;
        }).join('');
      }

      modal.style.display = 'flex';
    }

    function closeOverridesModal(event) {
      if (event && event.target !== event.currentTarget) return;
      const modal = document.getElementById('overrides-modal');
      if (modal) modal.style.display = 'none';
    }

    async function clearAllOverrides() {
      if (!confirm('Clear all manual fulfilled markers and price overrides?')) return;
      try {
        const res = await fetch('/api/clear_overrides', { method: 'POST' });
        if (res.ok) {
          const data = await res.json();
          flipsData = data.flips || [];
          blackmarketData = data.blackmarket || [];
          craftingData = data.crafting || [];
          focusCraftingData = data.focus_crafting || [];
          pricesData = data.recent_prices || [];
          updateOverridesBadge(data);
          closeOverridesModal();
          renderCurrentView();
          showToast('✓ All overrides cleared. Original live prices restored.');
        }
      } catch (err) {
        console.error('Failed to clear overrides:', err);
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

    // Keyboard shortcuts for desktop companion experience
    window.addEventListener('keydown', (e) => {
      // Don't trigger shortcuts if user is typing in an input
      const tag = document.activeElement ? document.activeElement.tagName : '';
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') {
        if (e.key === 'Escape') {
          document.activeElement.blur();
        }
        return;
      }

      if (e.key === 'r' || e.key === 'R') {
        e.preventDefault();
        triggerManualRefresh();
      } else if (e.key === '/') {
        e.preventDefault();
        const sBox = document.getElementById('search-box');
        if (sBox) sBox.focus();
      } else if (e.key === '1') {
        switchTab('flips');
      } else if (e.key === '2') {
        switchTab('blackmarket');
      } else if (e.key === '3') {
        switchTab('crafting');
      } else if (e.key === '4') {
        switchTab('enchanting');
      } else if (e.key === '5') {
        switchTab('watchlist');
      } else if (e.key === '6') {
        switchTab('prices');
      } else if (e.key === 'Escape') {
        closeOverridesModal();
        closeSnifferModal();
      }
    });

    // Initialize Watchlist badge & Mount capacity display on startup
    updateWatchlistBadge();
    const initMount = document.getElementById('mount-cap-disp');
    if (initMount) initMount.textContent = Math.round(selectedMountCap).toLocaleString();
    const selElem = document.getElementById('mount-select');
    if (selElem) {
      if (['672','1075','1607','2364','3425','4923','800','1100','2500'].includes(String(selectedMountCap))) {
        selElem.value = String(selectedMountCap);
      } else {
        selElem.value = 'custom';
        const cIn = document.getElementById('mount-custom');
        if (cIn) { cIn.style.display = 'inline-block'; cIn.value = selectedMountCap; }
      }
    }
    const feeElem = document.getElementById('station-fee-input');
    if (feeElem) feeElem.value = currentStationFee;

    // Initial load + poll every 1.5s for instant live sniffer responsiveness
    fetchFlips();
    { const qr = document.getElementById('quality-rolls'); if (qr) qr.value = String(qualityRolls); }
    setInterval(fetchFlips, 1500);
  </script>


  <div id="toast-container" class="toast-container"></div>
</body>
</html>

"""


# Market location ids sent by albiondata-client (ao-bin-dumps formatted/world.txt).
# Only the markets this app tracks are mapped. Portals, rests (e.g. 1012 Merlyn's
# Rest) and Smugglers' Dens have separate order books, so their orders are ignored
# instead of being merged into a city's prices.
LOCATION_ID_TO_CITY: dict[str, str] = {
    "0007": "Thetford",
    "1002": "Lymhurst",
    "2004": "Bridgewatch",
    "3008": "Martlock",
    "4002": "Fort Sterling",
    "3005": "Caerleon",
    "3013-Auction2": "Caerleon",  # Caerleon's second market
    "3003": "Black Market",
    "5003": "Brecilien",
}


def location_to_city(location_id: Any) -> str | None:
    """Maps a client LocationId (string, sometimes without leading zeros) to a city."""
    raw = str(location_id or "").strip()
    if raw in LOCATION_ID_TO_CITY:
        return LOCATION_ID_TO_CITY[raw]
    if raw.isdigit():
        return LOCATION_ID_TO_CITY.get(raw.zfill(4))
    return None


# How long a market scan from the local sniffer marks a deal as sniffer-sourced.
SNIFFER_WINDOW_SECONDS = 3600


class FlipDataStore:
    """Thread-safe storage for flip opportunities, crafting data, and recent prices."""

    def __init__(self, overrides_path: Path | str | None = None) -> None:
        self._lock = threading.Lock()
        # Server mode keeps this in its data directory (the app directory is read-only under systemd).
        self.overrides_path = Path(overrides_path) if overrides_path else CACHE_DIR / "overrides.json"
        self.server: str = "europe"
        self.last_refresh: str = ""
        self.flips: list[dict[str, Any]] = []
        self.blackmarket: list[dict[str, Any]] = []
        self.bm_new_count: int = 0
        self.bm_recent_arrivals: list[dict[str, Any]] = []
        self._previous_bm_keys: set[tuple[str, int, int]] = set()
        self.crafting: list[dict[str, Any]] = []
        self.focus_crafting: list[dict[str, Any]] = []
        self.sniffer_crafting: list[dict[str, Any]] = []  # materials priced only from sniffer scans
        self.sniffer_focus_crafting: list[dict[str, Any]] = []
        self.enchanting: list[dict[str, Any]] = []
        self.artifact_materials: dict[str, Any] = {}
        self.recent_prices: list[dict[str, Any]] = []
        self.refresh_callback: Callable[[], Any] | None = None
        self.fulfilled_orders: dict[tuple[str, str, int], datetime] = {}
        self.price_overrides: dict[tuple[str, str, int], int] = {}
        self.last_prices: list[PriceRecord] = []
        self.last_history: list[HistoryRecord] = []
        self.last_config: AppConfig | None = None
        self.client: Any = None
        self.sniffer_manager: Any = None  # albion_flips.sniffer.SnifferManager when enabled
        # Live Sniffer Ingestion Tracking
        self.sniffer_scans: dict[tuple[str, str, int], datetime] = {}
        self.sniffer_packet_count: int = 0
        self.sniffer_last_city: str = ""
        self.sniffer_last_time: datetime | None = None
        self.sniffer_last_timestamp: float = 0.0
        self.live_sniffer_events: list[dict[str, Any]] = []
        self._recompute_timer: threading.Timer | None = None
        self._recompute_lock = threading.Lock()
        self._is_recomputing: bool = False
        # Server mode (albion_flips.server): 7-day reference prices from SQLite and a hook that
        # stores live-client prices persistently. Both stay None in desktop mode.
        self.reference: dict[tuple[str, str, int], Any] | None = None
        self.ingest_listener: Callable[[list[PriceRecord]], Any] | None = None
        # Server mode: receives {(item_id, city, quality): {"min_sell", "max_buy", "n"}} per ingest.
        self.order_observer: Callable[[dict[tuple[str, str, int], dict[str, int]]], Any] | None = None
        # Premium toggle from the dashboard; None = use config.json's "premium" value.
        self.premium_override: bool | None = None
        # Starred item ids, saved on disk (the desktop window's browser storage is not kept).
        self.watchlist: list[str] = []
        self._load_overrides()
        self._load_settings()

    def _load_settings(self) -> None:
        try:
            data = json.loads((CACHE_DIR / "settings.json").read_text(encoding="utf-8"))
            if isinstance(data.get("premium"), bool):
                self.premium_override = data["premium"]
            if isinstance(data.get("watchlist"), list):
                self.watchlist = [str(i) for i in data["watchlist"] if i]
        except (OSError, ValueError):
            pass

    def _save_settings(self) -> None:
        try:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            (CACHE_DIR / "settings.json").write_text(
                json.dumps({"premium": self.premium_override, "watchlist": self.watchlist}), encoding="utf-8"
            )
        except OSError as e:
            logger.warning("Could not save settings: %s", e)

    def set_watchlist(self, add: Sequence[str] = (), remove: Sequence[str] = (), clear: bool = False) -> None:
        with self._lock:
            items = [] if clear else [i for i in self.watchlist if i not in set(remove)]
            for item_id in add:
                if item_id and item_id not in items:
                    items.append(item_id)
            self.watchlist = items
        self._save_settings()

    def _watchlist_prices(self) -> dict[str, list[dict[str, Any]]]:
        """Latest quotes per city for each starred item (shown when it has no deal)."""
        if not self.watchlist:
            return {}
        watched = set(self.watchlist)
        now = datetime.now(timezone.utc)
        out: dict[str, list[dict[str, Any]]] = {}
        for p in self.last_prices:
            if p.item_id not in watched or (p.sell_price_min <= 0 and p.buy_price_max <= 0):
                continue
            ages = [calculate_age_minutes(d, now) for d, v in ((p.sell_price_min_date, p.sell_price_min), (p.buy_price_max_date, p.buy_price_max)) if v > 0]
            ages = [a for a in ages if a is not None]
            out.setdefault(p.item_id, []).append({
                "city": p.city,
                "quality": p.quality,
                "sell_price_min": p.sell_price_min,
                "buy_price_max": p.buy_price_max,
                "age_minutes": round(min(ages)) if ages else None,
            })
        for rows in out.values():
            rows.sort(key=lambda r: (r["quality"], r["city"]))
        return out

    def _effective_config(self, config: AppConfig | None) -> AppConfig:
        config = config or AppConfig()
        if self.premium_override is not None and config.premium != self.premium_override:
            config = replace(config, premium=self.premium_override)
        return config

    def set_premium(self, premium: bool) -> None:
        """Switches between premium (4%) and normal (8%) market tax and recalculates every tab."""
        self.premium_override = bool(premium)
        self._save_settings()
        self.recompute()

    def _load_overrides(self) -> None:
        cache_file = self.overrides_path
        if cache_file.is_file():
            try:
                with open(cache_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for item in data.get("fulfilled", []):
                    key = (item["item_id"], item["city"], int(item["quality"]))
                    dt = datetime.fromisoformat(item["timestamp"]).replace(tzinfo=timezone.utc)
                    self.fulfilled_orders[key] = dt
                for item in data.get("overrides", []):
                    key = (item["item_id"], item["city"], int(item["quality"]))
                    self.price_overrides[key] = int(item["price"])
            except Exception as e:
                logger.warning("Could not load overrides cache: %s", e)

    def _save_overrides(self) -> None:
        try:
            cache_file = self.overrides_path
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "fulfilled": [
                    {"item_id": k[0], "city": k[1], "quality": k[2], "timestamp": v.isoformat()}
                    for k, v in self.fulfilled_orders.items()
                ],
                "overrides": [
                    {"item_id": k[0], "city": k[1], "quality": k[2], "price": v}
                    for k, v in self.price_overrides.items()
                ],
            }
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
        except Exception as e:
            logger.warning("Could not save overrides cache: %s", e)

    def mark_fulfilled(self, item_id: str, city: str, quality: int = 1) -> None:
        with self._lock:
            key = (item_id, city, quality)
            self.fulfilled_orders[key] = datetime.now(timezone.utc)
            self.price_overrides.pop(key, None)
            self._save_overrides()
        self.recompute()

    def set_price_override(self, item_id: str, city: str, quality: int, price: int) -> None:
        with self._lock:
            key = (item_id, city, quality)
            if price <= 0:
                self.fulfilled_orders[key] = datetime.now(timezone.utc)
                self.price_overrides.pop(key, None)
            else:
                self.price_overrides[key] = price
                self.fulfilled_orders.pop(key, None)
            self._save_overrides()
        self.recompute()

    def clear_overrides(self) -> None:
        with self._lock:
            self.fulfilled_orders.clear()
            self.price_overrides.clear()
            self._save_overrides()
        self.recompute()

    def refresh_single_item(self, item_id: str) -> None:
        if not self.client or not self.last_config:
            return
        fresh_prices = self.client.get_prices(
            item_ids=[item_id],
            cities=self.last_config.cities,
            qualities=self.last_config.qualities,
            bypass_cache=True,
        )
        with self._lock:
            existing_prices = [p for p in self.last_prices if p.item_id != item_id]
            existing_prices.extend(fresh_prices)
            self.last_prices = existing_prices
        self.recompute()

    def schedule_recompute(self, delay: float = 0.25) -> None:
        """Schedules debounced background recomputation.

        Coalesces high-frequency packet bursts from the in-game market into a single
        smooth recalculation without blocking HTTP ingest responses.
        """
        with self._recompute_lock:
            if self._recompute_timer is not None:
                self._recompute_timer.cancel()
            self._recompute_timer = threading.Timer(delay, self._run_background_recompute)
            self._recompute_timer.daemon = True
            self._recompute_timer.start()

    def _run_background_recompute(self) -> None:
        if self._is_recomputing:
            self.schedule_recompute(delay=0.25)
            return
        self._is_recomputing = True
        try:
            self.recompute()
        except Exception as e:
            logger.error("Error in background recompute: %s", e)
        finally:
            self._is_recomputing = False

    def ingest_market_orders(self, orders: list[dict[str, Any]], sync: bool = True) -> dict[str, Any]:
        """Ingests live market orders directly from local albiondata-client sniffer in 0s."""
        if not orders:
            return {"orders_processed": 0}

        now = datetime.now(timezone.utc)
        now_iso = now.strftime("%Y-%m-%dT%H:%M:%S")

        city_counts: dict[str, int] = {}
        aggregated: dict[tuple[str, str, int], dict[str, int]] = {}

        for order in orders:
            # The game sends silver x 10,000 (AODP divides the same way).
            price_silver = int(order.get("UnitPriceSilver", 0) or 0) // 10000
            if price_silver <= 0:
                continue

            item_id = str(order.get("ItemTypeId", "")).strip()
            if not item_id:
                continue
            # The game already writes enchanted ids as T8_CAPE@3; add it if one ever lacks it.
            enchant = int(order.get("EnchantmentLevel", 0) or 0)
            if enchant > 0 and "@" not in item_id:
                item_id = f"{item_id}@{enchant}"

            city = location_to_city(order.get("LocationId"))
            if city is None:
                continue

            quality = int(order.get("QualityLevel", 1))
            auction_type = str(order.get("AuctionType", "")).lower()

            key = (item_id, city, quality)
            if key not in aggregated:
                aggregated[key] = {"min_sell": 0, "max_buy": 0, "n": 0}
            aggregated[key]["n"] += 1

            if auction_type in ("offer", "sell"):
                current_min = aggregated[key]["min_sell"]
                if current_min == 0 or price_silver < current_min:
                    aggregated[key]["min_sell"] = price_silver
            elif auction_type in ("request", "buy"):
                current_max = aggregated[key]["max_buy"]
                if current_max == 0 or price_silver > current_max:
                    aggregated[key]["max_buy"] = price_silver

            city_counts[city] = city_counts.get(city, 0) + 1

        with self._lock:
            price_map = {(p.item_id, p.city, p.quality): p for p in self.last_prices}
            last_city = ""

            for (item_id, city, quality), data in aggregated.items():
                last_city = city
                min_sell = data["min_sell"]
                max_buy = data["max_buy"]

                # The market list arrives in several pages; pages a few seconds apart
                # belong to the same scan, so keep the best price across them.
                prev_scan = self.sniffer_scans.get((item_id, city, quality))
                prev = price_map.get((item_id, city, quality))
                if prev is not None and prev_scan is not None and (now - prev_scan).total_seconds() < 10:
                    if prev.sell_price_min > 0 and (min_sell == 0 or prev.sell_price_min < min_sell):
                        min_sell = prev.sell_price_min
                    if prev.buy_price_max > max_buy:
                        max_buy = prev.buy_price_max
                    data["min_sell"], data["max_buy"] = min_sell, max_buy

                # Mark sniffer timestamp for 0s badge
                self.sniffer_scans[(item_id, city, quality)] = now

                # Clear fulfilled order marker if fresh item was spotted
                self.fulfilled_orders.pop((item_id, city, quality), None)

                if (item_id, city, quality) in price_map:
                    p = price_map[(item_id, city, quality)]
                    if min_sell > 0:
                        p.sell_price_min = min_sell
                        p.sell_price_min_date = now_iso
                        p.sell_price_max = max(p.sell_price_max, min_sell)
                        p.sell_price_max_date = now_iso
                    if max_buy > 0:
                        p.buy_price_max = max_buy
                        p.buy_price_max_date = now_iso
                        p.buy_price_min = min(p.buy_price_min, max_buy) if p.buy_price_min > 0 else max_buy
                        p.buy_price_min_date = now_iso
                else:
                    new_p = PriceRecord(
                        item_id=item_id,
                        city=city,
                        quality=quality,
                        sell_price_min=min_sell,
                        sell_price_min_date=now_iso if min_sell > 0 else "",
                        sell_price_max=min_sell,
                        sell_price_max_date=now_iso if min_sell > 0 else "",
                        buy_price_min=max_buy,
                        buy_price_min_date=now_iso if max_buy > 0 else "",
                        buy_price_max=max_buy,
                        buy_price_max_date=now_iso if max_buy > 0 else "",
                    )
                    self.last_prices.append(new_p)
                    price_map[(item_id, city, quality)] = new_p

            updated_records = [price_map[k] for k in aggregated if k in price_map]
            self.sniffer_packet_count += len(orders)
            if last_city:
                self.sniffer_last_city = last_city
            self.sniffer_last_time = now
            self.sniffer_last_timestamp = time.time()

            # Record events to live_sniffer_events
            vol_days = self.last_config.volume_days if self.last_config else 3
            for (item_id, city, quality), data in aggregated.items():
                price_val = data["min_sell"] if data["min_sell"] > 0 else data["max_buy"]
                h_name = get_human_name(item_id)
                t = parse_tier(item_id) or 4
                e = parse_enchant(item_id)
                step_str = "Material" if ("RUNE" in item_id or "SOUL" in item_id or "RELIC" in item_id) else f"{t}.{e}"

                daily_vol = compute_daily_volume(self.last_history, item_id, city, quality=quality, volume_days=vol_days)
                if (daily_vol is None or daily_vol == 0) and not ("RUNE" in item_id or "SOUL" in item_id or "RELIC" in item_id):
                    for q in (1, 2, 3):
                        q_vol = compute_daily_volume(self.last_history, item_id, city, quality=q, volume_days=vol_days)
                        if q_vol is not None and q_vol > 0:
                            daily_vol = q_vol
                            break

                if "RUNE" in item_id or "SOUL" in item_id or "RELIC" in item_id:
                    liq = "material"
                    verdict = f"✨ Scanned Artifact Material: {price_val:,}s in {city} (Foundry costs updated)"
                elif daily_vol is not None:
                    if daily_vol >= 20.0:
                        liq = "high"
                        verdict = f"🔥 HIGH DEMAND (~{round(daily_vol)} sold/d) • Safe to mass enchant!"
                    elif daily_vol >= 5.0:
                        liq = "active"
                        verdict = f"🟢 ACTIVE LIQUIDITY (~{round(daily_vol)} sold/d) • Solid turnover."
                    elif daily_vol >= 1.0:
                        liq = "slow"
                        verdict = f"🟡 SLOW MOVER (~{daily_vol:.1f} sold/d) • Enchant in small batches."
                    else:
                        liq = "dead"
                        verdict = "⚠️ DEAD ITEM (0 sales recorded/day) • High risk of unsellable stock!"
                else:
                    liq = "untracked"
                    verdict = "❓ No sales history yet (Click chart icon in-game to sniff sales history)"

                event = {
                    "item_id": item_id,
                    "item_name": h_name,
                    "tier": t,
                    "enchant": e,
                    "step_label": step_str,
                    "city": city,
                    "price": price_val,
                    "price_type": "Sell Order" if data["min_sell"] > 0 else "Buy Order",
                    "daily_volume": round(daily_vol, 1) if daily_vol is not None else None,
                    "liquidity_status": liq,
                    "verdict": verdict,
                    "time": now.strftime("%H:%M:%S UTC"),
                    "timestamp": time.time(),
                }
                self.live_sniffer_events = [event] + [ev for ev in self.live_sniffer_events if not (ev["item_id"] == item_id and ev["city"] == city)][:9]

        if self.ingest_listener is not None:
            try:
                self.ingest_listener(updated_records)
            except Exception as e:
                logger.error("Ingest listener failed: %s", e)

        if self.order_observer is not None:
            # Overlay: which items were just seen, with their best prices and order counts.
            try:
                self.order_observer({k: dict(v) for k, v in aggregated.items()})
            except Exception as e:
                logger.error("Order observer failed: %s", e)

        if sync:
            self.recompute()
        else:
            self.schedule_recompute(delay=0.25)

        logger.info("⚡ Ingested %d live market orders for %s from local sniffer", len(orders), last_city)
        return {
            "orders_processed": len(orders),
            "items_updated": len(aggregated),
            "cities": city_counts,
        }

    def ingest_market_histories(self, uploads: list[dict[str, Any]], sync: bool = True) -> dict[str, Any]:
        """Ingests sales history sniffed when the player opens an item's price chart.

        Payload (albiondata-client lib.MarketHistoriesUpload):
            {"AlbionId": 2888, "LocationId": "3008", "QualityLevel": 1, "Timescale": 1,
             "MarketHistories": [{"ItemAmount": 24, "SilverAmount": 960000, "Timestamp": <.NET ticks>}]}
        SilverAmount is the total silver x 10,000. Points are hourly (Timescale 0) or
        6-hourly (Timescale 1/2); they are summed into daily buckets to match the
        AODP time-scale=24 history used everywhere else.
        """
        if not uploads:
            return {"histories_processed": 0}

        vol_days = self.last_config.volume_days if self.last_config else 3
        now = datetime.now(timezone.utc)
        processed = 0
        with self._lock:
            history_index = {(h.item_id, h.location, h.quality): h for h in self.last_history}
            for upload in uploads:
                item_id = str(upload.get("AlbionIdString") or "").strip()
                if not item_id:
                    try:
                        item_id = ALBION_ID_TO_ITEM.get(int(upload.get("AlbionId", 0)), "")
                    except (TypeError, ValueError):
                        item_id = ""
                city = location_to_city(upload.get("LocationId"))
                points = upload.get("MarketHistories") or []
                if not item_id or city is None or not points:
                    continue
                quality = int(upload.get("QualityLevel", 1) or 1)

                daily: dict[str, list[int]] = {}
                for pt in points:
                    try:
                        ticks = int(pt.get("Timestamp", 0))
                        amount = int(pt.get("ItemAmount", 0))
                        silver = int(pt.get("SilverAmount", 0))
                    except (TypeError, ValueError):
                        continue
                    if ticks <= _TICKS_AT_EPOCH or amount < 0:
                        continue
                    day = datetime.fromtimestamp((ticks - _TICKS_AT_EPOCH) / 10_000_000, tz=timezone.utc)
                    bucket = daily.setdefault(day.strftime("%Y-%m-%dT00:00:00"), [0, 0])
                    bucket[0] += amount
                    bucket[1] += silver
                if not daily:
                    continue

                record = history_index.get((item_id, city, quality))
                if record is None:
                    record = HistoryRecord(location=city, item_id=item_id, quality=quality, data=[])
                    self.last_history.append(record)
                    history_index[(item_id, city, quality)] = record
                by_day = {p.timestamp: p for p in record.data}
                for day_ts, (amount, silver) in daily.items():
                    avg = silver // amount // 10000 if amount > 0 else 0
                    existing = by_day.get(day_ts)
                    # A partial day (hourly scale) must not shrink a fuller daily total.
                    if existing is None or amount >= existing.item_count:
                        by_day[day_ts] = HistoryPoint(item_count=amount, avg_price=avg, timestamp=day_ts)
                record.data = sorted(by_day.values(), key=lambda p: p.timestamp)
                processed += 1

                daily_vol = compute_daily_volume(history_index, item_id, city, quality=quality, volume_days=vol_days)
                avg_price = record.data[-1].avg_price if record.data else 0
                if daily_vol is None:
                    liq, verdict = "untracked", "❓ Sales history logged"
                elif daily_vol >= 20.0:
                    liq, verdict = "high", f"🔥 VERIFIED HIGH DEMAND (~{round(daily_vol)} sold/d) • Safe to mass enchant!"
                elif daily_vol >= 5.0:
                    liq, verdict = "active", f"🟢 VERIFIED ACTIVE (~{round(daily_vol)} sold/d) • Healthy trade velocity."
                elif daily_vol >= 1.0:
                    liq, verdict = "slow", f"🟡 VERIFIED SLOW (~{daily_vol:.1f} sold/d) • Low turnover volume."
                else:
                    liq, verdict = "dead", f"⚠️ VERIFIED DEAD ITEM (0 sales in last {vol_days} days) • DO NOT MASS ENCHANT!"

                t = parse_tier(item_id) or 4
                e = parse_enchant(item_id)
                event = {
                    "item_id": item_id,
                    "item_name": get_human_name(item_id),
                    "tier": t,
                    "enchant": e,
                    "step_label": f"{t}.{e}",
                    "city": city,
                    "price": avg_price if avg_price > 0 else None,
                    "price_type": "Sales History",
                    "daily_volume": round(daily_vol, 1) if daily_vol is not None else None,
                    "liquidity_status": liq,
                    "verdict": verdict,
                    "time": now.strftime("%H:%M:%S UTC"),
                    "timestamp": time.time(),
                }
                self.live_sniffer_events = [event] + [ev for ev in self.live_sniffer_events if not (ev["item_id"] == item_id and ev["city"] == city)][:9]

        if sync:
            self.recompute()
        else:
            self.schedule_recompute(delay=0.25)

        return {"histories_processed": processed}

    def _keep_sniffer_prices(self, prices: Sequence[PriceRecord]) -> tuple[list[PriceRecord], bool]:
        """Keeps sniffer-captured quotes that are newer than what the API returned.

        A refresh replaces all prices with the API's copy, which can lag behind what the
        local sniffer just saw (or not cover the item at all). Returns (prices, changed).
        """
        if not self.sniffer_scans:
            return list(prices), False
        old = {(p.item_id, p.city, p.quality): p for p in self.last_prices}
        merged = list(prices)
        index = {(p.item_id, p.city, p.quality): i for i, p in enumerate(merged)}
        changed = False

        def newest(p: PriceRecord) -> str:
            return max(p.sell_price_min_date or "", p.buy_price_max_date or "")

        for key in self.sniffer_scans:
            mine = old.get(key)
            if mine is None:
                continue
            i = index.get(key)
            if i is None:
                merged.append(mine)
                changed = True
            elif newest(mine) > newest(merged[i]):
                merged[i] = mine
                changed = True
        return merged, changed

    def recompute(self) -> None:
        if not self.last_prices:
            return
        config = self._effective_config(self.last_config)
        now = datetime.now(timezone.utc)
        opps = analyze_flips(
            prices=self.last_prices,
            history=self.last_history,
            config=config,
            now=now,
            fulfilled_orders=self.fulfilled_orders,
            price_overrides=self.price_overrides,
            reference=self.reference,
        )
        self.update(
            server=self.server,
            last_refresh=now.strftime("%Y-%m-%d %H:%M:%S UTC"),
            opportunities=opps,
            prices=self.last_prices,
            history=self.last_history,
            now=now,
            config=config,
        )

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
        history: Sequence[HistoryRecord] | None = None,
        now: datetime | None = None,
        config: AppConfig | None = None,
    ) -> None:
        if now is None:
            now = datetime.now(timezone.utc)
        elif now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)

        # Drop sniffer scans older than an hour and index the rest by item id.
        self.sniffer_scans = {k: v for k, v in self.sniffer_scans.items() if (now - v).total_seconds() < SNIFFER_WINDOW_SECONDS}

        sniffer_kept = False
        if prices:
            if prices is not self.last_prices:
                prices, sniffer_kept = self._keep_sniffer_prices(prices)
            self.last_prices = list(prices)
        if history:
            self.last_history = list(history)
        if config:
            self.last_config = config
        raw_config = config if config is not None else self.last_config
        config = self._effective_config(raw_config)

        # Flips computed by the caller miss sniffer prices newer than the API and the
        # dashboard's premium setting: redo them from the merged prices when needed.
        if self.last_prices and (sniffer_kept or (raw_config or AppConfig()).premium != config.premium):
            opportunities = analyze_flips(
                prices=self.last_prices,
                history=self.last_history,
                config=config,
                now=now,
                fulfilled_orders=self.fulfilled_orders,
                price_overrides=self.price_overrides,
            )
            prices = self.last_prices
        scans_by_item: dict[str, list[tuple[str, int, datetime]]] = {}
        for (s_item, s_city, s_quality), s_time in self.sniffer_scans.items():
            scans_by_item.setdefault(s_item, []).append((s_city, s_quality, s_time))

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

            # Live Sniffer Detection
            is_sniffer = False
            sniffer_age_secs = None
            for scan_city, scan_quality, scan_time in scans_by_item.get(o.item_id, ()):
                if scan_quality == o.quality and scan_city in (o.buy_city, o.sell_city):
                    age_s = max(0.0, (now - scan_time).total_seconds())
                    if age_s < SNIFFER_WINDOW_SECONDS:
                        is_sniffer = True
                        if sniffer_age_secs is None or age_s < sniffer_age_secs:
                            sniffer_age_secs = round(age_s)

            final_age = round((sniffer_age_secs or 0) / 60.0, 1) if is_sniffer else o.data_age_minutes
            if is_sniffer:
                is_fresh = True

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
                "est_daily_profit": o.est_daily_profit,
                "data_age_minutes": final_age,
                "risk": o.risk,
                "score": score,
                "deal_tier": tier_letter,
                "deal_label": label,
                "history_missing": o.history_missing,
                "is_new": is_new,
                "is_fresh": is_fresh,
                "is_live_sniffer": is_sniffer,
                "sniffer_age_seconds": sniffer_age_secs,
                "weight": round(o.weight, 2),
                "profit_per_kg": round(o.profit_per_kg, 1),
                # robust pricing (robust.py)
                "raw_buy_price": o.raw_buy_price,
                "raw_sell_price": o.raw_sell_price,
                "price_note": o.price_note,
                "ref_avg7": round(o.ref_avg7) if o.ref_avg7 else None,
                "units_per_day": round(o.units_per_day, 1),
            }


            if o.sell_city == "Black Market":
                serialized_blackmarket.append(entry)
            else:
                serialized_flips.append(entry)

        if current_bm_keys:
            self._previous_bm_keys = current_bm_keys

        # Sort by profit per item, but before cutting to 2,500 rows keep flips whose destination has
        # trade history first. Otherwise unverifiable listings on rarely traded items (often millions
        # "profit" per piece) fill the list and push out real opportunities. Live data 2026-10-29:
        # 2,480 of the 2,500 kept rows had no destination history.
        def _keep_order(x: dict[str, Any]) -> tuple[int, float]:
            unverified = x.get("history_missing") or "no_history" in (x.get("price_note") or "")
            return (1 if unverified else 0, -x["profit_per_item"])

        # Rows priced by a live sniffer scan are always kept, whatever their rank.
        serialized_flips.sort(key=_keep_order)
        serialized_flips = [x for i, x in enumerate(serialized_flips) if i < 2500 or x.get("is_live_sniffer")]
        serialized_flips.sort(key=lambda x: x["profit_per_item"], reverse=True)

        serialized_blackmarket.sort(key=_keep_order)
        serialized_blackmarket = [x for i, x in enumerate(serialized_blackmarket) if i < 2500 or x.get("is_live_sniffer")]
        serialized_blackmarket.sort(key=lambda x: x["profit_per_item"], reverse=True)


        # Clean & deduplicate prices first so all tabs are strictly synchronized:
        clean_prices = deduplicate_prices(
            prices=prices if prices else [],
            now=now,
            fulfilled_orders=self.fulfilled_orders,
            price_overrides=self.price_overrides,
        )

        serialized_prices = []
        if clean_prices:
            for p in clean_prices:
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

                    is_p_sniffer = (p.item_id, p.city, p.quality) in self.sniffer_scans
                    sniffer_secs = None
                    if is_p_sniffer:
                        age_s = max(0.0, (now - self.sniffer_scans[(p.item_id, p.city, p.quality)]).total_seconds())
                        if age_s < SNIFFER_WINDOW_SECONDS:
                            sniffer_secs = round(age_s)
                            age_minutes = round(age_s / 60.0, 1)
                        else:
                            is_p_sniffer = False

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
                        "is_live_sniffer": is_p_sniffer,
                        "sniffer_age_seconds": sniffer_secs,
                    })

            # Sort prices so freshest appear at the top, cap at top 2500
            serialized_prices.sort(key=lambda x: x["age_minutes"])
            serialized_prices = serialized_prices[:2500]

        # Crafting calculations using synchronized clean_prices and overrides
        serialized_crafting: list[dict[str, Any]] = []
        serialized_focus_crafting: list[dict[str, Any]] = []

        def scan_age(item_id: str, city: str, any_quality: bool = False) -> int | None:
            """Seconds since the sniffer captured item_id in city (quality 1 unless any_quality)."""
            best = None
            for s_city, s_quality, s_time in scans_by_item.get(item_id, ()):
                if s_city == city and (any_quality or s_quality == 1):
                    age = round(max(0.0, (now - s_time).total_seconds()))
                    best = age if best is None else min(best, age)
            return best

        def serialize_crafts(ops: Sequence[Any], limit: int) -> list[dict[str, Any]]:
            """Profitable crafts only, at most `limit` per category (plus every sniffer-priced one)."""
            rows: list[dict[str, Any]] = []
            per_type: dict[str, int] = {}
            best_rolls = quality_chances(MAX_QUALITY_ROLLS)
            for c in ops:
                # Profitable as Normal, or on average once better quality rolls are counted
                # (the dashboard picks how many rolls the player gets).
                avg_revenue = sum(ch * n for ch, n in zip(best_rolls, c.quality_nets or [0.0] * 5))
                cheapest_cost = min([c.effective_cost] + [o["effective_cost"] for o in c.city_options])
                if c.profit_per_item <= 0 and avg_revenue - cheapest_cost <= 0:
                    continue
                ingredients = []
                ages = []
                for ing in c.ingredients:
                    age = scan_age(ing["id"], ing["city"], any_quality=True)
                    if age is not None:
                        ages.append(age)
                    ingredients.append({**ing, "is_live_sniffer": age is not None, "sniffer_age_seconds": age})
                output_age = scan_age(c.item_id, c.sell_city)
                if output_age is not None:
                    ages.append(output_age)
                is_sniffer = bool(ages)
                # Keep the top deals of each category plus every deal that uses a sniffer price.
                rank = per_type.get(c.craft_type, 0)
                if rank >= limit and not is_sniffer:
                    continue
                per_type[c.craft_type] = rank + 1
                rows.append({
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
                    "city_return_rates": c.city_return_rates,
                    # Other profitable craft cities, shown when the Craft City filter picks one.
                    "quality_nets": c.quality_nets,
                    "quality_exits": c.quality_exits,
                    "expected_profit": round(c.expected_profit, 1),
                    "city_options": [
                        {**opt, "effective_cost": round(opt["effective_cost"], 1),
                         "profit_per_item": round(opt["profit_per_item"], 1), "margin_pct": round(opt["margin_pct"], 2),
                         "returned_value": round(opt["returned_value"], 1),
                         "sniffed": [scan_age(ing["id"], src, any_quality=True) is not None
                                     for ing, src in zip(c.ingredients, opt["cities"])]}
                        for opt in c.city_options
                    ],
                    "ingredients_desc": c.ingredients_desc,
                    "ingredients": ingredients,
                    "tax_paid": round(c.tax_paid, 1),
                    "returned_value": round(c.returned_value, 1),
                    "craft_silver": round(c.craft_silver, 1),
                    "sell_instant": c.sell_instant,
                    "city_bonus": c.city_bonus,
                    "tier": c.tier,
                    "enchant": c.enchant,
                    "weight": get_item_weight(c.item_id),
                    "item_value": round(c.item_value, 2),
                    "daily_volume": round(c.daily_volume, 1) if c.daily_volume is not None else None,
                    "is_live_sniffer": is_sniffer,
                    "output_sniffed": output_age is not None,
                    "sniffer_age_seconds": min(ages) if ages else None,
                })
            return rows

        # "Sniffer scans only": materials priced exclusively from sniffer captures.
        serialized_sniffer_crafting: list[dict[str, Any]] = []
        serialized_sniffer_focus_crafting: list[dict[str, Any]] = []
        sniffed_quotes = {(item, city) for (item, city, _quality) in self.sniffer_scans}
        if clean_prices:
            runs = [(False, 200, serialized_crafting, None), (True, 200, serialized_focus_crafting, None)]
            if sniffed_quotes:
                runs += [(False, 200, serialized_sniffer_crafting, sniffed_quotes),
                         (True, 200, serialized_sniffer_focus_crafting, sniffed_quotes)]
            for focus, limit, target, quotes in runs:
                craft_ops = analyze_crafting(
                    clean_prices,
                    config,
                    focus=focus,
                    item_names=ITEM_NAMES,
                    now=now,
                    fulfilled_orders=self.fulfilled_orders,
                    price_overrides=self.price_overrides,
                    history=self.last_history,
                    material_quotes=quotes,
                    reference=self.reference,  # server mode: cap sell orders at the 7-day average
                )
                target.extend(serialize_crafts(craft_ops, limit))

        # Enchanting & Artifact Foundry calculations
        serialized_enchanting: list[dict[str, Any]] = []
        artifact_mats_dict: dict[str, Any] = {}
        if clean_prices:
            enchant_ops, artifact_mats_dict = analyze_enchanting(
                prices=clean_prices,
                config=config,
                history=self.last_history,
                now=now,
                fulfilled_orders=self.fulfilled_orders,
                price_overrides=self.price_overrides,
                item_names=ITEM_NAMES,
            )
            # Prioritization & Complete Coverage for Enchanting:
            # 1. Any opportunity where the gear or mat was scanned by the sniffer is prioritized.
            # 2. Every slot type (Bags, Capes, Weapons, Armor, etc.) is preserved and not crowded out.
            # 3. Limit expanded to 3,500 items so all relevant gear is included.

            opp_sniffer_info: list[tuple[EnchantingOpportunity, bool, int | None, bool]] = []
            for e in enchant_ops:
                is_gear_sniffer = False
                is_mat_sniffer = False
                e_sniffer_age_secs = None
                for scanned_item in (e.base_item_id, e.target_item_id, *e.enchant_mat_id.split(" + ")):
                    is_mat = scanned_item not in (e.base_item_id, e.target_item_id)
                    for scanned_city, _, scan_time in scans_by_item.get(scanned_item, ()):
                        if scanned_city not in (e.city, e.sell_city):
                            continue
                        age_s = max(0.0, (now - scan_time).total_seconds())
                        if is_mat:
                            is_mat_sniffer = True
                        else:
                            is_gear_sniffer = True
                        if e_sniffer_age_secs is None or age_s < e_sniffer_age_secs:
                            e_sniffer_age_secs = round(age_s)

                is_any_sniffer = is_gear_sniffer or is_mat_sniffer
                opp_sniffer_info.append((e, is_any_sniffer, e_sniffer_age_secs, is_gear_sniffer))

            selected_entries: list[tuple[EnchantingOpportunity, bool, int | None]] = []
            seen_selected_keys: set[tuple[str, str, str, str]] = set()

            def add_entry(entry: tuple[EnchantingOpportunity, bool, int | None, bool]) -> None:
                op, snif, age, _ = entry
                k = (op.base_item_id, op.target_item_id, op.city, op.sell_city)
                if k not in seen_selected_keys:
                    seen_selected_keys.add(k)
                    selected_entries.append((op, snif, age))

            # 1. Gear directly sniffed (always top)
            for entry in opp_sniffer_info:
                if entry[3]:  # is_gear_sniffer
                    add_entry(entry)

            # 2. Material sniffed
            for entry in opp_sniffer_info:
                if entry[1]:  # is_any_sniffer
                    add_entry(entry)

            # 3. All Bags, Capes, Off-hands (ensure 100% coverage of accessories)
            for entry in opp_sniffer_info:
                if entry[0].item_type in ("Bag", "Cape", "Off-hand"):
                    add_entry(entry)

            # 4. Profitable opportunities
            for entry in opp_sniffer_info:
                if entry[0].is_profitable and len(selected_entries) < 3000:
                    add_entry(entry)

            # 5. Remaining opportunities up to 3,500
            for entry in opp_sniffer_info:
                if len(selected_entries) >= 3500:
                    break
                add_entry(entry)

            for e, is_e_sniffer, e_sniffer_age_secs in selected_entries:
                serialized_enchanting.append({
                    "base_item_id": e.base_item_id,
                    "target_item_id": e.target_item_id,
                    "item_name": e.item_name,
                    "tier": e.tier,
                    "from_enchant": e.from_enchant,
                    "to_enchant": e.to_enchant,
                    "step_label": f"{e.tier}.{e.from_enchant} ➔ {e.tier}.{e.to_enchant}",
                    "city": e.city,
                    "sell_city": e.sell_city,
                    "base_item_price": e.base_item_price,
                    "enchant_mat_id": e.enchant_mat_id,
                    "enchant_mat_name": e.enchant_mat_name,
                    "enchant_mat_qty": e.enchant_mat_qty,
                    "enchant_mat_unit_price": e.enchant_mat_unit_price,
                    "enchant_mat_total_cost": e.enchant_mat_total_cost,
                    "total_cost": e.total_cost,
                    "sell_price": e.sell_price,
                    "net_revenue": e.net_revenue,
                    "profit_per_item": e.profit_per_item,
                    "margin_pct": e.margin_pct,
                    "is_profitable": e.is_profitable,
                    "item_type": e.item_type,
                    "data_age_minutes": round(e.data_age_minutes, 1),
                    "is_live_sniffer": is_e_sniffer,
                    "sniffer_age_seconds": e_sniffer_age_secs,
                    "target_daily_volume": round(e.target_daily_volume, 1) if e.target_daily_volume is not None else None,
                    "target_history_missing": e.target_history_missing,
                    "liquidity_status": e.liquidity_status,
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
            self.sniffer_crafting = serialized_sniffer_crafting
            self.sniffer_focus_crafting = serialized_sniffer_focus_crafting
            self.enchanting = serialized_enchanting
            self.artifact_materials = artifact_mats_dict
            self.recent_prices = serialized_prices

    def get_data(self) -> dict[str, Any]:
        with self._lock:
            overrides_list = []
            for (item_id, city, quality), dt in self.fulfilled_orders.items():
                overrides_list.append({
                    "type": "fulfilled",
                    "item_id": item_id,
                    "item_name": get_human_name(item_id),
                    "city": city,
                    "quality": quality,
                    "time": dt.strftime("%H:%M:%S UTC"),
                })
            for (item_id, city, quality), price in self.price_overrides.items():
                overrides_list.append({
                    "type": "override",
                    "item_id": item_id,
                    "item_name": get_human_name(item_id),
                    "city": city,
                    "quality": quality,
                    "price": price,
                })

            sniffer_active = (time.time() - self.sniffer_last_timestamp < 180) if self.sniffer_last_timestamp else False
            secs_since = round(time.time() - self.sniffer_last_timestamp) if self.sniffer_last_timestamp else None

            profitable_enchant_count = len([e for e in self.enchanting if e.get("is_profitable")])

            sniffer_process = self.sniffer_manager.status() if self.sniffer_manager is not None else {"state": "disabled"}
            if sniffer_process.get("location_id"):
                sniffer_process["location_city"] = location_to_city(sniffer_process["location_id"])
            tax_config = self._effective_config(self.last_config)
            return {
                "server": self.server,
                "last_refresh": self.last_refresh,
                "premium": tax_config.premium,
                "tax_rate": tax_config.active_tax_rate,
                "setup_fee_rate": tax_config.setup_fee_rate,
                "sniffer_process": sniffer_process,
                "sniffer_status": {
                    "active": sniffer_active,
                    "packet_count": self.sniffer_packet_count,
                    "last_city": self.sniffer_last_city,
                    "last_time": self.sniffer_last_time.strftime("%H:%M:%S UTC") if self.sniffer_last_time else "",
                    "seconds_since_last": secs_since,
                },
                "flips": list(self.flips),
                "blackmarket": list(self.blackmarket),
                "bm_new_count": self.bm_new_count,
                "bm_recent_arrivals": list(self.bm_recent_arrivals),
                "crafting": list(self.crafting),
                "focus_crafting": list(self.focus_crafting),
                "sniffer_crafting": list(self.sniffer_crafting),
                "sniffer_focus_crafting": list(self.sniffer_focus_crafting),
                "enchanting": list(self.enchanting),
                "enchant_count": profitable_enchant_count,
                "artifact_materials": self.artifact_materials,
                "recent_prices": list(self.recent_prices),
                "live_sniffer_events": list(self.live_sniffer_events),
                "watchlist": list(self.watchlist),
                "watchlist_prices": self._watchlist_prices(),
                "watchlist_names": {i: get_human_name(i) for i in self.watchlist},
                "overrides_count": len(self.fulfilled_orders) + len(self.price_overrides),
                "overrides": overrides_list,
            }


class FlipRequestHandler(BaseHTTPRequestHandler):
    store: FlipDataStore

    def log_message(self, format: str, *args: Any) -> None:
        logger.debug("%s - - [%s] %s", self.address_string(), self.log_date_time_string(), format % args)

    def do_OPTIONS(self) -> None:
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_HEAD(self) -> None:
        clean_path = self.path.split("?")[0]
        if clean_path in ("/", "/index.html"):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
        elif clean_path in ("/api/flips", "/api/refresh", "/api/fulfill", "/api/override", "/api/clear_overrides", "/api/refresh_item", "/api/ingest", "/marketorders.ingest", "/markethistories.ingest"):
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
        length = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8")) if length > 0 else {}
        except Exception:
            body = {}

        is_history = "markethistories.ingest" in clean_path or (isinstance(body, dict) and "MarketHistories" in body)
        if is_history:
            try:
                uploads = body if isinstance(body, list) else [body]
                res = self.store.ingest_market_histories(uploads, sync=False)
                self._send_json({"status": "ok", "ingested_histories": res.get("histories_processed", 0), "details": res})
            except Exception as e:
                logger.error("Error in /api/ingest histories: %s", e)
                self.send_response(500)
                self.end_headers()
        elif clean_path.endswith(".ingest") and "marketorders.ingest" not in clean_path:
            self._send_json({"status": "ignored"})
        elif "marketorders.ingest" in clean_path or clean_path.startswith("/api/ingest"):
            try:
                orders = []
                if isinstance(body, list):
                    orders = body
                elif isinstance(body, dict):
                    orders = body.get("Orders") or body.get("orders") or []
                    if not orders and "ItemTypeId" in body:
                        orders = [body]
                res = self.store.ingest_market_orders(orders, sync=False)
                self._send_json({"status": "ok", "ingested": len(orders), "details": res})
            except Exception as e:
                logger.error("Error in /api/ingest: %s", e)
                self.send_response(500)
                self.end_headers()
        elif clean_path == "/api/refresh":
            self.store.trigger_refresh()
            self._send_json(self.store.get_data())
        elif clean_path == "/api/fulfill":
            try:
                item_id = body.get("item_id", "")
                city = body.get("city", "Black Market")
                quality = int(body.get("quality", 1))
                if item_id:
                    self.store.mark_fulfilled(item_id, city, quality)
                self._send_json(self.store.get_data())
            except Exception as e:
                logger.error("Error in /api/fulfill: %s", e)
                self.send_response(500)
                self.end_headers()
        elif clean_path == "/api/override":
            try:
                item_id = body.get("item_id", "")
                city = body.get("city", "Black Market")
                quality = int(body.get("quality", 1))
                price = int(body.get("price", 0))
                if item_id:
                    self.store.set_price_override(item_id, city, quality, price)
                self._send_json(self.store.get_data())
            except Exception as e:
                logger.error("Error in /api/override: %s", e)
                self.send_response(500)
                self.end_headers()
        elif clean_path in ("/api/sniffer/start", "/api/sniffer/stop", "/api/sniffer/restart", "/api/sniffer/show"):
            manager = self.store.sniffer_manager
            if manager is None:
                self._send_json({"status": "disabled"})
                return
            action = clean_path.rsplit("/", 1)[-1]
            if action == "show":
                manager.show_window()
            else:
                # start/restart can take a while (download, UAC prompt): run in the background.
                threading.Thread(target=getattr(manager, action), daemon=True).start()
            self._send_json({"status": "ok", "sniffer_process": manager.status()})
        elif clean_path == "/api/watchlist":
            if isinstance(body, dict):
                as_list = lambda v: [str(x) for x in v] if isinstance(v, list) else ([str(v)] if v else [])
                self.store.set_watchlist(add=as_list(body.get("add")), remove=as_list(body.get("remove")), clear=bool(body.get("clear")))
            self._send_json({"status": "ok", "watchlist": list(self.store.watchlist)})
        elif clean_path == "/api/settings":
            if isinstance(body, dict) and isinstance(body.get("premium"), bool):
                self.store.set_premium(body["premium"])
            self._send_json(self.store.get_data())
        elif clean_path == "/api/clear_overrides":
            self.store.clear_overrides()
            self._send_json(self.store.get_data())
        elif clean_path == "/api/refresh_item":
            try:
                item_id = body.get("item_id", "")
                if item_id:
                    self.store.refresh_single_item(item_id)
                self._send_json(self.store.get_data())
            except Exception as e:
                logger.error("Error in /api/refresh_item: %s", e)
                self.send_response(500)
                self.end_headers()
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
        elif clean_path == "/logo.svg" and asset_file("logo.svg").is_file():
            data = asset_file("logo.svg").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "image/svg+xml")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "max-age=86400")
            self.end_headers()
            self.wfile.write(data)
        elif clean_path == "/favicon.ico":
            icon_path = asset_file("icon.ico")
            if not icon_path.is_file():
                icon_path = asset_file("icon.png")
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


class LocalThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


LoopbackHTTPServer = LocalThreadingHTTPServer  # name used on main


def start_web_server(
    store: FlipDataStore,
    port: int = 8765,
    host: str = "127.0.0.1",
    handler_cls: type[FlipRequestHandler] = FlipRequestHandler,
) -> HTTPServer:
    """Starts the web server.

    Binds to 127.0.0.1 by default. The original version listened on all interfaces
    ("::"), so anyone on the same network could post fake prices to /api/ingest or
    overrides to /api/override, which have no login in desktop mode. Use host "::"
    or "0.0.0.0" only behind the login of ``albion_flips.server``.
    """
    class BoundHandler(handler_cls):  # type: ignore[valid-type, misc]
        pass

    BoundHandler.store = store
    server: HTTPServer
    if host in ("::", ""):
        try:
            server = DualStackHTTPServer(("::", port), BoundHandler)
        except Exception:
            server = LocalThreadingHTTPServer(("0.0.0.0", port), BoundHandler)
    else:
        server = LocalThreadingHTTPServer((host, port), BoundHandler)

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    logger.info("Web interface listening on %s port %d", host or "::", port)
    return server
