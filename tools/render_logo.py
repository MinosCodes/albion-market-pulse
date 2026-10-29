"""Renders assets/logo.svg into the app icons with headless Microsoft Edge (Windows).

    python tools/render_logo.py

Writes assets/icon.png (1024 px), assets/icon.ico (16-256 px) and assets/AppIcon.icns.
ICO and ICNS files embed PNG images directly, so no imaging library is needed.
"""
from __future__ import annotations

import os
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
EDGE_CANDIDATES = [
    Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Microsoft/Edge/Application/msedge.exe",
    Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Microsoft/Edge/Application/msedge.exe",
]


def find_edge() -> Path:
    for path in EDGE_CANDIDATES:
        if path.is_file():
            return path
    sys.exit("Microsoft Edge not found")


def render(edge: Path, svg: str, size: int, out: Path, workdir: Path) -> bytes:
    page = workdir / f"logo_{size}.html"
    page.write_text(
        "<!doctype html><html><head><style>html,body{margin:0;background:transparent;overflow:hidden}"
        f"svg{{display:block;width:{size}px;height:{size}px}}</style></head><body>{svg}</body></html>",
        encoding="utf-8",
    )
    subprocess.run(
        [str(edge), "--headless=new", "--disable-gpu", "--hide-scrollbars", "--force-device-scale-factor=1",
         "--default-background-color=00000000", f"--window-size={size},{size}",
         f"--user-data-dir={workdir / 'profile'}", f"--screenshot={out}", page.as_uri()],
        check=True, capture_output=True, timeout=60,
    )
    return out.read_bytes()


def write_ico(images: dict[int, bytes], path: Path) -> None:
    sizes = sorted(images)
    header = struct.pack("<HHH", 0, 1, len(sizes))
    offset = 6 + 16 * len(sizes)
    entries, blobs = b"", b""
    for size in sizes:
        data = images[size]
        dim = 0 if size >= 256 else size  # 0 means 256 in ICO entries
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(data), offset)
        blobs += data
        offset += len(data)
    path.write_bytes(header + entries + blobs)


def write_icns(images: dict[int, bytes], path: Path) -> None:
    types = {16: b"icp4", 32: b"icp5", 64: b"icp6", 128: b"ic07", 256: b"ic08", 512: b"ic09", 1024: b"ic10"}
    body = b"".join(t + struct.pack(">I", len(images[s]) + 8) + images[s] for s, t in types.items() if s in images)
    path.write_bytes(b"icns" + struct.pack(">I", len(body) + 8) + body)


def main() -> None:
    edge = find_edge()
    svg = (ASSETS / "logo.svg").read_text(encoding="utf-8")
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        images = {s: render(edge, svg, s, work / f"icon_{s}.png", work)
                  for s in (16, 24, 32, 48, 64, 128, 256, 512, 1024)}
    (ASSETS / "icon.png").write_bytes(images[1024])
    write_ico({s: images[s] for s in (16, 24, 32, 48, 64, 128, 256)}, ASSETS / "icon.ico")
    write_icns(images, ASSETS / "AppIcon.icns")
    print("wrote icon.png, icon.ico, AppIcon.icns")


if __name__ == "__main__":
    main()
