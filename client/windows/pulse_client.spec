# PyInstaller build for the Windows live client.
#   pip install pyinstaller==6.11.1
#   pyinstaller client/windows/pulse_client.spec      -> dist/AlbionMarketPulseClient.exe
# Run client/windows/bake_defaults.py first if the release should open with a default server.
import os

HERE = os.path.dirname(os.path.abspath(SPEC))          # client/windows
ROOT = os.path.dirname(os.path.dirname(HERE))          # repository root

a = Analysis(
    [os.path.join(HERE, "pulse_client.py")],
    pathex=[ROOT],
    datas=[
        (os.path.join(HERE, "defaults.json"), "."),
        (os.path.join(ROOT, "assets", "icon.ico"), "."),
    ],
    hiddenimports=["albion_flips.liveclient"],
    excludes=["requests", "rich", "pytest", "webview", "sqlite3"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="AlbionMarketPulseClient",
    icon=os.path.join(ROOT, "assets", "icon.ico"),
    console=False,
    upx=False,
    runtime_tmpdir=None,
)
