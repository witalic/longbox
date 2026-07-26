---
paths:
  - "backend/tests/**"
---

# Tests

- Tests are **offline** — no network, no real sites. Vaults live in `tmp_path`; archives are
  built in-test with `zipfile` / `py7zr`.
- Run via the repo venv: `cd backend && ../.venv/Scripts/python.exe -m pytest`.
- Every data-safety fix gets a regression test (see `test_hardening.py` for the pattern:
  reconcile edge cases, zip-invariant conversions, guard refusals, migration corner cases).
- Use the `TestClient` fixture pattern from `test_downloads.py` for endpoint flows (arm → start →
  complete), and service-level `Library(tmp_path)` for vault behavior.
