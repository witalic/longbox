# Playable copies

The window plays what Chromium decodes, and some stored episodes are not that:

- a **container** it does not open — `avi`, `ts`, `mov`, an `mkv` of other codecs (HEVC, AC-3, DTS…);
- a **picture** this machine does not decode — HEVC beyond Main10 (Range Extensions: 4:4:4, 4:2:2,
  12-bit), or H.264 High 10 (the usual anime encode). Chromium decodes HEVC only through the
  platform, and on Windows only through D3D11, which NVIDIA does not expose for Range Extensions;
  its H.264 decoder stops at 8-bit. The file opens, `canPlayType` says "probably", and the
  picture is dropped without an error: sound over black. The profile is read from the file's own
  decoder configuration (`avcC`/`hvcC`, or the mkv track's CodecPrivate).

Saying so is not a fix. Such an episode gets a **playable copy**.

## Decisions (owner, 2026-11-09)

- **Beside the original.** The original is never touched — it stays the archive's copy of record.
  The playable copy lives in the vault next to it (`chapters/<chapter-id>.play.mp4`), so it moves
  with the library and every machine that opens it plays it without converting again.
- **Automatically.** Nothing waits for a click: an episode that cannot play is queued the moment it
  is stored, every library open queues what is still missing, and a window that decodes no picture
  asks for one.

## What gets made

ffmpeg (shipped with the app through `imageio-ffmpeg`, a system ffmpeg as fallback; GPL v3, its
licence and source pointer in `packaging/licenses/`) writes an mp4 with the index in front:

- **Remux** — a picture every Chromium decodes in software (H.264 8-bit 4:2:0, VP9, AV1 4:2:0) is
  carried over untouched; only the container changes. Lossless, seconds. A remux ffmpeg refuses
  (broken timestamps in an avi or ts) falls through to an encode.
- **Encode** — anything else becomes H.264 High, 8-bit 4:2:0, no wider than 4K: the hardware
  encoder that runs here first (NVENC, Quick Sync, AMF, VideoToolbox), then libx264, then Media
  Foundation. HEVC is never kept — whether it plays depends on the graphics.
- **Sound** — AAC, MP3 and Opus are copied; anything else (AC-3, DTS, FLAC…) becomes AAC.
- First picture stream and first sound stream; embedded subtitles and attachments are left out.

## How it is kept

- The episode's sidecar records `copy: {codec, audio, remuxed, size, made}`; `playable` keeps describing
  the ORIGINAL. The chapter DTO reports `playable` (as stored or through its copy), `playableCopy`,
  and the codecs of what is actually played. The media version gains the copy's size, so its URL
  changes when the copy lands.
- `/video` serves the copy wherever one exists. Stills are cut from what plays.
- The copy is a derived file (`vault.derived_owner`), like the stills and subtitles: never taken
  for the media, never a stray, deleted with the episode, and dropped when a new file replaces the
  episode — it was a copy of the old bytes.
- A copy that cannot be made is remembered as `copyFailed: {of, error}` against the bytes it was
  tried on (revision, size, digest), so no launch retries a hopeless file on its own; the human can.

## When it runs

- `Library._index` — every write of a title — offers its episodes to the queue (`copies.py`); so
  do the end of a library's verification and the end of a one-time pass, off the index. Nothing
  is queued on the way to the first paint, and ffmpeg is located, never run, to answer whether
  copies can be made.
- While a one-time pass re-describes the vault the queue is **held**: sidecars may still say what
  an older build thought (every mkv "unplayable"). A job always re-reads the sidecar before it
  starts and is dropped if the file no longer needs a copy.
- Before the vault deletes, replaces or moves an episode's file (`Vault.let_go`: chapter delete,
  ingest, title delete, shelf moves) the copy of it is cancelled and its ffmpeg waited for — on
  Windows ffmpeg holds the original without delete-sharing.
- One copy at a time, on a background thread, ffmpeg at lowered priority with no console window.
- The conversion holds no lock. It writes a `.tmp` beside the episode and lands under the title lock
  only if the episode is still the bytes it read; otherwise the result is thrown away and the new
  bytes are offered again.
- A cancel or the app closing kills ffmpeg; the `.tmp` goes with it. A cancel holds for the
  session (the next write of the title does not queue it back); asking for the copy undoes it.
- ffmpeg is tied to the sidecar's lifetime: on Windows through a job object that kills it when the
  sidecar ends however it ends (the shell stops it with TerminateProcess); elsewhere the shell's
  SIGTERM closes the library, which stops it. The `.tmp` a hard stop leaves is cleared by the next
  attempt at that episode; the vault check never reports the copy being written as a leftover.
- New bytes behind an entry reset a failed or waiting copy of the old ones.

## What the window shows

- An episode waiting for its copy: "Making a playable copy" with the progress; a failed one says
  why, with Try again. The file can always be saved as it is.
- Activity lists every copy (progress, Cancel, Try again, Dismiss); the corner counts them.
- A window that opens a file the vault called playable and gets no picture — no video size on
  opening, no frame drawn over five seconds of playback, or an outright refusal — asks for a copy
  and shows the same state instead of a black screen.
- Copies that landed come back numbered from `/copies`, so a window that polls slowly still
  re-reads the titles they belong to.

## Not in this pass

- Embedded mkv subtitles (they need extracting to WebVTT — design/subtitles.md §4).
- Choosing between several sound tracks: the first one is kept.
- HDR tone mapping: a 10-bit HDR source encoded to 8-bit keeps its numbers, not its look.
