# PyInstaller spec for the longbox sidecar.
#
#     pyinstaller packaging/sidecar.spec --noconfirm
#
# ONEDIR, deliberately: a one-file build unpacks itself into a temp directory on
# every launch, which is exactly the startup cost the library work just removed.
# The built frontend rides along inside the bundle — the sidecar serves it.
import json
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

# Windows shows a nameless executable as its file name, which in a task manager
# reads like something that does not belong to the app. The version resource is
# generated from the app's own metadata so there is still ONE source of truth.
META = json.loads((ROOT / "app-meta.json").read_text(encoding="utf-8"))
VER = tuple(int(x) for x in (META["version"].split(".") + ["0", "0", "0"])[:4])
VERSION_FILE = Path(SPECPATH) / "win-version.txt"
VERSION_FILE.write_text(f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={VER}, prodvers={VER}, mask=0x3f, flags=0x0,
                    OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable("040904B0", [
        StringStruct("CompanyName", "longbox"),
        StringStruct("FileDescription", "longbox background service"),
        StringStruct("FileVersion", "{META['version']}"),
        StringStruct("InternalName", "longbox-sidecar"),
        StringStruct("OriginalFilename", "longbox-sidecar.exe"),
        StringStruct("ProductName", "longbox"),
        StringStruct("ProductVersion", "{META['version']}"),
    ])]),
    VarFileInfo([VarStruct("Translation", [1033, 1200])]),
  ],
)
""", encoding="utf-8")

a = Analysis(
    [str(ROOT / "backend" / "sidecar.py")],
    pathex=[str(ROOT / "backend")],
    binaries=[],
    # the built UI, and the app's identity — Settings reads the same file the
    # shell and this spec do, so a packaged build knows its own version
    datas=[(str(DIST), "frontend_dist"), (str(ROOT / "app-meta.json"), ".")],
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
    icon=str(ROOT / "shell" / "icon.ico"),
    version=str(VERSION_FILE),
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="longbox-sidecar",
)
