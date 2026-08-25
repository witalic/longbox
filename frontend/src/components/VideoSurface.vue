<script setup lang="ts">
// The reader's OTHER surface. A title can hold both kinds at once — episodes
// plus a bonus image set — so the choice is made per chapter, not per title,
// and this component owns only playback: the shell around it (chapter list,
// navigation, progress marks) is the reader's, exactly as for pages.
//
// Position is the video answer to "which page was I on": written through on a
// slow tick and on pause, restored on open. Duration is what only a player can
// measure without an ffprobe the app does not ship — reported once.
//
// Nothing is streamed until playback is ASKED for. Until then the surface is
// the contact sheet cut from the episode (frames.ts): an episode should look
// like something before it plays, and a black rectangle with a scrub bar tells
// nobody what is in the file. Each cell of the sheet is the spot it was cut
// from, so clicking one starts there.
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import Icon from './Icon.vue'
import { api } from '../api'
import { matches } from '../keys'
import { setPlaybackPosition } from '../store'
import { SHEET_CELLS, SHEET_COLS, SHEET_GRID, SHEET_ROWS, cellFraction, ensureSheet, isCutting }
  from '../frames'
import { UNSUPPORTED_HINT, formatDuration, unsupportedTitle, type Chapter, type Title }
  from '../data'

const props = withDefaults(
  defineProps<{ title: Title; chapter: Chapter; autoplay?: boolean }>(),
  { autoplay: false })

const el = ref<HTMLVideoElement | null>(null)
const failed = ref(false)
// The player is MOUNTED only once playback is asked for — an unmounted one
// fetches nothing, which is the whole point of showing the sheet first.
const armed = ref(props.autoplay)
// a cell was clicked: start THERE, not at the resume point
const startAt = ref<number | null>(null)
// A stall has to be VISIBLE. Without this the two ways playback can fail —
// bytes not arriving, or frames not decoding in time — look identical, and
// the player just seems broken.
const stalled = ref(false)

// Why a file may behave badly — ASKED, not assumed. Whether a codec plays well
// depends on the machine, so claiming "HEVC stutters" on a machine that decodes
// it in hardware would be a lie the user can see through.
const decodeWarning = ref('')

async function checkDecoding(video: HTMLVideoElement) {
  decodeWarning.value = ''
  const caps = navigator.mediaCapabilities
  if (!caps?.decodingInfo || !props.chapter.codec || !video.videoWidth) return
  const PROFILES: Record<string, string> = {
    hevc: 'video/mp4; codecs="hvc1.1.6.L153.B0"',
    h264: 'video/mp4; codecs="avc1.640033"',
    av1: 'video/mp4; codecs="av01.0.08M.08"',
    vp9: 'video/webm; codecs="vp09.00.10.08"',
  }
  const contentType = PROFILES[props.chapter.codec]
  if (!contentType) return
  try {
    const info = await caps.decodingInfo({
      type: 'file',
      video: {
        contentType, width: video.videoWidth, height: video.videoHeight,
        bitrate: Math.round((props.chapter.duration ? 8 * 628e6 / props.chapter.duration : 8e6)),
        framerate: 30,
      },
    })
    if (!info.supported) decodeWarning.value = 'This machine cannot decode this codec.'
    else if (!info.smooth) {
      decodeWarning.value = `This machine decodes ${props.chapter.codec.toUpperCase()} at `
        + `${video.videoWidth}×${video.videoHeight} in software — playback and seeking will stutter.`
    }
  } catch { /* the query is a courtesy; its absence is not an error */ }
}

const caveat = computed(() => {
  const c = props.chapter
  if (c.kind !== 'video') return ''
  if (decodeWarning.value) return decodeWarning.value
  if (c.faststart === false) {
    return 'This file keeps its index at the end, so the player fetches the tail before the '
      + 'first frame — that is the slow start. Seeking far can also take a moment: the '
      + 'decoder has to replay from the previous keyframe.'
  }
  return ''
})
const src = computed(() => api.chapterVideoSrc(props.title.id, props.chapter.id, props.chapter.v))

const hasSheet = computed(() => props.chapter.sheet === SHEET_GRID)
const sheetSrc = computed(() => (hasSheet.value
  ? api.chapterFramesSrc(props.title.id, props.chapter.id, 'sheet', props.chapter.stills) : ''))
const posterSrc = computed(() => (!hasSheet.value && props.chapter.poster
  ? api.chapterFramesSrc(props.title.id, props.chapter.id, 'poster', props.chapter.stills, 640) : ''))
const cellTimes = computed(() =>
  Array.from({ length: SHEET_CELLS }, (_, i) => props.chapter.duration * cellFraction(i)))
const gridStyle = {
  gridTemplateColumns: `repeat(${SHEET_COLS}, 1fr)`,
  gridTemplateRows: `repeat(${SHEET_ROWS}, 1fr)`,
}
// Each tile shows ITS OWN frame out of the one stored sheet — the sheet is a
// sprite, and a tile is one window onto it. That is what keeps a timestamp on
// the frame it names whatever shape the pane happens to be: the tiles ARE the
// picture, so there is nothing to line up and no letterbox to leave behind.
function cellStyle(i: number) {
  const col = i % SHEET_COLS
  const row = Math.floor(i / SHEET_COLS)
  return {
    backgroundImage: `url("${sheetSrc.value}")`,
    backgroundSize: `${SHEET_COLS * 100}% ${SHEET_ROWS * 100}%`,
    backgroundPosition: `${(col / (SHEET_COLS - 1)) * 100}% ${(row / (SHEET_ROWS - 1)) * 100}%`,
  }
}

function play(at?: number) {
  startAt.value = at ?? null
  armed.value = true
}

// A container Chromium cannot open (mkv, avi…) is stored and listed like any
// other episode — it simply has no surface here until the app can remux it.
const playable = computed(() => props.chapter.playable !== false && !failed.value)

// A resume point does not need to be current to the second, and each write
// costs a document rewrite on the vault the stream is reading from — so
// playback saves rarely, and the moments that matter (pause, end, leaving)
// save immediately.
// Every write rewrites the title document on the very disk the episode is
// streaming from, so playback saves rarely and NEVER on a seek — the moments
// that matter (pause, end, leaving the surface) force one instead.
const SAVE_EVERY_SECONDS = 30
let lastSaved = 0
// The last position this surface SAW. Leaving the page takes the <video> with
// it — by the time the component is told it is going, the element (and its
// currentTime) can already be gone, and asking it then read 0 and stored
// nothing. So the number is kept here, where nothing can remove it.
let lastSeen = 0
function remember(seconds: number, force = false) {
  // ZERO is never a resume point. Tearing the element down resets it and fires
  // a last `pause` at 0 — writing that (forced, as a pause is) is exactly how
  // leaving the page erased the place you had got to.
  if (!Number.isFinite(seconds) || seconds <= 0) return
  lastSeen = seconds
  if (!force && Math.abs(seconds - lastSaved) < SAVE_EVERY_SECONDS) return
  lastSaved = seconds
  void setPlaybackPosition(props.title, props.chapter.id, seconds)
}

// Watched to the end: the resume point is dropped ON PURPOSE, which is the one
// zero that means something and the only one written.
function finished() {
  lastSeen = 0
  lastSaved = 0
  void setPlaybackPosition(props.title, props.chapter.id, 0)
}

function onLoaded() {
  const v = el.value
  if (!v) return
  void checkDecoding(v)
  // a clicked cell wins over the resume point; otherwise carry on where the
  // human stopped, unless they finished it
  const at = startAt.value ?? props.chapter.position
  if (at > 0 && (!v.duration || at < v.duration - 5)) v.currentTime = at
  startAt.value = null
  if (!props.autoplay) void v.play().catch(() => { /* the sheet stays up */ })
  if (v.duration && Math.abs(v.duration - props.chapter.duration) > 0.5) {
    void api.setVideoMeta(props.title.id, props.chapter.id, v.duration)
  }
}

// switching episodes inside the reader keeps this component mounted: the one
// being left has to record where it got to before its player is repointed
watch(() => props.chapter.id, (_next, previous) => {
  const at = el.value?.currentTime || lastSeen
  if (previous && at > 0) void setPlaybackPosition(props.title, previous, at)
  lastSeen = 0
  failed.value = false
  stalled.value = false
  armed.value = props.autoplay
  startAt.value = null
  lastSaved = 0
})

// The sheet is cut on first sight of an episode and kept in the vault, so this
// asks once per chapter and never again. A surface that autoplays never needs
// one: it is already showing frames.
watch(() => [props.chapter.id, props.chapter.sheet], () => {
  if (!props.autoplay && playable.value) ensureSheet(props.title, props.chapter)
}, { immediate: true })

// Arrows scrub the episode. A player is the one surface where the page-turn
// keys have nothing to turn, so wherever this component is running — the reader
// or the title page — they belong to it, and only while something is playing.
const SEEK_STEP = 5
const SEEK_COALESCE = 150 // ms — the fastest a seek is worth repeating

// A held arrow must not become a burst of seeks: every seek abandons the range
// request in flight and opens another, and the decoder replays from the previous
// keyframe each time. So presses are RATE-LIMITED, not debounced — the first one
// moves at once and everything pressed inside the window is added up and applied
// when the window closes, which then opens another while keys keep coming.
//
// (Debouncing it, as this first did, means a held key never moves at all: key
// repeat fires every ~30ms and each press pushed the deadline further away.)
let queued = 0
let window_: ReturnType<typeof setTimeout> | null = null
function seekTo(delta: number) {
  const v = el.value
  if (!v) return
  const at = v.currentTime + delta
  v.currentTime = Math.max(0, v.duration ? Math.min(at, v.duration - 0.1) : at)
}
function closeWindow() {
  window_ = null
  if (!queued) return
  seekTo(queued)
  queued = 0
  window_ = setTimeout(closeWindow, SEEK_COALESCE) // still being held: keep the pace
}
function seekBy(delta: number) {
  if (!el.value) return
  if (window_ !== null) { queued += delta; return }
  seekTo(delta)
  window_ = setTimeout(closeWindow, SEEK_COALESCE)
}

// CAPTURE: the player's own keyboard handling runs on the element, which is
// deeper than this listener would be on the way back up — by then it has
// already seeked its own five seconds, and both jumps land as one of ten.
function onKey(e: KeyboardEvent) {
  if (!el.value || !armed.value) return
  const tag = (e.target as HTMLElement | null)?.tagName
  if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return
  const back = matches('video.back', e)
  if (!back && !matches('video.fwd', e)) return
  e.preventDefault()
  seekBy(back ? -SEEK_STEP : SEEK_STEP)
}
onMounted(() => document.addEventListener('keydown', onKey, true))

onBeforeUnmount(() => {
  document.removeEventListener('keydown', onKey, true)
  if (window_ !== null) clearTimeout(window_)
  const at = el.value?.currentTime || lastSeen
  if (at > 0) void setPlaybackPosition(props.title, props.chapter.id, at)
})


</script>

<template>
  <div class="vstage">
    <video
      v-if="playable && armed"
      ref="el"
      class="vplayer"
      :src="src"
      controls
      :autoplay="autoplay"
      preload="auto"
      @loadedmetadata="onLoaded"
      @timeupdate="remember(el?.currentTime ?? 0)"
      @pause="remember(el?.currentTime ?? 0, true)"
      @seeked="remember(el?.currentTime ?? 0)"
      @ended="finished"
      @waiting="stalled = true"
      @playing="stalled = false"
      @canplay="stalled = false"
      @error="failed = true"
    ></video>

    <!-- what an episode looks like before anyone asks it to play -->
    <div v-else-if="playable" class="vpre">
      <div v-if="hasSheet" class="vcells" :style="gridStyle">
        <button v-for="(at, i) in cellTimes" :key="i" class="vcell" :style="cellStyle(i)"
                :title="chapter.duration ? `Play from ${formatDuration(at)}` : 'Play'"
                @click="play(chapter.duration ? at : undefined)">
          <span v-if="chapter.duration" class="vcellat mono">{{ formatDuration(at) }}</span>
        </button>
      </div>
      <img v-else-if="posterSrc" class="vstill" :src="posterSrc" alt="" />
      <button class="vplay" :title="chapter.position > 1 ? 'Carry on where you stopped' : 'Play from the start'"
              @click="play()">
        <span class="vplayring"><Icon name="play" :size="22" :sw="1.4" fill="currentColor" /></span>
        <span class="vplaylbl">{{ chapter.position > 1 ? `Resume ${formatDuration(chapter.position)}` : 'Play' }}</span>
      </button>
      <div v-if="isCutting(title, chapter)" class="vprenote">Cutting a preview from the episode…</div>
    </div>

    <!-- stored, listed, catalogued — the app simply cannot open this container -->
    <div v-else class="vunplayable">
      <Icon name="film" :size="28" :sw="1.6" />
      <div class="vtitle">{{ unsupportedTitle(chapter) }}</div>
      <div class="vhint">
        {{ chapter.num }}<template v-if="chapter.lang"> · {{ chapter.lang }}</template>
        <template v-if="chapter.duration"> · {{ formatDuration(chapter.duration) }}</template>
        — {{ UNSUPPORTED_HINT }}
      </div>
      <a class="btn ghost" :href="src" download>Save a copy</a>
    </div>

    <div v-if="playable && stalled" class="vstall">Buffering…</div>

    <!-- said once, quietly, and only when the file explains itself -->
    <div v-if="playable && caveat" class="vcaveat">{{ caveat }}</div>
  </div>
</template>

<style scoped>
/* min-height: 0 is load-bearing. As a flex item in a column, the stage's
   automatic minimum size is the min-content height of what it holds — and a
   <video> reports its own frame height, so without this the stage refuses to
   shrink below 1080px and the player hangs out past the pane with it. */
.vstage { position: relative; flex: 1; min-width: 0; min-height: 0; display: flex; align-items: center; justify-content: center; background: #000; user-select: none; }
/* The stage is a fixed box — the same one whether an episode is previewing or
   playing — so what is drawn inside it fits itself to the box and never the
   other way round. */
.vplayer { width: 100%; height: 100%; object-fit: contain; background: #000; }
/* the focus ring is dealt with globally — every <video> in the app, one rule
   set, in styles.css beside the other element-level resets */
.vunplayable {
  display: flex; flex-direction: column; align-items: center; gap: 10px;
  color: var(--tx2); padding: 40px 30px; text-align: center; background: var(--bg);
  width: 100%; height: 100%; justify-content: center;
}
.vtitle { font: 600 15px/1.3 system-ui; color: var(--tx); }
.vcaveat {
  position: absolute; left: 0; right: 0; bottom: 0;
  padding: 8px 14px; font: 400 11px/1.4 system-ui;
  color: var(--tx2); background: color-mix(in srgb, var(--bg) 88%, transparent);
  border-top: 1px solid var(--line);
}
.vstall {
  position: absolute; top: 12px; left: 50%; transform: translateX(-50%);
  padding: 5px 12px; border-radius: 999px; pointer-events: none;
  font: 500 12px/1 system-ui; color: var(--tx);
  background: color-mix(in srgb, var(--bg) 86%, transparent);
  border: 1px solid var(--line);
}
.vhint { font: 400 12px/1.5 system-ui; color: var(--tx3); max-width: 420px; }

/* the pre-play surface: the sheet, its cells, and the one button that starts it */
.vpre { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; }
/* the cells are laid over the IMAGE, not the stage — a letterboxed sheet would
   otherwise put every timestamp beside the frame it belongs to */
/* The mosaic FILLS the stage — it is the picture, not something placed on top
   of one — so the preview is exactly the size of the box in every window. */
.vcells { position: absolute; inset: 0; display: grid; gap: 1px; background: var(--line); }
.vstill { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: cover; }
.vcell {
  border: none; background-color: #000; background-repeat: no-repeat; cursor: pointer; padding: 0;
  display: flex; align-items: center; justify-content: center;
}
.vcell:hover { box-shadow: inset 0 0 0 2px var(--accent); }
.vcellat {
  opacity: 0; font: 600 10.5px/1 ui-monospace, monospace; color: var(--tx);
  padding: 3px 6px; border-radius: 4px; background: color-mix(in srgb, #000 62%, transparent);
}
.vcell:hover .vcellat { opacity: 1; }
/* Dead centre of the surface, where nothing can push it past an edge. */
.vplay {
  position: absolute; left: 50%; top: 50%; transform: translate(-50%, -50%); z-index: 2;
  display: flex; flex-direction: column; align-items: center; gap: 9px;
  border: none; background: none; padding: 0; cursor: pointer;
}
.vplayring {
  display: flex; align-items: center; justify-content: center; width: 62px; height: 62px;
  border-radius: 999px; color: #fff; padding-left: 4px;
  background: color-mix(in srgb, #000 52%, transparent);
  border: 1.5px solid color-mix(in srgb, #fff 70%, transparent);
}
.vplaylbl {
  padding: 4px 11px; border-radius: 999px; font: 600 11.5px/1 system-ui; color: #fff;
  background: color-mix(in srgb, #000 52%, transparent);
}
.vplay:hover .vplayring { background: color-mix(in srgb, var(--accent) 78%, transparent); border-color: #fff; }
.vprenote {
  position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%);
  font: 400 12px/1.4 system-ui; color: var(--tx3); text-align: center; padding: 0 20px;
}
</style>
