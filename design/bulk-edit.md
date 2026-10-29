# longbox — editing many titles at once, from the library

Status: **built** — mockup `design/bulk-edit-mockup.html`, approved by the owner with D1–D3 as
proposed (2026-10-29). Panel: `frontend/src/components/BulkEdit.vue`.
Companion to `design/metadata-management.md` (the operation engine this runs on) and
`design/state-model.md` §5 (provenance).

## 1. Why

"Give these twelve titles the tag `isekai`" is a question about TITLES. The Metadata view is
built around VALUES — pick values of one field, rename, merge, remove them — so reaching titles
from there takes a detour (pick, re-scope, switch field, act). The titles are picked where they
are seen: in the library's select mode. That is where editing them belongs.

## 2. The way in

Browsing, the library's head band does not change: search, sort, density, Filters, and the ⋯
menu with *Select titles…* and *Add title*. In select mode, **Edit fields** takes the place of
*Edit in Metadata*:

```
today      37 selected · All 120 · Edit in Metadata · Delete · Cancel
proposed   37 selected · All 120 · [Edit fields]    · Delete · Cancel
```

It opens a panel docked on the right; the grid stays beside it and the selection can still
change — the panel follows it. *Edit in Metadata* moves into the panel's head (*In Metadata*),
one click away from where these titles are being edited.

*Delete* stays where it is: deleting titles is a different act from editing what they hold.

## 3. The panel: what the selection holds, and what to change

The fields in the editor's own blocks (who made it, what it is, topics, your fields), each
drawn by its shape:

- **A list** (authors, artists, studio, characters, genres, tags, your lists): every value at
  least one selected title holds, as a chip with how many hold it. A value **all** of them hold
  is a plain chip; one only **some** hold is dashed, with its count (`drama 12/37`).
  - `+ add…` (the field's own suggestions) adds a value to every selected title;
  - `×` on a chip removes it from every title that holds it;
  - a partial chip's `→ all` gives it to the rest;
  - *Clear* empties the field on all of them.
- **A single value** (type, status, year, your text, number and date fields): the values the
  selection holds with their counts, and *Set…* (the field's vocabulary) / *Clear*.
- **Flags**: each flag on for all, some or none; a click sets it on for all, the next off for
  all, the next leaves it as it was.

Out of reach on purpose: title, alternative titles and description (one per title), the cover,
chapters, and the user layer (rating, favourite, read), which has its own instant controls.

## 4. Nothing is written until Apply

Every click is a **pending change**, drawn on its chip (`+ isekai → 37`, a struck-through
`drama`) and listed in the panel's footer, each one removable. **Apply** shows the count it
will touch — "37 titles · 8 of the fields were typed by you" — and runs every change as ONE
operation through the Metadata engine (`operations.run` with the selection as its scope): one
read and one write per title, journaled, revertible from Metadata › History, with *Undo* in
the toast that reports it.

## 5. Provenance

Editing titles here is the owner editing those titles — the same act as typing on a title
page, only many at once. So a field it changes becomes **manual**: the next automatic capture
leaves it alone (I3), and the tag just added does not vanish when the source is captured
again. (A Metadata operation keeps a field's origin, because it tidies a vocabulary; it offers
a rule instead.) The footer says so: *Fields you change here become yours — capture will not
overwrite them.* (D1)

## 6. How it is built

- **What the selection holds** — `POST /api/metadata/held {scope, fields}` → for each field, how
  many titles hold anything in it and every value with its count. The panel asks again whenever
  the selection or the library changes; the docked panel takes the filter sidebar's place.
- **Apply** — the pending changes become the engine's own actions (`add`, `remove`, `set`,
  `clear`, flags as `add`/`remove` of a flag name), previewed for the count on the button and run
  with `own: true`. The run marks every field it changed `manual` (`operations.claim`) and
  journals each field's previous origin as a `provenance:<field>` row; a revert puts an origin
  back only together with its value, so a value typed since keeps the mark that protects it.
- **Undo** — the toast after Apply reverts that run; it stays in Metadata › History either way.

## 7. Decisions (owner, 2026-10-29)

- **D1 — provenance.** Changed fields become manual.
- **D2 — when it is written.** Pending changes, one Apply.
- **D3 — "Edit in Metadata".** Moves into the panel's head (*In Metadata*).
