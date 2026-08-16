# PyInstaller spec for the longbox sidecar.
#
#     pyinstaller packaging/sidecar.spec --noconfirm
#
# ONEDIR, deliberately: a one-file build unpacks itself into a temp directory on
# every launch, which is exactly the startup cost the library work just removed.
# The built frontend rides along inside the bundle — the sidecar serves it.
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).resolve().parent
DIST = ROOT / "frontend" / "dist"
if not DIST.is_dir():
    raise SystemExit("frontend/dist is missing — run `npm run build` in frontend/ first")

# uvicorn picks its loop, protocol and lifespan implementations by name at
# runtime, so nothing imports them statically for the freezer to find.
hidden = collect_submodules("uvicorn") + [
    "app.main",
    "anyio._backends._asyncio",
]

a = Analysis(
    [str(ROOT / "backend" / "sidecar.py")],
    pathex=[str(ROOT / "backend")],
    binaries=[],
    datas=[(str(DIST), "frontend_dist")],
    hiddenimports=hidden,
    hookspath=[],
    runtime_hooks=[],
    # every megabyte here ships to the user; none of these are ever imported
    excludes=["tkinter", "pytest", "numpy", "matplotlib", "IPython", "setuptools"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="longbox-sidecar",
    console=False,  # no console window flashing up beside the app
    debug=False,
    strip=False,
    upx=False,  # UPX-packed binaries trip antivirus heuristics
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="longbox-sidecar",
)
