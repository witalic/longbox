# Git & commits

Loaded every session (global rule — no `paths`).

- **Never commit or push unless the owner explicitly asks.** Work accumulates in the working tree;
  the owner tests and decides when a block lands.
- Conventional Commits (`feat:` / `fix:` / `refactor:` / `chore:` / `docs:` / `test:`), optional
  scope (`feat(scraper/backend): …`); one logical change per commit.
- A commit must be a logically-complete, tested block — no dangling references to not-yet-built
  pieces.
- **Releases.** The version lives in `app-meta.json` only; `python tools/version.py X.Y.Z` sets it
  (with today's date) and writes every copy a tool insists on. Commit as `chore(release): X.Y.Z`,
  then the owner pushes the `vX.Y.Z` tag — CI refuses a tag or a copy that disagrees.
