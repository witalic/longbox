# Process

Loaded every session (global rule — no `paths`).

- **The owner does all live testing** in the running app. Never take over the screen to poke the
  UI; verify with the offline test suite, the type-check and the build — and for UI changes a
  smoke load of the built UI over the test vault (`.dev/vault`) or an off-screen Electron harness
  — then hand over.
- Verification pipeline after any change:
  - backend: `cd backend && ../.venv/Scripts/python.exe -m pytest`
  - frontend: `cd frontend && npx vue-tsc --noEmit && npm run build`
  - shell scripts: `node --check shell/<file>.js`
- **UI redesigns are mockup-first:** build a self-contained HTML mockup in `design/`, screenshot
  it to verify it actually looks right, get the owner's approval, only then implement. When the
  owner says lines/spacing need "fixing", fix within the current design — do not redesign.
- Design-system law (from the approved handoff): heights 40 (chrome) / 44 (work bands: view
  heads, footers, filter top/bottom) / 30 (controls); ONE `--line` color; radii 6 (controls) /
  10 (cards) / 999 (pills); text scale 11/12/13/15/22 with `h1` = 22. Tokens have a single
  source: `frontend/src/styles.css`.
- New backend behavior lands with tests in `backend/tests/`.
