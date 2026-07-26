#!/usr/bin/env python
"""Launch longbox.

    python run.py             build the frontend, then launch the Electron shell
    python run.py --no-build  launch the shell without rebuilding the frontend
    python run.py --backend   run only the FastAPI sidecar (dev, no auth token)

The shell spawns its own sidecar; the built frontend is served by that sidecar at
/app/, so the frontend must be built (frontend/dist) before the shell starts.
"""
from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
IS_WIN = platform.system() == "Windows"
VENV_PY = ROOT / ".venv" / ("Scripts/python.exe" if IS_WIN else "bin/python")


def run(cmd: list[str], cwd: Path, *, shell: bool = False) -> None:
    print(f"$ {' '.join(cmd)}  (in {cwd})")
    subprocess.run(cmd, cwd=cwd, check=True, shell=shell)


def build_frontend() -> None:
    run(["npm", "run", "build"], ROOT / "frontend", shell=IS_WIN)


def _electron_cache_zip(version: str) -> Path | None:
    plat = "win32" if IS_WIN else ("darwin" if sys.platform == "darwin" else "linux")
    arch = "arm64" if platform.machine().lower() in ("arm64", "aarch64") else "x64"
    name = f"electron-v{version}-{plat}-{arch}.zip"
    if IS_WIN:
        root = Path(os.environ.get("LOCALAPPDATA", "")) / "electron" / "Cache"
    elif sys.platform == "darwin":
        root = Path.home() / "Library" / "Caches" / "electron"
    else:
        root = Path.home() / ".cache" / "electron"
    zip_path = root / name
    return zip_path if zip_path.is_file() else None


def ensure_electron(shell: Path) -> None:
    """Make sure the Electron binary is present, self-healing common failures."""
    pkg = shell / "node_modules" / "electron"
    binary = pkg / "dist" / ("electron.exe" if IS_WIN else "electron")
    if not (shell / "node_modules").is_dir():
        run(["npm", "install"], shell, shell=IS_WIN)
    if binary.exists():
        return
    # Trigger the package's own download+extract (bypassing npm's script gate).
    print("Electron binary missing — running its installer…")
    try:
        run(["node", str(Path("node_modules") / "electron" / "install.js")], shell, shell=IS_WIN)
    except subprocess.CalledProcessError:
        pass
    if binary.exists():
        return
    # electron's extract-zip can flake on the large binary; extract the cached zip ourselves.
    version = json.loads((pkg / "package.json").read_text())["version"]
    cached = _electron_cache_zip(version)
    if cached:
        print(f"Extracting Electron from cache: {cached}")
        with zipfile.ZipFile(cached) as zf:
            zf.extractall(pkg / "dist")
        (pkg / "path.txt").write_text("electron.exe" if IS_WIN else "electron")
        if not IS_WIN and binary.exists():
            binary.chmod(0o755)
    if not binary.exists():
        raise SystemExit(
            "Electron binary unavailable. Check network access to github.com "
            f"(Electron releases) or the cache. Expected: {binary}"
        )


def launch_shell() -> None:
    shell = ROOT / "shell"
    ensure_electron(shell)
    run(["npm", "start"], shell, shell=IS_WIN)


def run_backend() -> None:
    run([str(VENV_PY), "-m", "uvicorn", "app.main:app", "--app-dir", "backend",
         "--host", "127.0.0.1", "--port", "8787", "--reload"], ROOT)


def main(argv: list[str]) -> int:
    if "--backend" in argv:
        run_backend()
        return 0
    if "--no-build" not in argv:
        build_frontend()
    launch_shell()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
