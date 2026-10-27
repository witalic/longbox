# longbox — importing a folder you already have

Status: **built** — decisions in §12, the UI from the approved `design/folder-import-mockup.html`. Companion to `design/state-model.md` (§8 the vault, §9 ingest)
and `design/metadata-management.md` (§4 rules). Nothing here touches the user layer.

## 1. The problem

A library rarely starts empty. The owner already has media on disk, sorted by hand, and every
collection is sorted its own way: a manga folder per series with a folder or an archive per
chapter; a creator's folder holding batches of videos; volumes, years or seasons nested in
between. Today each work is made by hand and each file attached one at a time.

Hard-coding a layout ("manga = Series/Chapter") answers one collection and fails the next. The
import instead lets the owner say what each level of THEIR tree means, shows exactly what will be
created before anything is written, and keeps the answer as a profile.

## 2. The model — levels and roles

The owner picks a root folder. Every folder on the way down sits at a **level**: `L0` is the root
itself, `L1` its children, and so on. A profile gives each level a **role**:

| Role | The folder… | Creates |
|---|---|---|
| **Collection** | only groups things | nothing (its name is still a token, §3) |
| **Creator** | names a person, a circle or a studio | values in the fields the owner picks (§4) |
| **Work** | is one work | a title — exactly one level has this role |
| **Part** | divides a work: a volume, a season, a year, a batch | an entry section (§5) |

Creator and Collection sit above Work, Part below it. Levels deeper than the last one with a role
are parts too, so an irregular tree — one work holding its videos directly, the next one holding
`2025.1/` and `2025.2/` — needs no special case. Picking a creator's own folder as the root makes
`L0` the Creator: one creator shared by every work under it.

For trees whose depth varies ABOVE the work (some works directly in the root, others under a
creator folder), the Work role can instead be anchored to **the folder that holds the entries**;
the levels above it are then read from the top as usual, and parts are not available.

**Entries are found, not configured.** Inside a work and its parts:

- a video file → one episode;
- an archive (zip, cbz, rar, cbr, 7z, cb7) → one chapter;
- a folder holding only images → one chapter, packed into a zip on the way in;
- images lying loose in a work or part folder → one entry together (an image set).

An image named `cover`, `folder` or `poster` in a work folder becomes the work's cover (an
option, on by default). Anything else (`Thumbs.db`, `desktop.ini`, `.nfo`, `.txt`, …) is listed
as ignored — counted in the preview, never silently dropped.

A creator's folder of batches, read two ways — only the roles differ:

```
clips\                           L0  Collection
  vidhub @kumo Kumo Works\       L1  Creator   → authors: Kumo Works
    2025.1\                      L2  Work      → title "Kumo Works 2025.1", 5 episodes
    2025.2\                      L2  Work      → title "Kumo Works 2025.2", 7 episodes

clips\                           L0  Collection
  vidhub @kumo Kumo Works\       L1  Work      → title "Kumo Works", 12 episodes
    2025.1\                      L2  Part      → section "2025.1"
    2025.2\                      L2  Part      → section "2025.2"
```

## 3. Names

**Tokens.** Every level gives `{L0}`, `{L1}`, … (its folder name); roles give `{folder}` (the
work's folder), `{creator}` and `{part}` (nested parts join as `2025 / 1`). An entry adds `{file}`
(the original name without its extension), `{n}` (its number) and `{date}` (the file's date).

**A level's name pattern** splits a folder name into more tokens: `{site} @{handle} {creator}`
reads `vidhub @kumo Kumo Works` as site `vidhub`, handle `kumo`, creator `Kumo Works`. Literal text
between tokens must match; the last token takes the rest. A folder that does not match keeps its
whole name in the role's token and is flagged in the preview — never guessed. A regular expression
with named groups is the escape hatch for names a pattern cannot describe.

**Templates.** A work's title is a template over tokens: `{creator} {folder}` gives
`Kumo Works 2025.1`, `{folder}` alone gives `2025.1`. An entry's name defaults to `{file}`, with
optional clean-ups: drop `[bracketed tags]`, underscores to spaces. Parentheses are left alone —
`桜の舞(春)` carries meaning.

**Numbers.** Either **in order** — 1, 2, 3 through the work, parts in their order, files in natural
order (`2` before `10`; `step` before `step ロングver`) or oldest file first — or
**from the name** with a pattern (`Ch. {n}`, `E{n}`, `- {n}`), a file the pattern misses falling
back to its place in order — or **none**: the entry's name is its whole label, the way an entry
typed by hand carries its name, and a new work keeps the found order by hand (its chapter order is
`manual`; labels with no number would otherwise sort by their text). Numbers run through the whole work and never restart per part: an
entry's identity is number + language + group, and two "episode 1"s in one work would be one
entry. A pattern that yields the same number twice is a conflict in the preview.

## 4. Metadata — for every work, or for one

The import has a **Metadata** tab, and it is the editor a title already has (`MetadataEditor`):
the same blocks (what it is, who made it, topics, your own fields), the same controls — the type
and status pickers offer what the vault holds, chips suggest the vault's values, flags are the
same three toggles. Every field of the registry is there, built-in or the owner's own.

- **Every work** (the default scope): a filled field goes to every work the import creates or
  extends; an empty field is left alone. So "all of these are `mmd`, tagged `mmd`, `dance`" is
  typed once.
- **One work**: clicking a work's name in the preview scopes the same editor to that work. The
  values for every work show dim; what is typed there is that work's own — a list GAINS it, a
  single field is REPLACED. The preview marks such a work *own values*. (Several works at once —
  a selection, as in the library's select mode — is the same mechanism.)
- **Every entry**: one more block, only in the import — the entries' language and group, and
  whether an entry's date is its file's date.

Any value may hold a token — `{site}`, `{creator}`, `{folder}`, `{part}` — filled in per work:
`tags: {site}` gives each work the site its creator folder names. The Creator role (§2) shows up
here as a locked `{creator}` chip in the fields the owner chose for it — **authors**, **artists**,
**studio**, any of them (a circle that draws is an artist, an animation house is a studio); it is
changed in the structure, not here.

**Type** decides the shelf, so it is marked as such; leaving it empty leaves the work typeless.

Values enter the way captured ones do: **automatic** provenance, through the library's **rules**
(`rules.json`; only rules without a site condition apply — a folder has no site). An author
spelled two ways across folders lands as the one spelling a rule already enforces. For a work the
library already has, a single field is filled only where it is empty or automatic, and a list
gains values and never loses one — what the owner typed by hand is never touched, however often
the import runs (the provenance rule).

The metadata for every work is part of the profile; values given to one work belong to this run.

## 5. Parts — the nested folders

A part is anything that divides a work. Entries used to hold a number, a title, a language, a
group and a date — nowhere to keep "this episode is in 2025.2". So entries gain one free-text
field, **`section`**:

- the title page groups entries by it, in the order sections first appear; entries without one
  sit in no group, exactly as before;
- the contents editor sets it like any other entry field;
- it is a `title.json` shape change — one migration step, schema +1 (nothing to rewrite: an older
  document simply has no sections);
- the import writes `{part}` there (nested parts join as `2025 / 1`). It is useful beyond the
  import: manga volumes have the same shape.

Numbering is unchanged by it: numbers still run through the whole work, and a section never takes
part in an entry's identity (number + language + group).

## 6. Files, and what already exists

Options, kept per profile:

- **Files: copy | move.** Copy leaves the originals untouched (disk use doubles until the owner
  deletes them). Move deletes each original only after its entry is stored and its sidecar
  written; on the vault's own drive it is a rename and copies nothing. A folder of images packed
  into a zip is removed only once the zip is in place.
- **A work that already exists** (same title, case-insensitively): **add what is missing** to it |
  **create a separate work** | **skip it**. Two folders whose templates yield one title in the
  same run are a conflict in the preview.
- **An entry that already exists**: **skip** | **replace its media** (the entry keeps its id, read
  state and position — the same replace a manual attach does).

**Running it again is safe.** Each imported entry records where it came from in its sidecar:
`importedFrom: "folder"`, `origPath` (relative to the WORK's folder, so choosing a parent folder
next time still recognises it) and `origSize` (the stored file's own size differs after a repack
or a faststart). A second run over the same tree recognises them and skips — which is also how an
interrupted import is resumed. A file an earlier run stored is that entry for good: changed since,
it is that entry changed (replaced only with *replace*), never a second one under a new number;
and a newcomer bearing the number of an entry another file of the folder already is, is a
conflict — two files never take turns at one entry.

Entries a work already holds are found by either spelling of their language and group — as the
rules leave it, and as typed — exactly as a captured or downloaded chapter is, so a row stored
before a rule existed is never doubled. A token no folder of a work fills (`{site}` where the
pattern did not match) is left out of that work's values, never stored as text.

**Moving is never through a link.** The scan does not follow junctions or symbolic links, never
enters the library, and an entry refuses any source inside the vault, whatever its path says. A
move hard-links a writable original into the vault and deletes it once the entry is stored; a
read-only one is copied. When the delete fails, the vault's file is copied over itself, so it is
never one file with something outside.

## 7. Preview, then run

Nothing is written until the owner presses Import. The plan is a pure function of (scan, profile,
overrides, the library's titles): a change to a role or a template re-plans from the scan already
in memory, with no disk access per keystroke.

The preview lists the works to be created or extended, and each work's entries with number, name,
kind, size and a state: **new**, **in library** (skipped), **replace**, **conflict** (a duplicate
number or title), **unreadable** (checked cheaply at plan time: a zip's directory, an archive's
header, a video's first bytes — listed and left out, never stored as an opaque file),
**ignored**. Each work's row shows the metadata it will receive. The owner can untick a work or an
entry, retype a work's title, or give a work values of its own (§4); those choices belong to this
run, not to the profile.

The run is a background job: works one at a time, each under its title lock; every entry goes
through the same ingest as a download (zip invariant, faststart, ComicInfo, a sidecar stamped from
the stored file). Progress counts files and bytes. **Stop** lets the file in flight finish — the
vault never holds half an entry. A failed file does not stop the run; the report names it with the
reason. The index is written per work, so the library fills in while the run goes.

## 8. Profiles

A profile is the roles, patterns, templates, the metadata for every work and the options under a
name: "Creator video folders", "Manga · series / volume / chapter". Profiles hold no paths — they describe a shape, not
a place — and live in the vault (`import-profiles.json`, beside `rules.json`), so they travel with
the library. The last root used with each profile is remembered per machine, in the app config.

## 9. Where it lives

A view, **Import**, in the sidebar between Metadata and Settings, also reached from the library's
add menu: the rules on the left in three tabs — **Structure** (levels, roles, names, numbers),
**Metadata** (§4), **Files** (§6) — the preview on the right, a footer with the summary and the
Import button; while a run is going, the footer is its progress. The sidebar rail lists the
profiles and the last runs with their reports.

## 10. Backend

- `library/folder_import.py`:
  - `scan(root)` — one `os.scandir` walk into a tree held in memory (sizes and dates come with the
    directory entries; a network drive pays one pass);
  - `plan(scan, profile, overrides, library)` — pure; this is what most tests exercise;
  - the run — creation through `Library.create`, metadata into a title already there through
    `Library.amend`, each entry through `Library.import_entry` (one critical section: stage on
    the vault's volume, `_row_for`, the ingest every download takes, ComicInfo, commit), so chapter
    ids come from `chapter_id_for` and `_free_id` only. A file that fails is reported and the
    run goes on.
- `routers/imports.py`:
  - `POST /api/import/scan {root}` → a scan id, the levels with sample names, counts by kind;
  - `POST /api/import/plan {scanId, profile, overrides}` → the plan;
  - `POST /api/import/run {scanId, profile, overrides}` → the report, once the run ends; it takes
    the one vault-pass slot, so progress (with the file in flight) and Stop are the passes' own
    `/api/settings/pass/status` and `/api/settings/pass/stop`;
  - `GET /api/import/runs` → the last reports, kept per machine (they name its paths);
  - `GET|PUT /api/import/profiles`.
- The root comes from the shell's own folder picker and is trusted input on the loopback API, like
  a finished download's temp path.

## 11. Not in this pass

- Watching a folder and importing what appears in it.
- Metadata files beside the media (`.nfo`, `info.json`); ComicInfo inside archives is read already.
- A source link built from tokens (`vidhub @kumo` → the creator's page on that site).
- Leaving files in place and pointing at them — the vault owns its files (state-model §8).

## 12. Decisions (owner, 2026-10-27)

1. Parts become a **`section` field on entries** (§5) — not a prefix in the name.
2. Copy/move and what to do with existing works and entries are options of **each import
   profile** — no defaults in Settings.
3. Import is **its own item in the sidebar**.
4. Profiles live **in the vault** (`import-profiles.json`) and travel with the library.
