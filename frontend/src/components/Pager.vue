<script setup lang="ts">
// The footer pager cluster (‹ [page] / N ›), shared by the Library and Authors
// footers. Owns the input parsing; the host owns where the page number lives
// and what to scroll. Styles (.pgbtn/.pgin/.pgof) are global.
defineProps<{ page: number; pages: number }>()
const emit = defineEmits<{ (e: 'go', page: number): void }>()
function onInput(e: Event) {
  emit('go', parseInt((e.target as HTMLInputElement).value, 10) || 1)
}
</script>

<template>
  <button class="pgbtn" :disabled="page <= 1" title="Previous page" @click="emit('go', page - 1)">‹</button>
  <input class="pgin mono" :value="page" title="Go to page…" @change="onInput" @keydown.enter="onInput" />
  <span class="pgof mono">/ {{ pages }}</span>
  <button class="pgbtn" :disabled="page >= pages" title="Next page" @click="emit('go', page + 1)">›</button>
</template>
