<script setup lang="ts">
import { confirmState, resolveConfirm } from '../store'
</script>

<template>
  <Transition name="fade">
    <div v-if="confirmState.open" class="overlay" @click.self="resolveConfirm(false)" @keydown.esc="resolveConfirm(false)">
      <div class="card" role="dialog" aria-modal="true">
        <div class="title">{{ confirmState.req.title || 'Please confirm' }}</div>
        <div class="msg">{{ confirmState.req.message }}</div>
        <div class="actions">
          <button class="btn ghost" @click="resolveConfirm(false)">Cancel</button>
          <button class="btn" :class="confirmState.req.danger ? 'danger' : 'accent'" autofocus @click="resolveConfirm(true)">
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
.btn.danger { background: var(--fav); border-color: var(--fav); color: #fff; }
.btn.danger:hover { filter: brightness(1.06); border-color: var(--fav); }

.fade-enter-active, .fade-leave-active { transition: opacity .14s ease; }
.fade-enter-from, .fade-leave-to { opacity: 0; }
</style>
