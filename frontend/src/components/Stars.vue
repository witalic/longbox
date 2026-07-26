<script setup lang="ts">
import Icon from './Icon.vue'
const props = withDefaults(defineProps<{ value: number; size?: number; interactive?: boolean }>(), {
  size: 12, interactive: false,
})
const emit = defineEmits<{ (e: 'set', v: number): void }>()
</script>

<template>
  <span style="display:inline-flex;gap:1px">
    <span v-for="i in 5" :key="i"
          :style="{ cursor: props.interactive ? 'pointer' : 'default', color: i <= props.value ? 'var(--warn)' : 'var(--tx3)', display: 'flex' }"
          @click.stop="props.interactive && emit('set', i)">
      <Icon name="star" :size="props.size" :sw="1.5" :fill="i <= props.value ? 'var(--warn)' : 'none'" />
    </span>
  </span>
</template>
