<script setup lang="ts">
// Single-path 24x24 line icons ported from the Claude Design "longbox" project.
const P: Record<string, string> = {
  library: 'M4 5h11v14H4zM17 7h3v12h-3',
  authors: 'M12 4a4 4 0 1 0 0 8 4 4 0 0 0 0-8M4 20c0-4 4-6 8-6s8 2 8 6',
  // The globe belongs to BROWSING the web; a source is a link you keep to one
  // site. They used to be a globe and a compass — two drawings of the same idea
  // sitting next to each other in the nav, which is a coin toss, not a choice.
  sources: 'M10.5 13.5a4 4 0 0 0 5.7 0l2.8-2.8a4 4 0 0 0-5.7-5.7l-1.6 1.6M13.5 10.5a4 4 0 0 0-5.7 0l-2.8 2.8a4 4 0 0 0 5.7 5.7l1.6-1.6',
  meta: 'M4 7h9M4 12h16M4 17h6M17 5v4M13 15v4',
  // a folder with something going into it — bringing in what is already on disk
  import: 'M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2zM12 10v6M9 13l3 3 3-3',
  folder: 'M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z',
  settings: 'M12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6M19 12a7 7 0 0 0-.1-1l2-1.5-2-3.4-2.3 1a7 7 0 0 0-1.7-1l-.3-2.6h-4l-.3 2.6a7 7 0 0 0-1.7 1l-2.3-1-2 3.4 2 1.5a7 7 0 0 0 0 2l-2 1.5 2 3.4 2.3-1a7 7 0 0 0 1.7 1l.3 2.6h4l.3-2.6a7 7 0 0 0 1.7-1l2.3 1 2-3.4-2-1.5c.1-.3.1-.7.1-1z',
  home: 'M4 5h11v14H4zM17 7h3v12h-3',
  plus: 'M12 5v14M5 12h14',
  search: 'M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14M20 20l-3.5-3.5',
  chevron: 'M6 9l6 6 6-6',
  back: 'M15 18l-6-6 6-6',
  forward: 'M9 18l6-6-6-6',
  star: 'M12 2l2.9 6.3 6.9.7-5.1 4.6 1.4 6.8L12 17.8 5.9 20.4l1.4-6.8L2.2 9l6.9-.7z',
  download: 'M12 3v12m0 0l-4-4m4 4l4-4M4 21h16',
  activity: 'M3 12h4l3-8 4 16 3-8h4',
  edit: 'M12 20h9M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z',
  refresh: 'M21 12a9 9 0 1 1-2.6-6.3M21 4v4h-4',
  lock: 'M6 10V8a6 6 0 0 1 12 0v2M5 10h14v10H5z',
  // the vault stopped answering (VaultLost.vue)
  offline: 'M2 2l20 20M8.5 16.5a5 5 0 0 1 7 0M5 12.9a10 10 0 0 1 5-2.8M19 12.9a10 10 0 0 0-2.3-1.6M2 8.8a15 15 0 0 1 4.2-2.6M22 8.8A15 15 0 0 0 11 5M12 20h.01',
  x: 'M6 6l12 12M18 6L6 18',
  check: 'M20 6L9 17l-5-5',
  grid: 'M4 4h7v7H4zM13 4h7v7h-7zM4 13h7v7H4zM13 13h7v7h-7z',
  rows: 'M4 6h16M4 10h16M4 14h16M4 18h16',
  detail: 'M4 5h16v6H4zM4 13h16v6H4z',
  // the open entry large, the entries down a column beside it
  tiles: 'M3 4h10v16H3zM16 4h5v4h-5zM16 10h5v4h-5zM16 16h5v4h-5z',
  // a wall of pages, each taller than wide
  pages: 'M3 3h3.6v8H3zM8 3h3.6v8H8zM13 3h3.6v8H13zM18 3h3.6v8H18zM3 13h3.6v8H3zM8 13h3.6v8H8zM13 13h3.6v8H13z',
  // crosshair target — "pick this element on the page" (NOT a star; stars mean favorites)
  pick: 'M12 7a5 5 0 1 0 0 10 5 5 0 0 0 0-10M12 2v4M12 18v4M2 12h4M18 12h4',
  moon: 'M12 3a9 9 0 1 0 9 9 7 7 0 0 1-9-9z',
  // a release is out (the sidebar's footer)
  update: 'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18M12 16V8M8.5 11.5 12 8l3.5 3.5',
  image: 'M4 16l5-5 4 4 3-3 4 4M4 5h16v14H4z',
  film: 'M3 5h18v14H3zM7 5v14M17 5v14M3 9h4M3 15h4M17 9h4M17 15h4',
  expand: 'M4 5h7v7H4zM13 5h7v7h-7zM4 14h7v6H4zM13 14h7v6h-7z',
  browser: 'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18M3 12h18M12 3c3 3 3 15 0 18M12 3c-3 3-3 15 0 18',
  minus: 'M5 12h14',
  pin: 'M9 3h6v6l3 3H6l3-3zM12 12v9',
  code: 'M8 6l-5 6 5 6M16 6l5 6-5 6',
  panel: 'M4 5h16v14H4zM15 5v14',
  more: 'M5 12h.01M12 12h.01M19 12h.01',
  filter: 'M4 5h16l-6.2 7.4V19l-3.6 2v-8.6z',
  // a closed triangle — pass fill to make it solid, as a play button wants
  play: 'M8 5l11 7-11 7z',
  pause: 'M9 5v14M15 5v14',
  shield: 'M12 3l7 3v5c0 4.6-3 8.3-7 10-4-1.7-7-5.4-7-10V6z',
  sound: 'M4 9h4l5-4v14l-5-4H4zM17 9a4 4 0 0 1 0 6',
  muted: 'M4 9h4l5-4v14l-5-4H4zM17 10l4 4M21 10l-4 4',
  eyeoff: 'M3 3l18 18M10.6 6.3A9.6 9.6 0 0 1 12 6.2c5 0 8.5 5.8 8.5 5.8a17 17 0 0 1-2.9 3.6M6.4 8.2A17 17 0 0 0 3.5 12s3.5 5.8 8.5 5.8c1 0 2-.2 2.8-.5',
  eye: 'M3.5 12S7 6.2 12 6.2 20.5 12 20.5 12 17 17.8 12 17.8 3.5 12 3.5 12zM12 9.5a2.5 2.5 0 1 0 0 5 2.5 2.5 0 0 0 0-5',
  key: 'M8 11a4 4 0 1 0 0 8 4 4 0 0 0 0-8M10.9 12.1L20 3M17 6l3 3M14.5 8.5l2 2',
  unlock: 'M6 10V8a6 6 0 0 1 11.3-2.8M5 10h14v10H5z',
  copy: 'M9 9h11v11H9zM5 15H4V4h11v1',
  wand: 'M4 20L14.5 9.5M16 3l.9 2.1L19 6l-2.1.9L16 9l-.9-2.1L13 6l2.1-.9zM20 11l.6 1.4L22 13l-1.4.6L20 15l-.6-1.4L18 13l1.4-.6z',
  trash: 'M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13M10 11v6M14 11v6',
  undo: 'M9 14L4 9l5-5M4 9h11a5 5 0 0 1 0 10h-3',
  clock: 'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18M12 7v5l3 2',
  info: 'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18M12 11v6M12 7.5h.01',
  warn: 'M12 3l10 18H2zM12 10v5M12 18h.01',
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
