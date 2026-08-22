<script setup lang="ts">
// The reader's OTHER surface. A title can hold both kinds at once — episodes
// plus a bonus image set — so the choice is made per chapter, not per title,
// and this component owns only playback: the shell around it (chapter list,
// navigation, progress marks) is the reader's, exactly as for pages.
//
// Position is the video answer to "which page was I on": written through on a
// slow tick and on pause, restored on open. Duration is what only a player can
// measure without an ffprobe the app does not ship — reported once.
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import Icon from './Icon.vue'
import { api } from '../api'
import { setPlaybackPosition } from '../store'
import { formatDuration, type Chapter, type Title } from '../data'

const props = withDefaults(
  defineProps<{ title: Title; chapter: Chapter; autoplay?: boolean }>(),
  { autoplay: false })

const el = ref<HTMLVideoElement | null>(null)
const failed = ref(false)
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

// A container Chromium cannot open (mkv, avi…) is stored and listed like any
// other episode — it simply has no surface here until the app can remux it.
const playable = computed(() => props.chapter.playable !== false && !failed.value)

// A resume point does not need to be current to the second, and each write
// costs a document rewrite on the vault the stream is reading from — so
// playback saves rarely, and the moments that matter (pause, end, leaving)
// save immediately.
const SAVE_EVERY_SECONDS = 30
let lastSaved = 0
function remember(seconds: number, force = false) {
  if (!Number.isFinite(seconds) || seconds < 0) return
  if (!force && Math.abs(seconds - lastSaved) < SAVE_EVERY_SECONDS) return
  lastSaved = seconds
  void setPlaybackPosition(props.title, props.chapter.id, seconds)
}

function onLoaded() {
  const v = el.value
  if (!v) return
  void checkDecoding(v)
  // resume where the human stopped, unless they finished it
  const at = props.chapter.position
  if (at > 0 && (!v.duration || at < v.duration - 5)) v.currentTime = at
  if (v.duration && Math.abs(v.duration - props.chapter.duration) > 0.5) {
    void api.setVideoMeta(props.title.id, props.chapter.id, v.duration)
  }
}

// switching episodes inside the reader keeps this component mounted: the one
// being left has to record where it got to before its player is repointed
watch(() => props.chapter.id, (_next, previous) => {
  const at = el.value?.currentTime ?? 0
  if (previous && at > 0) void setPlaybackPosition(props.title, previous, at)
  failed.value = false
  stalled.value = false
  lastSaved = 0
})

onBeforeUnmount(() => {
  const at = el.value?.currentTime ?? 0
  if (at > 0) void setPlaybackPosition(props.title, props.chapter.id, at)
})


</script>

<template>
  <div class="vstage">
    <video
      v-if="playable"
      ref="el"
      class="vplayer"
      :src="src"
      controls
      :autoplay="autoplay"
      preload="auto"
      @loadedmetadata="onLoaded"
      @timeupdate="remember(el?.currentTime ?? 0)"
      @pause="remember(el?.currentTime ?? 0, true)"
      @seeked="remember(el?.currentTime ?? 0, true)"
      @ended="remember(0, true)"
      @waiting="stalled = true"
      @playing="stalled = false"
      @canplay="stalled = false"
      @error="failed = true"
    ></video>

    <!-- stored, listed, catalogued — just not openable by this browser engine -->
    <div v-else class="vunplayable">
      <Icon name="film" :size="28" :sw="1.6" />
      <div class="vtitle">This container cannot be played here yet</div>
      <div class="vhint">
        {{ chapter.num }}<template v-if="chapter.lang"> · {{ chapter.lang }}</template>
        — stored in your vault{{ chapter.duration ? `, ${formatDuration(chapter.duration)}` : '' }}.
        MP4 and WebM play in the app; the rest waits for remuxing.
      </div>
      <a class="btn ghost" :href="src" download>Save a copy</a>
    </div>

    <div v-if="playable && stalled" class="vstall">Buffering…</div>

    <!-- said once, quietly, and only when the file explains itself -->
    <div v-if="playable && caveat" class="vcaveat">{{ caveat }}</div>
  </div>
</template>

<style scoped>
.vstage { position: relative; flex: 1; min-width: 0; display: flex; align-items: center; justify-content: center; background: #000; }
.vplayer { width: 100%; height: 100%; max-height: 100%; background: #000; }
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
</style>
