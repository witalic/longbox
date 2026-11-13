<script setup lang="ts">
import { nextTick, ref, watch } from 'vue'
import { confirmState, resolveConfirm } from '../confirm'
import Icon from './Icon.vue'

// The keyboard rests where a stray Enter does no harm: on Cancel when the
// answer destroys, on the answer otherwise.
const cancelBtn = ref<HTMLButtonElement | null>(null)
const okBtn = ref<HTMLButtonElement | null>(null)
watch(() => confirmState.open, async (open) => {
  if (!open) return
  await nextTick()
  ;(confirmState.req.danger ? cancelBtn : okBtn).value?.focus()
})
</script>

<template>
  <Transition name="fade">
    <div v-if="confirmState.open" class="overlay" @click.self="resolveConfirm(false)" @keydown.esc="resolveConfirm(false)">
      <div class="card" role="dialog" aria-modal="true">
        <div class="title">{{ confirmState.req.title || 'Please confirm' }}</div>
        <div class="msg">{{ confirmState.req.message }}</div>
        <div v-if="confirmState.req.choices?.length" class="choices">
          <button v-for="(c, i) in confirmState.req.choices" :key="i" class="choice" :class="{ on: c.on }"
                  role="checkbox" :aria-checked="c.on" @click="c.on = !c.on">
            <span class="fbox" :class="{ on: c.on }"><Icon v-if="c.on" name="check" :size="9" :sw="3.2" /></span>
            <span class="ctext"><span class="clbl">{{ c.label }}</span><span v-if="c.hint" class="chint">{{ c.hint }}</span></span>
          </button>
        </div>
        <div class="actions">
          <button ref="cancelBtn" class="btn ghost" @click="resolveConfirm(false)">Cancel</button>
          <button v-if="confirmState.req.altLabel" class="btn" @click="resolveConfirm('alt')">{{ confirmState.req.altLabel }}</button>
          <button ref="okBtn" class="btn" :class="confirmState.req.danger ? 'danger' : 'accent'" @click="resolveConfirm(true)">
            {{ confirmState.req.okLabel || 'OK' }}
          </button>
        </div>
      </div>
    </div>
  </Transition>
</template>

<style scoped>
.overlay {
  position: fixed; inset: 0; z-index: 1000; display: flex; align-items: center; justify-content: center;
  background: rgba(6, 7, 9, .58); backdrop-filter: blur(2px);
}
.card {
  width: min(420px, calc(100vw - 40px)); background: var(--panel); border: 1px solid var(--line);
  border-radius: 12px; box-shadow: 0 24px 60px rgba(0, 0, 0, .55); padding: 20px 22px 18px;
}
.title { font: 700 15px/1.2 system-ui; color: var(--tx); }
.msg { margin-top: 10px; font: 400 13px/1.55 system-ui; color: var(--tx2); }
.actions { margin-top: 20px; display: flex; justify-content: flex-end; gap: 9px; }
.choices { margin-top: 14px; display: flex; flex-direction: column; gap: 6px; }
.choice { display: flex; align-items: center; gap: 10px; width: 100%; padding: 8px 10px; border: 1px solid var(--line); border-radius: 6px; background: var(--bg2); color: var(--tx); cursor: pointer; text-align: left; }
.choice:hover { border-color: var(--tx3); }
.ctext { display: flex; flex-direction: column; gap: 3px; min-width: 0; }
.clbl { font: 600 12px/1.2 system-ui; }
.chint { font: 400 11px/1.3 system-ui; color: var(--tx3); }
.btn.danger { background: var(--fav); border-color: var(--fav); color: #fff; }
.btn.danger:hover { filter: brightness(1.06); border-color: var(--fav); }

.fade-enter-active, .fade-leave-active { transition: opacity .14s ease; }
.fade-enter-from, .fade-leave-to { opacity: 0; }
</style>
