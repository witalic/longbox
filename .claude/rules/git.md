# Git & commits

Loaded every session (global rule — no `paths`).

- **Commit as the work goes, one logical block at a time.** When a block is done and verified (the
  `/verify` pipeline; a reviewer's findings fixed), commit it right then — never let several
  blocks pile up in the working tree to be split into history afterwards. A block that touches
  files another block will touch next is still committed first. **Never push** unless the owner
  asks — pushing is part of a release (below).
- Conventional Commits (`feat:` / `fix:` / `refactor:` / `chore:` / `docs:` / `test:`), optional
  scope (`feat(scraper/backend): …`); one logical change per commit.
- A commit must be a logically-complete, tested block — no dangling references to not-yet-built
  pieces.
- **Releases.** The version lives in `app-meta.json` only; `python tools/version.py X.Y.Z` sets it
  (with today's date) and writes every copy a tool insists on. Commit as `chore(release): X.Y.Z`.
  When the owner asks for the build, push `main` and a lightweight `vX.Y.Z` tag (as earlier
  releases), then watch the `build` run — the tag is what starts it, and CI refuses a tag or a
  copy that disagrees.
