// Episode stills — decoded ONCE, in the window, kept in the vault.
//
// longbox ships no decoder (the faststart remux is hand-written byte surgery),
// so a frame cannot be cut on the backend. But the WINDOW decodes these files
// already: it plays them. So it seeks, draws what it finds, and hands the JPEGs
// to the vault. Two things come out of the one pass:
//
//   poster — the liveliest single frame, worn by a tile in the library. A black
//            title card is not a poster, so three or twelve spots are sampled
//            and the flattest ones lose.
//   sheet  — the contact grid a preview shows instead of a black player. The
//            grid IS the preview: an episode should look like something before
//            anyone asks it to play.
//
// One at a time, only for episodes actually on screen, and never twice for the
// same chapter in a session: a grid of forty episodes must not become forty
// concurrent video reads.
import { reactive } from 'vue'
import { api } from './api'
import { cache, store } from './store'
import type { Chapter, Title } from './data'

// SQUARE, and that is the whole point: a 3x3 grid of frames has exactly the
// aspect ratio of ONE frame, so the mosaic and the episode itself fit the same
// box — no letterbox around either, and nothing resizes when playback starts.
export const SHEET_COLS = 3
export const SHEET_ROWS = 3
export const SHEET_CELLS = SHEET_COLS * SHEET_ROWS
export const SHEET_GRID = `${SHEET_COLS}x${SHEET_ROWS}`

const TILE_SPOTS = [0.2, 0.45, 0.7] // a tile needs one frame, not a story
const CELL_EDGE = 540               // one cell of the sheet, longest side
const POSTER_EDGE = 640             // a tile is ~200px wide; no need for 4K

// Which episodes are being cut right now — a preview says so instead of
// pretending an empty pane is the finished thing.
const cutting = reactive(new Set<string>())
export function isCutting(t: Title, c: Chapter): boolean {
  return cutting.has(`${t.id}:${c.id}`)
}

type Job = { tid: string; chapter: Chapter; sheet: boolean }

const attempted = new Set<string>()
const queue: Job[] = []
let running = false

/** The one frame a tile wears. Cheap: three spots. */
export function ensurePoster(t: Title, c: Chapter): void {
  if (!c.poster) enqueue(t, c, false)
}

/** The contact grid a preview shows — and the poster too, if it is still missing. */
export function ensureSheet(t: Title, c: Chapter): void {
  // a sheet cut in some other geometry cannot be sliced by this build: re-cut it
  if (c.sheet !== SHEET_GRID) enqueue(t, c, true)
}

function enqueue(t: Title, c: Chapter, sheet: boolean): void {
  if (c.kind !== 'video' || !c.dl || c.playable === false) return
  const key = `${t.id}:${c.id}:${c.v}:${sheet ? 'sheet' : 'poster'}`
  if (attempted.has(key)) return
  attempted.add(key)
  queue.push({ tid: t.id, chapter: c, sheet })
  void drain()
}

async function drain(): Promise<void> {
  if (running) return
  running = true
  try {
    for (let job = queue.shift(); job; job = queue.shift()) {
      const mark = `${job.tid}:${job.chapter.id}`
      cutting.add(mark)
      try {
        const cut = await grab(job.tid, job.chapter, job.sheet)
        // The pass took seconds, and `cache()` replaces a title's chapter list
        // wholesale — the queued object is detached by now. Ask the store what
        // this chapter looks like NOW before deciding what it still needs.
        const now = store.byId[job.tid]?.chapters.find((c) => c.id === job.chapter.id)
          ?? job.chapter
        // the poster rides along with a sheet pass, but only if none was stored
        if (cut.poster && !now.poster) {
          cache([await api.putChapterFrames(job.tid, job.chapter.id, 'poster', cut.poster)])
        }
        if (cut.sheet) {
          cache([await api.putChapterFrames(job.tid, job.chapter.id, 'sheet', cut.sheet,
                                            SHEET_GRID)])
        }
      } catch {
        /* an episode that will not decode simply keeps its icon */
      } finally {
        cutting.delete(mark)
      }
    }
  } finally {
    running = false
  }
}

function once(el: HTMLVideoElement, event: string, ms = 15000): Promise<void> {
  return new Promise((resolve, reject) => {
    const done = () => { cleanup(); resolve() }
    const fail = () => { cleanup(); reject(new Error(`${event} failed`)) }
    const timer = setTimeout(fail, ms)
    function cleanup() {
      clearTimeout(timer)
      el.removeEventListener(event, done)
      el.removeEventListener('error', fail)
    }
    el.addEventListener(event, done, { once: true })
    el.addEventListener('error', fail, { once: true })
  })
}

// How much is going on in a frame. A flat one (black, a white card) scores ~0,
// so the samples are enough to avoid posting a poster of nothing.
function liveliness(data: Uint8ClampedArray): number {
  let sum = 0
  let sq = 0
  let n = 0
  for (let i = 0; i < data.length; i += 16) { // every 4th pixel is plenty
    const luma = 0.299 * data[i] + 0.587 * data[i + 1] + 0.114 * data[i + 2]
    sum += luma
    sq += luma * luma
    n++
  }
  if (!n) return 0
  const mean = sum / n
  return Math.sqrt(Math.max(0, sq / n - mean * mean))
}

function sized(edge: number, w: number, h: number): { w: number; h: number } {
  const scale = Math.min(1, edge / Math.max(w || 1, h || 1))
  return { w: Math.max(1, Math.round((w || edge) * scale)), h: Math.max(1, Math.round((h || edge) * scale)) }
}

/** The spot a sheet cell was cut at, as a fraction of the episode. */
export function cellFraction(index: number): number {
  return (index + 0.5) / SHEET_CELLS
}

async function grab(tid: string, c: Chapter, wantSheet: boolean):
    Promise<{ poster: Blob | null; sheet: Blob | null }> {
  const nothing = { poster: null, sheet: null }
  const video = document.createElement('video')
  video.preload = 'auto'
  video.muted = true
  video.playsInline = true
  video.src = api.chapterVideoSrc(tid, c.id, c.v)
  try {
    await once(video, 'loadedmetadata')
    const duration = Number.isFinite(video.duration) ? video.duration : 0
    if (!duration) return nothing
    // The pass had to open the file anyway, so the length is known HERE — and a
    // preview cannot label its cells without it. Reporting it now means an
    // episode carries its duration before anyone has played it.
    if (Math.abs(duration - c.duration) > 0.5) cache([await api.setVideoMeta(tid, c.id, duration)])

    const frame = sized(POSTER_EDGE, video.videoWidth, video.videoHeight)
    const one = document.createElement('canvas')
    one.width = frame.w
    one.height = frame.h
    const ctx = one.getContext('2d')
    if (!ctx) return nothing

    const cell = sized(CELL_EDGE, video.videoWidth, video.videoHeight)
    const grid = document.createElement('canvas')
    let gctx: CanvasRenderingContext2D | null = null
    if (wantSheet) {
      grid.width = cell.w * SHEET_COLS
      grid.height = cell.h * SHEET_ROWS
      gctx = grid.getContext('2d')
    }

    const spots = wantSheet
      ? Array.from({ length: SHEET_CELLS }, (_, i) => cellFraction(i))
      : TILE_SPOTS
    let best: ImageData | null = null
    let bestScore = -1
    for (let i = 0; i < spots.length; i++) {
      video.currentTime = Math.min(duration * spots[i], Math.max(0, duration - 0.1))
      await once(video, 'seeked')
      ctx.drawImage(video, 0, 0, one.width, one.height)
      // the cell is drawn from the frame canvas, so each spot is decoded once
      gctx?.drawImage(one, (i % SHEET_COLS) * cell.w, Math.floor(i / SHEET_COLS) * cell.h,
                      cell.w, cell.h)
      const shot = ctx.getImageData(0, 0, one.width, one.height)
      const score = liveliness(shot.data)
      if (score > bestScore) {
        bestScore = score
        best = shot
      }
    }
    if (!best || bestScore < 3) return nothing // nothing but flat frames in there
    ctx.putImageData(best, 0, 0)
    return {
      poster: await encode(one),
      sheet: wantSheet ? await encode(grid, 0.78) : null,
    }
  } finally {
    video.removeAttribute('src')
    video.load() // let the range requests go
  }
}

function encode(canvas: HTMLCanvasElement, quality = 0.82): Promise<Blob | null> {
  return new Promise((r) => canvas.toBlob(r, 'image/jpeg', quality))
}

// Everything an on-screen list should ask for, in one call.
export function ensurePostersFor(titles: Title[]): void {
  if (!store.total) return
  for (const t of titles) {
    for (const c of t.chapters) ensurePoster(t, c)
  }
}
