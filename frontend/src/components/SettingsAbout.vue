<script setup lang="ts">
// What this build IS — from app-meta.json, the one source of app identity, so
// a packaged build and a checkout cannot disagree about their own version —
// and whether a newer one is out (design/update-check.md).
import { computed } from 'vue'
import { ago } from '../data'
import { answer, available, canCheck, checkNow, openRelease, updates } from '../updates'

defineProps<{
  meta: { name?: string; version?: string; updated?: string; description?: string }
}>()

const status = computed<{ tone: '' | 'good' | 'out' | 'bad'; line: string; hint: string }>(() => {
  if (updates.checking) return { tone: '', line: 'Checking…', hint: 'Asking GitHub for the latest release.' }
  if (updates.error) {
    const again = updates.check ? 'The next check is tomorrow; Check now tries again.' : 'Check now tries again.'
    const why = updates.error.charAt(0).toUpperCase() + updates.error.slice(1)
    return { tone: 'bad', line: 'The check failed', hint: `${why}. ${again}` }
  }
  const found = available.value
  if (found) {
    return { tone: 'out', line: `${found.latest} is out — released ${ago(found.published)}`, hint: `Checked ${ago(updates.last)}.` }
  }
  if (updates.last) return { tone: 'good', line: 'You have the latest', hint: `Last checked ${ago(updates.last)}.` }
  return { tone: '', line: 'Not checked yet', hint: 'Check now asks GitHub once.' }
})
</script>

<template>
  <section class="card">
    <div class="row">
      <span class="applogo">
        <svg viewBox="0 0 72 72" style="width:34px;height:34px" aria-hidden="true">
          <defs><linearGradient id="lbgrad2" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" stop-color="#8194f4" /><stop offset="1" stop-color="#3e4eb2" />
          </linearGradient></defs>
          <rect width="72" height="72" rx="17" fill="url(#lbgrad2)" />
          <path d="M27 19h9v25h13v9H27z" fill="#fff" />
        </svg>
      </span>
      <div class="text">
        <div class="label">
          {{ meta.name || 'longbox' }}
          <span class="mini mono" style="margin-left:6px">v{{ meta.version || '?' }}</span>
        </div>
        <div class="hint">{{ meta.description || '' }}</div>
      </div>
      <span v-if="meta.updated" class="mini mono">updated {{ meta.updated }}</span>
    </div>
    <!-- the question asked once, answered here any time; only the desktop app can ask -->
    <div v-if="canCheck" class="row">
      <div class="text">
        <div class="label">Check for new versions</div>
        <div class="hint">{{ updates.check
          ? "Once a day, from GitHub — it sees this computer's address and nothing else."
          : 'Off — longbox makes no request of its own. Check now asks once.' }}</div>
      </div>
      <span class="toggle" :class="{ on: updates.check }" role="switch" tabindex="0"
            :aria-checked="!!updates.check" aria-label="Check for new versions"
            @click="answer(!updates.check)" @keydown.enter.space.prevent="answer(!updates.check)"></span>
    </div>
    <div v-if="canCheck" class="row">
      <div class="text">
        <div class="label status" :class="status.tone"><span class="d"></span>{{ status.line }}</div>
        <div class="hint">{{ status.hint }}</div>
      </div>
      <template v-if="available">
        <button class="btn" @click="openRelease(available.latest)">What's new</button>
        <button class="btn" @click="openRelease('latest')">Download</button>
      </template>
      <button class="btn" :disabled="updates.checking" @click="checkNow">Check now</button>
    </div>
  </section>
</template>

<style scoped>
.status { display: inline-flex; align-items: center; gap: 6px; }
.status .d { width: 7px; height: 7px; border-radius: 999px; background: var(--tx3); }
.status.good .d { background: var(--good); }
.status.out .d { background: var(--accent); }
.status.bad .d { background: var(--adult); }
</style>
