"""Runs the official Albion Data Client in the background, managed by this app.

The client (github.com/ao-data/albiondata-client) is a separate open-source program
that passively reads the game's market packets; this module never touches game
traffic itself. It only downloads, starts, monitors and stops that program, pointing
its uploads at the local dashboard (and, by default, the community AODP database):

    albiondata-client.exe -i "https+pow://albion-online-data.com,http://127.0.0.1:8765/api/ingest"

Windows only. Requirements and behaviour:
- Npcap must be installed (https://npcap.com). If Npcap is in "admin only" mode the
  client is started elevated, which shows one UAC prompt.
- The client is put in a Job Object that kills it when this app exits or crashes,
  including the copy it starts when it updates itself.
- The client opens its own status window at launch; with hide_window it is hidden
  (the client's tray icon stays, so the window can still be opened from there).
- The download is only installed when its SHA-256 matches the digest GitHub
  publishes for the release asset (see liveclient.install_data_client).
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable

from albion_flips.paths import USER_DIR

logger = logging.getLogger(__name__)

IS_WINDOWS = sys.platform == "win32"
AODP_UPLOAD = "https+pow://albion-online-data.com"
NPCAP_URL = "https://npcap.com/#download"
CLIENT_WINDOW_TITLE = "Albion Data Client"
LOCATION_UNKNOWN_TEXT = "players location has not yet been set"
LOCATION_UPDATE = re.compile(r"Updating player location to ([^.\s]+)")
_ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*m")  # terminal colour codes in the client log


# --------------------------------------------------------------------------- #
# Windows helpers (ctypes, no extra dependencies)
# --------------------------------------------------------------------------- #
if IS_WINDOWS:
    import ctypes
    import winreg
    from ctypes import wintypes

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _u32 = ctypes.WinDLL("user32", use_last_error=True)
    _shell32 = ctypes.WinDLL("shell32", use_last_error=True)

    _k32.CreateJobObjectW.restype = wintypes.HANDLE
    _k32.OpenProcess.restype = wintypes.HANDLE
    _k32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    _k32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
    _k32.TerminateJobObject.argtypes = (wintypes.HANDLE, wintypes.UINT)
    _k32.TerminateProcess.argtypes = (wintypes.HANDLE, wintypes.UINT)
    _k32.CloseHandle.argtypes = (wintypes.HANDLE,)
    _k32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    _k32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    _k32.GetProcessId.argtypes = (wintypes.HANDLE,)
    _k32.QueryFullProcessImageNameW.argtypes = (wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD))

    _WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    _u32.EnumWindows.argtypes = (_WNDENUMPROC, wintypes.LPARAM)
    _u32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
    _u32.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
    _u32.IsWindowVisible.argtypes = (wintypes.HWND,)
    _u32.ShowWindow.argtypes = (wintypes.HWND, ctypes.c_int)
    _u32.SetForegroundWindow.argtypes = (wintypes.HWND,)
    _u32.PostMessageW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
    _u32.FindWindowExW.restype = wintypes.HWND
    _u32.FindWindowExW.argtypes = (wintypes.HWND, wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR)
    _u32.GetClassNameW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)

    class _NOTIFYICONDATAW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND), ("uID", wintypes.UINT),
            ("uFlags", wintypes.UINT), ("uCallbackMessage", wintypes.UINT), ("hIcon", wintypes.HICON),
            ("szTip", wintypes.WCHAR * 128), ("dwState", wintypes.DWORD), ("dwStateMask", wintypes.DWORD),
            ("szInfo", wintypes.WCHAR * 256), ("uVersion", wintypes.UINT), ("szInfoTitle", wintypes.WCHAR * 64),
            ("dwInfoFlags", wintypes.DWORD), ("guidItem", ctypes.c_byte * 16), ("hBalloonIcon", wintypes.HICON),
        ]

    _shell32.Shell_NotifyIconW.argtypes = (wintypes.DWORD, ctypes.POINTER(_NOTIFYICONDATAW))
    WM_CLOSE = 0x0010
    NIM_DELETE = 0x00000002
    HWND_MESSAGE = wintypes.HWND(-3)
    # The client is a Wails app: closing this hidden window runs its normal quit path,
    # which also removes its tray icon (a killed process leaves a ghost icon behind).
    WAILS_QUIT_WINDOW = "__wails_hidden_mainthread"

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    PROCESS_TERMINATE = 0x0001
    PROCESS_SET_QUOTA = 0x0100
    SW_HIDE, SW_SHOW = 0, 5
    CREATE_NO_WINDOW = 0x08000000
    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
    JobObjectExtendedLimitInformation = 9

    class _IO_COUNTERS(ctypes.Structure):
        _fields_ = [(n, ctypes.c_ulonglong) for n in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

    class _BASIC_LIMIT(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _EXTENDED_LIMIT(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _BASIC_LIMIT),
            ("IoInfo", _IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    class _SHELLEXECUTEINFOW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD), ("fMask", ctypes.c_ulong), ("hwnd", wintypes.HWND),
            ("lpVerb", wintypes.LPCWSTR), ("lpFile", wintypes.LPCWSTR), ("lpParameters", wintypes.LPCWSTR),
            ("lpDirectory", wintypes.LPCWSTR), ("nShow", ctypes.c_int), ("hInstApp", wintypes.HINSTANCE),
            ("lpIDList", ctypes.c_void_p), ("lpClass", wintypes.LPCWSTR), ("hkeyClass", wintypes.HKEY),
            ("dwHotKey", wintypes.DWORD), ("hIconOrMonitor", wintypes.HANDLE), ("hProcess", wintypes.HANDLE),
        ]

    def _create_kill_on_close_job() -> int | None:
        job = _k32.CreateJobObjectW(None, None)
        if not job:
            return None
        info = _EXTENDED_LIMIT()
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        _k32.SetInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
        if not _k32.SetInformationJobObject(job, JobObjectExtendedLimitInformation, ctypes.byref(info), ctypes.sizeof(info)):
            _k32.CloseHandle(job)
            return None
        return job

    def _process_image(pid: int) -> str | None:
        handle = _k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return None
        try:
            buf = ctypes.create_unicode_buffer(1024)
            size = wintypes.DWORD(len(buf))
            if _k32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                return buf.value
            return None
        finally:
            _k32.CloseHandle(handle)

    def _client_pids() -> list[tuple[int, str]]:
        """(pid, image path) of every running albiondata-client process."""
        try:
            out = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq albiondata-client.exe", "/FO", "CSV", "/NH"],
                capture_output=True, text=True, timeout=10, creationflags=CREATE_NO_WINDOW,
            ).stdout
        except (OSError, subprocess.SubprocessError):
            return []
        found = []
        for line in out.splitlines():
            parts = [p.strip('"') for p in line.split('","')]
            if len(parts) > 1 and parts[1].isdigit():
                pid = int(parts[1])
                found.append((pid, _process_image(pid) or ""))
        return found

    def _set_client_windows(pids: set[int], show: bool) -> int:
        """Hides or shows the client's top-level status window(s). Returns how many changed."""
        changed = 0

        @_WNDENUMPROC
        def callback(hwnd: int, _lparam: int) -> bool:
            nonlocal changed
            owner = wintypes.DWORD()
            _u32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
            if owner.value not in pids:
                return True
            title = ctypes.create_unicode_buffer(256)
            _u32.GetWindowTextW(hwnd, title, 256)
            if CLIENT_WINDOW_TITLE not in title.value:
                return True
            visible = bool(_u32.IsWindowVisible(hwnd))
            if show and not visible:
                _u32.ShowWindow(hwnd, SW_SHOW)
                _u32.SetForegroundWindow(hwnd)
                changed += 1
            elif show and visible:
                _u32.SetForegroundWindow(hwnd)
            elif not show and visible:
                _u32.ShowWindow(hwnd, SW_HIDE)
                changed += 1
            return True

        _u32.EnumWindows(callback, 0)
        return changed

    def _client_window_handles(pids: set[int]) -> list[tuple[int, str, str]]:
        """(hwnd, class, title) of every window owned by pids, hidden and message-only ones included."""
        found: list[tuple[int, str, str]] = []

        def describe(hwnd: int) -> None:
            owner = wintypes.DWORD()
            _u32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
            if owner.value in pids:
                cls = ctypes.create_unicode_buffer(256)
                _u32.GetClassNameW(hwnd, cls, 256)
                title = ctypes.create_unicode_buffer(256)
                _u32.GetWindowTextW(hwnd, title, 256)
                found.append((hwnd, cls.value, title.value))

        @_WNDENUMPROC
        def callback(hwnd: int, _lparam: int) -> bool:
            describe(hwnd)
            return True

        _u32.EnumWindows(callback, 0)
        child = None
        while True:
            child = _u32.FindWindowExW(HWND_MESSAGE, child, None, None)
            if not child:
                break
            describe(child)
        return found

    def _ask_client_to_quit(pids: set[int]) -> None:
        for hwnd, _cls, title in _client_window_handles(pids):
            if title == WAILS_QUIT_WINDOW:
                _u32.PostMessageW(hwnd, WM_CLOSE, 0, 0)

    def _remove_tray_icons(pids: set[int]) -> None:
        """Removes the client's tray icon before it is force-killed."""
        for hwnd, cls, _title in _client_window_handles(pids):
            if not cls.startswith("Wails"):
                continue
            for uid in range(8):
                nid = _NOTIFYICONDATAW(cbSize=ctypes.sizeof(_NOTIFYICONDATAW), hWnd=hwnd, uID=uid)
                _shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))

    def _is_elevated() -> bool:
        try:
            return bool(_shell32.IsUserAnAdmin())
        except OSError:
            return False


def npcap_state() -> dict[str, Any]:
    """Npcap install state from the registry (the same keys the client checks)."""
    if not IS_WINDOWS:
        return {"installed": False, "admin_only": False, "winpcap_only": False}
    for path in (r"SOFTWARE\Npcap", r"SOFTWARE\WOW6432Node\Npcap"):
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path) as key:
                try:
                    admin_only = bool(winreg.QueryValueEx(key, "AdminOnly")[0])
                except OSError:
                    admin_only = False
                return {"installed": True, "admin_only": admin_only, "winpcap_only": False}
        except OSError:
            continue
    winpcap = False
    for path in (r"SOFTWARE\WinPcap", r"SOFTWARE\WOW6432Node\WinPcap"):
        try:
            winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path).Close()
            winpcap = True
        except OSError:
            pass
    return {"installed": False, "admin_only": False, "winpcap_only": winpcap}


# --------------------------------------------------------------------------- #
# Manager
# --------------------------------------------------------------------------- #
class SnifferManager:
    """Downloads, starts, watches and stops the Albion Data Client for this app."""

    def __init__(
        self,
        port: int = 8765,
        install_dir: Path | None = None,
        auto_download: bool = True,
        hide_window: bool = True,
        share_with_aodp: bool = True,
        http_fn: Callable[..., Any] | None = None,
        download_fn: Callable[[str, Path], None] | None = None,
    ) -> None:
        self._http_fn = http_fn          # tests only: GitHub API call
        self._download_fn = download_fn  # tests only: file download
        self.port = port
        self.install_dir = install_dir or USER_DIR / "albiondata-client"
        self.exe_path = self.install_dir / "albiondata-client.exe"
        self.log_path = USER_DIR / "albiondata-client.log"
        self.auto_download = auto_download
        self.hide_window = hide_window
        self.share_with_aodp = share_with_aodp

        self._lock = threading.RLock()
        self._state = "stopped" if IS_WINDOWS else "unsupported"
        self._message = "" if IS_WINDOWS else "Automatic start is Windows-only; run albiondata-client manually."
        self._help_url: str | None = None
        self._proc: subprocess.Popen[bytes] | None = None
        self._elevated_handle: int | None = None
        self._job: int | None = None
        self._pid: int | None = None
        self._elevated = False
        self._started_at: float | None = None
        self._stop_requested = False
        self._log_tail: deque[str] = deque(maxlen=12)
        # In-game location as seen by the client. Until the player changes zone after
        # the client starts, it does not know the location and drops every market scan.
        self._location_id: str | None = None
        self._needs_zone_change = False
        self._version: str | None = None
        self._hide_until = 0.0  # hide the client's window until this time (see _schedule_hide)

    # ----- public API ----------------------------------------------------- #
    @property
    def ingest_targets(self) -> str:
        local = f"http://127.0.0.1:{self.port}/api/ingest"
        return f"{AODP_UPLOAD},{local}" if self.share_with_aodp else local

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "state": self._state,
                "message": self._message,
                "help_url": self._help_url,
                "pid": self._pid,
                "elevated": self._elevated,
                "version": self._version,
                "uptime_seconds": round(time.time() - self._started_at) if self._started_at and self._state == "running" else None,
                "client_path": str(self.exe_path),
                "log_tail": list(self._log_tail),
                "location_id": self._location_id,
                "needs_zone_change": self._needs_zone_change and self._state == "running",
                "npcap": npcap_state(),
                "supported": IS_WINDOWS,
            }

    def start_async(self) -> None:
        threading.Thread(target=self.start, name="sniffer-start", daemon=True).start()

    def start(self) -> None:
        """Ensures the client is present and running. Safe to call repeatedly."""
        if not IS_WINDOWS:
            return
        with self._lock:
            if self._state in ("running", "starting", "downloading"):
                return
            self._stop_requested = False
            self._set("starting", "Starting the Albion Data Client...")

        try:
            npcap = npcap_state()
            if not npcap["installed"]:
                msg = ("Npcap is not installed, so the Albion Data Client cannot capture market data. "
                       "Install Npcap (leave 'Restrict Npcap driver's access to Administrators only' unchecked), then click Start.")
                if npcap["winpcap_only"]:
                    msg = "Only the outdated WinPcap driver is installed. " + msg
                self._set("needs_npcap", msg, help_url=NPCAP_URL)
                return

            if not self.exe_path.is_file():
                if not self.auto_download:
                    self._set("missing", f"albiondata-client.exe not found in {self.install_dir}")
                    return
                self._download_client()

            external = [pid for pid, image in _client_pids() if Path(image or "x").resolve() != self.exe_path.resolve()]
            if external:
                self._set("external",
                          "Another Albion Data Client is already running (pid "
                          f"{', '.join(map(str, external))}). It does not feed this app; close it and click Start.")
                return
            ours = sorted(self._our_pids())
            if ours:
                # Left over from a previous run (e.g. an elevated copy). Adopt it.
                with self._lock:
                    self._pid = ours[0]
                    self._started_at = time.time()
                self._set("running", "Albion Data Client is running.")
                self._schedule_hide()
                return

            if npcap["admin_only"] and not _is_elevated():
                self._start_elevated()
            else:
                self._start_hidden()
        except Exception as exc:  # surfaced in the dashboard
            logger.exception("Could not start albiondata-client")
            self._set("error", f"Could not start the Albion Data Client: {exc}")

    def stop(self, grace_seconds: float = 3.0) -> None:
        if not IS_WINDOWS:
            return
        with self._lock:
            self._stop_requested = True
        # Ask the client to quit by itself first so it removes its tray icon; force
        # it only if it is still running after grace_seconds.
        pids = self._our_pids()
        if pids:
            _ask_client_to_quit(pids)
            deadline = time.time() + grace_seconds
            while time.time() < deadline:
                time.sleep(0.2)
                pids = self._our_pids()
                if not pids:
                    break
            if pids:
                logger.info("Albion Data Client did not quit by itself; forcing it to close.")
                _remove_tray_icons(pids)
            else:
                logger.info("Albion Data Client quit cleanly.")
        with self._lock:
            proc, job, elevated_handle = self._proc, self._job, self._elevated_handle
            self._proc, self._job, self._elevated_handle = None, None, None
        if job:
            _k32.TerminateJobObject(job, 0)
            _k32.CloseHandle(job)
        if proc and proc.poll() is None:
            proc.kill()
        if elevated_handle:
            _k32.TerminateProcess(elevated_handle, 0)
            _k32.CloseHandle(elevated_handle)
        # A copy adopted from an earlier run is in no job; end it directly.
        for pid in self._our_pids():
            handle = _k32.OpenProcess(PROCESS_TERMINATE, False, pid)
            if handle:
                _k32.TerminateProcess(handle, 0)
                _k32.CloseHandle(handle)
        with self._lock:
            self._pid = None
            self._started_at = None
        self._set("stopped", "Albion Data Client stopped.")

    def restart(self) -> None:
        self.stop()
        self.start()

    def show_window(self) -> bool:
        """Brings the client's own status window to the front."""
        if not IS_WINDOWS:
            return False
        with self._lock:
            self._hide_until = 0.0  # the user wants to see it: stop auto-hiding
        pids = self._our_pids()
        if pids:
            _set_client_windows(pids, show=True)
        return bool(pids)

    # ----- internals -------------------------------------------------------- #
    def _our_pids(self) -> set[int]:
        """Running client processes started from this app's copy of the exe."""
        target = self.exe_path.resolve()
        return {pid for pid, image in _client_pids() if image and Path(image).resolve() == target}

    def _set(self, state: str, message: str, help_url: str | None = None) -> None:
        with self._lock:
            self._state = state
            self._message = message
            self._help_url = help_url
        logger.info("Sniffer: %s - %s", state, message)

    def _download_client(self) -> None:
        """Same checked install as the Windows live client: the gzip file must match the SHA-256
        GitHub publishes for the release asset, otherwise nothing is unpacked or started."""
        from albion_flips import liveclient  # local import: keeps sniffer importable on its own

        self._set("downloading", "Downloading the Albion Data Client from GitHub (ao-data/albiondata-client)...")
        asset = liveclient.fetch_release(http_fn=self._http_fn)
        installed = liveclient.install_data_client(self.install_dir, asset, self._download_fn)
        with open(installed, "rb") as f:
            if f.read(2) != b"MZ":
                installed.unlink(missing_ok=True)
                raise RuntimeError("downloaded file is not a Windows executable")
        if installed.resolve() != self.exe_path.resolve():
            os.replace(installed, self.exe_path)
        with self._lock:
            self._version = asset.version
        logger.info("Downloaded albiondata-client %s to %s (SHA-256 %s verified)", asset.version, self.exe_path, asset.sha256)

    def _start_hidden(self) -> None:
        log = open(self.log_path, "w", encoding="utf-8", errors="replace", buffering=1)
        proc = subprocess.Popen(
            [str(self.exe_path), "-i", self.ingest_targets],
            cwd=str(self.install_dir),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            creationflags=CREATE_NO_WINDOW,
        )
        job = _create_kill_on_close_job()
        if job and not _k32.AssignProcessToJobObject(job, int(proc._handle)):  # type: ignore[attr-defined]
            _k32.CloseHandle(job)
            job = None
        with self._lock:
            self._proc, self._job, self._pid, self._elevated = proc, job, proc.pid, False
            self._started_at = time.time()
            self._log_tail.clear()
            self._location_id, self._needs_zone_change = None, False
        self._set("running", "Albion Data Client is running in the background. Open the market in-game to stream prices.")
        threading.Thread(target=self._pump_output, args=(proc, log), name="sniffer-log", daemon=True).start()
        threading.Thread(target=self._watch, name="sniffer-watch", daemon=True).start()
        self._schedule_hide()

    def _start_elevated(self) -> None:
        """Npcap admin-only mode: start the client as Administrator (one UAC prompt)."""
        info = _SHELLEXECUTEINFOW()
        info.cbSize = ctypes.sizeof(info)
        info.fMask = 0x00000040  # SEE_MASK_NOCLOSEPROCESS
        info.lpVerb = "runas"
        info.lpFile = str(self.exe_path)
        info.lpParameters = f'-i "{self.ingest_targets}"'
        info.lpDirectory = str(self.install_dir)
        info.nShow = SW_HIDE
        _shell32.ShellExecuteExW.argtypes = (ctypes.POINTER(_SHELLEXECUTEINFOW),)
        if not _shell32.ShellExecuteExW(ctypes.byref(info)) or not info.hProcess:
            self._set("error", "Npcap is in admin-only mode and the Administrator prompt was declined. "
                               "Click Start to try again, or reinstall Npcap without the admin-only option.",
                      help_url=NPCAP_URL)
            return
        with self._lock:
            self._elevated_handle = info.hProcess
            self._pid = _k32.GetProcessId(info.hProcess)
            self._elevated = True
            self._started_at = time.time()
        self._set("running", "Albion Data Client is running as Administrator (Npcap admin-only mode).")
        threading.Thread(target=self._watch, name="sniffer-watch", daemon=True).start()
        self._schedule_hide()

    def _pump_output(self, proc: subprocess.Popen[bytes], log: Any) -> None:
        try:
            assert proc.stdout is not None
            for raw in proc.stdout:
                line = _ANSI_ESCAPE.sub("", raw.decode("utf-8", errors="replace")).rstrip()
                if not line:
                    continue
                log.write(line + "\n")
                with self._lock:
                    self._log_tail.append(line[-300:])
                self._note_location(line)
        finally:
            log.close()

    def _note_location(self, line: str) -> None:
        """Tracks whether the client knows the player's location (see __init__)."""
        if LOCATION_UNKNOWN_TEXT in line:
            with self._lock:
                first = not self._needs_zone_change
                self._needs_zone_change = True
            if first:
                logger.warning("Albion Data Client: location unknown, market scans are dropped until the player changes zone.")
            return
        match = LOCATION_UPDATE.search(line)
        if match:
            with self._lock:
                self._location_id = match.group(1)
                self._needs_zone_change = False

    def _watch(self) -> None:
        """Notices when the client exits. A self-update restarts it as a new process
        (still inside our job), so that case is adopted instead of reported as an error."""
        while True:
            with self._lock:
                proc, handle = self._proc, self._elevated_handle
                if self._stop_requested:
                    return
            if proc is not None:
                code = proc.wait()
            elif handle:
                _k32.WaitForSingleObject(handle, 0xFFFFFFFF)
                exit_code = wintypes.DWORD()
                _k32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
                code = exit_code.value
            else:
                return
            with self._lock:
                if self._stop_requested:
                    return

            time.sleep(3)  # give an updated copy time to appear
            ours = sorted(self._our_pids())
            if ours:
                with self._lock:
                    self._proc = None
                    self._pid = ours[0]
                self._set("running", "Albion Data Client updated itself and is running.")
                self._schedule_hide()
                self._poll_until_gone(ours[0])
                return

            tail = " | ".join(list(self._log_tail)[-3:])
            with self._lock:
                self._proc, self._pid, self._started_at = None, None, None
            self._set("error", f"Albion Data Client exited (code {code}). {tail}".strip())
            return

    def _poll_until_gone(self, pid: int) -> None:
        while True:
            time.sleep(5)
            with self._lock:
                if self._stop_requested:
                    return
            if pid not in {p for p, _ in _client_pids()}:
                with self._lock:
                    self._pid, self._started_at = None, None
                self._set("error", "Albion Data Client is no longer running. Click Start to relaunch it.")
                return

    def _schedule_hide(self) -> None:
        """The client shows its status window at launch; hide it for the first 30 s
        (and no longer, so opening it later from the tray still works)."""
        if not self.hide_window:
            return

        with self._lock:
            self._hide_until = time.time() + 30
            known_pid = self._pid

        def hide_loop() -> None:
            pids: set[int] = {known_pid} if known_pid else set()
            next_lookup = time.time() + 3
            while True:
                with self._lock:
                    if time.time() >= self._hide_until:
                        return
                if time.time() >= next_lookup:  # tasklist is slow; refresh pids every 3 s
                    pids |= self._our_pids()
                    next_lookup = time.time() + 3
                if pids:
                    _set_client_windows(pids, show=False)
                time.sleep(0.1)

        threading.Thread(target=hide_loop, name="sniffer-hide", daemon=True).start()
