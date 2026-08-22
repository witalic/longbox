<script setup lang="ts">
// Single-path 24x24 line icons ported from the Claude Design "longbox" project.
const P: Record<string, string> = {
  library: 'M4 5h11v14H4zM17 7h3v12h-3',
  authors: 'M12 4a4 4 0 1 0 0 8 4 4 0 0 0 0-8M4 20c0-4 4-6 8-6s8 2 8 6',
  sources: 'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18M3 12h18M12 3c3 3 3 15 0 18M12 3c-3 3-3 15 0 18',
  settings: 'M12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6M19 12a7 7 0 0 0-.1-1l2-1.5-2-3.4-2.3 1a7 7 0 0 0-1.7-1l-.3-2.6h-4l-.3 2.6a7 7 0 0 0-1.7 1l-2.3-1-2 3.4 2 1.5a7 7 0 0 0 0 2l-2 1.5 2 3.4 2.3-1a7 7 0 0 0 1.7 1l.3 2.6h4l.3-2.6a7 7 0 0 0 1.7-1l2.3 1 2-3.4-2-1.5c.1-.3.1-.7.1-1z',
  home: 'M4 5h11v14H4zM17 7h3v12h-3',
  plus: 'M12 5v14M5 12h14',
  search: 'M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14M20 20l-3.5-3.5',
  chevron: 'M6 9l6 6 6-6',
  back: 'M15 18l-6-6 6-6',
  forward: 'M9 18l6-6-6-6',
  heart: 'M12 21s-7.5-4.6-10-9.2C.3 8.4 2 5 5.5 5 7.7 5 9.3 6.4 12 9c2.7-2.6 4.3-4 6.5-4C22 5 23.7 8.4 22 11.8 19.5 16.4 12 21 12 21z',
  star: 'M12 2l2.9 6.3 6.9.7-5.1 4.6 1.4 6.8L12 17.8 5.9 20.4l1.4-6.8L2.2 9l6.9-.7z',
  download: 'M12 3v12m0 0l-4-4m4 4l4-4M4 21h16',
  edit: 'M12 20h9M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z',
  refresh: 'M21 12a9 9 0 1 1-2.6-6.3M21 4v4h-4',
  lock: 'M6 10V8a6 6 0 0 1 12 0v2M5 10h14v10H5z',
  x: 'M6 6l12 12M18 6L6 18',
  check: 'M20 6L9 17l-5-5',
  grid: 'M4 4h7v7H4zM13 4h7v7h-7zM4 13h7v7H4zM13 13h7v7h-7z',
  rows: 'M4 6h16M4 10h16M4 14h16M4 18h16',
  detail: 'M4 5h16v6H4zM4 13h16v6H4z',
  // crosshair target — "pick this element on the page" (NOT a star; stars mean favorites)
  pick: 'M12 7a5 5 0 1 0 0 10 5 5 0 0 0 0-10M12 2v4M12 18v4M2 12h4M18 12h4',
  moon: 'M12 3a9 9 0 1 0 9 9 7 7 0 0 1-9-9z',
  image: 'M4 16l5-5 4 4 3-3 4 4M4 5h16v14H4z',
  film: 'M3 5h18v14H3zM7 5v14M17 5v14M3 9h4M3 15h4M17 9h4M17 15h4',
  expand: 'M4 5h7v7H4zM13 5h7v7h-7zM4 14h7v6H4zM13 14h7v6h-7z',
  compass: 'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18M16 8l-2.5 5.5L8 16l2.5-5.5z',
  minus: 'M5 12h14',
  pin: 'M9 3h6v6l3 3H6l3-3zM12 12v9',
  code: 'M8 6l-5 6 5 6M16 6l5 6-5 6',
  panel: 'M4 5h16v14H4zM15 5v14',
}
const props = withDefaults(defineProps<{ name: string; size?: number; sw?: number; fill?: string }>(), {
  size: 16, sw: 1.8, fill: 'none',
})
</script>

<template>
  <svg :width="props.size" :height="props.size" viewBox="0 0 24 24"
       :fill="props.fill" stroke="currentColor" :stroke-width="props.sw"
       stroke-linecap="round" stroke-linejoin="round" style="flex:none">
    <path :d="P[props.name] || ''" />
  </svg>
</template>
