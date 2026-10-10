#!/usr/bin/env python3
"""Albion Market Pulse — Desktop Launcher

Launches the application as a standalone desktop companion app.
"""
from __future__ import annotations

import sys
from albion_flips.cli import main

if __name__ == "__main__":
    # Ensure --desktop flag is passed
    args = sys.argv[1:]
    if "--desktop" not in args:
        args.append("--desktop")
    sys.exit(main(args))
