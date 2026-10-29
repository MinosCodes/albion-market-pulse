"""Albion Market Pulse - Windows live client (GUI).

Build:  see .github/workflows/windows-client.yml (PyInstaller, one file, no console)
Run from source:  python client/windows/pulse_client.py
Headless check:   pulse_client.exe --check [--report result.txt]

First start: enter the server address (pre-filled when the release was built with a default
server) and the key, or paste the pairing link the server owner gives you. After that the program
connects and starts the Albion Data Client automatically every time it is opened.
"""
from __future__ import annotations

import argparse
import locale
import sys
import threading
import webbrowser
from pathlib import Path

# allow running from the source tree: python client/windows/pulse_client.py
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from albion_flips import liveclient as lc  # noqa: E402

VERSION = "1.0.0"

_DE = (locale.getlocale()[0] or "").lower().startswith(("de", "german"))
T = {
    "title": "Albion Market Pulse – Live-Client" if _DE else "Albion Market Pulse – Live Client",
    "server": "Server-Adresse" if _DE else "Server address",
    "key": "Schlüssel" if _DE else "Key",
    "paste": "Kopplungslink einfügen" if _DE else "Paste pairing link",
    "test": "Verbindung testen" if _DE else "Test connection",
    "start": "Speichern und starten" if _DE else "Save and start",
    "auto": "Beim Öffnen automatisch verbinden und starten" if _DE else "Connect and start automatically when opened",
    "npcap_missing": "Npcap fehlt – ohne Npcap kann der Data Client nichts mitlesen." if _DE else "Npcap is missing – the Data Client cannot capture anything without it.",
    "npcap_get": "Npcap herunterladen" if _DE else "Get Npcap",
    "intro": ("Der Live-Client schickt deine Marktbesuche an die Community-Datenbank und an deinen Server. "
              "Der Server ruft auch ohne dich laufend Preise ab; das hier macht nur deine eigenen Besuche sofort sichtbar.")
             if _DE else
             ("The live client sends your market visits to the community database and to your server. "
              "The server keeps polling prices without you; this only makes your own visits show up instantly."),
    "checking": "Prüfe Verbindung …" if _DE else "Checking connection …",
    "downloading": "Lade den offiziellen Albion Data Client {v} (Prüfsumme wird kontrolliert) …" if _DE else "Downloading the official Albion Data Client {v} (checksum verified) …",
    "installed": "Albion Data Client {v} installiert, Prüfsumme stimmt." if _DE else "Albion Data Client {v} installed, checksum OK.",
    "running": "Der Albion Data Client läuft bereits. Schließe ihn und starte hier neu, falls er ohne deinen Server gestartet wurde." if _DE else "The Albion Data Client is already running. Close it and start again here if it was started without your server.",
    "admin": "Starte den Albion Data Client (Windows fragt nach Administratorrechten) …" if _DE else "Starting the Albion Data Client (Windows asks for administrator rights) …",
    "done": "Läuft. Öffne einen Markt in Albion Online – im Dashboard erscheint 'Sniffer: ACTIVE'." if _DE else "Running. Open a market in Albion Online – the dashboard shows 'Sniffer: ACTIVE'.",
    "saved": "Einstellungen gespeichert." if _DE else "Settings saved.",
}


def connect_and_start(s: lc.ClientSettings, log) -> None:
    """Full flow used by the GUI and by --start."""
    log(T["checking"])
    log(lc.check_connection(s))
    dest = lc.install_dir() / "albiondata-client"
    exe = dest / "albiondata-client.exe"
    asset = lc.fetch_release(s.data_client_version)
    if not exe.is_file() or lc.installed_version(dest) != asset.version:
        if lc.data_client_running():
            log(T["running"])
            return
        log(T["downloading"].format(v=asset.version))
        lc.install_data_client(dest, asset)
        log(T["installed"].format(v=asset.version))
    if lc.data_client_running():
        log(T["running"])
        return
    log(T["admin"])
    lc.start_data_client(exe, lc.data_client_args(s))
    log(T["done"])


def run_gui() -> None:
    import tkinter as tk
    from tkinter import ttk

    s = lc.load_settings()
    root = tk.Tk()
    root.title(T["title"])
    root.geometry("620x440")
    root.minsize(520, 380)
    try:
        icon = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2] / "assets")) / "icon.ico"
        if icon.is_file():
            root.iconbitmap(str(icon))
    except Exception:
        pass

    frm = ttk.Frame(root, padding=14)
    frm.pack(fill="both", expand=True)
    ttk.Label(frm, text=T["intro"], wraplength=580, justify="left").grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 10))

    server_var = tk.StringVar(value=s.server_url)
    key_var = tk.StringVar(value=s.ingest_key)
    auto_var = tk.BooleanVar(value=s.auto_start)
    ttk.Label(frm, text=T["server"]).grid(row=1, column=0, sticky="w")
    ttk.Entry(frm, textvariable=server_var, width=52).grid(row=1, column=1, columnspan=2, sticky="we", pady=2)
    ttk.Label(frm, text=T["key"]).grid(row=2, column=0, sticky="w")
    ttk.Entry(frm, textvariable=key_var, show="•", width=40).grid(row=2, column=1, sticky="we", pady=2)

    def paste_link() -> None:
        try:
            url, key = lc.parse_pairing_link(root.clipboard_get())
            server_var.set(url)
            key_var.set(key)
            log("OK: " + url)
        except Exception as e:
            log(str(e))
    ttk.Button(frm, text=T["paste"], command=paste_link).grid(row=2, column=2, sticky="e", padx=(6, 0))
    ttk.Checkbutton(frm, text=T["auto"], variable=auto_var).grid(row=3, column=1, columnspan=2, sticky="w", pady=(4, 8))

    btns = ttk.Frame(frm)
    btns.grid(row=4, column=0, columnspan=3, sticky="we")
    out = tk.Text(frm, height=10, wrap="word", state="disabled", relief="flat", background="#f4f4f4")
    out.grid(row=5, column=0, columnspan=3, sticky="nsew", pady=(10, 0))
    frm.columnconfigure(1, weight=1)
    frm.rowconfigure(5, weight=1)

    def log(msg: str) -> None:
        def _do() -> None:
            out.configure(state="normal")
            out.insert("end", msg + "\n")
            out.see("end")
            out.configure(state="disabled")
        root.after(0, _do)

    def current() -> lc.ClientSettings:
        s.server_url, s.ingest_key, s.auto_start = server_var.get(), key_var.get(), bool(auto_var.get())
        return s

    def in_thread(fn) -> None:
        def run() -> None:
            try:
                fn()
            except lc.ClientError as e:
                log("✖ " + str(e))
            except Exception as e:  # never show the key, only the error type
                log(f"✖ {type(e).__name__}: {e}")
        threading.Thread(target=run, daemon=True).start()

    def do_test() -> None:
        in_thread(lambda: log(lc.check_connection(current())))

    def do_start() -> None:
        def flow() -> None:
            lc.save_settings(current())
            log(T["saved"])
            connect_and_start(s, log)
        in_thread(flow)

    ttk.Button(btns, text=T["test"], command=do_test).pack(side="left")
    ttk.Button(btns, text=T["start"], command=do_start).pack(side="left", padx=8)

    if not lc.npcap_installed():
        bar = ttk.Frame(frm)
        bar.grid(row=6, column=0, columnspan=3, sticky="we", pady=(8, 0))
        ttk.Label(bar, text=T["npcap_missing"], foreground="#b45309").pack(side="left")
        ttk.Button(bar, text=T["npcap_get"], command=lambda: webbrowser.open("https://npcap.com/#download")).pack(side="right")

    if s.complete() and s.auto_start:
        root.after(300, lambda: in_thread(lambda: connect_and_start(s, log)))
    root.mainloop()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Albion Market Pulse live client")
    ap.add_argument("--check", action="store_true", help="check settings and server connection, then exit")
    ap.add_argument("--start", action="store_true", help="connect and start the Data Client without a window")
    ap.add_argument("--report", help="also write the output to this file (useful for the windowed .exe)")
    ap.add_argument("--version", action="store_true")
    args = ap.parse_args(argv)
    if not (args.check or args.start or args.version):
        run_gui()
        return 0

    lines: list[str] = []
    def log(msg: str) -> None:
        lines.append(msg)
        try:
            print(msg)
        except Exception:  # windowed build has no console
            pass

    code = 0
    try:
        if args.version:
            log(f"Albion Market Pulse live client {VERSION}")
        s = lc.load_settings()
        if args.check:
            log(lc.check_connection(s))
        if args.start:
            connect_and_start(s, log)
    except lc.ClientError as e:
        log("ERROR: " + str(e))
        code = 1
    if args.report:
        Path(args.report).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return code


if __name__ == "__main__":
    sys.exit(main())
