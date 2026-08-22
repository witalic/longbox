<script setup lang="ts">
// The reader's OTHER surface. A title can hold both kinds at once — episodes
// plus a bonus image set — so the choice is made per chapter, not per title,
// and this component owns only playback: the shell around it (chapter list,
// navigation, progress marks) is the reader's, exactly as for pages.
//
// Position is the video answer to "which page was I on": written through on a
// slow tick and on pause, restored on open. Duration is what only a player can
// measure without an ffprobe the app does not ship — reported once.
import { computed, ref, watch } from 'vue'
import Icon from './Icon.vue'
import { api } from '../api'
import { setPlaybackPosition } from '../store'
import { formatDuration, type Chapter, type Title } from '../data'

const props = defineProps<{ title: Title; chapter: Chapter }>()

const el = ref<HTMLVideoElement | null>(null)
const failed = ref(false)
const src = computed(() => api.chapterVideoSrc(props.title.id, props.chapter.id, props.chapter.v))

// A container Chromium cannot open (mkv, avi…) is stored and listed like any
// other episode — it simply has no surface here until the app can remux it.
const playable = computed(() => props.chapter.playable !== false && !failed.value)

let lastSaved = 0
function remember(seconds: number, force = false) {
  if (!Number.isFinite(seconds) || seconds < 0) return
  if (!force && Math.abs(seconds - lastSaved) < 5) return // a tick, not a stream of writes
  lastSaved = seconds
  void setPlaybackPosition(props.title, props.chapter.id, seconds)
}

function onLoaded() {
  const v = el.value
  if (!v) return
  // resume where the human stopped, unless they finished it
  const at = props.chapter.position
  if (at > 0 && (!v.duration || at < v.duration - 5)) v.currentTime = at
  if (v.duration && Math.abs(v.duration - props.chapter.duration) > 0.5) {
    void api.setVideoMeta(props.title.id, props.chapter.id, v.duration)
  }
}

// switching episodes inside the reader keeps this component mounted
watch(() => props.chapter.id, () => {
  failed.value = false
  lastSaved = 0
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
      autoplay
      preload="metadata"
      @loadedmetadata="onLoaded"
      @timeupdate="remember(el?.currentTime ?? 0)"
      @pause="remember(el?.currentTime ?? 0, true)"
      @ended="remember(0, true)"
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
  </div>
</template>

<style scoped>
.vstage { flex: 1; min-width: 0; display: flex; align-items: center; justify-content: center; background: #000; }
.vplayer { width: 100%; height: 100%; max-height: 100%; background: #000; }
.vunplayable {
  display: flex; flex-direction: column; align-items: center; gap: 10px;
  color: var(--tx2); padding: 40px 30px; text-align: center; background: var(--bg);
  width: 100%; height: 100%; justify-content: center;
}
.vtitle { font: 600 15px/1.3 system-ui; color: var(--tx); }
.vhint { font: 400 12px/1.5 system-ui; color: var(--tx3); max-width: 420px; }
</style>
