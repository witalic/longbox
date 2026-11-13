# Contributing to longbox

Thanks for looking. longbox is a one-person project, so the most useful things are clear bug
reports (what you did, what you expected, what happened, your OS and the app's version from
Settings → About) and ideas that fit what the app is for. Before a larger change, open an issue to
talk it through — [ROADMAP.md](ROADMAP.md) says what is already planned. A security problem goes
privately, as [SECURITY.md](SECURITY.md) describes; everyone keeps to the
[code of conduct](CODE_OF_CONDUCT.md).

## Scope

longbox is a vault: the files are the source of truth and what goes into them is the user's
business. Changes that ship knowledge of particular websites — built-in scrapers, source catalogs,
shared recipe packs, online metadata services, tracker sync — are out of scope and will not be
merged.

## Setup

Requires Python ≥ 3.11, Node.js and npm. `run.py` expects the virtualenv at the repository root:

```bash
python -m venv .venv                  # then activate it (.venv\Scripts\activate on Windows)
pip install -e "backend[dev]"         # the sidecar, its test tools and pyinstaller
cd frontend && npm ci && cd ..
cd shell && npm ci && cd ..           # fetches Electron itself
```

```bash
python run.py             # build the frontend, launch the Electron shell (spawns the sidecar)
python run.py --no-build  # skip the frontend rebuild
python run.py --backend   # only the FastAPI sidecar on 127.0.0.1:8787 (dev, no auth token)
```

Work against a **test vault**, never your real library. `.dev/` at the repository root is ignored
by git for exactly this: when the first launch asks where to make a vault, make one there (say
`.dev/vault`); the sidecar-only mode opens it with `LONGBOX_LIBRARY_PATH=.dev/vault`. longbox never
creates a vault by opening a path.

F12 (or Ctrl+Shift+I) opens DevTools on the app's window in a development run; a released build
does that only when started with `LONGBOX_DEVTOOLS=1` in its environment.

## Checks

Every change passes these before it lands:

```bash
cd backend && python -m pytest        # offline API and vault tests
cd frontend && npx vue-tsc --noEmit   # type-check (strict; an unused import fails it)
cd frontend && npm run build          # production build (served by the sidecar at /app/)
node --check shell/main.js            # after touching shell scripts
python tools/version.py               # every copy of the version agrees with app-meta.json
```

New backend behaviour comes with tests in `backend/tests/`; tests are offline (no network, no real
sites) and build their vaults and archives in a temporary folder.

The same checks run on GitHub for every push to `main` and every pull request, on Windows and Linux
(`.github/workflows/checks.yml`).

## Conventions

- **English** for code, comments, UI text and documentation.
- **Conventional Commits** (`feat:`, `fix:`, `perf:`, `refactor:`, `docs:`, `test:`, `chore:`), an
  optional scope (`feat(sources): …`), one logical, tested change per commit.
- **Comments state constraints the code cannot show** — never narrate what the next line does.
- **UI redesigns start as a mockup** — a self-contained HTML file in `design/` — before any code.
- **The design docs are authoritative**: [ARCHITECTURE.md](ARCHITECTURE.md) for the process shape,
  vault layout and module map; [design/state-model.md](design/state-model.md) for the capture,
  edit and storage model and its invariants. Code follows them, and a change that diverges updates
  them in the same commit.
- The detailed rules — backend, frontend, tests, design, git — are in [.claude/rules/](.claude/rules/).
  They are written for the AI assistant the project is developed with, and apply to every
  contributor the same way.

Using an AI assistant for a contribution is fine; say so in the pull request.

## Versions and releases

The version has one source, `app-meta.json`, which the sidecar, the shell and the About page read
directly. The files that must carry a literal of their own — `shell/package.json` and its
lockfile (electron-builder names the installers and the release after it),
`backend/pyproject.toml` and the project page — are written by one command, never by hand:

```bash
python tools/version.py 3.4.0         # set the version (and today's date) and write every copy
python tools/version.py               # check that nothing disagrees
```

A release is that bump committed as `chore(release): 3.4.0`, with what was under `Unreleased` in
[CHANGELOG.md](CHANGELOG.md) moved into a `## [3.4.0] — date` section, then `main` and the
lightweight tag `v3.4.0` pushed: the tag starts the build. That section becomes the release's notes
on GitHub (`python tools/release_notes.py 3.4.0` shows them).

## Packaging

Builds run on GitHub Actions (`.github/workflows/build.yml`) for Windows, macOS (arm64 and x64) and
Linux: a pushed `v*` tag naming the version in `app-meta.json` publishes a release, and the
workflow can be run by hand for artifacts. CI refuses a tag — or any copy of the version — that
disagrees, and a tag the changelog has no section for. Locally:

```bash
cd frontend && npm run build                                   # the UI the sidecar serves
pyinstaller packaging/sidecar.spec --noconfirm --distpath dist-sidecar --workpath build-sidecar
cd shell && npm run pack                                       # installers into dist-app/
```

Every build turns off Electron's run-as-Node, `NODE_OPTIONS` and `--inspect` fuses and fails if
one stays on (`shell/after-pack.js`); macOS builds are signed ad hoc after that
(`shell/adhoc-sign.js`), since flipping a fuse breaks a signature.

A packaged app carries the sidecar as a frozen binary (with the built UI inside it), so it needs
neither Python nor a checkout. The ffmpeg that makes playable copies rides inside the sidecar, from
the pinned `imageio-ffmpeg` wheel; its license and where its source is ship in the app's
`resources/licenses/` ([packaging/licenses/FFmpeg.txt](packaging/licenses/FFmpeg.txt)).

## Repository layout

```
backend/    FastAPI sidecar: the vault, its SQLite index, chapter media, recipes, the API
frontend/   Vue 3 + Vite + TypeScript UI: library, title page, reader, capture browser, settings
shell/      Electron: sidecar spawn and auth, the embedded browser, download interception, preloads
design/     design docs (state-model.md is the canonical architecture) and UI mockups
docs/       the project page (witalic.github.io/longbox) and the user guide
packaging/  the frozen-sidecar spec and the bundled ffmpeg's license notice
.github/    the checks (every push), the build (a version tag), the issue and pull request forms
tools/      version.py (the one version, everywhere) · release_notes.py (a release's notes, from
            the changelog) · screenshots.py (the project page's images) · bench_metadata.py (what a
            bulk metadata change costs)
```
