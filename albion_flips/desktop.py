from __future__ import annotations

import logging
import sys
import threading
import time
import webbrowser
from typing import Callable

logger = logging.getLogger("albion_flips.desktop")


def open_desktop_window(
    url: str = "http://127.0.0.1:8765",
    title: str = "Albion Market Pulse — Desktop Edition",
    width: int = 1380,
    height: int = 880,
    on_closed: Callable[[], None] | None = None,
) -> None:
    """Launches the application in a native desktop window using pywebview.
    
    Uses Microsoft Edge WebView2 on Windows 10/11, native WebKit on macOS,
    and WebKitGTK on Linux. If GUI bindings are missing or fail, falls back
    to opening the user's default browser.
    """
    try:
        import webview  # type: ignore

        logger.info("Opening native desktop window via pywebview...")
        window = webview.create_window(
            title=title,
            url=url,
            width=width,
            height=height,
            min_size=(960, 600),
            background_color="#0b0d14",
            text_select=True,
            zoomable=True,
        )

        if on_closed:
            window.events.closed += on_closed

        # Start native event loop (blocks until window is closed)
        webview.start(debug=False)

    except Exception as exc:
        logger.warning(
            "Native desktop window unavailable (%s). Falling back to system web browser at %s",
            exc,
            url,
        )
        print(f"\n[Desktop App] Notice: GUI window unavailable ({exc}).")
        print(f"[Desktop App] Opening in your web browser at {url} ...\n")
        webbrowser.open_new_tab(url)
        try:
            # Keep process alive if falling back to browser
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            if on_closed:
                on_closed()
