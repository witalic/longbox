# longbox — metadata management: operations across titles, and rules

Status: **built** — decisions in §11, the UI from the approved
`design/metadata-management-mockup.html`. Companion to
`design/state-model.md` (§4 the draft, §5 provenance) and `design/metadata-model.md` (the
field registry). Nothing here touches the user layer (fav/rating/read/position/reader).

## 1. The problem

A library built from many sites drifts. The same genre arrives as `Romance`,
`romance (255)` and `Романтика`; one person is `Kentaro Miura` on one site and `Miura
Kentarou` on another; a site files a status, an age rating or a format among its tags.
Today the only fix is opening titles one at a time — and the next capture brings the
drift straight back, because nothing remembers what the owner decided.

Two tools answer two different questions, and they share one vocabulary of actions:

| | **Operation** | **Rule** |
|---|---|---|
| changes | what titles ALREADY hold | what ENTERS a title |
| when | once, when the owner runs it | every time, from then on |
| scope | the whole library, the current filter, or a selection | optional conditions |
| runs | as a vault pass (progress, stop, report) | in the draft, before the preview |

## 2. Actions — the shared vocabulary

Every field of the registry (built-in and the owner's own), not just tags.

**List fields** (genres, tags, authors, artists, characters, studio, flags, custom lists):

- **Rename / merge** — one or several values become one value (`Romance`, `романтика` →
  `romance`). Merging is renaming several values to the same target.
- **Remove** — the value goes from every title in scope.
- **Move** — the value leaves this field for another, optionally renamed on the way
  (`tags: completed` → `status: completed`; `tags: Kentaro Miura` → `authors`). Moving into a
  single-value field (type, status, year…) fills it only where it is EMPTY — two candidates
  for one slot are reported, never decided silently. A value leaves its field only where it
  was placed: a title whose target slot is taken keeps it, and the preview counts it as a
  conflict.
- **Add** — a value is added to every title in scope (an operation), or whenever the rule's
  condition holds (a rule: "from site X, add tag `doujin`").

**Single-value fields** (title, alt, type, status, year, description, custom text/number/date):

- **Rename a value** — the same as for lists: `Ongoing (raw)` → `ongoing`.
- **Set** / **clear** — over a scope (an operation only).
- **Replace text** — a literal find → replace inside the value (strip ` (Official)` from
  titles). A `number`/`date` field only accepts what its type promises.

**Type** is a value like the others, but it decides the SHELF a title lives on, so changing
it moves files:

- renaming a type that every title on its shelf carries, into a type no shelf has yet
  (`manhwa` → `webtoon`), **renames the shelf directory** — one rename, however many titles;
- anything else (a filtered scope, a merge into a type that already has a shelf) moves each
  title's directory onto the target shelf, and sweeps a shelf left empty.

**Chapter values** — a chapter row's **language**, **translation group** and **section** are
fields too (`EN` / `en` / `English`; `Team X` / `TeamX`; `Vol. 1` / `vol 1`): rename / merge,
set or clear for a scope, and rules. A chapter's id never changes with them (it is the stem of
its files, state-model §8); a section is not part of the identity at all.
Two rows that end up sharing number + language + group stay two rows — each may hold its
own downloaded file — and the preview says how many will.

**Out of reach on purpose:** cover, source, chapters' media, provenance itself, and the user
layer.

## 3. Scope — "with filtering"

An operation runs over one of:

1. **the whole library**;
2. **the current filter** — exactly what the library shows now: shelf, search, include /
   exclude facets, favorites, progress, rating (the same selection `GET /api/library`
   takes, so "what you see is what changes");
3. **a selection** — the titles picked in the library's select mode ("Edit 37 titles").

…intersected with what the action needs: renaming `romance` only ever touches titles that
hold `romance`. Rules have **conditions** instead of a scope: the source domain the value
came from, and/or another value the draft holds (`tags` has `yaoi` → set flag `adult`).

## 4. Rules

A rule is `field + match + action [+ conditions]`, kept in an ordered list.

- **Match** — the value as capture has already cleaned it (`normalize.ts`: counts stripped,
  case folded where the field folds case), compared case-insensitively and whole. Pattern
  matching (contains / regex) is a decision (§11).
- **Order** — top to bottom, ONE pass: each rule sees what the previous ones produced, and
  no rule re-fires on its own output, so two rules can never loop.
- **Where they apply** — every value that enters a title, whoever brings it:
  - capture into a draft — auto-fill, a per-field pick, a merge from another page;
  - the owner's own typing in the editor — the chip or field shows what the rule made of it
    ("→ romance · rule"), so the rule is never a surprise;
  - chapter values on their way in — the chapter list capture, an armed download's identity,
    an import, page capture.

  The draft holds the rule's OUTPUT, so the preview shows exactly what will be stored (I4).
  A rule that moves or adds a value INTO another field sees what the draft already holds
  there (a single field is filled only where empty), and capture never writes into a field
  the owner typed — the same I3 the merge keeps.
- **One implementation** — the rule engine lives in the backend. The draft asks it
  (`POST /api/metadata/rules/apply`) before taking a value in — one local round trip — and the
  server-side entry points (arm, import) call it directly. No second copy of the logic in
  TypeScript that could disagree with the pass that applies rules to titles already stored.
- **Where they live** — the vault (`rules.json`, through `Vault.doc_transaction`, versioned
  by its own `SCHEMA`): they describe this library's vocabulary and travel with it, like its
  recipes.
- **Retroactive only on request** — saving a rule changes nothing already stored. The rule
  editor offers "apply to the N titles that hold it now", which is an operation (§5) with a
  preview. Persistent changes stay explicit (state-model §11).

## 5. Operations, and what keeps them safe

- **Preview first, always.** The change is computed from the index before anything is
  written: how many titles change, a sample of before → after per title, how many of the
  touched fields were typed by the owner (`manual`), how many titles would change shelf.
  Nothing is written until the owner confirms.
- **One pass at a time** — the existing slot (`passes.py`): progress, stop, and a line in
  `health.json` ("renamed `Romance` → `romance` in 1,284 titles, 4.1 s"). A stopped pass
  leaves every title either fully changed or untouched, and says how far it got. A title
  that cannot be read or written — a broken `title.json`, one written by a newer longbox, a
  file in use — is left as it is and counted, never guessed at; the rest of the run goes on.
- **Undo** — every run writes a journal of what it changed: per title, the field, the value
  before and the value it wrote (and, for a type change, the shelf it came from). *Revert*
  restores only fields that still hold the value the run wrote, so a later edit is never
  rolled back; a reverted type change moves the title back.

  The journal lives in **`index.db`**, in a table of its own: it is written in the same
  transactions as the index rows, costs no extra files, and is queried rather than parsed.
  That table is the one thing in the index a rebuild does not recreate — so it has its own
  version, a rebuild and a `titles` schema change leave it alone, and only deleting
  `index.db` loses it. What is lost then is the ability to undo, never content: the vault is
  still the truth (I7). Old runs are pruned (the last 50 are kept).
- **"Also make it a rule"** — offered (on by default) for rename, merge, remove and move,
  because of provenance (§6).

## 6. Provenance (I3)

- A value a rule produced at capture is still **auto**: it is what capture produces, and the
  next capture produces the same thing.
- An operation does **not** change a field's origin. So an `auto` field an operation changed
  would be reverted by the next capture of that title — unless a rule says otherwise. That is
  why the operation offers to remember itself as a rule, and the preview warns: "312 of these
  fields are automatic and no rule covers them; the next capture will bring the old value
  back."
- An operation **does** change `manual` fields. The provenance rule protects the owner's
  typing from AUTOMATIC capture, not from the owner's own bulk decision. The preview counts
  them separately so it is a decision, not an accident.
- The one exception is the library's bulk editor (design/bulk-edit.md): editing the picked
  titles is the owner editing each of them, so a run with `own` makes the fields it changes
  `manual`, and its revert puts their origin back with their value.

## 7. Where it lives in the app (to be mocked up)

One view of its own, **Metadata**, beside Sources in the rail — values, rules and their
history in one place, with the scope always in sight:

- **Scope** — the library's filter sidebar (the same `FilterBlock`), plus "selected titles"
  when the view is opened from the library's select mode (the bulk editor's *In Metadata* hands
  the selection over).
- **Fields** — every field of the registry, title fields and chapter fields, each with its
  number of distinct values in the scope.
- **Values** — the chosen field's values with their title counts, searchable and
  multi-select; actions on the selection: rename / merge, remove, move, add, and for
  single-value fields set / clear / replace text. Each opens the preview (§5), with "also
  make it a rule".
- **Rules** — the ordered list (drag to reorder, disable without deleting), an editor with a
  live "try a value" box, and per rule "apply to existing titles".
- **History** — the runs in the journal, each with what it changed and *Revert*.

## 8. Performance — thousands of titles is the normal case

The cost of a bulk change must scale with the titles that CHANGE, never with the library,
and each changed title must cost as little as the vault allows.

- **Scope and diff from the index.** Every title's document is already in the index, so the
  affected set and the exact new values are computed in memory without opening a file. A
  title that would not change costs nothing; the preview is that same computation.
- **One write per changed title.** Several actions run as one batch (rename twelve tags and
  move three in one go): a title is loaded, changed by all of them and saved ONCE.
- **A metadata-only index refresh.** Today every title write re-indexes through `_index`,
  which re-reads the title's chapter directory and every chapter sidecar. A metadata change
  touches neither, so the pass keeps the row's media and cover columns and updates only the
  document and its stamp — no per-title directory scan, which on a network vault is the
  difference between seconds and minutes.
- **Batched index transactions.** Rows are committed in chunks (one transaction per few
  hundred titles), not a commit per title.
- **Parallel file I/O.** Reads and writes go through a small worker pool; the per-title locks
  already make that safe, and on a network share each write is a round trip worth
  overlapping.
- **Archives are not rewritten.** A metadata change never rewrites gigabytes of zips: the
  `ComicInfo.xml` mirrors go stale and Settings → *Metadata inside the archives* brings them in
  line when the owner chooses (it already narrows to what differs).
- **The shelf order holds.** A bulk change is maintenance, not "I updated these 3,000
  titles": like a reader-settings write, it moves each document's stamp by the minimum step
  instead of to "now".
- **Type changes are the exception** — they move directories, so the preview counts them and
  they run last, after every in-place change. A whole shelf renamed into a new type is ONE
  directory rename; otherwise each move is a rename within the vault's own volume — no bytes
  are copied.
- **The journal costs no extra I/O** — its rows go into the same index transactions as the
  refreshed titles.
- **Rules are a lookup, not a scan.** Compiled per field into a dictionary of matches; a
  captured value costs one lookup per field.
- **One refresh at the end.** The UI reloads the library, facets and vocabulary once when the
  pass finishes, not per title.

**Measured, not assumed** — `tools/bench_metadata.py` generates a vault and renames one tag
over the same titles twice: the way the older passes write (load, save, full re-index, a
commit per title) and through the engine. 10,000 titles × 5 chapters, 5,023 holding the tag,
a local SSD on Windows, the vault's one-time migrations settled first:

| | older passes | engine |
|---|---|---|
| scope + preview | — | 0.72 s + 0.07 s |
| rename on 5,023 titles | 19.0 s (3.79 ms/title) | 8.1 s (1.61 ms/title) |

Single-threaded, the engine's cost per title is ~1 ms and almost all of it is the system
calls a vault write needs (open, write, rename, `utime`); Python's share is ~0.1 ms. Local
numbers swing by a factor of two to three between runs (a virus scanner reading every new
file, the filesystem cache), so the worker pool cannot be judged on a local disk — it exists
for a network share, where each of those calls is a round trip. **Network numbers are still
to be recorded:** `.venv/Scripts/python.exe tools/bench_metadata.py --vault //host/share/bench`.

## 9. API

The router (`backend/app/routers/metadata.py`) is authoritative. A scope travels in the
request body — a selection can be thousands of ids.

```
POST  /api/metadata/fields                 {scope} → {inScope, fields: [{id, label, kind, shape, distinct}]}
POST  /api/metadata/values                 {scope, field} → [{value, titles, typed, rule, ruleText}]
POST  /api/metadata/held                   {scope, fields[]} → {inScope, fields: {id: {titles,
                                           values: [{value, titles}]}}} — the bulk editor's view
POST  /api/metadata/preview                {scope, actions[]} → the preview (§5)
POST  /api/metadata/run                    {scope, actions[], remember, own} → runs as a vault
                                           pass; `own` makes the changed fields manual
GET   /api/metadata/runs                   the journal's runs, newest first
POST  /api/metadata/runs/{id}/revert       undo from the journal
GET   /api/metadata/rules · PUT …/rules    the ordered rule list (rules.json) + fired counts
POST  /api/metadata/rules/apply            {values, domain, rules?} → the values as the rules
                                           leave them (`rules` tries unsaved ones, uncounted)
POST  /api/metadata/rules/{id}/preview|run {scope} → a rule applied to stored titles
```

## 10. Not in this pass

- Splitting one value into several (`Romance, Comedy` → two genres).
- Conditions beyond "source domain" and "the draft holds value X".
- Pattern matching in rules (contains / regular expressions).

## 11. Decisions (owner, 2026-10-24)

1. **Rules apply to the owner's typing too**, visibly (§4).
2. **Match modes:** whole value only in this pass; patterns later.
3. **Undo:** a per-run journal, kept in `index.db` (§5).
4. **Shelf order:** bulk changes keep the "updated" order (§8).
5. **Scope of fields:** everything — title fields, chapter language, group and section, and the type,
   with its shelf directory renamed or its titles moved (§2).
6. **Placement:** one dedicated **Metadata** view (§7).
