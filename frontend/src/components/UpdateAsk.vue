<script setup lang="ts">
// "Check for new versions?" — asked once, never as a dialog (design/update-check.md):
// over the foot of the sidebar, where the notice of a release will later be,
// covering nothing the library needs. Two answers of equal weight: neither is
// chosen for the user, and the window stays usable while the question waits.
import { answer } from '../updates'
</script>

<template>
  <section class="ask" aria-labelledby="updask-title">
    <div id="updask-title" class="t">Check for new versions?</div>
    <p>
      longbox can ask GitHub once a day whether a newer release is out, and tell you — with what
      changed, and whether it fixes the built-in browser's security. GitHub sees this computer's
      address and nothing else; nothing is downloaded by itself.
    </p>
    <div class="acts">
      <button class="btn" @click="answer(true)">Check for new versions</button>
      <button class="btn" @click="answer(false)">Don't check</button>
    </div>
    <div class="foot">You can change this any time in Settings → About.</div>
  </section>
</template>

<style scoped>
.ask {
  position: fixed; left: 10px; bottom: 52px; z-index: 250; width: 372px;
  background: var(--panel2); border: 1px solid var(--line); border-radius: 10px;
  box-shadow: 0 14px 34px rgba(0, 0, 0, .45); padding: 16px 16px 14px;
}
.t { font: 600 15px/1.2 system-ui; color: var(--tx); }
p { margin: 8px 0 0; font: 400 12px/1.5 system-ui; color: var(--tx2); }
.acts { display: flex; gap: 8px; margin-top: 14px; }
.acts .btn { flex: 1; }
.foot { margin-top: 10px; font: 400 11px/1.4 system-ui; color: var(--tx3); }
</style>
