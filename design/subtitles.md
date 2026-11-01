# longbox — subtitles, and the downloads nobody armed

Status: **built** — mockup `design/subtitles-mockup.html`, approved by the owner with D1–D4 as
proposed (2026-10-30).

## 1. Why

Some sites serve an episode's subtitles as a file of their own (`.srt`, `.ass`, `.vtt`). Today
the browser keeps a download only when an entry is armed for it — anything else is cancelled —
and an episode has nowhere to keep subtitles: the player has none.

## 2. Downloads nobody armed

- **Armed** — as today: the file goes into the entry it was armed for.
- **Not armed** — no longer cancelled: it is saved into the library's own downloads folder,
  `<vault>/downloads/`. It moves with the library and is never read as a type shelf: it is one
  of the vault's own folders, and a type called *downloads* gets a shelf of its own (D1).
- **What it takes** — what the library can use: subtitles (`srt vtt ass ssa`), archives (`zip cbz
  rar cbr 7z cb7`), images and videos. Anything else (an installer, a script, a PDF) is still
  refused, and the Activity panel says so (D2).
- **What is kept** — the file under the name it came with (a clash becomes `name (2).ext`) and,
  in `downloads/index.json`, where it came from (page, file URL), its size and when. A file put
  there by hand shows too.
- **Where it shows** — a toast as it lands (*Saved to the library's downloads*), the activity
  corner counts what waits there (so it is never out of sight), and the Activity panel's section
  **In the library's downloads** lists each file with *Show* (the folder) and *Delete*. A subtitle
  is filed from its episode's form (§3) and leaves the folder; an archive or a video is attached
  with the entry's *Attach a file*, picked from the folder.

## 3. Subtitles on an episode

- An episode holds any number of subtitle tracks, each with a language, a label, and one marked
  **default** (shown when playback starts).
- **Adding them** — where an entry is edited, not on the page you watch:
  1. In the contents' **edit mode** an episode's row gets a **CC** action beside Edit entry and
     Attach. It opens a small form under the row, the way Edit entry does: the tracks it has
     (default, make default, remove) and one to add — a file dropped on the form or picked, or one
     from the library's downloads: subtitle files, newest first, one whose name carries the
     episode's number offered first. The language is read from the name (`.en.srt`, `[ENG]`,
     `ukr`) and can be changed; the label follows it.
  2. In the capture dock: arm the episode and download its subtitle file — it attaches as the
     episode's subtitles instead of being refused, and the video stays (D4).
- **Viewing gains nothing that takes room**: the row's mark **CC EN · UK**, and the player's own
  CC menu.
- **Stored as WebVTT**, the format the player shows: SRT is converted; ASS/SSA becomes text cues
  (styles, positions and karaoke flattened). The original is kept beside it (D3). A file with no
  readable cue is refused.
- **Files** — `chapters/<episode>.sub-<n>.vtt` (+ `.sub-<n>.orig.<ext>`), listed in the episode's
  sidecar: `subtitles: [{id, lang, label, default, origName, format, fileUrl}]`. They belong to the
  episode: replacing its video keeps them, removing the file or the entry removes them, and the
  vault check knows them (never strays).
- **Played** — one `<track>` per subtitle in the player, on the title page and in the reader; the
  player's own CC menu switches them.

## 4. Not in this pass

- Subtitles inside an `.mkv` — extracting them needs a demuxer the app does not have.
- Styled ASS rendering; shifting a track's timing.

## 5. How it is built

- `library/loose.py` keeps the folder: `files()` (what is there that the library can use, with
  where it came from), `take()` (a finished download moved in, a clashing name numbered),
  `remove()`. `/api/downloads/start` with nothing armed opens a loose item — or records a refused
  one and answers 409, which the shell takes as "drop it" as before; `/complete` moves a loose
  item into the folder.
- `library/subtitles.py` reads SRT, VTT and ASS/SSA (UTF-8, UTF-16 or a Windows Cyrillic code
  page) into WebVTT and a language off a name. `Library.add_subtitle` / `update_subtitle` /
  `remove_subtitle` work under the title lock; the tracks ride in the episode's sidecar and come
  out on each chapter as `subtitles`, served at `.../subtitles/{id}?v=<digest>`.
- The vault treats `<episode>.sub-*` as the episode's: never its media, never a stray, kept by a
  new video file, removed with the episode's file.
- `attach_chapter_media` turns a subtitle file for an episode into its subtitles (D4) — the armed
  flow and the entry's *Attach a file* alike; for an entry with no video it is refused.

## 6. Decisions (owner, 2026-10-30)

- **D1 — the folder.** `downloads`, a name a file manager shows (a share hides dot names); never
  a shelf, and a type called that gets `downloads-shelf`.
- **D2 — what it takes.** Files the library can use; anything else is refused and said.
- **D3 — the original.** Kept beside the WebVTT.
- **D4 — an armed episode.** Takes a subtitle file as its subtitles; the video stays.
