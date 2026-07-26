---
name: verify
description: Run the full offline verification pipeline (backend tests, frontend type-check + build, shell syntax checks) and report a pass/fail summary. Use after any code change, before handing over to the owner.
---

# Verify

Run everything that can be verified without launching the app (live testing is the owner's):

1. **Backend tests**
   ```bash
   cd backend && ../.venv/Scripts/python.exe -m pytest -q
   ```
2. **Frontend type-check + build** (strict TS — unused imports fail here)
   ```bash
   cd frontend && npx vue-tsc --noEmit && npm run build
   ```
3. **Shell scripts** — only if `shell/*.js` changed:
   ```bash
   node --check shell/main.js
   node --check shell/app-preload.js
   node --check shell/pick-preload.js
   ```

Report one line per step (pass/fail + the failing output when red). If anything fails, fix it and
re-run before handing over — never report a red pipeline as done.
