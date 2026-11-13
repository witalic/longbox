<script setup lang="ts">
// What the shield in the browser's toolbar opens (design/password-manager.md,
// "Ad blocking, from the shield"): blocking on every site or none, this site —
// what was blocked on its page, paused or not — the cookie banners and
// pop-ups, the sites it is paused on, and when its lists were fetched. The
// browser view owns the state (shell/adblock.js); this draws it and asks.
import Icon from './Icon.vue'

defineProps<{
  state: AdblockState
  domain: string  // the page's site, '' when it is none
  paused: string  // the paused entry that covers it (a pause covers subdomains), or ''
  blocked: number // requests blocked on the page in front
}>()
const emit = defineEmits<{
  (e: 'set', change: { on?: boolean; annoyances?: boolean; site?: string; paused?: boolean }): void
  (e: 'update'): void
}>()

function listsAge(at: number): string {
  const days = Math.floor((Date.now() - at) / 86400000)
  return days < 1 ? 'today' : days === 1 ? 'yesterday' : `${days} days ago`
}
const listsDate = (at: number) => new Date(at).toLocaleDateString([], { day: 'numeric', month: 'short' })
</script>

<template>
  <div class="ab">
    <div class="head">
      <Icon name="shield" :size="15" :sw="2" class="si" :class="{ on: state.on }" />
      <span class="t">Ad blocking<small>ads and trackers, on every site</small></span>
      <button class="toggle" :class="{ on: state.on }" role="switch" :aria-checked="state.on"
              :title="state.on ? 'Stop blocking everywhere' : 'Block ads and trackers on every site'"
              @click="emit('set', { on: !state.on })"></button>
    </div>
    <template v-if="state.on">
      <div v-if="domain" class="sec">
        <div class="plbl">THIS SITE</div>
        <div class="row">
          <span class="who"><b class="mono">{{ domain }}</b>
            <span>{{ paused ? `paused on ${paused}` : `${blocked} blocked on this page` }}</span></span>
          <button v-if="paused" class="btn small" @click="emit('set', { site: paused, paused: false })">
            <Icon name="play" :size="12" :sw="2" />Resume</button>
          <button v-else class="btn small" @click="emit('set', { site: domain, paused: true })">
            <Icon name="pause" :size="12" :sw="2" />Pause here</button>
        </div>
      </div>
      <div class="sec">
        <div class="row">
          <span class="who"><b>Hide cookie banners, pop-ups</b>
            <span>a page that waits for a consent it never got may not load</span></span>
          <button class="toggle" :class="{ on: state.annoyances }" role="switch" :aria-checked="state.annoyances"
                  @click="emit('set', { annoyances: !state.annoyances })"></button>
        </div>
      </div>
      <div v-if="state.paused.length" class="sec">
        <div class="plbl">PAUSED ON <span class="n">{{ state.paused.length }}</span></div>
        <div v-for="d in state.paused" :key="d" class="prow">
          <span class="mono dom">{{ d }}</span>
          <button class="iconbtn plain" :title="`Block ads on ${d} again`" @click="emit('set', { site: d, paused: false })">
            <Icon name="x" :size="12" :sw="2.2" /></button>
        </div>
      </div>
    </template>
    <div class="foot">
      <span class="mini" :title="state.ready ? `Fetched ${listsAge(state.updatedAt)}` : state.failed">{{
        state.updating ? 'Getting the lists…' : !state.ready ? 'The lists could not be fetched'
        : state.listsAt ? `EasyList from ${listsDate(state.listsAt)}` : `Lists from ${listsAge(state.updatedAt)}` }}</span>
      <span class="sp"></span>
      <button class="btn small ghost" :disabled="state.updating" :title="state.failed || 'Fetch the lists again now'"
              @click="emit('update')">{{ state.ready ? 'Update the lists' : 'Try again' }}</button>
    </div>
  </div>
</template>

<style scoped>
.ab { width: 360px; }
.head { display: flex; align-items: center; gap: 8px; padding: 10px 12px; border-bottom: 1px solid var(--line); min-height: 44px; }
.si { color: var(--tx3); }
.si.on { color: var(--good); }
.t { flex: 1; min-width: 0; font: 600 13px/1.2 system-ui; color: var(--tx); }
.t small { display: block; margin-top: 3px; font: 400 11px/1.2 system-ui; color: var(--tx3); }
.sec { padding: 10px 12px; }
.sec + .sec { border-top: 1px solid var(--line); }
.plbl { font: 700 9px/1 ui-monospace, monospace; letter-spacing: .12em; color: var(--tx3); margin-bottom: 8px; }
.plbl .n { color: var(--tx2); }
.row { display: flex; align-items: center; gap: 10px; min-height: 36px; }
.who { flex: 1; min-width: 0; }
.who b { display: block; font: 500 12.5px/1.25 system-ui; color: var(--tx); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.who b.mono { font-size: 12px; }
.who span { display: block; font: 400 11px/1.35 system-ui; color: var(--tx3); }
.prow { display: flex; align-items: center; gap: 10px; min-height: 28px; }
.dom { flex: 1; min-width: 0; font-size: 11.5px; color: var(--tx2); overflow: hidden; text-overflow: ellipsis; }
.prow .iconbtn { width: 26px; height: 26px; }
.toggle { border: none; padding: 0; }
.foot { display: flex; align-items: center; gap: 8px; padding: 8px 12px; border-top: 1px solid var(--line); min-height: 44px; }
.sp { flex: 1; }
.mini { font: 400 11px/1.3 system-ui; color: var(--tx3); }
</style>
