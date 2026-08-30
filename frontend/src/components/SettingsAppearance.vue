<script setup lang="ts">
// The one look-and-feel choice the app has. `store.theme` owns the preference
// and its persistence; this is the switch.
import { store, type ThemePref } from '../store'

const themeChips: { v: ThemePref; label: string }[] = [
  { v: 'dark', label: 'Dark' }, { v: 'light', label: 'Light' }, { v: 'system', label: 'System' },
]
</script>

<template>
  <section class="card">
    <div class="row">
      <div class="text">
        <div class="label">Colour scheme</div>
        <div class="hint">Switch the whole app between dark and light, or follow the system.
          Your choice is remembered.</div>
      </div>
      <div class="seg">
        <button v-for="c in themeChips" :key="c.v" class="opt" :class="{ on: store.theme === c.v }"
                @click="store.theme = c.v">{{ c.label }}</button>
      </div>
    </div>
  </section>
</template>
