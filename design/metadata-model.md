# longbox — metadata: standard fields, custom fields, and what each surface shows

Status: **§2–§4, the registry and custom fields are built; §6's surfaces are landing.** Companion to `design/state-model.md` (§4 the draft,
§5 provenance, §8 vault shape). Everything here is the metadata layer; the user layer
(fav/rating/read/position) is untouched by all of it.

## 1. Why now

The vault holds more than one kind of work. An episode wants a studio; a photo set
wants neither an author nor a chapter count; a manga wants neither a studio nor a
runtime. Today `TitleMeta` is a fixed list of fields, every one of them rendered for
every title, and the only way to record anything else is to abuse `tags`.

Three separate problems hide in that sentence, and they need separate answers:

1. **A missing standard field.** `studio` is genuinely universal enough to be built in.
2. **Fields the app cannot know about.** Whatever this particular library tracks —
   a rating from a site, a shelf code, a translator's note — belongs to the user, not
   to longbox. That needs *custom fields*, and they need types.
3. **Too many fields on screen.** Once both of the above exist, no surface should show
   all of them. What is worth showing depends on WHERE you are: a capture dock follows
   the site being captured; the library filters follow the person using them.

## 2. Standard fields

`studio: list[str]` joins `TitleMeta`, behaving exactly as `authors` does: a chip list,
an open library-wide vocabulary, a facet of its own, one provenance entry.

A list rather than a single string — co-productions are normal, and a single string
would be a value people immediately start splitting by comma.

This is a `title.json` shape change, so it is a migration step (`schema 2 → 3`) in
`library/migrations.py`: upgrade on read, persist on the next commit. Never an in-place
rewrite of the user's files.

## 3. Custom fields

### 3.1 Definitions live in the vault

```jsonc
// <root>/vault.json
{
  "fields": [
    { "id": "f_7c1a", "label": "Studio note", "type": "text",   "order": 0 },
    { "id": "f_9d20", "label": "MAL score",   "type": "number", "order": 1 },
    { "id": "f_3e88", "label": "Shelf",       "type": "list",   "order": 2 }
  ]
}
```

In the vault, not in app config, for one reason: they describe the user's *content*.
Move the library to another machine and its schema has to travel with it, or every
title is suddenly full of values nothing can name.

**`id` is stable and generated; the label is not the key.** Renaming "MAL score" to
"Score" must not orphan a single value.

### 3.2 Five types

| type | stored as | control | filters as |
|---|---|---|---|
| `text` | `str` | single-line input | facet, when asked |
| `description` | `str` | multi-line box | never — prose has no vocabulary to tick |
| `number` | `str` holding a number | numeric input | — (range later) |
| `list` | `list[str]` | chip list with vocabulary | facet, like tags |
| `date` | `str`, ISO `YYYY-MM-DD` | date input | — (range later) |

`boolean` remains deferred (§8).

**A type is a promise about the data, so a change carries the data with it.** Only a
list is shaped differently from the rest, so only a change into or out of one moves
anything; the separator it joins on (and splits back over) is the caller's, because
only they know what the values look like. Two rules keep the promise honest:

- **number and date can be left but never entered.** A field is a number because it
  was created as one; declaring arbitrary text a number is the same category error as
  calling a list of names one, and no check of the current values makes it true later.
- **a join no value survives is refused.** `["Ito, Junji", "Mori"]` folded on `", "`
  reads back as three names, and nothing afterwards can tell it was two.

Removing a field is two intentions, asked separately: drop the definition and keep
every value (re-defining the field brings them back), or take the data with it.

`date` is stored as a plain ISO string, not a timestamp: these are calendar days
(released, bought, finished), and a timestamp would invent a timezone nobody asked
for. `boolean` filters the way `fav` already does — tri-state, because "not set" and
"set to no" are different answers.

### 3.3 Values on a title

```jsonc
// title.json, inside the meta layer
"custom": { "malscore": "8.4", "shelf": ["boxed", "reread"] }
```

One map, keyed by field id, inside `meta` — so it is carried by the existing draft →
commit path, merged by the existing layer rules, and copied by `metaOf()` without a
new code path.

**The id is readable and chosen by the user** (`shelf`, `malscore`), not generated: it
is what a filter URL carries (`?f=shelf:boxed`) and what a stored value hangs on, so it
is fixed at creation while the LABEL stays free to change.

**Provenance keeps working unchanged.** It is a map keyed by field id, and a custom
field is keyed the same way as a built-in — the draft carries every field flat and
`metaForWire()` nests the user-defined ones once, on the way to the wire. The rule that
automatic capture may write only into `auto`/empty fields therefore applies to custom
fields for free, with no second mechanism.

### 3.4 Deleting a definition

Removing a field removes the DEFINITION only; the values stay in the documents. They
become invisible, and re-creating a field with that id brings them back. A vault-wide
write that strips values out of hundreds of `title.json` files is not something a
misclick should be able to do.

## 4. The field registry — what made the rest possible

`backend/app/library/fields.py` is the one description of what a field IS: its id,
label, type, control, whether it is editable, whether it is a facet, and how to read
its filterable values off a document. **It is served to the client** (`GET /api/fields`),
so the editor and the filter sidebar draw themselves from it.

The id is the SAME everywhere — query, facets, provenance, the custom map — which also
retired a standing confusion where the same field was `type` in the document and
`types` in the query.

The measure that prompted this: adding `studio` by hand cost about thirty edits across
ten files. With the registry it is a `Field(...)` line, a `TitleMeta` attribute and a
migration step; the frontend needs nothing at all.

## 4b. What this cost the query API

This was the one genuinely large piece, and it is worth stating plainly rather than
discovering halfway.

`LibraryQuery` today is about twenty hand-written parameters — `genres`, `genres_not`,
`tags`, `tags_not`, `authors`, `authors_not`, … Every facet is spelled out twice, in
the API, in the client, and in the SQL. Custom fields cannot be spelled out in advance,
so that shape cannot hold them.

A filter is a pair of repeated parameters:

```
GET /library?f=genres:action&f=studio:kyoani&nf=tags:ntr
```

with the field id as the key — and **standard fields keep their existing names as ids**
(`genres`, `tags`, `authors`, `studio`). One filter model, one facet model, one code
path; custom fields are then not a special case anywhere.

The index is a rebuildable cache, so it absorbs this cheaply: a `custom_values(title_id,
field_id, value)` table alongside the existing rows, filled by `Library._index` at write
time like everything else a listing needs.

**Cost, paid:** the library query, the facets endpoint, the store's filter state and
every call site moved at once. There is no half-migration that is not worse than both
ends. `Facets` stopped being a fixed class and became a map keyed by field id — a fixed
class would have meant a user-defined field could never have a facet.

## 5. Which fields a surface shows

Two surfaces, two different owners — this is the part with a real decision in it.

### 5.1 The capture dock → bound to the SOURCE

`Recipe` is already per-domain and already knows which fields it can extract. It gains:

```jsonc
{ "domain": "example.org", "show": ["title", "authors", "tags", "f_9d20"] }
```

Default when a recipe is learned: the fields it actually extracts, plus `title`.
A site that never gives a studio should not show a studio row in a 344px dock.

### 5.2 The library filters → bound to the USER

The filter sidebar already keeps its UI state in `local.ts` (`lb.facetOpen`). Field
visibility is the same class of preference and goes next to it (`lb.filterFields`).
It is a view preference, not content: it should not travel with the vault, and it
should not need a server round trip to change.

### 5.3 The title page's own editor — decided

The metadata editor is ONE component used by both the capture dock and the title
page's edit mode. The dock has a source domain; the title page may not.

**The title editor follows the user's own set**, the same preference the filters read.
Predictable, independent of where the title came from, and a title with no source at
all still edits fully. The dock is about a site; the title page is about the library.

## 6. What has to change in the UI

`MetadataEditor.vue` currently writes every field out by hand in the template. It
becomes schema-driven: a list of field definitions (standard + custom, in order),
rendered by type — text, number, chip list, vocabulary combo. Cover, flags and source
stay bespoke; they are not fields in this sense.

This is the change that makes custom fields possible at all, and it also removes the
existing duplication where adding a standard field means editing the template, the
draft seed and `metaOf()` in three places.

New surfaces:

- **Settings → Custom fields**: add, rename, reorder, retype, remove.
- **A "Fields" control** on the capture dock (writes the recipe) and on the filter
  sidebar (writes the local preference).

Both are UI work, so they go through a `design/*.html` mockup before implementation.

## 7. Order of work

1. ~~The registry, the generalised filter/facet model and the query API.~~ **Done.**
2. ~~`studio` + migration `schema 2 → 3` — landed THROUGH the registry, not around it.~~
   **Done.**
3. ~~Schema-driven `MetadataEditor`.~~ **Done** — same rows, same look, drawn from the
   served registry instead of written out by hand.
4. ~~Field definitions + `meta.custom` + provenance keys.~~ **Done** — the definitions
   live in `<vault>/fields.json` (they describe THIS library's data, so they travel
   with it), `PUT`/`DELETE /api/fields/{id}` maintain them, and deleting one keeps
   every value it held.
5. ~~Visibility, in its two scopes.~~ **Done** — ONE `FieldVisibility` widget over two
   different owners: the title page and the filters read a per-surface USER setting
   (`store.ts`, persisted locally), while the capture dock reads the SOURCE's own list,
   which rides in that domain's recipe. Hiding a facet clears its picks, so nothing
   filters from off-screen.
6. ~~Settings surface for creating and retyping definitions.~~ **Done** — a `Fields`
   card in Settings: the list is the manager, the id is derived from the first label
   and then frozen, and removing a field keeps every value it held.

## 8. Deliberately not in this pass

- Range filters on `number` and `date` (the value is stored and shown; it just is not a
  facet). Both are edited and stored as TEXT for the same reason `year` is: an unset
  number has to stay different from `0`.
- The `boolean` type. Its editor is a toggle and its filter is tri-state, and neither is
  built — so the API refuses the type rather than letting a field exist that no surface
  can draw or ask about.
- Per-media-type field sets (an anime showing `studio`, a manga not). It is a third
  visibility axis on top of the two above, and it should not be designed before the
  two exist.
- Custom fields on CHAPTERS. This is the title's metadata layer only.
