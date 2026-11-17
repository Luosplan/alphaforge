<script setup lang="ts">
import { computed } from 'vue'

const props = withDefaults(
  defineProps<{
    label: string
    value: string
    delta?: string
    tone?: 'up' | 'down' | 'flat' | 'brand' | ''
    hint?: string
    strong?: boolean
  }>(),
  { tone: '', strong: false },
)

const toneClass = computed(() => props.tone || 'flat')
</script>

<template>
  <div class="metric" :class="{ strong }">
    <div class="label">
      <span>{{ label }}</span>
      <el-tooltip v-if="hint" :content="hint" placement="top" :show-after="240">
        <span class="hint">i</span>
      </el-tooltip>
    </div>
    <div class="value num" :class="toneClass">{{ value }}</div>
    <div v-if="delta" class="delta num" :class="toneClass">{{ delta }}</div>
  </div>
</template>

<style scoped>
.metric {
  display: flex;
  flex-direction: column;
  gap: 3px;
  min-width: 0;
}

.metric.strong {
  background: linear-gradient(180deg, #fbfcff 0%, #ffffff 100%);
}

.label {
  display: flex;
  align-items: center;
  gap: 5px;
  font-size: 11.5px;
  color: var(--af-ink-3);
  white-space: nowrap;
}

.hint {
  display: inline-grid;
  place-items: center;
  width: 13px;
  height: 13px;
  border-radius: 50%;
  border: 1px solid var(--af-border-strong);
  color: var(--af-ink-4);
  font-size: 9px;
  font-style: normal;
  cursor: help;
  line-height: 1;
}

.value {
  font-size: 25px;
  font-weight: 640;
  letter-spacing: -0.025em;
  line-height: 1.16;
  color: var(--af-ink);
}

.value.up {
  color: var(--af-up);
}
.value.down {
  color: var(--af-down);
}
.value.brand {
  color: var(--af-ink);
}

.delta {
  font-size: 11.5px;
  color: var(--af-ink-3);
}

.delta.up {
  color: var(--af-up);
}
.delta.down {
  color: var(--af-down);
}

@media (max-width: 767px) {
  .value {
    font-size: 19px;
  }
  .label {
    font-size: 11px;
  }
}
</style>
