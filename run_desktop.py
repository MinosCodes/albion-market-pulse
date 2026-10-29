#!/usr/bin/env python3
"""Albion Market Pulse — Desktop Launcher

Launches the application as a standalone desktop companion app. This is also the
entry point of the packaged Windows .exe (see build_exe.bat).
"""
from __future__ import annotations

import logging
import os
import shutil
import sys


def _prepare_frozen_app() -> None:
    """First-run setup for the packaged .exe.

    - Creates an editable config.json next to the .exe from the bundled template.
    - A windowed .exe has no console (sys.stdout is None), so output goes to
      albion-market-pulse.log next to config.json instead.
    """
    from albion_flips.paths import BUNDLE_DIR, USER_DIR

    config_path = USER_DIR / "config.json"
    template = BUNDLE_DIR / "config.example.json"
    if not config_path.exists() and template.exists():
        shutil.copyfile(template, config_path)

    if sys.stdout is None or sys.stderr is None:
        log = open(USER_DIR / "albion-market-pulse.log", "w", encoding="utf-8", buffering=1)
        sys.stdout = sys.stdout or log
        sys.stderr = sys.stderr or log
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )


if __name__ == "__main__":
    if getattr(sys, "frozen", False):
        _prepare_frozen_app()

    from albion_flips.cli import main

    args = sys.argv[1:]
    if "--desktop" not in args:
        args.append("--desktop")
    code = main(args)
    # The window is closed and the Albion Data Client stopped: end the process now so
    # no leftover background thread (refresh, web server) keeps it alive.
    logging.shutdown()
    os._exit(code or 0)
