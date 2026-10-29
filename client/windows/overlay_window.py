"""In-game overlay of the Windows live client.

What Overwolf apps do, and what this does instead
-------------------------------------------------
Overwolf injects a renderer into the game process to draw on top of it. Albion's developers
only tolerate tools that *look and analyse*; touching the game process is exactly what
anti-cheat looks for. So this overlay never touches the game:

* a normal, borderless, always-on-top window (works with Albion in *windowed* or
  *borderless window* mode, not in exclusive fullscreen),
* click-through (WS_EX_TRANSPARENT): mouse clicks go to the game below it,
* never takes focus (WS_EX_NOACTIVATE) and has no taskbar button (WS_EX_TOOLWINDOW),
* shown only while Albion (or this overlay) is the foreground window, like Overwolf does,
* global hotkeys via RegisterHotKey (the same API media players use, no keyboard hook):
    Ctrl+Shift+O  show / hide
    Ctrl+Shift+L  unlock to move it with the mouse, press again to lock (position is saved)
* content comes from your server (``/api/overlay/<key>/events``): when you open an item in
  the market, the live client uploads its orders and the server answers with an item card.
"""
from __future__ import annotations

import ctypes
import os
import queue
import re
import sys
import threading
import time
from ctypes import wintypes
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from albion_flips import liveclient as lc  # noqa: E402

IS_WIN = sys.platform == "win32"

# window styles
GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
GA_ROOT = 2
# hotkeys
MOD_CONTROL, MOD_SHIFT, MOD_NOREPEAT = 0x2, 0x4, 0x4000
WM_HOTKEY, WM_QUIT = 0x0312, 0x0012
HOTKEY_TOGGLE, HOTKEY_LOCK = 1, 2
VK_O, VK_L = 0x4F, 0x4C

COLORS = {
    "bg": "#0f1420", "panel": "#161d2d", "line": "#26314a", "text": "#e6e9ef", "muted": "#8a93a6",
    "good": "#4ade80", "bad": "#f87171", "warn": "#fbbf24", "accent": "#38bdf8",
}
VERDICT = {
    "cheap": ("GÜNSTIG", "good"), "fair": ("NORMAL", "muted"), "expensive": ("TEUER", "bad"), "unknown": ("KEIN Ø7", "muted"),
}
FONT = "Segoe UI"
WIDTH = 320
CARD_STALE_SECONDS = 180


def tier_label(item_id: str) -> str:
    m = re.match(r"^T(\d)_.*?(?:@(\d))?$", item_id or "")
    return f"T{m.group(1)}.{m.group(2) or 0}" if m else ""


def item_title(name: str | None, item_id: str) -> str:
    """'Master's Bow' + T6_2H_BOW@1 -> "Master's Bow T6.1"; fallback names already start with the tier."""
    name = name or item_id
    tier = tier_label(item_id)
    if re.match(r"^T\d\b", name) and tier.endswith(".0"):
        return name  # 'T4 Bag' already says the tier
    return f"{name} {tier}".strip()


# ---------------------------------------------------------------- Win32 helpers
if IS_WIN:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetAncestor.restype = wintypes.HWND
    user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetWindowLongW.restype = ctypes.c_long
    user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.SetWindowLongW.restype = ctypes.c_long
    user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]


def foreground_process_name() -> tuple[str, int]:
    """(exe name in lower case, pid) of the window the user is looking at."""
    if not IS_WIN:
        return "", 0
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return "", 0
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    h = kernel32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return "", pid.value
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(len(buf))
        if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return os.path.basename(buf.value).lower(), pid.value
        return "", pid.value
    finally:
        kernel32.CloseHandle(h)


def set_click_through(hwnd: int, enabled: bool) -> None:
    if not IS_WIN or not hwnd:
        return
    ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    ex |= WS_EX_LAYERED | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
    ex = ex | WS_EX_TRANSPARENT if enabled else ex & ~WS_EX_TRANSPARENT
    user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ex)


class HotkeyThread(threading.Thread):
    """RegisterHotKey + a tiny message loop. No keyboard hook, no key logging."""

    def __init__(self, out: queue.Queue) -> None:
        super().__init__(name="overlay-hotkeys", daemon=True)
        self.out = out
        self.tid = 0
        self.registered: list[str] = []

    def run(self) -> None:
        if not IS_WIN:
            return
        self.tid = kernel32.GetCurrentThreadId()
        for hk_id, vk, name in ((HOTKEY_TOGGLE, VK_O, "Ctrl+Shift+O"), (HOTKEY_LOCK, VK_L, "Ctrl+Shift+L")):
            if user32.RegisterHotKey(None, hk_id, MOD_CONTROL | MOD_SHIFT | MOD_NOREPEAT, vk):
                self.registered.append(name)
            else:
                self.out.put(("log", f"Hotkey {name} ist schon belegt (anderes Programm)."))
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY:
                self.out.put(("hotkey", int(msg.wParam)))
        for hk_id in (HOTKEY_TOGGLE, HOTKEY_LOCK):
            user32.UnregisterHotKey(None, hk_id)

    def stop(self) -> None:
        if IS_WIN and self.tid:
            user32.PostThreadMessageW(self.tid, WM_QUIT, 0, 0)


class StreamThread(threading.Thread):
    """Keeps the server's event stream open and reconnects with back-off."""

    def __init__(self, settings: lc.ClientSettings, out: queue.Queue) -> None:
        super().__init__(name="overlay-stream", daemon=True)
        self.settings, self.out = settings, out
        self.stopped = threading.Event()
        self._resp = None

    def run(self) -> None:
        delay = 2.0
        while not self.stopped.is_set():
            try:
                self.out.put(("status", "connecting"))
                self._resp = lc.open_event_stream(self.settings)
                self.out.put(("status", "live"))
                delay = 2.0
                for ev in lc.iter_sse(self._resp):
                    if self.stopped.is_set():
                        break
                    self.out.put(("event", ev))
            except lc.ClientError as e:
                self.out.put(("status", "error"))
                self.out.put(("log", str(e)))
                if "rejected" in str(e) or "no overlay" in str(e):
                    delay = 60.0
            except Exception as e:  # timeouts, resets: reconnect
                self.out.put(("status", "offline"))
                self.out.put(("log", f"Verbindung unterbrochen ({type(e).__name__}), neuer Versuch …"))
            finally:
                try:
                    if self._resp is not None:
                        self._resp.close()
                except Exception:
                    pass
                self._resp = None
            self.stopped.wait(delay)
            delay = min(30.0, delay * 2)

    def stop(self) -> None:
        self.stopped.set()
        try:
            if self._resp is not None:
                self._resp.close()
        except Exception:
            pass


class OverlayWindow:
    def __init__(self, master: Any, settings: lc.ClientSettings, log: Callable[[str], None] | None = None,
                 always_visible: bool = False, on_prefs: Callable[[dict], None] | None = None) -> None:
        import tkinter as tk

        self.tk = tk
        self.master = master
        self.settings = settings
        self.prefs = lc.clean_overlay_prefs(settings.overlay)
        self.log = log or (lambda _m: None)
        self.always_visible = always_visible
        self.on_prefs = on_prefs
        self.q: queue.Queue = queue.Queue()
        self.user_hidden = False
        self.locked = True
        self.status = "connecting"
        self.summary: dict | None = None
        self.card: dict | None = None
        self.card_at = 0.0
        self._shown = False
        self._drag = (0, 0)
        self._closed = False

        w = tk.Toplevel(master)
        self.win = w
        w.withdraw()
        w.overrideredirect(True)
        w.attributes("-topmost", True)
        w.attributes("-alpha", self.prefs["alpha"])
        w.configure(bg=COLORS["line"])
        w.geometry(f"+{self.prefs['x']}+{self.prefs['y']}")
        self.frame = tk.Frame(w, bg=COLORS["bg"], padx=10, pady=8)
        self.frame.pack(fill="both", expand=True, padx=1, pady=1)
        self.hwnd = 0
        try:  # DPI-aware process: pixel sizes have to grow with the display scaling
            self.scale = max(1.0, float(master.winfo_fpixels("1i")) / 96.0)
        except Exception:
            self.scale = 1.0

        self.stream = StreamThread(settings, self.q)
        self.hotkeys = HotkeyThread(self.q)

    # ---- lifecycle
    def start(self) -> None:
        self.render()
        self.stream.start()
        self.hotkeys.start()
        self.win.after(150, self._tick)
        self.win.after(500, self._visibility)

    def close(self) -> None:
        self._closed = True
        self.stream.stop()
        self.hotkeys.stop()
        try:
            self.win.destroy()
        except Exception:
            pass

    # ---- event pump (tk is single-threaded: threads only put into the queue)
    def _tick(self) -> None:
        if self._closed:
            return
        changed = False
        try:
            while True:
                kind, val = self.q.get_nowait()
                if kind == "event":
                    if val.get("type") == "summary":
                        self.summary = val
                    elif val.get("type") == "item":
                        self.card, self.card_at = val, time.time()
                    changed = True
                elif kind == "status":
                    changed = changed or val != self.status
                    self.status = val
                elif kind == "hotkey":
                    self._hotkey(val)
                    changed = True
                elif kind == "log":
                    self.log("Overlay: " + str(val))
        except queue.Empty:
            pass
        if changed:
            self.render()
        self.win.after(150, self._tick)

    def _hotkey(self, hk: int) -> None:
        if hk == HOTKEY_TOGGLE:
            self.user_hidden = not self.user_hidden
            self._apply_visibility()
        elif hk == HOTKEY_LOCK:
            self.locked = not self.locked
            self.user_hidden = False
            set_click_through(self.hwnd, self.locked)
            if self.locked:
                self.prefs["x"], self.prefs["y"] = self.win.winfo_x(), self.win.winfo_y()
                if self.on_prefs:
                    self.on_prefs(dict(self.prefs))
            self._apply_visibility()

    # ---- show only over the game
    def _game_in_front(self) -> bool:
        if self.always_visible or not self.locked:
            return True
        name, pid = foreground_process_name()
        return name in lc.GAME_PROCESSES or pid == os.getpid()

    def _visibility(self) -> None:
        if self._closed:
            return
        self._apply_visibility()
        if self.card and time.time() - self.card_at > CARD_STALE_SECONDS:
            self.card = None
            self.render()
        self.win.after(500, self._visibility)

    def _apply_visibility(self) -> None:
        want = not self.user_hidden and self._game_in_front()
        if want and not self._shown:
            self.win.deiconify()
            self.win.attributes("-topmost", True)
            self.win.update_idletasks()
            if IS_WIN:
                self.hwnd = user32.GetAncestor(self.frame.winfo_id(), GA_ROOT) or 0
            set_click_through(self.hwnd, self.locked)
            self._shown = True
        elif not want and self._shown:
            self.win.withdraw()
            self._shown = False

    # ---- drawing
    def _label(self, parent: Any, text: str, color: str = "text", size: int = 9, bold: bool = False, **kw: Any) -> Any:
        lab = self.tk.Label(parent, text=text, fg=COLORS[color], bg=kw.pop("bg", COLORS["bg"]),
                            font=(FONT, size, "bold" if bold else "normal"), anchor="w", justify="left", **kw)
        return lab

    def _row(self, parent: Any, bg: str = COLORS["bg"]) -> Any:
        r = self.tk.Frame(parent, bg=bg)
        r.pack(fill="x")
        return r

    def render(self) -> None:
        for c in self.frame.winfo_children():
            c.destroy()
        tk = self.tk
        head = self._row(self.frame)
        dot = {"live": "good", "connecting": "warn", "offline": "warn", "error": "bad"}.get(self.status, "muted")
        self._label(head, "●", dot, 9).pack(side="left")
        self._label(head, " Market Pulse", "text", 10, True).pack(side="left")
        info = {"live": "live", "connecting": "verbinde …", "offline": "offline", "error": "Fehler"}.get(self.status, "")
        if self.summary and self.summary.get("live_client_seconds_ago") is not None and self.status == "live":
            ago = int(self.summary["live_client_seconds_ago"])
            info = "Sniffer aktiv" if ago < 180 else "live"
        self._label(head, info, "muted", 8).pack(side="right")

        if self.card:
            self._card(self.card)
        top = (self.summary or {}).get("top") or []
        sep = tk.Frame(self.frame, bg=COLORS["line"], height=1)
        sep.pack(fill="x", pady=(6, 4))
        self._label(self._row(self.frame), "Beste Flips (mit 7-Tage-Historie)", "muted", 8).pack(side="left")
        if not top:
            self._label(self._row(self.frame), "Noch keine geprüften Flips." if self.summary else "Lade …", "muted", 9).pack(side="left")
        for i, f in enumerate(top[:5], 1):
            r = self._row(self.frame)
            name = item_title(f.get("item_name"), str(f.get("item_id")))
            self._label(r, f"{i}. {name[:26]}", "text", 9).pack(side="left")
            self._label(r, f"+{lc.silver(f.get('est_daily_profit'))}/Tag", "good", 9, True).pack(side="right")
            r2 = self._row(self.frame)
            self._label(r2, f"    {f.get('buy_city')} → {f.get('sell_city')}  ·  +{lc.silver(f.get('profit_per_item'))}/Stk", "muted", 8).pack(side="left")

        if not self.locked:
            hint = self._row(self.frame)
            self._label(hint, "Verschieben: ziehen · Strg+Umschalt+L sperrt", "warn", 8).pack(side="left", pady=(6, 0))
        self.frame.update_idletasks()
        self.win.geometry(f"{int(WIDTH * self.scale)}x{self.frame.winfo_reqheight() + 2}")
        self._bind_drag(self.frame)

    def _card(self, c: dict) -> None:
        box = self.tk.Frame(self.frame, bg=COLORS["panel"], padx=8, pady=6)
        box.pack(fill="x", pady=(6, 0))
        bg = COLORS["panel"]
        r = self._row(box, bg)
        title = item_title(c.get("item_name"), str(c.get("item_id")))
        if (c.get("quality") or 1) > 1:
            title += f" · Q{c['quality']}"
        self._label(r, title[:34], "text", 10, True, bg=bg).pack(side="left")

        seen = c.get("seen") or {}
        price = seen.get("sell_min") or seen.get("buy_max")
        word, col = VERDICT.get(seen.get("verdict") or "unknown", VERDICT["unknown"])
        r = self._row(box, bg)
        kind = "Angebot" if seen.get("sell_min") else "Gesuch"
        self._label(r, f"{seen.get('city') or '?'} {kind}: {lc.silver(price)}", "text", 9, bg=bg).pack(side="left")
        self._label(r, word, col, 9, True, bg=bg).pack(side="right")
        if seen.get("avg7"):
            pct = seen.get("vs_avg_pct")
            r = self._row(box, bg)
            self._label(r, f"Ø 7 Tage {lc.silver(seen['avg7'])}" + (f"  ({pct:+d} %)" if pct is not None else ""), "muted", 8, bg=bg).pack(side="left")

        bi, bo = c.get("best_instant"), c.get("best_order")
        if bi:
            r = self._row(box, bg)
            self._label(r, f"Sofort verkaufen: {bi['city']}", "muted", 9, bg=bg).pack(side="left")
            self._label(r, f"{lc.silver(bi['net'])} netto", "accent", 9, True, bg=bg).pack(side="right")
        if bo:
            r = self._row(box, bg)
            self._label(r, f"Verkaufsauftrag: {bo['city']}", "muted", 9, bg=bg).pack(side="left")
            self._label(r, f"{lc.silver(bo['net'])} netto", "accent", 9, True, bg=bg).pack(side="right")
        for f in (c.get("flips") or [])[:2]:
            r = self._row(box, bg)
            self._label(r, f"Flip {f.get('buy_city')} → {f.get('sell_city')}", "muted", 8, bg=bg).pack(side="left")
            self._label(r, f"+{lc.silver(f.get('profit_per_item'))}/Stk", "good", 8, True, bg=bg).pack(side="right")
        if not bi and not bo:
            self._label(self._row(box, bg), "Keine frischen Preise in anderen Städten.", "muted", 8, bg=bg).pack(side="left")

    # ---- moving (only while unlocked)
    def _bind_drag(self, widget: Any) -> None:
        widget.bind("<ButtonPress-1>", self._drag_start)
        widget.bind("<B1-Motion>", self._drag_move)
        for ch in widget.winfo_children():
            self._bind_drag(ch)

    def _drag_start(self, e: Any) -> None:
        self._drag = (e.x_root - self.win.winfo_x(), e.y_root - self.win.winfo_y())

    def _drag_move(self, e: Any) -> None:
        if self.locked:
            return
        self.win.geometry(f"+{e.x_root - self._drag[0]}+{e.y_root - self._drag[1]}")
