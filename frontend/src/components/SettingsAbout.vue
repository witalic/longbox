<script setup lang="ts">
// What this build IS — from app-meta.json, the one source of app identity, so
// a packaged build and a checkout cannot disagree about their own version.
defineProps<{
  meta: { name?: string; version?: string; updated?: string; description?: string }
}>()
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
  </section>
</template>
